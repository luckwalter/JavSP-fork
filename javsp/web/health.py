"""刮削渠道健康监控与熔断 (移植自 JavBoss internal/jav/availability.go 的能力, Python 实现)

==== 为什么需要 ====
受限出口下多个源会陆续失效(JavSP-fork v0.1.28 实证: javlib 已废、javdb 网站被
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
from contextvars import ContextVar
from typing import Dict, List, Optional
from urllib.parse import urlsplit

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

# 各源实际访问的站点, 供前端展示(便于核对是否配了镜像/判断当前出口能不能通)。仅提示, 不参与探活。
#
# ⚠️ 必须与各爬虫模块里真实使用的域名保持一致, 否则面板会误导人(历史教训):
#   - airav 曾被错标成 'javdb.com', 实际是 www.airav.wiki —— 两个不同站点, 会让人误判为"重复源";
#   - fc2 曾被错标成 'fc2club.com', 代码实际访问 adult.contents.fc2.com(fc2club 是 fc2fan 的镜像);
#   - fc2fan 曾被标成 '(本地镜像)', 代码里其实是写死的 fc2club.top。
# 下面每一项都对照 javsp/web/<源>.py 的 base_url / permanent_url / BASE_URL 逐一核对过。
# (fc2fan 已于 0.2.12 移除: 主站 fc2club.top 实测只返回 493 字节 'Loading...' 壳页, 站点已关停)
DOMAIN_HINTS = {
    # airav 已迁至 airav.io(旧站 www.airav.wiki 已停服 HTTP 521), 且从 JSON API 改成了
    # 服务端渲染 HTML —— airav.py 已按新架构重写(搜索定位 + JSON-LD/HTML 解析)
    'airav': 'airav.io',
    'avsox': 'avsox.click',
    'avwiki': 'av-wiki.net',
    'dl_getchu': 'dl.getchu.com',
    'fanza': 'dmm.co.jp',
    'fc2': 'adult.contents.fc2.com',
    'fc2ppvdb': 'fc2ppvdb.com',
    'gyutto': 'gyutto.com',
    # jav321 是 DMM 商品数据的镜像聚合站(cid 沿用 DMM Content ID, 封面 hotlink
    # pics.dmm.co.jp), 不是自建番号库。2026-10-21 实测: 站点已恢复开放(2024 年
    # 曾整体宕机), 但服务器证书过期, 严格 TLS 校验下探活报 tls_error。
    'jav321': 'www.jav321.com (DMM 数据镜像站)',
    'javbus': 'www.javbus.com',
    'javdb': 'javdb.com',
    # javdbapi 走的是 javdb 手机 App 私有 API, 与主站 javdb.com 是同一份数据、不同入口
    'javdbapi': 'jdforrepam.com (javdb App API)',
    'javdatabase': 'www.javdatabase.com',
    'javfree': 'javfree.me',
    'javlib': 'www.javlibrary.com',
    'javmenu': 'javmenu.com',
    'mgstage': 'www.mgstage.com',
    'njav': 'njav.tv',
    'prestige': 'www.prestige-av.com',
}

# 人工核实的"站点现状"结论 —— 这些**无法从异常类型推断**, 必须显式记录:
# avsox/avwiki 抛的都是 MovieNotFoundError, 与"该站真的没收录这部片子"完全同形;
# 若交给 _classify 自动判断, 面板会显示"可达但未收录", 让主人误以为源是好的,
# 反复排查"是不是样本号不对"(实测就绕了很大一圈)。
# 每条都是**实测核对过**的结论(2026-10-11), 不是推测。
SOURCE_STATUS_OVERRIDE = {
    'avsox': ('client_rendered',
              '站点已改为纯前端渲染: 任意路径都返回同一个 1520 字节空壳页'
              '(内容由 JS 注入), 静态抓取取不到任何数据'),
    'avwiki': ('source_defunct',
               '该站不是番号库(实测标题为"AV女優の名前が知りたい!"女优名字查询站), '
               '没有影片详情页, 搜索页返回 404, 对刮削无意义'),
    # (javbus 已于 0.2.14 复活: 该站 2026-10 上旬更换年龄验证机制 —— age=verified
    #  失效, 改带内置 dv=1 cookie 即可放行, 见 web/javbus.py; 此前的
    #  source_defunct(driver-verify 墙)结论作废, 恢复常规探活。)
}

STATUS_TEXT = {
    'ok': '正常',
    'not_found': '可达但未收录(不熔断)',
    'invalid_response': '可达但内容异常(不熔断)',
    'duplicate': '可达但结果重复(不熔断)',
    'code_mismatch': '番号不适用(不熔断)',
    'outage': '出口整体故障(不熔断)',
    # 站点自身"不可用"的两类。这两个状态**都会让刮削跳过该源**(见 is_tripped),
    # 所以文案里不再写"(不熔断)" —— 那是内部实现细节, 反而让人以为还能继续用。
    'client_rendered': '前端渲染·无法静态刮削',
    'source_defunct': '站点已失效/非番号库',
    'timeout': '超时',
    'dns_error': 'DNS 解析失败',
    # tls_error 两类常见根因, 文案要点出来: ①目标站证书过期/配置错误(实测
    # jav321 2026-10-21 即此: 站点已恢复但证书没人续) ②链路被 MITM 代理重签。
    # 具体是哪类看 reason 列的异常原文(certificate has expired 等)。
    'tls_error': 'TLS/证书校验失败(站点证书过期或被重签)',
    'network_error': '网络不可达',
    'http_error': 'HTTP 错误',
    'blocked': '被反爬拦截',
    # 三类封禁的处置方式不同, 文案必须写明**真实原因 + 怎么解**, 否则用户看到
    # 一堆"被拦截/IP被封"却不知道该换地区代理还是换 IP(实测踩过)。
    'ip_banned': 'IP 被封禁(需换代理 IP)',
    'geo_blocked': '地区版权封锁(需换日本等地区代理)',
    'challenge_required': '需人机验证(浏览器可过)',
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
    'blocked', 'geo_blocked', 'credential_error', 'error', 'unknown',
    # 出口被封 / 需真人校验: 在**当前出口下**该源必然抓不到, 与 not_found 有本质区别
    # (not_found 是"源能用, 只是这部片子没收录"), 故计入熔断, 避免每次刮削都白等超时。
    'ip_banned', 'challenge_required',
})
# 可接受的"源本身没问题"状态: 只更新健康档案, 不改熔断计数
# code_mismatch 见 _classify 的说明: 它说明站点活着, 只是探活样本番号不属于它
# client_rendered / source_defunct: 站点还在但**静态抓取已不可行**(改前端渲染/已变质),
#   重试毫无意义, 故既不熔断(免得白白禁用一个仍可人工访问的站点), 也不算"健康"。
_HEALTHY_STATUSES = {'ok', 'not_found', 'invalid_response', 'duplicate', 'code_mismatch',
                     'outage', 'client_rendered', 'source_defunct'}

# 展示优先级: 数字越小越需要用户关注(前端按此排序, 避免问题源被淹没)
_STATUS_SEVERITY = {
    # 需要主人换出口代理才能恢复, 与"站点自身坏了"不同, 故排在 blocked 之后
    'ip_banned': 1, 'challenge_required': 2,
    'blocked': 3, 'geo_blocked': 3, 'credential_error': 1, 'error': 2, 'tls_error': 4,
    'http_error': 4, 'network_error': 5, 'dns_error': 6, 'timeout': 7,
    'invalid_response': 8, 'duplicate': 9, 'not_found': 10, 'code_mismatch': 10,
    'outage': 10, 'canceled': 11,
    # 站点还在但用不了: 需要用户知道"不是配置问题, 是站点变了", 但不紧急
    'client_rendered': 11, 'source_defunct': 12,
    'unknown': 13, 'unchecked': 14, 'ok': 99,
}

# 出口整体故障的判定门槛(见 _guard_against_outage): 至少探了这么多源, 且失败占比达到此值
OUTAGE_MIN_SOURCES = 5
OUTAGE_FAIL_RATIO = 0.7

# ---- 「这个源现在还能不能刮到数据」 ------------------------------------
# 与 _HEALTHY_STATUSES 的区别: 那一组是"不熔断"(源没坏, 只是这部片子没收录/临时故障);
# 这一组是**源本身用不了**, 再刮多少次都是白费 —— 面板必须如实说"不可用", 否则
# 主人看到"参与刮削"会以为它是正常源, 反复去查为什么没数据。
# 判定依据是**站点侧的硬性阻断**, 与出口网络无关(not_found/timeout 都不算)。
_UNUSABLE_STATUSES = frozenset({
    'ip_banned',            # 出口 IP/ASN 被封, 换代理 IP 才可能恢复
    'geo_blocked',          # 地区版权封锁, 换日本等地区代理才可能恢复
    'challenge_required',   # 需真人校验, 程序过不了
    'client_rendered',      # 已改前端渲染, 静态抓取取不到数据
    'source_defunct',       # 站点已失效 / 本就不是番号库
    'credential_error',     # 缺凭据, 不配就永远失败
})

# 每种不可用状态对应的处理建议(直接展示给用户, 省去"这该怎么办"的猜测)
_UNUSABLE_HINTS = {
    'ip_banned': '当前出口 IP 被该站封禁(Error 1005/blocked), 需更换代理 IP 才能恢复',
    'geo_blocked': '该站按**IP 所在地区**做版权封锁, 需换用日本等地区的代理出口',
    'challenge_required': '需在浏览器完成人机验证(Cloudflare 等), 静态抓取无法通过',
    'client_rendered': '站点已改为前端渲染, 静态刮削无法获取数据',
    'source_defunct': '站点已失效或本就不是番号库, 建议在设置中关闭该源',
    'credential_error': '缺少登录/API 凭据, 需在设置中补齐后才能使用',
}

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

    必须同时处理**三类** URL —— requests/urllib3 异常里三种形态都极常见, 漏一种
    就等于把凭据直接写进日志与 `/api/channels`(实测踩过, 见下方注释):
      1. 带 scheme: 'https://user:pw@host/path?token=abc'
      2. **协议相对**: '//IPX-001?token=SECRET'
      3. **path-only(单斜杠)**: 'Max retries exceeded with url: /search?PHPSESSID=SECRET'
         —— urllib3 的真实形态是**单斜杠**, 只写 // 规则会整条漏掉(实测 token 存活)

    ⚠️ 本函数的输出会经 `rec['reason']` 直接进 `/api/channels` 给前端渲染,
    服务默认监听 0.0.0.0(容器内), 故**不能只考虑"日志好看"**, 必须真的脱敏。
    """
    if not text:
        return ''
    s = str(text)
    # 1) 带 scheme 的 URL
    s = re.sub(r'\b[a-zA-Z][a-zA-Z0-9+.-]*://\S+', '<url>', s)
    # 2) 协议相对 URL: //host/path?query
    s = re.sub(r'''(?<!:)//[^\s'"<>]+''', '<url>', s)
    # 3) path-only 查询串(单斜杠): "with url: /search?PHPSESSID=SECRET"
    #    前面补哨兵以避免重复匹配 2) 已处理的部分
    s = re.sub(r"""(?<![\w:])/(?!/)[^\s'"<>?]*\?[^\s'"<>]+""", '<url+query>', s)
    # 4) 兜底: 任何 key=value 里带敏感键名的一律抹掉值(token/Cookie/密钥…)
    s = re.sub(r'(?i)\b([a-z0-9_-]*(?:token|secret|session|sess|passwd|password|'
               r'auth|cookie|key|sig|signature|phpsessid)[a-z0-9_-]*)=[^&\s\'"<>]+',
               r'\1=<redacted>', s)
    # 5) 代理/连接池形态: host='<内网地址>', port=<端口> —— 内网拓扑不该出进程
    s = re.sub(r"(?i)\b(host|proxy|server)='[^']*'(?:\s*,\s*port=\d+)?", r'\1=<addr>', s)
    # 6) 裸 IP(含内网)与本机文件路径
    s = re.sub(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?(?![\w.])', '<ip>', s)
    s = re.sub(r'(?i)[A-Za-z]:[\\/][^\s\'"<]+|/(?:home|root|tmp|var|usr|etc|opt)/[^\s\'"<]+',
               '<path>', s)
    # 7) 形如 user:pass@host 的裸凭据(可能不在 URL 语法内)
    s = re.sub(r'\b[\w.+-]+:[\w.+-]*@[A-Za-z0-9.\-]+', '<cred>', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s[:limit]


# 番号不适用: 各爬虫对"番号根本不属于本站"的报错形如 `Invalid GETCHU number: IPX-001`
# (dl_getchu / fc2 / fc2ppvdb / gyutto)。探活用的是固定样本番号 IPX-001, 拿它去问
# FC2、GETCHU 这类只收录特定番号段的站点, 必然命中这条 —— 但站点本身是活的。
# 若按通用异常归成 error, 两轮探活就会把这一批好源**全部误熔断**(实测中招:
# fc2 / fc2ppvdb / dl_getchu / gyutto 各失败 1 次, 再探一轮即被跳过)。
_CODE_MISMATCH_RE = re.compile(r'Invalid\s+\w+\s+number', re.I)

# 地区版权封锁: 部分站点对受限地区(如中国大陆 IP)返回 200 + 版权提示页而非硬拦截,
# 需按页面文本识别。命中走 geo_blocked 状态(见 _classify), 便于主人快速判断"需换地区代理"。
#
# ⚠️ 两处踩坑教训, 词表必须精确:
#  1. 必须覆盖**我们自己抛异常时用的中文措辞**(javdb 检测到版权页后抛
#     '当前IP所在地区禁止访问(版权限制)...'), 否则地区封锁会被误判成 blocked;
#  2. **绝不能包含"禁止访问"这种宽泛词** —— javdb.py 里 403/503 的**所有**分支模板都是
#     f'JavDB: {code} 禁止访问: {url}', 匹配它等于把 CF 1010(浏览器指纹封禁)、
#     普通 403、503 全判成"地区封锁"。而 geo_blocked 属不可用(is_tripped 恒 True 且
#     不走半开恢复), 一次误判就永久停用该源, 按提示换日本代理也解决不了 1010。
_GEO_BLOCK_RE = re.compile(
    r'copyright restrictions|prohibited in the country|'
    r'版權限制|版權限製|禁止了你的網路|access to this site is prohibited|'
    r'your (internet|network) is located|'
    r'版權地區|地區版權|地区版权|'
    # javdb 地区封锁分支的专用文案(比"禁止访问"精确得多)
    r'屏蔽了来自日本地区的IP|'
    # 我们自己抛的中文异常里的**特征词**(取"版权限制"而非整个句子)
    r'版权限制',
    re.I)

# ---- Cloudflare 的两种"人机门槛"必须区分开(实测三站特征各异, 见下) ----
# ① ip_banned: **出口 IP/ASN 被封禁**, 换代理即可。特征:
#    - fc2ppvdb: 403 + "Error 1005 ... has banned the autonomous system number (ASN) your IP
#      address is in (16509)" —— 这是**整段 ASN 封禁**, 站点专门封机房/云厂商网段;
#    - njav:     403 + "Sorry, you have been blocked" + "Attention Required!"。
#    二者本质相同(换个出口 IP 就能用), 所以归为同一状态。
_IP_BAN_RE = re.compile(
    r'error\s*1005|has banned the autonomous system number|'
    r'you have been blocked|'
    r'your ip (?:address )?(?:has been|is) (?:blocked|banned)',
    re.I)

# ② challenge_required: **需要过真人校验**(浏览器点一下就能进), 并不是 IP 被封。
#    判据只用**挑战页独有**的特征: cf-mitigated 响应头(CF 明确标注这是 challenge)、
#    "Just a moment..."、"Enable JavaScript and cookies to continue"、cf_chl_opt。
#    ⚠️ 刻意**不含** "/cdn-cgi/challenge-platform": 那是 Cloudflare **所有错误页**都会加载的
#    脚本路径(实测 njav/fc2ppvdb 的封禁页里也有它), 用它做判据会把封禁误判成挑战。
_CHALLENGE_RE = re.compile(
    r'just a moment\.\.\.|enable javascript and cookies to continue|'
    r'cf_chl_opt|正在进行安全验证|'
    r'验证您不是自动程序|安全服务防护',
    re.I)


def _classify_response(resp) -> str:
    """按**响应内容**区分「IP 被封」与「需真人校验」

    两者状态码都是 403, 不看正文一律会归成 http_error, 主人就无法判断
    "该换代理"还是"该用浏览器过一下"。这里以正文特征判定, 比人工列举源更通用。

    ⚠️ 顺序很重要: **封禁特征优先于挑战特征**。Cloudflare 的封禁页与挑战页同源模板,
    封禁页同样会加载 challenge-platform 脚本, 若挑战判据放前面, njav(you have been
    blocked)、fc2ppvdb(Error 1005) 都会被误判成"需真人校验"(实测踩过)。
    """
    if resp is None:
        return ''
    # Cloudflare 专用头: mitigated=challenge 明确表示"这是挑战页, 不是封禁"
    mitigated = (resp.headers.get('cf-mitigated') or '').strip().lower()
    text = ''
    try:
        # ⚠️ requests 的 `.text` 是纯 property, **不缓存**: 每次访问都重新解码, 且
        # encoding 为 None 时会触发 apparent_encoding 的全量字符集探测。实测扫描成本
        # 随页面体积线性增长(8 KiB→0.48ms, 2 MiB→109ms), 而 CF 的封禁/挑战特征都
        # 在 <head> 与首屏内, 故只取前 8 KiB 足够判定, 能挡掉"误撞大错误页"的 CPU 浪费。
        text = (resp.text or '')[:8192]
    except Exception as exc:       # noqa: BLE001 流被消费(stream=True+iter_content)时
        # 不能静默降级: 降级会让 CF 封禁页退化成 http_error, 于是"换代理就能好"的源
        # 被永久熔断, 而运维看到的只是一句无用的"HTTP 错误"。至少留一条 debug 痕迹。
        logger.debug('读取响应正文失败, 无法区分 ip_banned/challenge_required: %s',
                     type(exc).__name__)
        text = ''
    # 1) 先看封禁特征(错误 1005 / you have been blocked / ASN banned)
    if _IP_BAN_RE.search(text):
        return 'ip_banned'
    # 2) 再看挑战特征(cf-mitigated 头 或 挑战页正文)
    if mitigated == 'challenge' or _CHALLENGE_RE.search(text):
        return 'challenge_required'
    return ''


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
        # 地区版权封锁(如 javdb 对中国 IP 返回版权提示页)单独归类, 与"反爬拦截"区分
        if _GEO_BLOCK_RE.search(str(exc)):
            return 'geo_blocked'
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
        if isinstance(sc, int) and sc >= 400:
            # 403 有多种成因, 必须看响应内容才分得清: 封禁(换代理) / 真人校验(手动过)
            # / 其它。不看正文一律归 http_error, 主人就只能看到一句无用的"HTTP 错误"。
            resp = getattr(exc, 'response', None)
            if sc == 403:
                by_body = _classify_response(resp)
                if by_body:
                    return by_body
            return 'http_error'
        return 'invalid_response'
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
    # 兜底: 解析类异常(在异常/壳页上硬取值越界等, 如 javdb 在地区封锁页上 xpath 越界)
    # 应归为 invalid_response(不熔断), 否则死站/封锁页会让源被误判成"内部错误"并累计熔断。
    if isinstance(exc, (IndexError, KeyError, AttributeError, ValueError, TypeError)):
        return 'invalid_response'
    return 'error'


# 各源"问得动"的样本查询串。探活判据是**真的解析出有效标题**, 所以样本必须落在
# 该站的收录范围内 —— 拿通用番号 IPX-001 去问 FC2 / GETCHU / gyutto 这类只收录特定
# 番号段的站点, 它们会直接抛 `Invalid XX number`: 那是"问错了对象", 不是站点故障。
# (实测: 用 IPX-001 探活时 fc2 / fc2ppvdb / dl_getchu / gyutto 全部报这类错)
_SAMPLE_BY_SOURCE = {
    # FC2-1234567 是编造的号, 该站根本没有 -> 永远返回 not_found, 探活形同虚设。
    # 换成实测真实存在、且字段完整的经典号(FC2-718323 / FC2-1254456 均实测可解析出
    # 标题/女优/封面/样本图/发售日)。
    'fc2': 'FC2-718323',
    'fc2ppvdb': 'FC2-718323',
    'dl_getchu': 'GETCHU-12345',
    'gyutto': 'GYUTTO-12345',
    'fanza': 'ipx00120',        # FANZA 按 cid 查询, 形如 ipx00120(品番小写去横线); 原 ipx00001 在 FANZA 未收录, 会永远误报"未收录"
    # mgstage / prestige 都是**プレステージ(PRESTIGE)** 发行商的收录渠道, 收录范围
    # 偏向自有/片库系番号, 不收 IPX 这种他家厂商的通用番号 —— 用通用样本 IPX-001
    # 去问它们必然永远"未收录", 探活形同虚设(主人手工访问发现 ABF-001 可搜到)。
    # 实测(2026-10-11) 两站对 ABF-001 都能解析出完整字段:
    #   mgstage  -> 'どちゃくそエロい最高級ギャルと中出ししまくった、あの夜。 04 八掛うみ'
    #               女优=八掛うみ / 发行商=プレステージ / 发售日=2023-06-29
    #   prestige -> 同一部片子(标题略去演员名) 女优=八掛うみ / 发售日=2023-07-06
    # 两站发售日不同, 属**互补关系**(不同收录渠道), 不是重复源, 都应保留。
    'mgstage': 'ABF-001',
    'prestige': 'ABF-001',
    # javbus: 曾因 driver-verify 年龄验证墙被标 source_defunct 并关闭; 2026-10-12
    # 实测该站已更换验证机制 —— 内置 dv=1 cookie 即可放行(见 web/javbus.py),
    # IPX-001 能完整解析出标题/女优/发行日期/封面, 故显式记录样本依据;
    # 同时用 ZZZZ-999 对照确认返回的是 404 未收录而非"任何号都成功"的假 ok。
    'javbus': 'IPX-001',
}


def _sample_for(source: str) -> str:
    """挑一个该源必然支持的样本查询串"""
    return _SAMPLE_BY_SOURCE.get(source, DEFAULT_SAMPLE)


def _samples_for(source: str) -> List[tuple]:
    """探活样本清单: [(查询串, 是否按 cid 查询), ...]

    大多数源只认 dvdid; FANZA 只认 cid; jav321 两条路径都支持(普通番号 +
    DMM Content ID), 必须都测 —— 刮削页给了它 CID 类别 chip, 探活若只验
    dvdid 路径, cid 路径挂了页面照样显示 ok, 就是在骗人。
    """
    if source == 'fanza':
        return [(_sample_for(source), True)]
    if source == 'jav321':
        # ipx00001 即 IPX-001(品番小写去横线、数字补零到 5 位的 DMM Content ID),
        # 2026-10-21 实测 jav321 对该 cid 可完整解析(标题/发行商/9 genre/封面),
        # 且 sn=ipx00001 与 sn=IPX-001 返回同一影片页、字段一致。
        return [('IPX-001', False), ('ipx00001', True)]
    return [(_sample_for(source), False)]


# ---------------------------------------------------------------- 实际站点捕获
# 面板「站点」列原先只显示 DOMAIN_HINTS 里的静态值。镜像功能虽已移除(Network.domain
# 字段已删), 但 base_url 仍**可能**被改动或运行时兜底(javlib 的 init_network_cfg 会在
# 直连/代理/主站之间选一个)。静态值一旦与实际不符, 面板就在骗人 —— 所以探活时顺带
# 把**真正请求到的 host** 记下来, 与静态值不符时前端高亮提示。
_HOST_TRACKER = threading.local()
# 探活期间的超时覆盖(仅探活线程内生效, 用 contextvar 避免污染真实刮削)。
# 各爬虫在 import 时就把超时固化成 Cfg().network.timeout, 不靠这个覆盖的话
# probe(timeout=) 就形同虚设(PROBE_TIMEOUT 会是死常量)。
_probe_timeout: ContextVar[Optional[float]] = ContextVar('javsp_probe_timeout',
                                                           default=None)
# 钩子的安装是**进程级全局**的, 而 hosts 集合是 thread-local 的。二者必须分开管理:
#   - 用引用计数保证"装一次、还原一次", 避免并发探活互相把对方的 spy 装回去;
#   - _spy 必须**闭包捕获**原始 request, 不能在调用时再去读 thread-local 的 prev
#     (否则两个探活线程交错时会读到自己, 造成无限递归 RecursionError ——
#      曾导致全部渠道被误判为 outage)。
_capture_lock = threading.Lock()
_capture_depth = 0
_original_request = None


_ABS_URL_RE = re.compile(r'^[a-zA-Z][\w+.-]*://')


def _host_of(url) -> str:
    """取 URL 的 host(小写, 剥端口与凭据); 取不到返回空串

    ⚠️ 不要用 `re.match(r'://([^/?#:]+)')` 手撸 —— 实测它有两个真 bug:
       - 'https://user:SECRET@javbus.com/' 返回 **'user'**(把 URL 用户名当主机名),
         而这个值会经 domain_actual 送到 /api/channels, 等于把凭据送到前端;
       - 'http://[2001:db8::1]:8080/' 返回 **'[2001'**(IPv6 被方括号截断)。
    `urlsplit().hostname` 两者都正确。实测两者速度相当(urlsplit 1.04µs vs 正则 0.99µs,
    相对单请求数百毫秒的网络耗时可忽略), 故选正确性优先的那个。
    """
    try:
        s = url if isinstance(url, str) else str(url)
        if not _ABS_URL_RE.match(s):
            return ''                      # 协议相对或裸路径: requests 会自行补全, 不必处理
        return (urlsplit(s).hostname or '').lower()
    except Exception:
        return ''


def _start_host_capture():
    """开始捕获当前线程内所有请求的目标 host(进程内首次调用时才真正装钩子)"""
    global _capture_depth, _original_request
    with _capture_lock:
        if _capture_depth == 0:
            original = requests.sessions.Session.request

            # ⚠️ 必须用**局部变量** original 闭包捕获, 不能在 _spy 里去读模块级
            # _original_request: 后者在还原时会被置 None, 而嵌套/并发的还原顺序不保证,
            # 读到 None 会抛 UnboundLocalError/TypeError, 且钩子永远装不回去。
            def _spy(session, method, url, *args, _orig=original, **kwargs):
                host = _host_of(url)
                if host:
                    bucket = getattr(_HOST_TRACKER, 'hosts', None)
                    if bucket is not None:
                        bucket.add(host)
                return _orig(session, method, url, *args, **kwargs)

            requests.sessions.Session.request = _spy
            _original_request = original      # 仅用于还原时取回, 不参与 spy 内部调用
        _capture_depth += 1
    _HOST_TRACKER.hosts = set()


def _stop_host_capture() -> list:
    """停止捕获并返回本次采集到的 host 列表(去重后按字母序)"""
    global _capture_depth, _original_request
    hosts = sorted(getattr(_HOST_TRACKER, 'hosts', set()) or set())
    with _capture_lock:
        if _capture_depth > 0:
            _capture_depth -= 1
            if _capture_depth == 0:
                # ⚠️ 必须先取到全局值再判断: global 声明会让 _original_request 成为
                # 函数局部变量, 直接写在 `if ... and _original_request is not None`
                # 条件里会在"尚未赋值"时抛 UnboundLocalError(本地并发压测实测)。
                saved = _original_request
                _original_request = None
                if saved is not None:
                    requests.sessions.Session.request = saved
    _HOST_TRACKER.hosts = None
    return hosts


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
        # 探活时**实际请求到**的 host 列表; 与 DOMAIN_HINTS 不符时前端会高亮,
        # 提示"展示值可能已失真"(防的是有人改了 base_url 却忘了同步展示值)
        'hosts': [],
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
    static_domain = DOMAIN_HINTS.get(row['source'], '')
    hosts = list(row.get('hosts') or [])
    row['domain'] = static_domain
    row['domain_static'] = static_domain
    # 实测 host: 优先用探活真正请求到的; 没探过(未检查)则回退到静态值
    row['hosts'] = hosts
    row['domain_actual'] = ', '.join(hosts) if hosts else static_domain
    # 与静态值不符 => 展示值可能失真(有人改了 base_url / 走了镜像 / 域名迁移未同步)
    row['domain_mismatch'] = bool(hosts) and not any(
        static_domain.split(' ')[0].lower() in h or h in static_domain.lower()
        for h in hosts)
    row['status_text'] = STATUS_TEXT.get(row['status'], row['status'])
    # severity 供 overview() 排序(问题源最前), 不可缺失
    row['severity'] = _STATUS_SEVERITY.get(row['status'], 50)
    row['breaker_text'] = {
        STATE_CLOSED: '正常参与刮削',
        STATE_OPEN: '已熔断(跳过)',
        STATE_HALF_OPEN: '试探中',
    }.get(row['breaker'], row['breaker'])
    row['active'] = active
    row['sample'] = _sample_for(row['source'])

    # ---- 「配置启用」≠「实际可用」, 两者也与「是否展示命中率」正交 ----
    # active 只反映"配置里勾没勾这个源"(active_sources 读 selection), 与健康状态无关。
    # 于是 IP 被封 / 需人机验证 / 前端渲染 / 站点已失效的源, 在"刮削参与"列里仍显示
    # "参与" —— 而它们每次刮削必然失败, 这个说法会误导主人以为是正常源。
    # usable 明确回答"这个源现在还能不能刮到数据"。
    row['usable'] = row['status'] not in _UNUSABLE_STATUSES      # 只看状态, 不掺"是否勾选"
    row['participating'] = active and row['usable']              # 真正会参与刮削 = 勾选 且 可用
    row['unusable_reason'] = _UNUSABLE_HINTS.get(row['status'], '') if not row['usable'] else ''

    # 命中率: 仅当"**已启用且不可用**"时隐藏 —— 那才是"成功率必然 0%、展示只会
    # 让人误以为偶发失败"的情形。
    # ⚠️ 不可用要把 active 一并纳入: 未启用是**用户主动关掉**, 与"源用不了"是两回事,
    # 直接置 None 会抹掉历史命中率(用户下次勾回来时正需要它判断该不该开)。
    suppress_hit_rate = active and not row['usable']
    if row['scrape_samples'] and not suppress_hit_rate:
        row['hit_rate'] = round(row['scrape_success'] / row['scrape_samples'] * 100)
    else:
        row['hit_rate'] = None
    # 连续失败计数同理: 不可用的源不该再提示"再失败一次就熔断"(它早就熔断了/永远不可用)
    row['show_fail_count'] = (row['usable'] and active
                              and row['breaker'] == STATE_CLOSED)
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
    """该源当前是否应被刮削跳过。**刮削路径的热路径, 务必轻量**

    ⚠️ 这里同时覆盖两类"跳过", 因为二者对刮削的含义完全一样 —— **别去请求它**:
      1. **熔断**(breaker=open): 临时性故障, 冷却后会自动恢复再试;
      2. **不可用**(_UNUSABLE_STATUSES): IP 被封 / 需真人校验 / 前端渲染 / 站点已失效
         / 缺凭据。这类**不会自己恢复**, 若不跳过, 每部影片都要在它身上白等
         retry x timeout(实测一个源就是几十秒), 而结果必然是失败 —— 这正是主人
         要求的"不能刮削的就不要浪费时间"。
    之前只看 breaker, 导致面板已标"不可用"、实际刮削仍在请求的割裂。
    """
    with _lock:
        rec = _records.get(source)
        if rec is None:
            return False                      # 未知源: 从未探活过, 照常尝试(首次运行不该全跳过)
        # 不可用: 无视冷却直接跳过(它不会自己好), 但仍返回 False 给一次探活恢复的机会:
        # probe() 不走 is_tripped, 所以恢复路径不受影响。
        if rec['status'] in _UNUSABLE_STATUSES:
            return True
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
    started = time.perf_counter()
    status, reason, status_code = 'unknown', '', 0
    hosts = []
    _start_host_capture()          # 顺带记录实际请求到的 host, 供面板核对展示值
    # 让 probe 的 timeout 参数真正生效: 各爬虫在 import 时就把超时固化成
    # Cfg().network.timeout(airav 甚至硬性抬高到 20s 下限), 若不在此处覆盖,
    # PROBE_TIMEOUT 就是死常量 —— 用户把 network.timeout 调到 60s 时探活会整体拖到
    # 分钟级, 定时任务持续堆积。用 contextvar 而非全局量, 避免影响真实刮削。
    _prev_timeout = _probe_timeout.set(timeout)
    try:
        mod = __import__('javsp.web.' + source, fromlist=['parse_data'])
        samples = _samples_for(source)
        failures = []              # [(样本, 是否cid路径, 状态, 原因, 状态码)]
        for sample, as_cid in samples:
            # cid 路径必须 dvdid=None: parse_data 里 dvdid 优先, 若 dvdid 也填了
            # 样本值就永远走不到 cid 分支, 测的等于还是 dvdid 路径。
            # (FANZA 只按 cid 查询, 形如 ipx00001; 早期只设 dvdid 时它拿空 cid
            # 去拼详情页 URL, 拿到非详情页 -> xpath 取不到标题 -> IndexError,
            # 被误判成"内部错误"并计入熔断, 实测 fanza 就是这么被扣上内部错误的)
            movie = MovieInfo(cid=sample) if as_cid else MovieInfo(sample)
            try:
                mod.parse_data(movie)
                # 不能只看"没抛异常": 很多死站返回 200 + 壳页/跳转页, 传输层毫无异常。
                # 判定标准必须是**真的解析出了有效标题**, 且标题不能就是番号本身。
                title = (getattr(movie, 'title', '') or '').strip()
                if title and title.upper() != sample.upper():
                    continue
                failures.append((sample, as_cid, 'invalid_response',
                                 '未解析出有效标题(疑似壳页/跳转页)', 0))
            except BaseException as e:      # noqa: BLE001 单样本失败绝不影响整体与其他源
                st = _classify(e)
                rs = _sanitize(f'{type(e).__name__}: {e}')
                resp = getattr(e, 'response', None)
                sc = getattr(resp, 'status_code', 0)
                sc = sc if isinstance(sc, int) else 0
                if st in ('not_found', 'invalid_response') and sc >= 400:
                    st = 'http_error'
                failures.append((sample, as_cid, st, rs, sc))
        if not failures:
            status = 'ok'
        else:
            # 多样本源(如 jav321)任一维度的路径不通即报; reason 标注是哪个样本
            # 哪条路径失败的, 避免主人看到"证书过期"却以为是普通番号路径的问题。
            sample, as_cid, status, reason, status_code = failures[0]
            if len(samples) > 1:
                path = 'cid' if as_cid else 'dvdid'
                reason = f'[{path} 样本 {sample}] {reason}'
    except BaseException as e:      # noqa: BLE001 单源失败绝不影响整体与其他源
        status = _classify(e)
        reason = _sanitize(f'{type(e).__name__}: {e}')
        resp = getattr(e, 'response', None)
        sc = getattr(resp, 'status_code', 0)
        status_code = sc if isinstance(sc, int) else 0
        if status in ('not_found', 'invalid_response') and status_code >= 400:
            status = 'http_error'
    finally:
        # 必须在 finally 里还原: 否则一次异常就会让 requests 的钩子永久留在进程里,
        # 之后所有请求都会被计入 hosts(线程局部变量, 泄漏后无人能清理)
        hosts = _stop_host_capture()
        try:
            _probe_timeout.reset(_prev_timeout)
        except Exception:       # noqa: BLE001 极端情况下不可让还原失败拖垮探活
            pass
    elapsed = int((time.perf_counter() - started) * 1000)

    # 人工核实的站点现状优先于自动分类: 这些源无论怎么探都会得到
    # MovieNotFoundError / 403 这类"看起来源还好"的结果, 显示出来会误导主人
    # 去排查"样本号不对""是不是代理坏了"。实测结论见 SOURCE_STATUS_OVERRIDE。
    override = SOURCE_STATUS_OVERRIDE.get(source)
    if override and status != 'ok':
        status, reason = override[0], override[1]

    stale = _is_stale(source, seq)
    _commit(source, seq, status, reason, elapsed, status_code, origin='active')
    with _lock:
        if hosts:
            _get(source)['hosts'] = hosts
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
        # ⚠️ 必须重算派生字段: usable / unusable_reason / hit_rate / show_fail_count /
        # severity 都是 _decorate 按 status 算出来的。outage 覆盖了 status 却不同步它们,
        # 就会出现"面板显示不参与(usable=False) 而实际会参与(is_tripped 读的是 _records
        # 里的 status=outage -> False)"这种**面板与实际行为相反**的矛盾, 且副标题还
        # 会残留"需换代理 IP"这类针对旧状态的错误排查建议。
        actives = set(active_sources())
        fresh = _decorate(_get(src), src in actives)
        r.update({k: fresh[k] for k in ('usable', 'participating', 'unusable_reason',
                                        'hit_rate', 'show_fail_count', 'severity',
                                        'domain_actual', 'domain_mismatch')})
        names.append(src)
    if names:
        logger.warning(f'出口整体故障: {len(names)}/{len(results)} 个源同时失败, '
                       f'已豁免熔断 -> {", ".join(names)}')


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

    ⚠️ allowlist 必须**在循环外算一次**: 放循环里会变成 O(n²)(每次都重建集合,
    19 源 = 361 次加锁), 且因 is_tripped 有副作用而使 skipped 取值随循环顺序漂移。
    """
    if not isinstance(sources, dict):
        return sources
    # 循环外算一次; 用无副作用的纯查询, 不触碰熔断状态机
    allow = {s for s in active_sources() if not is_tripped(s)}
    out = {}
    with _lock:
        for name, data in sources.items():
            row = dict(data or {})
            rec = _get(name)
            row['health_status'] = rec['status']
            row['health_text'] = STATUS_TEXT.get(rec['status'], rec['status'])
            row['breaker'] = rec['breaker']
            row['skipped'] = (rec['breaker'] == STATE_OPEN
                              or rec['status'] in _UNUSABLE_STATUSES) \
                and name not in allow
            row['trip_reasons'] = rec['consecutive_failures']
            out[name] = row
    return out


def summary_for_log() -> str:
    rows = overview(include_inactive=False)
    if not rows:
        return '渠道健康: 无启用源'
    ok = sum(1 for r in rows if r['status'] == 'ok')
    tripped = sum(1 for r in rows if r['breaker'] == STATE_OPEN)
    return f'渠道健康: {ok}/{len(rows)} 正常, {tripped} 熔断跳过'