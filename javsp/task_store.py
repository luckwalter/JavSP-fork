"""TASKS 运行内存缓存的封装：TTL 过期 + 容量上限 + 活跃任务保护

背景
----
`javsp/server.py` 里的 `TASKS` 是 Web 层的运行内存缓存（`guid -> Movie`）。
写入点只有两处：`/api/scan`（扫描入库）与 `/api/scrape`（按 avid 刮削后入库），
而读取点有 `/api/movies`、`/api/organize`、`/api/batch`。原实现是一个**普通 dict，
没有任何删除路径**——每扫描一个新目录、每多刮削一部新影片就多一份 Movie 常驻内存。
在 NAS 上连续运行数天、或反复扫描不同媒体库时，内存会无界增长，只能靠重启容器释放
（这也是「容器重启后 TASKS 清空」被当作已知现象的原因）。

设计取舍
--------
做成 **dict 兼容**的类（实现 `__setitem__` / `__getitem__` / `get` / `__contains__` /
`values` / `__len__`），而不是引入一套全新的仓储抽象。这样 server.py 里既有的
`TASKS[guid] = m`、`TASKS.get(guid)`、`g in TASKS`、`for m in TASKS.values()`
等用法**无需任何改动**，回归面最小；同时把「何时可以安全回收」这一容易出错的策略
集中到一处，便于单测覆盖。

三种回收手段
------------
1. **TTL 过期**：超过 `ttl` 未被访问的条目在下次写入时顺带清理，避免隔夜/久置残留。
   使用 `time.monotonic()`，不受系统时间跳变（NTP 校时、时区切换）影响。
2. **容量上限**：条目数超过 `max_tasks` 时按「最旧优先」淘汰，防止单次超大目录扫描
   （几千部影片）一次性把内存打满。
3. **活跃保护**：正在被 scrape / organize 的条目登记到 `_active`，回收时跳过，
   避免任务做到一半被删导致落盘失败。

清理**只在写入时触发**（`_evict_locked`）：内存增长必然来自写入，因此在写入点维护即可，
无需引入后台定时线程；若长时间没有新写入，内存占用本身是稳定的，不清理也无害。
`evict_expired()` 另作为显式 API 暴露，便于测试与潜在的按需调用。

参数可通过环境变量按部署规模调整（`JAVSP_TASK_TTL` 秒 / `JAVSP_TASK_MAX` 条）。
"""
import os
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Set

# 默认 6 小时：覆盖「批量刮削 → 整理 → 用户查看结果」的完整会话，
# 又能保证隔夜残留不会无限累积。长跑场景主要靠容量上限兜底。
DEFAULT_TTL_SECONDS = int(os.getenv('JAVSP_TASK_TTL', str(6 * 3600)))
DEFAULT_MAX_TASKS = int(os.getenv('JAVSP_TASK_MAX', '2000'))


class TaskStore:
    """线程安全的 guid -> Movie 内存缓存，带 TTL 过期与容量上限。

    对外表现为 dict，因此可直接替换原先的普通 dict 而无需改动调用方。
    """

    def __init__(self, ttl: float = DEFAULT_TTL_SECONDS,
                 max_tasks: int = DEFAULT_MAX_TASKS):
        # ttl <= 0 表示禁用 TTL 过期；max_tasks <= 0 表示不限制容量
        self._ttl = ttl
        self._max = max_tasks
        self._data: Dict[str, Any] = {}
        # guid -> 最后写入/访问时刻（monotonic 秒）
        self._ts: Dict[str, float] = {}
        # 正在处理中的 guid：scrape / organize 期间受保护，不参与回收
        self._active: Set[str] = set()
        self._lock = threading.RLock()

    # ------------------------- dict 兼容接口 -------------------------

    def __setitem__(self, key, value) -> None:
        with self._lock:
            self._data[key] = value
            self._ts[key] = time.monotonic()
            self._evict_locked()

    def __getitem__(self, key):
        with self._lock:
            # 命中即刷新时间戳：让 TTL 表达「最近一次活跃」而非「创建时间」，
            # 避免用户持续操作某个影片时被误回收。
            value = self._data[key]
            self._ts[key] = time.monotonic()
            return value

    def get(self, key, default=None):
        with self._lock:
            if key not in self._data:
                return default
            self._ts[key] = time.monotonic()
            return self._data[key]

    def __contains__(self, key) -> bool:
        with self._lock:
            return key in self._data

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def __iter__(self) -> Iterator[str]:
        return iter(self.keys())

    def keys(self) -> List[str]:
        with self._lock:
            return list(self._data.keys())

    def values(self) -> List[Any]:
        with self._lock:
            return list(self._data.values())

    def items(self):
        with self._lock:
            return list(self._data.items())

    # --------------------------- 回收策略 ---------------------------

    def evict_expired(self) -> List[str]:
        """删除超过 TTL 未访问的条目（活跃中的除外），返回被删的 guid 列表。

        主要供测试与按需调用；正常路径由每次写入时的 `_evict_locked` 顺带完成。
        """
        if self._ttl <= 0:
            return []
        with self._lock:
            now = time.monotonic()
            expired = [k for k, ts in self._ts.items()
                       if (now - ts) > self._ttl and k not in self._active]
            for k in expired:
                self._data.pop(k, None)
                self._ts.pop(k, None)
            return expired

    def _evict_locked(self) -> None:
        """写入后触发回收：先清过期项，仍超容量则按最旧优先淘汰。

        调用方须已持有 `self._lock`。两项都会跳过活跃条目——宁可暂时超容量，
        也不能把正在处理的影片从缓存里删掉。
        """
        if self._ttl > 0:
            now = time.monotonic()
            for k in [k for k, ts in self._ts.items()
                      if (now - ts) > self._ttl and k not in self._active]:
                self._data.pop(k, None)
                self._ts.pop(k, None)

        if self._max <= 0 or len(self._data) <= self._max:
            return
        # 容量仍超限 → 按时间戳从旧到新淘汰
        for k, _ts in sorted(self._ts.items(), key=lambda kv: kv[1]):
            if len(self._data) <= self._max:
                break
            if k in self._active:
                continue
            self._data.pop(k, None)
            self._ts.pop(k, None)

    @contextmanager
    def mark_active(self, *keys: str):
        """把任务标记为「处理中」，期间不被 TTL/容量回收。

        用于 organize / batch 等会长时间持有 Movie 的流程；必须用 with 保证
        异常路径下也能解除标记，否则条目会被永久保护、永不回收。
        """
        with self._lock:
            for k in keys:
                self._active.add(k)
        try:
            yield
        finally:
            with self._lock:
                for k in keys:
                    self._active.discard(k)

    def is_active(self, key) -> bool:
        with self._lock:
            return key in self._active

    def stats(self) -> Dict[str, Any]:
        """当前状态快照，供 `/api/health` 等观测使用。"""
        with self._lock:
            return {
                'count': len(self._data),
                'active': len(self._active),
                'ttl_seconds': self._ttl,
                'max_tasks': self._max,
            }