"""刮削渠道健康监控与熔断 (移植自 JavBoss internal/jav/availability.go 的能力, Python 实现)

==== 为什么需要 ====
受限出口下多个源会陆续失效(JavSP-fork v0.1.28 实证: javlib/javbus 已废、javdb 网站被
Cloudflare 403)。旧实现的代价是**纯浪费**: 每次刮削仍会对已死站点完整跑完
retry(默认3次) x timeout(默认10s) 的重试, 一部影片白等几十秒, 用户只看到"没刮到"。
本模块让死源在刮削前被**自动跳过**, 同时把每个源的真实可用性与失败原因暴露给 WebUI。

==== 核心机制: 熔断器(circuit breaker) ====
借鉴 JavBoss 的 availability 检查, 但**不止于"看", 更要"省"**:

    closed(正常) --连续失败达阈值--> open(熔断, 刮削直接跳过)
    open --冷却期到--> half_open(放一个请求试探)
    half_open --成功--> closed      half_open --失败--> open(冷却期翻倍)

关键设计取舍:
1. **熔断阈值 2 而非 1**: 单次失败可能只是网络抖动(实测 squid 偶发超时)。熔断太激进会
   把好源误杀, 导致该源的数据永远拿不到 —— 那是比"多等几秒"严重得多的回归。
2. **冷却期随失败次数翻倍**(10s -> 20s -> 40s..., 上限 5 分钟): 避免对已死站点持续
   无效重试而白白消耗出口带宽; 又能自动恢复, 无需人工干预。
3. **只熔断"通道级故障"(network/timeout/dns/tls/blocked/http_error) 与硬错误**;
   **绝不因 not_found / duplicate 熔断** —— 那只说明"这个源没收录这部影片", 源本身是好的。
   混淆这两者会导致好源被误杀。
4. **半开只放一个请求**, 且成功才恢复, 失败立刻重新熔断 —— 标准熔断语义, 防并发雪崩。
5. 探活**在后台线程定时跑**(默认 300s), 不阻塞刮削; 刮削路径只读缓存状态, 零额外请求。
   探活只覆盖当前**启用**的源, 且每次只查 1 个样本, 对出口压力可忽略。

==== 状态分类(对齐 JavBoss, 便于对照)====
ok / timeout / dns_error / tls_error / network_error / http_error / blocked /
credential_error / error = 以上皆非的硬错误
invalid_response / not_found / duplicate = 源可达但本次没拿到有效数据(**不熔断**)
canceled / unknown / unchecked
"""
import logging
import re
import threading
import time
from typing import Dict, List, Optional

import requests
from requests.exceptions import (
    ConnectionError as ReqConnectionError,
    ConnectTimeout,
    ProxyError,
    ReadTimeout,
    SSLError,
    Timeout,
)

from javsp.config import Cfg, CrawlerID
from javsp.datatype import MovieInfo
from javsp.web.exceptions import (
    CredentialError,
    MovieDuplicateError,
    MovieNotFoundError,
    SiteBlocked,
    SitePermissionError,
)


logger = logging.getLogger(__name__)

# ---- 熔断参数 ----
FAILURE_THRESHOLD = 2          # 连续失败多少次后熔断(不取1: 防网络抖动误杀好源)
COOLDOWN_BASE = 10.0           # 冷却基数(秒); 随连续失败翻倍, 上限 COOLDOWN_MAX
COOLDOWN_MAX = 300.0           # 冷却上限(秒)
PROBE_INTERVAL = 300.0         # 后台自动探活周期(秒)
PROBE_TIMEOUT = 20.0           # 单源探活超时(秒)

# 通用番号源样本: 选一个几乎所有源都收录的老番号。判定标准不是"域名能连通",
# 而是**真的解析出了有效标题** —— 因为很多死站会返回 200 + 壳页/跳转页, 传输层毫无异常。
DEFAULT_SAMPLE = 'IPX-001'

# 各源详情页域名, 供前端展示(便于核对是否配了镜像)。仅提示, 不参与探活。
DOMAIN_HINTS = {
    'airav': 'javdb.com', 'avsox': 'avsox.click', 'avwiki': 'av-wiki.net',
    'dl_getchu': 'getchu.com', 'fanza': 'dmm.co.jp', 'fc2': 'fc2club.com',
    'fc2fan': '(本地镜像)', 'fc2ppvdb': 'fc2ppvdb.com', 'gyutto': 'gyutto.com',
    'jav321': 'jav321.com', 'javbus': 'javbus.com', 'javdb': 'javdb.com',
    'javdbapi': 'jdforrepam.com', 'javdatabase': 'javdatabase.com',
    'javlib': 'javlibrary.net', 'javmenu': 'javmenu.com', 'mgstage': 'mgstage.com',
    'njav': 'njav.tv', 'prestige': 'prestige-av.com', 'arzon': 'arzon.jp',
    'arzon_iv': 'arzon.jp',
}

STATUS_TEXT = {
    'ok': '正常',
    'not_found': '可达但未收录(不熔断)',
    'invalid_response': '可达但内容异常(不熔断)',
    'duplicate': '可达但结果重复(不熔断)',
    'code_mismatch': '番号不适用(不熔断)',
    'outage': '出口整体故障(不熔断)',
    'timeout': '超时',
    'dns_error': 'DNS 解析失败',
    'tls_error': 'TLS/证书校验失败',
    'network_error': '网络不可达',
    'http_error': 'HTTP 错误',
    'blocked': '被反爬拦截',
    'credential_error': '缺少凭据',
    'error': '内部错误',
    'canceled': '已取消',
    'unknown': '未知',
    'unchecked': '未检查',
}

# 会触发熔断的状态(通道级故障)。**不含 not_found/invalid_response/duplicate**
# 注意 network_error 有两个来源: health.probe 里对 RequestException 的分类, 以及
# core 在「网络重试全部耗尽」时上报的状态 —— 后者更常见, 因为 core 的重试分支
# 原先完全不上报结果, 导致熔断器看不到死源(只见 start/成功)。
_TRIP_STATUSES = frozenset({
    'timeout', 'dns_error', 'tls_error', 'network_error', 'http_error',
    'blocked', 'credential_error', 'error', 'unknown',
})
# 可接受的"源本身没问题"状态: 只更新健康档案, 不改熔断计数
# code_mismatch 见 _classify 的说明: 它说明站点活着, 只是探活样本番号不属于它
_HEALTHY_STATUSES = {'ok', 'not_found', 'invalid_response', 'duplicate', 'code_mismatch',
                     'outage'}

# 展示优先级: 数字越小越需要用户关注(前端按此排序, 避免问题源被淹没)
_STATUS_SEVERITY = {
    'blocked': 0, 'credential_error': 1, 'error': 2, 'tls_error': 3,
    'http_error': 4, 'network_error': 5, 'dns_error': 6, 'timeout': 7,
    'invalid_response': 8, 'duplicate': 9, 'not_found': 10, 'code_mismatch': 10,
    'outage': 10, 'canceled': 11,
    'unknown': 12, 'unchecked': 13, 'ok': 99,
}

# 出口整体故障的判定门槛(见 _guard_against_outage): 至少探了这么多源, 且失败占比达到此值
OUTAGE_MIN_SOURCES = 5
OUTAGE_FAIL_RATIO = 0.7

STATE_CLOSED, STATE_OPEN, STATE_HALF_OPEN = 'closed', 'open', 'half_open'

_lock = threading.RLock()
_records: Dict[str, dict] = {}
_in_flight: Dict[str, int] = {}
_seq = 0
_probe_timer = None
_probe_stop = threading.Event()


# ---------------------------------------------------------------- 分类与脱敏

def _sanitize(text, limit: int = 180) -> str:
    """把异常压成安全的一行摘要: 剥掉 URL(可能含代理凭据/token/设备标识), 限长

    必须同时处理两类 URL —— requests 异常里两种形态都极常见:
      1. 带 scheme: 'https://user:pw@host/path?token=abc'
      2. **无 scheme**: "Max retries exceeded with url: //IPX-001?token=SECRET"
         (urllib3 拼装后的常见形态, 只写 scheme:// 正则会漏掉, token 会直接进日志/前端)
    另外 `user:pw@host` 这种裸凭据串也一并抹掉。
    """
    if not text:
        return ''
    s = str(text)
    # 1) 带 scheme 的 URL
    s = re.sub(r'\b[a-zA-Z][a-zA-Z0-9+.-]*://\S+', '<url>', s)
    # 2) 无 scheme 的协议相对 URL: //host/path?query —— 前面补一个哨兵以便匹配
    s = re.sub(r'''(?<!:)//[^\s'"<>]+''', '<url>', s)
    # 3) 兜底: 形如 user:pass@host 的裸凭据(可能不在 URL 语法内)
    s = re.sub(r'\b[\w.+-]+:[\w.+-]*@[A-Za-z0-9.\-]+', '<cred>', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s[:limit]


# 番号不适用: 各爬虫对"番号根本不属于本站"的报错形如 `Invalid GETCHU number: IPX-001`
# (dl_getchu / fc2 / fc2ppvdb / gyutto)。探活用的是固定样本番号 IPX-001, 拿它去问
# FC2、GETCHU 这类只收录特定番号段的站点, 必然命中这条 —— 但站点本身是活的。
# 若按通用异常归成 error, 两轮探活就会把这一批好源**全部误熔断**(实测中招:
# fc2 / fc2ppvdb / dl_getchu / gyutto 各失败 1 次, 再探一轮即被跳过)。
_CODE_MISMATCH_RE = re.compile(r'Invalid\s+\w+\s+number', re.I)


def _classify(exc) -> str:
    """把异常映射到状态分类(对齐 JavBoss availabilityErrorStatus 的思路)"""
    # 番号不适用: 必须最先判 —— 它是 ValueError, 会掉进末尾的通用 'error' 分支,
    # 从而被计进熔断(性质上它和 not_found 一样, 都是"源没问题")。
    if _CODE_MISMATCH_RE.search(str(exc)):
        return 'code_mismatch'
    # 先判业务异常: 它们是明确的"源的问题", 不该被 requests 通用分支吞掉
    if isinstance(exc, MovieNotFoundError):
        return 'not_found'
    if isinstance(exc, (SiteBlocked, SitePermissionError)):
        return 'blocked'
    if isinstance(exc, CredentialError):
        return 'credential_error'
    if isinstance(exc, MovieDuplicateError):
        return 'duplicate'
    # HTTPError 必须早于 RequestException 判定: 它本身带响应码, 能区分"未收录/被拦"
    # (404/403 -> http_error) 与"连不上"(network_error)。若让 RequestException 先命中,
    # 4xx 会被误判成网络故障 => 好源/仅未收录的源被无谓熔断。
    if isinstance(exc, requests.exceptions.HTTPError):
        sc = getattr(getattr(exc, 'response', None), 'status_code', 0)
        return 'http_error' if isinstance(sc, int) and sc >= 400 else 'invalid_response'
    if isinstance(exc, (ReadTimeout, ConnectTimeout, Timeout)):
        return 'timeout'
    if isinstance(exc, SSLError):
        return 'tls_error'
    if isinstance(exc, ProxyError):
        return 'network_error'
    if isinstance(exc, ReqConnectionError):
        # requests 的 ConnectionError 链里常含 DNS 失败, 需单独识别以区分"域名挂了"
        chain = f'{exc!r}'
        if ('Name or service not known' in chain
                or 'nodename nor servname' in chain
                or 'getaddrinfo failed' in chain
                or 'Temporary failure in name resolution' in chain
                or 'name or service not known' in chain.lower()):
            return 'dns_error'
        return 'network_error'
    if isinstance(exc, requests.exceptions.RequestException):
        return 'network_error'
    return 'error'


# 各源"问得动"的样本查询串。探活判据是**真的解析出有效标题**, 所以样本必须落在
# 该站的收录范围内 —— 拿通用番号 IPX-001 去问 FC2 / GETCHU / gyutto 这类只收录特定
# 番号段的站点, 它们会直接抛 `Invalid XX number`: 那是"问错了对象", 不是站点故障。
# (实测: 用 IPX-001 探活时 fc2 / fc2ppvdb / dl_getchu / gyutto 全部报这类错)
_SAMPLE_BY_SOURCE = {
    'fc2': 'FC2-1234567',
    'fc2ppvdb': 'FC2-1234567',
    'dl_getchu': 'GETCHU-12345',
    'gyutto': 'GYUTTO-12345',
    'fanza': 'ipx00001',        # FANZA 按 cid 查询, 形如 ssis00123(品番小写去横线)
}


def _sample_for(source: str) -> str:
    """挑一个该源必然支持的样本查询串"""
    return _SAMPLE_BY_SOURCE.get(source, DEFAULT_SAMPLE)


def _new_record(source: str) -> dict:
    return {
        'source': source,
        'status': 'unchecked',
        'reason': '',
        'elapsed_ms': 0,
        'checked_at': None,          # 最近一次**主动探活**时间
        'origin': 'unknown',         # active=探活 / scrape=刮削现场观测
        'breaker': STATE_CLOSED,     # closed / open / half_open
        'consecutive_failures': 0,
        'cooldown_remaining': 0.0,   # 熔断剩余秒数(便于前端展示)
        'scrape_samples': 0,         # 刮削现场观测样本数
        'scrape_success': 0,
        'scrape_trip': 0,            # 现场观测到的失败次数
        'trips': 0,                  # 累计熔断次数(诊断用)
        'last_status_code': 0,
        'last_error': '',
    }


def _get(source: str) -> dict:
    """取(必要时初始化)某源档案。调用方须持有 _lock"""
    rec = _records.get(source)
    if rec is None:
        rec = _new_record(source)
        _records[source] = rec
    return rec


# ---------------------------------------------------------------- 查询接口

def active_sources() -> List[str]:
    """当前配置里实际启用的源(去重, 保持 selection 顺序)"""
    out, seen = [], set()
    for _kind, mods in Cfg().crawler.selection.items():
        for m in mods:
            if m.value not in seen:
                seen.add(m.value)
                out.append(m.value)
    return out


def known_sources() -> List[str]:
    """全部已注册源(启用 + 未启用), 供前端展示"还有哪些源是可开但没开的" """
    out = list(active_sources())
    for c in CrawlerID:
        if c.value not in out:
            out.append(c.value)
    return out


def _decorate(rec: dict, active: bool) -> dict:
    """补上前端展示所需的派生字段"""
    row = dict(rec)
    row['domain'] = DOMAIN_HINTS.get(row['source'], '')
    row['status_text'] = STATUS_TEXT.get(row['status'], row['status'])
    row['severity'] = _STATUS_SEVERITY.get(row['status'], 50)
    row['breaker_text'] = {
        STATE_CLOSED: '正常参与刮削',
        STATE_OPEN: '已熔断(跳过)',
        STATE_HALF_OPEN: '试探中',
    }.get(row['breaker'], row['breaker'])
    row['active'] = active
    row['sample'] = _sample_for(row['source'])
    row['hit_rate'] = (round(row['scrape_success'] / row['scrape_samples'] * 100)
                       if row['scrape_samples'] else None)
    return row


def overview(include_inactive: bool = True) -> List[dict]:
    """全部源的健康档案(供 GET /api/health/crawlers), 按严重度排序(问题源最前)"""
    actives = set(active_sources())
    sources = known_sources() if include_inactive else sorted(actives)
    with _lock:
        out = [_decorate(_get(s), s in actives) for s in sources]
        now = time.time()
        for row in out:
            # 剩余冷却随时间递减, 避免前端拿到一个"永远不变"的过期数字
            if row['breaker'] == STATE_OPEN:
                elapsed = now - (row['checked_at'] or now)
                row['cooldown_remaining'] = max(
                    0.0, round(_cooldown_for(row['consecutive_failures']) - elapsed, 1))
    out.sort(key=lambda r: (r['severity'], r['source']))
    return out


def is_tripped(source: str) -> bool:
    """该源当前是否应被刮削跳过。**刮削路径的热路径, 务必轻量**"""
    with _lock:
        rec = _records.get(source)
        if rec is None:
            return False                      # 未知源: 从未探活过, 照常尝试(首次运行不该全跳过)
        if rec['breaker'] != STATE_OPEN:
            return False
        # 冷却期到 -> 转半开, 放一个请求试探(由并行爬虫正常调用 parse_data 触发)
        elapsed = time.time() - (rec['checked_at'] or 0)
        if elapsed >= _cooldown_for(rec['consecutive_failures']):
            rec['breaker'] = STATE_HALF_OPEN
            logger.info(f'渠道 {source}: 冷却结束, 进入半开试探')
            return False
        rec['cooldown_remaining'] = round(
            _cooldown_for(rec['consecutive_failures']) - elapsed, 1)
        return True


def _cooldown_for(consecutive_failures: int) -> float:
    """冷却期 = 基数 * 2^(失败次数-1), 封顶 COOLDOWN_MAX"""
    n = max(1, int(consecutive_failures))
    return min(COOLDOWN_BASE * (2 ** (n - 1)), COOLDOWN_MAX)


def allowlist() -> List[str]:
    """当前允许参与刮削的源(已剔除熔断中的)。scrape 前调用一次即可"""
    return [s for s in active_sources() if not is_tripped(s)]


# ---------------------------------------------------------------- 状态更新

def _cooldown_left(rec: dict) -> float:
    elapsed = time.time() - (rec['checked_at'] or time.time())
    return max(0.0, round(_cooldown_for(rec['consecutive_failures']) - elapsed, 1))


def note_scrape_outcome(source: str, status: str) -> None:
    """记录一次**刮削现场**观测(零额外请求)。由 core 的 progress_cb 调用。

    这是本项目相对 JavBoss 的增量: 不额外发探测请求, 顺手把日常刮削的真实结果
    汇入健康档案, 覆盖面比"定时探活"更广。
    """
    with _lock:
        rec = _get(source)
        rec['scrape_samples'] += 1
        if status == 'success':
            rec['scrape_success'] += 1
        elif status != 'start':
            rec['scrape_trip'] += 1
        # 现场观测到的通道级故障同样计入会熔断计数
        if status in _TRIP_STATUSES:
            _on_failure(rec, status, '', 0, origin='scrape')
        elif status == 'success':
            # 成功必须能恢复熔断(含半开试探成功这一唯一恢复路径)。
            # 注意不能只靠 _HEALTHY_STATUSES: 'ok' 不在其中, 否则熔断中的源即使
            # 半开试探成功也永远恢复不了 —— 这是熔断器的核心闭环, 漏掉即死锁。
            _on_success(rec, origin='scrape')
        elif status in _HEALTHY_STATUSES:
            _on_success(rec, origin='scrape')


def _on_failure(rec: dict, status: str, reason: str, status_code: int, origin: str) -> None:
    """记录一次失败并按阈值熔断。调用方须持有 _lock"""
    rec['status'] = status
    rec['reason'] = reason
    rec['last_error'] = reason
    rec['last_status_code'] = status_code
    rec['checked_at'] = time.time()
    rec['origin'] = origin
    rec['elapsed_ms'] = 0
    if status in _TRIP_STATUSES:
        rec['consecutive_failures'] += 1
        # 阈值判定必须只看"连续失败数是否达标", 不能因 breaker 当前不是 open 就直接熔断——
        # 否则首次失败即熔断, FAILURE_THRESHOLD 形同虚设, 网络抖动会误杀好源。
        if rec['consecutive_failures'] >= FAILURE_THRESHOLD and rec['breaker'] != STATE_OPEN:
            rec['trips'] += 1
            logger.warning(
                f'渠道 {rec["source"]}: 熔断({status}), 连续失败 '
                f'{rec["consecutive_failures"]} 次, 刮削将跳过该源')
        if rec['consecutive_failures'] >= FAILURE_THRESHOLD:
            rec['breaker'] = STATE_OPEN
            rec['cooldown_remaining'] = _cooldown_left(rec)
    else:
        # not_found / invalid_response / duplicate: 源是好的, 仅记录健康状态
        rec['breaker'] = STATE_CLOSED
        rec['consecutive_failures'] = 0
        rec['cooldown_remaining'] = 0.0


def _on_success(rec: dict, origin: str, elapsed: int = 0) -> None:
    """恢复。调用方须持有 _lock

    elapsed 需显式传入: 成功路径不设它, 否则档案里 elapsed_ms 永远停在 0,
    前端「耗时」列对正常源就全是 0ms, 该指标就废了。
    """
    rec['status'] = 'ok'
    rec['reason'] = ''
    rec['last_error'] = ''
    rec['last_status_code'] = 0
    rec['checked_at'] = time.time()
    rec['origin'] = origin
    rec['consecutive_failures'] = 0
    rec['cooldown_remaining'] = 0.0
    if elapsed:
        rec['elapsed_ms'] = elapsed
    if rec['breaker'] != STATE_CLOSED:
        logger.info(f'渠道 {rec["source"]}: 恢复正常, 重新参与刮削')
    rec['breaker'] = STATE_CLOSED


def _begin_check(source: str) -> int:
    global _seq
    with _lock:
        _seq += 1
        _in_flight[source] = _seq
        return _seq


def _is_stale(source: str, seq: int) -> bool:
    with _lock:
        return _in_flight.get(source) != seq


def _commit(source: str, seq: int, status: str, reason: str,
            elapsed: int, status_code: int, origin: str) -> bool:
    """提交结果; 已被更新的检查取代则丢弃(防过期回填, JavBoss sequence 同思路)"""
    with _lock:
        if _in_flight.get(source) != seq:
            return False
        rec = _get(source)
        if status == 'ok':
            _on_success(rec, origin=origin, elapsed=elapsed)
        elif status in _HEALTHY_STATUSES:
            rec['status'] = status
            rec['reason'] = reason
            rec['last_error'] = reason
            rec['checked_at'] = time.time()
            rec['origin'] = origin
            rec['elapsed_ms'] = elapsed
            rec['last_status_code'] = status_code
            rec['breaker'] = STATE_CLOSED
            rec['consecutive_failures'] = 0
            rec['cooldown_remaining'] = 0.0
        else:
            _on_failure(rec, status, reason, status_code, origin=origin)
            rec['elapsed_ms'] = elapsed
        return True


# ---------------------------------------------------------------- 主动探活

def probe(source: str, timeout: float = PROBE_TIMEOUT) -> dict:
    """对单个源做主动探活: 走**真实 parse_data**, 与实际刮削完全同路径"""
    seq = _begin_check(source)
    sample = _sample_for(source)
    started = time.perf_counter()
    status, reason, status_code = 'unknown', '', 0
    try:
        mod = __import__('javsp.web.' + source, fromlist=['parse_data'])
        movie = MovieInfo(sample)
        # FANZA 不按番号查询, 走 cid(形如 ipx00001)。只设 dvdid 的话它拿空 cid 去拼
        # 详情页 URL, 拿到的是非详情页 -> xpath 取不到标题 -> IndexError, 会被误判成
        # "内部错误"并计入熔断(实测 fanza 就是这么被扣上内部错误的)。
        if source == 'fanza':
            movie.cid = sample
        mod.parse_data(movie)
        # 不能只看"没抛异常": 很多死站返回 200 + 壳页/跳转页, 传输层毫无异常。
        # 判定标准必须是**真的解析出了有效标题**, 且标题不能就是番号本身。
        title = (getattr(movie, 'title', '') or '').strip()
        if title and title.upper() != sample.upper():
            status = 'ok'
        else:
            status = 'invalid_response'
            reason = '未解析出有效标题(疑似壳页/跳转页)'
    except BaseException as e:      # noqa: BLE001 单源失败绝不影响整体与其他源
        status = _classify(e)
        reason = _sanitize(f'{type(e).__name__}: {e}')
        resp = getattr(e, 'response', None)
        sc = getattr(resp, 'status_code', 0)
        status_code = sc if isinstance(sc, int) else 0
        if status in ('not_found', 'invalid_response') and status_code >= 400:
            status = 'http_error'
    elapsed = int((time.perf_counter() - started) * 1000)

    stale = _is_stale(source, seq)
    _commit(source, seq, status, reason, elapsed, status_code, origin='active')
    with _lock:
        rec = _decorate(_get(source), source in set(active_sources()))
    rec['stale'] = stale
    return rec


def probe_all(sources: Optional[List[str]] = None, timeout: float = PROBE_TIMEOUT) -> List[dict]:
    """并发探活多个源(限流), 任何单源异常都被吞掉, 保证总能拿到完整报告"""
    targets = sources or active_sources()
    results, lock = [], threading.Lock()
    sem = threading.Semaphore(4)          # 对出口友好: 最多 4 个并发探活

    def _work(name):
        with sem:
            try:
                r = probe(name, timeout=timeout)
            except BaseException as e:      # noqa: BLE001 兜底(probe 内部已全捕获)
                r = _decorate(_get(name), name in set(active_sources()))
                r.update({'status': 'error',
                          'status_text': STATUS_TEXT['error'],
                          'reason': _sanitize(f'{type(e).__name__}: {e}')})
        with lock:
            results.append(r)

    threads = [threading.Thread(target=_work, args=(s,), daemon=True) for s in targets]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    _guard_against_outage(results)
    order = {r['source']: r['severity'] for r in overview()}
    results.sort(key=lambda r: (order.get(r['source'], 50), r['source']))
    return results


def _guard_against_outage(results: List[dict]) -> None:
    """出口整体故障保护: 多数源同时失败时, 不要把它们逐个熔断

    判据: 本轮探活里 ≥70% 的源都失败、且至少探了 5 个 —— 站点各不相同却同时全挂,
    挂的只能是本机出口(代理/网关/网卡), 不是这些站点。若照常逐个计进熔断,
    **一次网络抖动就会把所有好源一起跳过**, 等网络恢复了用户还得等冷却期。

    实测触发场景: 容器重启后 squid 代理失效, 15 个启用源同一时刻全部 TLS 握手失败,
    10 个被熔断(其中 jav321 / javdbapi / javmenu 数分钟前还是"正常")。

    注意只豁免**通道级故障**, "未收录/番号不适用"本就不熔断, 不在此列。
    """
    if len(results) < OUTAGE_MIN_SOURCES:
        return
    failed = [r for r in results if r.get('status') in _TRIP_STATUSES]
    if not failed or len(failed) < len(results) * OUTAGE_FAIL_RATIO:
        return
    names = []
    for r in failed:
        src = r['source']
        with _lock:
            rec = _records.get(src)
            if not rec:
                continue
            reason = rec.get('reason', '')
            rec['status'] = 'outage'
            rec['reason'] = f'{reason}（判定为出口整体故障, 不计入熔断）'.strip('（）')
            rec['consecutive_failures'] = 0
            rec['breaker'] = STATE_CLOSED
            rec['cooldown_remaining'] = 0.0
        # 同步本次返回的展示字段, 避免界面仍按"熔断"渲染
        r.update({'status': 'outage', 'status_text': STATUS_TEXT['outage'],
                  'breaker': STATE_CLOSED, 'consecutive_failures': 0,
                  'cooldown_remaining': 0.0,
                  'breaker_text': '暂不参与判定'})
        names.append(src)
    if names:
        logger.warning(f'出口整体故障: {len(names)}/{len(results)} 个源同时失败, '
                       f'已豁免熔断 -> {", ".join(names)}')


def probe_all_background() -> List[dict]:
    """供后台定时任务调用"""
    return probe_all()


# ---------------------------------------------------------------- 后台定时探活

def _probe_loop():
    while not _probe_stop.is_set():
        try:
            srcs = active_sources()
            if srcs:
                res = probe_all(srcs)
                bad = [r['source'] for r in res if r['breaker'] == STATE_OPEN]
                logger.info(f'渠道探活完成: 共 {len(res)} 源, 熔断中 {len(bad)}'
                            + (f' ({", ".join(bad)})' if bad else ''))
        except Exception as e:      # noqa: BLE001 后台线程绝不能因异常而静默死亡
            logger.warning(f'渠道探活异常(本轮跳过): {_sanitize(str(e))}')
        _probe_stop.wait(PROBE_INTERVAL)


def start_auto_probe(interval: float = PROBE_INTERVAL, initial_delay: float = 20.0):
    """启动后台自动探活(幂等)。应用启动时调用"""
    global _probe_timer
    with _lock:
        if _probe_timer is not None and _probe_timer.is_alive():
            return False
        _probe_stop.clear()

        def _run():
            if _probe_stop.wait(initial_delay):
                return
            _probe_loop()

        _probe_timer = threading.Thread(target=_run, daemon=True, name='channel-probe')
        _probe_timer.start()
        logger.info(f'渠道自动探活已启动(周期 {interval}s)')
        return True


def stop_auto_probe():
    global _probe_timer
    _probe_stop.set()
    with _lock:
        if _probe_timer is not None:
            _probe_timer.join(timeout=2.0)
            _probe_timer = None
    return True


def reset() -> None:
    """清空档案(改配置后调用, 避免旧源结论误导)"""
    global _seq
    with _lock:
        _records.clear()
        _in_flight.clear()
        _seq = 0


# ---------------------------------------------------------------- 展示辅助

def merge_into_scrape_sources(sources: dict) -> dict:
    """把健康档案并入刮削结果的 sources, 供前端**一次渲染**

    刮削结果原本只有"这个源抓到了什么", 没有"这个源健康吗/是否被跳过"。合并后
    同一张表既能看到字段贡献, 也能看到熔断状态 —— 无需第二次请求。
    """
    if not isinstance(sources, dict):
        return sources
    out = {}
    with _lock:
        for name, data in sources.items():
            row = dict(data or {})
            rec = _get(name)
            row['health_status'] = rec['status']
            row['health_text'] = STATUS_TEXT.get(rec['status'], rec['status'])
            row['breaker'] = rec['breaker']
            row['skipped'] = rec['breaker'] == STATE_OPEN and name not in allowlist_names()
            row['trip_reasons'] = rec['consecutive_failures']
            out[name] = row
    return out


def allowlist_names() -> List[str]:
    """当前参与刮削的源集合(熔断中的已剔除)。注意会触发 half_open 转移, 故单独封装"""
    return [s for s in active_sources() if not is_tripped(s)]


def skipped_sources() -> List[dict]:
    """当前被熔断跳过的源(供日志与 API 顶层提示)"""
    out = []
    for row in overview(include_inactive=False):
        if row['breaker'] == STATE_OPEN:
            out.append(row)
    return out


def summary_for_log() -> str:
    rows = overview(include_inactive=False)
    if not rows:
        return '渠道健康: 无启用源'
    ok = sum(1 for r in rows if r['status'] == 'ok')
    tripped = sum(1 for r in rows if r['breaker'] == STATE_OPEN)
    return f'渠道健康: {ok}/{len(rows)} 正常, {tripped} 熔断跳过'


def format_report() -> str:
    """给运维看的一段可读报告(不含 URL/凭据)"""
    rows = overview(include_inactive=False)
    lines = ['渠道健康报告:', f'  样本: {_sample_for("*")}  启用源: {len(rows)}']
    for r in rows:
        flag = '熔断' if r['breaker'] == STATE_OPEN else '正常'
        cd = f' 冷却{r["cooldown_remaining"]}s' if r['cooldown_remaining'] else ''
        lines.append(f'  [{flag}] {r["source"]:<12} {r["status_text"]:<22} '
                     f'{r["elapsed_ms"]:>6}ms {r["domain"]}{cd}')
        if r['reason']:
            lines.append(f'        └ {r["reason"]}')
    return '\n'.join(lines)