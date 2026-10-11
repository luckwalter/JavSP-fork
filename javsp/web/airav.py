"""从 airav 抓取数据（2026-10 起适配新站 airav.io）

==== 站点变迁 ====
原 airav.wiki 走 JSON API(`/api/video/barcode/{番号}`), 现已停服(实测 HTTP 521)。
新站 **airav.io 是完全不同的架构**, 不是同一套接口的镜像:

  - 旧: JSON API, 详情页按番号寻址, 字段齐全(barcode/name/description/img_url/
       tags/actors/publish_date/images/factories 一次返回)
  - 新: **服务端渲染 HTML, 无 JSON API**; 详情页按**内部 ID**寻址
       (`/video?hid=QC-BT-4732114`), 番号要用搜索页(`/search_result?kw=番号`)定位

因此不能只换域名, 必须改为"搜索定位 -> HTML 解析 + JSON-LD 取封面"。

⚠️ **新站的 JSON-LD 里有两个假字段, 不要采信**(实测 IPZZ-989 与 JUR-852 均为同值):
   - `duration`    = 'PT10M0S'  → 采集站占位值, 与真实时长无关
   - `uploadDate`  = 采集当天    → 不是发行日期
故这两项**留空交给其它源补**, 只采信 name / thumbnailUrl / description。
"""
import json
import logging
import re
from html import unescape

from javsp.web.base import Request, resp2html
from javsp.web.exceptions import *
from javsp.config import Cfg
from javsp.datatype import MovieInfo

# 初始化Request实例
request = Request()
request.headers['Accept-Language'] = 'zh-TW,zh;q=0.9'
# 新站为普通站点(无 Cloudflare 挑战), 实测单次响应 <1.5s; 沿用 20s 下限以容忍偶发慢响应
_TIMEOUT_FLOOR = 20
request.timeout = max(_TIMEOUT_FLOOR, Cfg().network.timeout.total_seconds())

logger = logging.getLogger(__name__)
base_url = 'https://airav.io'
permanent_url = base_url

# 番号在标题/JSON-LD 里的形态: 允许 xxx-123 / FC2-PPV-1234567 / 012717_472 / 1pondo_012717_472
_DVDID_RE = re.compile(r'[A-Za-z0-9]+[-_][A-Za-z0-9\-]+')


def _json_ld(html):
    """取详情页里的 schema.org VideoObject(JSON-LD)"""
    for node in html.xpath("//script[@type='application/ld+json']"):
        raw = (node.text or '').strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            # 站点偶尔输出带尾逗号的非法 JSON, 去掉再试
            try:
                data = json.loads(re.sub(r',\s*([}\]])', r'\1', raw))
            except ValueError:
                continue
        for item in (data if isinstance(data, list) else [data]):
            if isinstance(item, dict) and item.get('@type') == 'VideoObject':
                return item
    return {}


def search_movie(dvdid):
    """用番号搜索, 返回该片在 airav.io 上的详情页地址(hid 形式)

    新站搜索页为 `/search_result?kw=<番号>`, 结果条目链接形如
    `https://airav.io/video?hid=QC-BT-4732114` —— 注意**不是**按番号寻址,
    所以只能从搜索结果里挑出与番号匹配的那一条。
    """
    url = base_url.rstrip('/') + '/search_result?kw=' + dvdid
    r = request.get(url)
    if r.status_code == 404:
        raise MovieNotFoundError(__name__, dvdid)
    r.raise_for_status()
    html = resp2html(r)
    links = html.xpath("//a[contains(@href, '/video?hid=')]/@href")
    if not links:
        raise MovieNotFoundError(__name__, dvdid)

    # 逐个候选详情页, 取回番号匹配的那一条(搜索页文本不含番号, 只能进详情页比对)
    target = dvdid.upper()
    for link in links:
        if not link.lower().startswith('http'):
            link = base_url.rstrip('/') + '/' + link.lstrip('/')
        try:
            cand = resp2html(request.get(link))
        except Exception:
            continue
        cand_dvdid = _pick_dvdid(cand, target)
        if cand_dvdid:
            return link, cand_dvdid
    raise MovieNotFoundError(__name__, dvdid, links)


def _pick_dvdid(html, target=None):
    """从详情页取出番号

    优先取 `div.video-title` 内的 span(结构化且就是番号本身), 取不到再从 h1 里
    抠开头的番号串。target 传入时校验是否匹配(大小写与连字符/下划线差异视作相同)。
    """
    dvdid = ''
    for node in html.xpath("//div[contains(@class,'video-title')]//span"):
        text = (node.text_content() or '').strip()
        if text and _DVDID_RE.fullmatch(text):
            dvdid = text
            break
    if not dvdid:
        h1 = html.xpath("//div[contains(@class,'video-title')]/h1/text()")
        if h1:
            m = _DVDID_RE.search(unescape(h1[0]).strip())
            if m:
                dvdid = m.group(0)
    if not dvdid:
        return ''
    if target:
        norm = lambda s: s.upper().replace('_', '-')
        if norm(dvdid) != norm(target):
            return ''
    return dvdid


def parse_data(movie: MovieInfo):
    """解析指定番号的影片数据"""
    # 空番号无意义(拼出的搜索地址会是站点首页, 拿到的是壳页)
    if not (getattr(movie, 'dvdid', '') or '').strip():
        raise MovieNotFoundError(__name__, '(空番号)')
    full_id = movie.dvdid
    # FC2 番号在该站以 FC2-PPV-xxxxx 形式收录
    # (实测: 2026-10 该站对 FC2 已**完全无收录** —— 'FC2-PPV-2735981'/'2735981'/
    #  'FC2-2735981' 四种写法搜索结果均为 0 条, 故这里转换后仍会如实报未收录,
    #  属正常表现而非 bug。FC2 番号请依赖 fc2/javdb 等源)
    if full_id.upper().startswith('FC2-'):
        full_id = 'FC2-PPV-' + full_id[4:]

    detail_url, dvdid = search_movie(full_id)
    html = resp2html(request.get(detail_url))

    ld = _json_ld(html)
    title = unescape((ld.get('name') or '').strip())
    if not title:
        h1 = html.xpath("//div[contains(@class,'video-title')]/h1/text()")
        title = unescape(h1[0]).strip() if h1 else ''
    if not title:
        # 页面结构变了(既无 JSON-LD 也无 h1) —— 报"内部错误"以便渠道监控归类,
        # 不能静默返回, 否则上层会写入一条全空影片
        raise WebsiteError('airav: 详情页未取到标题(页面结构可能已改变)')

    movie.dvdid = dvdid
    movie.url = detail_url
    movie.title = title.replace(dvdid, '').strip()

    # 封面: JSON-LD 的 thumbnailUrl 最稳
    thumb = ld.get('thumbnailUrl')
    if isinstance(thumb, list):
        thumb = thumb[0] if thumb else None
    if not thumb:
        thumb = (html.xpath("//meta[@property='og:image']/@content") or [None])[0]
    movie.cover = thumb

    # 女优: 详情页形如 /actor?id=34849 的链接
    actress = []
    for node in html.xpath("//a[contains(@href, '/actor?id=')]"):
        name = (node.text_content() or '').strip()
        # 导航里的"女優一覽"也会命中 /actors, 但不含 '?id=' 故不会混入;
        # 这里仍做一次"是否像人名"的兜底过滤
        if name and '一覽' not in name and name not in actress:
            actress.append(name)
    movie.actress = actress

    # 类型标签: /tag?tid=  ;  发行商: /tag?fid= (站点用同一机制, fid 表示厂商)
    genre = [(n.text_content() or '').strip()
             for n in html.xpath("//a[contains(@href, '/tag?tid=')]")]
    movie.genre = [g for g in genre if g]
    factory = [(n.text_content() or '').strip()
               for n in html.xpath("//a[contains(@href, '/tag?fid=')]")]
    if factory:
        movie.producer = factory[0]

    # 简介: JSON-LD 的 description 与标题同文, 信息量有限, 有非空才写
    desc = unescape((ld.get('description') or '').strip())
    movie.plot = desc or None

    # ⚠️ 刻意**不写** publish_date / duration: 新站 JSON-LD 的 uploadDate 与
    # duration 均为占位值(uploadDate 是采集当天, duration 恒为 PT10M0S),
    # 采信会把错误数据写进 NFO, 让 Jellyfin 显示错误发行日与"10分钟"时长。
    # 这两项留给其它源补(JavSP 多源合并时不会因缺字段报错)。

    # 站点会把部分影片标记为'馬賽克破壞版'等, 这类 title/plot/genre 不再准确
    for keyword in ('馬賽克破壞版', '馬賽克破解版', '無碼流出版'):
        if movie.title and keyword in movie.title:
            movie.title = None
            movie.genre = []
        if movie.plot and keyword in movie.plot:
            movie.plot = None
            movie.genre = []


if __name__ == "__main__":
    import pretty_errors
    pretty_errors.configure(display_link=True)
    logger.root.handlers[1].level = logging.DEBUG

    movie = MovieInfo('IPZZ-989')
    try:
        parse_data(movie)
        print(movie)
    except CrawlerError as e:
        logger.error(e, exc_info=1)