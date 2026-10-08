"""从 javdb 手机 App 私有 API 抓取数据（绕过 Cloudflare 网站墙）

为什么需要它:
    javdb.com 网站在受限出口(如 NAS 的 squid 机房 IP)下被 Cloudflare 拦截(403),
    原 `javdb.py`(网站版)因此长期取不到数据。但 javdb 同时提供手机 App 私有 API
    (`jdforrepam.com`), 不受 Cloudflare 墙限制, 在同样的出口下实测可取回完整元数据。

实现来源(合规):
    请求协议与字段映射移植自 github.com/Solr159/JavBoss 的 `internal/jav/javdbapi`
    (MIT, 其源码注释声明 App 协议源自 javdb-cli)。本文件为独立 Python 实现, 仅复用
    协议与字段定义, 不复制其 Go 代码; 按 MIT 要求保留来源署名。

与本项目规范的对接:
    - 复用 `javsp.web.base` 的 `read_proxy()` / `tls_verify()`: 每次请求现取代理与 TLS
      策略, 不在此模块级固化(否则 `config_reload` 后不会生效, 见 v0.1.12 教训)。
    - 对齐 `parse_data(movie)` 接口, 直接写入 `MovieInfo`, 由 `core.py` 动态发现。
    - 找不到影片时抛 `MovieNotFoundError`, 由 core 干净跳过(见 v0.1.28 优雅降级约定)。
"""
import re
import time
import logging
import hashlib
import requests

from javsp.web.base import read_proxy, tls_verify
from javsp.web.exceptions import *
from javsp.datatype import MovieInfo
from javsp.config import Cfg


logger = logging.getLogger(__name__)
BASE_URL = 'https://jdforrepam.com'
PERMANENT_URL = 'https://javdb.com'

# jdsignature 签名前缀(来自 javdb App 协议, 移植自 JavBoss javdbapi/api.go)
_JAVDB_API_SIGN_PREFIX = '71cf27bb3c0bcdf207b64abecddc970098c7421ee7203b9cdae54478478a199e7d5a6e1a57691123c1a931c057842fb73ba3b3c83bcd69c17ccf174081e3d8aa'
# 固定的设备标识(对应 JavBoss 的 device_uuid; 一台设备一个即可, 无需每次随机)
_DEVICE_UUID = 'a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d'

# 最小请求间隔: 对齐 JavBoss 的 500ms 限流, 避免连续打站点触发风控
_MIN_INTERVAL = 0.5
_last_req = 0.0

# FC2 番号在查询时去掉 PPV 段(与 JavBoss javDBAPIQueryCode 一致)
_FC2_RE = re.compile(r'(?i)^FC2-(?:PPV-)?([0-9]+)$')


def _throttle():
    """两次请求之间至少间隔 _MIN_INTERVAL 秒"""
    global _last_req
    now = time.monotonic()
    wait = _MIN_INTERVAL - (now - _last_req)
    if wait > 0:
        time.sleep(wait)
    _last_req = time.monotonic()


def _signature(ts: int) -> str:
    digest = hashlib.md5(f"{ts}{_JAVDB_API_SIGN_PREFIX}".encode()).hexdigest()
    return f"{ts}.lpw6vgqzsp.{digest}"


def _headers(ts: int) -> dict:
    return {
        'jdsignature': _signature(ts),
        'User-Agent': 'Dart/3.4 (dart:io)',
        'Accept': 'application/json',
        'Accept-Language': 'zh-TW',
    }


def _base_params(q: str = None) -> dict:
    params = {
        'app_channel': 'official',
        'app_version': '1.9.28',
        'app_version_number': '10928',
        'platform': 'android',
        'system_version': '13',
        'device_model': 'Pixel 6',
        'device_name': 'Pixel',
        'device_uuid': _DEVICE_UUID,
    }
    if q is not None:
        params['q'] = q
        params['page'] = '1'
        params['limit'] = '100'
    return params


def _normalize_code(code: str) -> str:
    """查询用归一: FC2-PPV-xxx -> FC2-xxx; 其余去空白(分隔符仍保留, 见 JavBoss 注释)"""
    code = (code or '').strip()
    m = _FC2_RE.match(code)
    if m:
        return 'FC2-' + m.group(1)
    return code


def _text(v):
    """把 API 返回的任意类型字段安全地转成去空白字符串(None/bool->空串)

    App API 对部分影片把 maker_name/release_date/duration 等返回成 number 而非统一
     string(JavBoss 侧的 javDBAPIValue 会自动归一, Python 端需自己处理, 否则 .strip() 崩)。
    """
    if v is None or isinstance(v, bool):
        return ''
    return str(v).strip()


def _resolve_image(value):
    """解析图片 URL: 绝对地址直接用, 相对路径拼 BASE_URL (对应 JavBoss ResolveSampleImageURL)"""
    value = _text(value)
    if not value or value == '#' or value.lower().startswith(('javascript:', 'data:')):
        return None
    if value.startswith(('http://', 'https://')):
        return value
    return BASE_URL.rstrip('/') + '/' + value.lstrip('/')


def _api_get(path: str, params: dict):
    """发起一次 App API 请求, 返回 envelope 的 data 字段; 404 视为未找到, 其余异常上抛由 core 重试"""
    _throttle()
    ts = int(time.time())
    proxies = read_proxy()
    verify = tls_verify()
    # jdforrepam.com 经受限出口(如 squid)响应偏慢, 给足下限避免偶发 ReadTimeout
    timeout = max(Cfg().network.timeout.total_seconds(), 15.0)
    try:
        r = requests.get(BASE_URL + path, params=params, headers=_headers(ts),
                         proxies=proxies, timeout=timeout, verify=verify)
    except requests.exceptions.RequestException as e:
        logger.debug(f'javdbapi 请求异常: {path} -> {e!r}')
        raise
    if r.status_code == 404:
        raise MovieNotFoundError(__name__, params.get('q') or path)
    if r.status_code != 200:
        raise WebsiteError(f'javdbapi: HTTP {r.status_code} ({path})')
    try:
        env = r.json()
    except ValueError as e:
        raise WebsiteError(f'javdbapi: 响应非 JSON ({path}): {e}')
    success = str(env.get('success', '')).strip().strip('"')
    if success not in ('1', 'true', 'True'):
        raise WebsiteError(f"javdbapi: 请求被拒绝 (action={env.get('action')})")
    return env.get('data')


def parse_data(movie: MovieInfo):
    """从 javdb App API 抓取并解析指定番号的数据, 直接写入 movie

    Args:
        movie (MovieInfo): 要解析的影片信息, 解析后的信息直接更新到此变量内
    """
    code = _normalize_code(movie.dvdid)
    if not code:
        raise MovieNotFoundError(__name__, movie.dvdid)

    # 1) 搜索: 取候选列表, 按 number 精确匹配(忽略大小写)
    search = _api_get('/api/v2/search', _base_params(code))
    movies = (search or {}).get('movies') or []
    matched = None
    for m in movies:
        if (m.get('number') or '').strip().lower() == code.lower():
            matched = m
            break
    if not matched or not matched.get('id'):
        raise MovieNotFoundError(__name__, movie.dvdid)
    mid = matched['id']

    # 2) 详情: /api/v4/movies/{id}, 响应可能是 {movie:{...}} 或直接 {...}
    detail = _api_get(f'/api/v4/movies/{mid}', _base_params())
    if isinstance(detail, dict) and 'movie' in detail:
        detail = detail['movie']
    if not isinstance(detail, dict):
        raise MovieNotFoundError(__name__, movie.dvdid)

    # 3) 填充 (字段映射对照 JavBoss javdbapi/api.go 的 LookupJavByCode)
    # 部分字段对某些影片返回 number 类型(非统一 string), 统一用 _text 防 strip 崩
    title = _text(detail.get('origin_title')) or _text(detail.get('title'))
    if title:
        movie.title = title
    # 不覆盖 movie.dvdid: core 以输入番号关联各源结果, 改写会破坏合并
    movie.url = f'{PERMANENT_URL}/movies/{mid}'

    cover = _resolve_image(detail.get('cover_url'))
    if cover:
        movie.cover = cover

    previews = []
    for pi in (detail.get('preview_images') or []):
        big = _resolve_image(pi.get('large_url') or pi.get('thumb_url'))
        if big:
            previews.append(big)
    if previews:
        movie.preview_pics = previews

    if _text(detail.get('maker_name')):
        movie.producer = _text(detail.get('maker_name'))
    if _text(detail.get('series_name')):
        movie.serial = _text(detail.get('series_name'))
    if _text(detail.get('release_date')):
        movie.publish_date = _text(detail.get('release_date'))
    dur = _text(detail.get('duration'))
    if dur:
        movie.duration = dur

    tags = [_text(t.get('name')) for t in (detail.get('tags') or []) if _text(t.get('name'))]
    if tags:
        movie.genre = tags
    # App API 的 tags 不带 id, 故不生成 genre_id (genre_norm 留空, 由其他源补充)
    actors = [_text(a.get('name')) for a in (detail.get('actors') or [])
              if str(a.get('gender')) == '0' and _text(a.get('name'))]
    if actors:
        movie.actress = actors

    t = detail.get('type')
    if str(t) in ('0', '1'):
        movie.uncensored = (str(t) == '1')


if __name__ == "__main__":
    import pretty_errors
    pretty_errors.configure(display_link=True)
    logger.root.handlers[1].level = logging.DEBUG

    movie = MovieInfo('IPX-001')
    try:
        parse_data(movie)
        print(movie)
    except CrawlerError as e:
        logger.error(e, exc_info=1)
