"""配置热重载：保存 config.yml 后让运行时立即生效（免重启）

为什么不只是「重载配置对象」
----------------------------
`Cfg` 是 confz 的单例（缓存在 `Cfg.confz_instance`），置空后下次 `Cfg()` 会重新读盘，
这一点就能让 core.py 里那些 `Cfg().xxx` 的读取立即生效。

但**爬虫不在此列**：`javsp/web/*.py` 在 import 时就创建了模块级 `request = Request(...)`，
而 `Request.__init__` 会把代理和超时**当场固化**（`self.proxies = read_proxy()`、
`self.timeout = Cfg().network.timeout.total_seconds()`）。也就是说，光重载 `Cfg`，
正在运行的爬虫仍会拿着旧代理发请求 —— 用户在界面上改了代理却看不到效果。
因此重载后还要遍历已加载的爬虫模块，把它们的出口刷一遍。

安全设计
--------
重载可能失败（比如写入了非法值）。此时若不处理，`Cfg.confz_instance` 会一直是 None，
后续任何 `Cfg()` 都会重新触发重建并抛异常 —— 等于把整个服务打瘫。
所以重载全程用 try/except 包裹，失败立即**恢复旧实例**，并把文件回滚。

线程安全
--------
Web 服务是多线程的（刮削用线程池），写入与重载用同一把锁串行化，避免「写到一半被重载读到」。
配合 `config_io._atomic_write`，读端永远只看到完整的新文件或旧文件。
"""
import os
import sys
import logging
import threading
import tempfile

from javsp.config_io import write_config_preserving_comments

__all__ = ['reload_runtime_config', 'apply_config_changes', 'describe_runtime',
           'config_transaction']

logger = logging.getLogger(__name__)

# 写入与重载共用，避免多线程下「写到一半被重载读到」
_lock = threading.RLock()


class config_transaction:
    """配置读-改-写的临界区上下文管理器（复用 apply_config_changes 的同一把 RLock）。

    并发 PUT /api/config 时，若「取快照 + 合并 + 算 diff」在锁外，两个请求会各自基于
    旧快照算 changes，后写者把先写者的改动覆盖掉（丢失更新）。调用方应把整个
    「读快照→合并→校验→apply」包进本上下文。
    """

    def __enter__(self):
        _lock.acquire()
        return _lock

    def __exit__(self, *exc):
        _lock.release()
        return False


def _iter_crawler_modules():
    """产出 (模块名, 模块对象)，仅限已加载且带模块级 request 实例的爬虫"""
    for name, mod in list(sys.modules.items()):
        if not name.startswith('javsp.web.'):
            continue
        if getattr(mod, 'request', None) is None:
            continue
        yield name, mod


def _refresh_crawler_requests():
    """把各爬虫模块级 Request 的代理/超时刷成当前配置

    超时下限取自模块内的 `_TIMEOUT_FLOOR`（airav=20s、javlib=5s，服务器特性所致），
    没有该常量的模块下限为 0，即直接用全局 `network.timeout`，与 Request 初始化行为一致。

    返回被刷新的爬虫名列表
    """
    from javsp.config import Cfg
    from javsp.web.base import read_proxy

    timeout = Cfg().network.timeout.total_seconds()
    proxies = dict(read_proxy())
    refreshed = []
    for name, mod in _iter_crawler_modules():
        try:
            req = mod.request
            floor = getattr(mod, '_TIMEOUT_FLOOR', 0) or 0
            req.timeout = max(floor, timeout)
            req.proxies = dict(proxies)
            refreshed.append(name.rsplit('.', 1)[-1])
        except Exception as e:      # 单个爬虫刷新失败不应影响其它爬虫
            logger.warning(f'刷新爬虫出口失败 {name}: {e}')
    return refreshed


def reload_runtime_config():
    """重新读取 config.yml 并刷新爬虫出口

    返回 dict: {'ok': bool, 'refreshed': [爬虫名], 'error': str|None}
    失败时运行时配置保持原样（旧实例已恢复），调用方可据此回滚文件。
    """
    from javsp.config import Cfg

    backup = Cfg.confz_instance
    try:
        Cfg.confz_instance = None           # 置空 → 下次 Cfg() 重新从磁盘加载
        Cfg()                               # 触发重建（配置非法时会在此抛 ValidationError）
        refreshed = _refresh_crawler_requests()
        logger.info(f'配置已热重载，刷新爬虫出口 {len(refreshed)} 个')
        return {'ok': True, 'refreshed': refreshed, 'error': None}
    except Exception as e:
        Cfg.confz_instance = backup         # 关键：恢复旧实例，避免后续所有 Cfg() 全崩
        logger.error(f'配置热重载失败，已回滚运行时配置: {e}')
        return {'ok': False, 'refreshed': [], 'error': str(e)}


def _slimeface_available():
    """slimeface 是否可用

    它是**可选依赖**：缺失时 AI 裁剪会静默回退到默认裁剪，功能看起来“开了没效果”。
    这里给出明确探针，界面可据此提示「依赖未安装」。
    """
    try:
        import slimeface       # noqa: F401  # pylint: disable=unused-import
        return True
    except Exception:
        return False


def describe_runtime():
    """取当前运行时配置的关键项（供接口返回/验证脚本断言）"""
    from javsp.config import Cfg
    from javsp.core import output_enabled  # 局部导入: 开关判据唯一出处, 避免与 Web/CLI 出现语义漂移

    def _mask_proxy(url):
        """代理URL 可能内嵌 user:pass@host, 属凭据, 输出给前端前抹掉 userinfo"""
        if not url:
            return None
        s = str(url)
        if '://' not in s or '@' not in s:
            return s
        scheme, rest = s.split('://', 1)
        userinfo, hostpart = rest.rsplit('@', 1)
        # 只保留用户名(便于排查是哪个账号), 抹掉密码
        user = userinfo.split(':', 1)[0]
        return f'{scheme}://{user}:***@{hostpart}'

    cfg = Cfg()
    crawlers = {}
    for name, mod in _iter_crawler_modules():
        req = mod.request
        proxies = dict(getattr(req, 'proxies', None) or {})
        crawlers[name.rsplit('.', 1)[-1]] = {
            'timeout': getattr(req, 'timeout', None),
            'proxies': {k: _mask_proxy(v) for k, v in proxies.items()},
        }
    crop_cfg = cfg.summarizer.cover.crop
    return {
        'proxy_server': _mask_proxy(cfg.network.proxy_server),
        'timeout': cfg.network.timeout.total_seconds(),
        'retry': cfg.network.retry,
        'max_concurrency': cfg.crawler.max_concurrency,
        'crawlers': crawlers,
        # 封面裁剪：enabled 是配置意图，available 是依赖是否真的装了，
        # 两者都为真 AI 裁剪才可能生效（最终还得看封面里检测不检测得到人脸）
        'cover_crop': {
            'engine': crop_cfg.engine.name if crop_cfg.engine else None,
            'enabled': crop_cfg.engine is not None,
            'available': _slimeface_available(),
            'on_id_pattern': list(crop_cfg.on_id_pattern or []),
        },
        # 输出开关：告诉调用方哪些产物这次会被生成（由 Jellyfin 自行管理元数据时可全部关闭）
        'output': {
            'poster': output_enabled('poster'),
            'fanart': output_enabled('fanart'),
            'extrafanart': output_enabled('extrafanart'),
            'nfo': output_enabled('nfo'),
        },
    }


def _read_bytes(path):
    with open(path, 'rb') as f:
        return f.read()


def _atomic_write_bytes(path, data):
    """原子写回（同目录临时文件 + os.replace）。

    回滚路径必须也是原子的：原先用 open(path,'wb') 会先把文件截断为 0，
    回滚窗口内并发 Cfg() 会读到空/半份配置；若进程在回滚中途崩溃，磁盘上会留下
    长度为 0 的 config.yml —— 下次启动直接失败(持久化损坏，需人工修)。
    """
    folder = os.path.dirname(os.path.abspath(path)) or '.'
    fd, tmp = tempfile.mkstemp(prefix='.cfg_', suffix='.tmp', dir=folder)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def apply_config_changes(cfg_path, changes):
    """写入变更并让运行时立即生效

    任一环节失败都会回滚：先恢复文件内容，再让运行时重新读回旧配置。
    返回 dict: {'written', 'missing', 'reloaded', 'refreshed', 'error', 'rolled_back'}
    """
    result = {'written': 0, 'missing': [], 'reloaded': False,
              'refreshed': [], 'error': None, 'rolled_back': False}
    if not changes:
        return result

    with _lock:
        original = _read_bytes(cfg_path)
        try:
            n, missing = write_config_preserving_comments(cfg_path, changes)
        except Exception as e:
            raise RuntimeError(f'写入 config.yml 失败: {e}') from e
        result['written'] = n
        result['missing'] = missing
        if n == 0:
            return result

        rr = reload_runtime_config()
        result['reloaded'] = rr['ok']
        result['refreshed'] = rr['refreshed']
        result['error'] = rr['error']
        if not rr['ok']:
            # 新配置加载不了 —— 回滚文件，并让运行时回到旧配置
            _atomic_write_bytes(cfg_path, original)
            reload_runtime_config()
            result['rolled_back'] = True
    return result
