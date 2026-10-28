"""翻译结果缓存

刮削同一部影片(或重跑失败任务)时不重复调用翻译引擎 —— 省 API 调用次数与配额,
也避免重复刮削时反复等待引擎响应。

设计要点:
1. **存储**: sqlite(标准库, 进程内多线程安全)。单表存 key/译文/断句/时间戳,
   key 用 sha256, 天然规避路径穿越与非法字符。
2. **目录**: 复用与缩略图一致的判定规则 —— 容器 ``/data/.javsp_cache/translations``
   (点开头, 媒体服务器默认跳过)、桌面版 exe 同级 ``cache/translations``;
   可用 ``cache.translation_dir`` 显式覆盖, 判定失败回退系统临时目录。
3. **失效**: key 由「引擎 + 模型 + 目标语种 + prompt 摘要 + 原文」算出, 任一项
   变化都会自动 miss, 不会出现换模型后仍复用旧译文的情况。
4. **降级**: 缓存是性能优化, 不是功能依赖。建库/读/写/清理任何一步失败都只记
   日志并返回空值, **绝不打断翻译主流程**。
5. **只缓存成功**: 引擎返回错误的结果不入缓存, 避免一次网络抖动被长期记住。
6. **容量可控**: 条数有上限(超限按时间淘汰最旧的一批), 清理后 VACUUM 真正收缩
   文件 —— 否则用户点了"清空"却看到占用字节纹丝不动。
"""
import hashlib
import logging
import os
import sqlite3
import sys
import tempfile
import threading
import time

logger = logging.getLogger(__name__)

# 容器形态的媒体库挂载点与缓存子目录(与 thumbs.py 保持一致)
_CONTAINER_DATA = '/data'
_CONTAINER_SUBDIR = os.path.join('.javsp_cache', 'translations')
_DB_NAME = 'translations.sqlite3'

# 缓存条数上限与超限时的淘汰比例。
# 译文条目很小(一条几百字节), 但长期运行的实例会持续累积: 每刮削一部影片新增
# 2 条(标题+简介), 换模型/改提示词/换目标语种又会产生一批新 key(这些 key 永不
# 命中)。不设上限, sqlite 文件就会无限增长 —— 容器里它长在媒体库所在的 /data,
# 桌面版长在 exe 同级, 都是用户的可见磁盘。达到上限时按 created 淘汰最旧的
# 一批(而不是只删一条), 避免频繁触发淘汰逻辑。
_MAX_ROWS = 20000
_EVICT_RATIO = 0.1
# 每写入多少次检查一次条数上限: 每次写入都 COUNT(*) 是浪费(有索引但仍要扫),
# 采样检查足以把规模压在上限附近。
_CHECK_EVERY = 100

# sqlite 连接是进程级共享资源: 建连接与建表都在这个锁内做, 避免多线程刮削时
# 并发初始化导致 "table already exists" 或连接被过早关闭。
_lock = threading.Lock()
_conn = None
_conn_path = None
_write_count = 0


def _config():
    """取当前配置; 取不到返回 None, 由调用方走默认值"""
    try:
        from javsp.config import Cfg
        return Cfg()
    except Exception as e:      # 配置异常不应让缓存(进而翻译)整体不可用
        logger.debug('读取配置失败, 使用默认缓存策略: %s', e)
        return None


def _default_dir() -> str:
    """自动判定缓存目录: 容器 -> /data 下隐藏目录; 否则 exe/项目根同级 cache"""
    if os.path.isdir(_CONTAINER_DATA) and os.access(_CONTAINER_DATA, os.W_OK):
        return os.path.join(_CONTAINER_DATA, _CONTAINER_SUBDIR)
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(os.path.abspath(sys.executable))
        return os.path.join(base, 'cache', 'translations')
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'cache', 'translations')


def cache_dir() -> str:
    """返回缓存目录(会尝试创建); 不可写时回退系统临时目录"""
    cfg = _config()
    custom = ''
    if cfg is not None:
        try:
            custom = (getattr(cfg.cache, 'translation_dir', '') or '').strip()
        except Exception:
            custom = ''
    path = custom or _default_dir()
    try:
        os.makedirs(path, exist_ok=True)
        return path
    except OSError as e:
        fallback = os.path.join(tempfile.gettempdir(), 'javsp_translations')
        logger.warning('翻译缓存目录 %s 不可用(%s), 回退到 %s', path, e, fallback)
        try:
            os.makedirs(fallback, exist_ok=True)
        except OSError:
            pass
        return fallback


def enabled() -> bool:
    """缓存总开关(cache.translation_enabled, 默认开)"""
    cfg = _config()
    if cfg is None:
        return True
    try:
        return bool(getattr(cfg.cache, 'translation_enabled', True))
    except Exception:
        return True


def make_key(engine_id: str, to: str, prompt: str, text: str) -> str:
    """缓存 key: 引擎/模型 + 目标语种 + prompt 摘要 + 原文

    prompt 参与计算是为了让"改了 LLM 提示词"自动失效, 否则旧译文会被一直复用。
    """
    digest = hashlib.sha256((prompt or '').encode('utf-8')).hexdigest()[:16]
    raw = f'{engine_id}\x00{to}\x00{digest}\x00{text}'
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _connect(path: str):
    """建立连接并确保表存在"""
    global _conn, _conn_path
    with _lock:
        if _conn is not None and _conn_path == path:
            return _conn
        conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
        conn.execute(
            'CREATE TABLE IF NOT EXISTS translations ('
            ' key TEXT PRIMARY KEY,'
            ' trans TEXT NOT NULL,'
            ' orig_break TEXT,'
            ' trans_break TEXT,'
            ' engine TEXT,'
            ' created REAL NOT NULL)'
        )
        conn.execute('CREATE INDEX IF NOT EXISTS idx_created ON translations(created)')
        conn.commit()
        _conn, _conn_path = conn, path
        return conn


def _vacuum(conn) -> None:
    """真正收缩数据库文件

    DELETE 只是把页标记为空闲(后续写入可复用), **文件本身不会变小**。用户在设置
    页点了"清空缓存"却看到占用字节纹丝不动, 只会以为清理没生效 —— 清理后
    VACUUM 一次把空间还给磁盘。
    """
    try:
        conn.commit()            # 结束 sqlite3 隐式开启的事务
        conn.execute('VACUUM')   # VACUUM 不能在事务内执行
    except Exception as e:
        logger.debug('VACUUM 失败(忽略): %s', e)


def _enforce_limit(conn) -> int:
    """条数超上限时按时间淘汰最旧的一批, 返回删除条数"""
    n = conn.execute('SELECT COUNT(*) FROM translations').fetchone()[0]
    if n <= _MAX_ROWS:
        return 0
    keep = int(_MAX_ROWS * (1 - _EVICT_RATIO))
    cur = conn.execute(
        'DELETE FROM translations WHERE key IN ('
        '  SELECT key FROM translations ORDER BY created ASC LIMIT ?)', (n - keep,))
    conn.commit()
    deleted = cur.rowcount or 0
    if deleted:
        logger.info('翻译缓存已达上限(%d 条 > %d), 淘汰最旧的 %d 条',
                    n, _MAX_ROWS, deleted)
    return deleted


def get(key: str):
    """取缓存的译文; 未命中或任何异常返回 None"""
    if not enabled():
        return None
    try:
        conn = _connect(os.path.join(cache_dir(), _DB_NAME))
        with _lock:
            row = conn.execute(
                'SELECT trans, orig_break, trans_break FROM translations WHERE key = ?', (key,)
            ).fetchone()
        if row is None:
            return None
        import json
        out = {'trans': row[0]}
        if row[1]:
            out['orig_break'] = json.loads(row[1])
        if row[2]:
            out['trans_break'] = json.loads(row[2])
        logger.debug('翻译缓存命中: %s', key[:12])
        return out
    except Exception as e:
        logger.debug('读取翻译缓存失败(忽略): %s', e)
        return None


def put(key: str, value: dict, engine: str = '') -> None:
    """写入缓存; 只存成功结果(调用方保证)。任何异常都忽略。"""
    global _write_count
    if not enabled() or not isinstance(value, dict) or 'trans' not in value:
        return
    try:
        import json
        conn = _connect(os.path.join(cache_dir(), _DB_NAME))
        with _lock:
            conn.execute(
                'INSERT OR REPLACE INTO translations(key, trans, orig_break, trans_break, engine, created)'
                ' VALUES (?,?,?,?,?,?)',
                (key, value['trans'],
                 json.dumps(value.get('orig_break'), ensure_ascii=False) if value.get('orig_break') else None,
                 json.dumps(value.get('trans_break'), ensure_ascii=False) if value.get('trans_break') else None,
                 engine, time.time())
            )
            conn.commit()
            _write_count += 1
            if _CHECK_EVERY <= 1 or _write_count % _CHECK_EVERY == 0:
                _enforce_limit(conn)
    except Exception as e:
        logger.debug('写入翻译缓存失败(忽略): %s', e)


def prune(retention_days: int = 30) -> int:
    """清理超过保留天数的条目, 返回删除条数"""
    try:
        cutoff = time.time() - max(int(retention_days), 1) * 86400
        conn = _connect(os.path.join(cache_dir(), _DB_NAME))
        with _lock:
            cur = conn.execute('DELETE FROM translations WHERE created < ?', (cutoff,))
            conn.commit()
            n = cur.rowcount or 0
        if n:
            _vacuum(conn)
            logger.info('已清理过期翻译缓存 %d 条', n)
        return n
    except Exception as e:
        logger.debug('清理翻译缓存失败(忽略): %s', e)
        return 0


def stats() -> dict:
    """缓存统计(供设置页展示): 条数 / 占用字节 / 目录 / 条数上限"""
    info = {'enabled': enabled(), 'count': 0, 'size': 0, 'dir': '', 'max': _MAX_ROWS}
    try:
        d = cache_dir()
        info['dir'] = d
        db = os.path.join(d, _DB_NAME)
        if os.path.isfile(db):
            info['size'] = os.path.getsize(db)
        conn = _connect(db)
        with _lock:
            info['count'] = conn.execute('SELECT COUNT(*) FROM translations').fetchone()[0]
    except Exception as e:
        logger.debug('统计翻译缓存失败(忽略): %s', e)
    return info


def clear() -> int:
    """清空全部翻译缓存, 返回删除条数"""
    try:
        conn = _connect(os.path.join(cache_dir(), _DB_NAME))
        with _lock:
            cur = conn.execute('DELETE FROM translations')
            conn.commit()
            n = cur.rowcount or 0
        # 清空后必须 VACUUM: DELETE 只标记空闲页, 文件不缩小, 用户看到的
        # "占用字节"会纹丝不动
        _vacuum(conn)
        logger.info('已手动清空翻译缓存 %d 条', n)
        return n
    except Exception as e:
        logger.debug('清空翻译缓存失败(忽略): %s', e)
        return 0


__all__ = ['cache_dir', 'clear', 'enabled', 'get', 'make_key', 'prune', 'put', 'stats']
