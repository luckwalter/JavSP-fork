"""从 javfree.me 抓取影片元数据

为什么加它:
    javfree 是一个番号库(WordPress, javfree.me), 在 NAS 日本出口下可达, 可作为第 22 个
    番号补充源, 补位已废/被墙的老源(如 javbus/javlib)。移植思路参考 metatube-community
    /metatube-sdk-go 的 provider/javfree (MIT), 但为独立 Python 实现, 不复用 Go 代码。

实现要点:
    - 复用 javsp.web.base.get_html(): 每次调用现取 read_proxy()/tls_verify(), 不模块级固化
      (符合 v0.1.12 热重载教训); 解析用 lxml, 与 jav321/javdatabase 等现有源一致。
    - 对齐 parse_data(movie) 接口, 直接写入 MovieInfo, 由 core 动态发现。
    - 找不到影片时抛 MovieNotFoundError, 由 core 干净跳过(优雅降级约定, 见 v0.1.28)。
    - 不覆盖 movie.dvdid: core 以输入番号关联各源结果, 改写会破坏合并(见 v0.1.x 教训)。

抓取策略:
    按番号搜索 `?s=<code>` 结果页, 从 article 标题前缀方括号里提取番号候选, 与输入番号
    (忽略横线)精确匹配, 取首条匹配进详情页解析。比直接猜详情页 URL 更稳(WordPress 固定
    链接重写规则在不同部署下不一致, 而搜索结果页的 article 结构是稳定的)。
"""
import re
import logging
from urllib.parse import quote, urljoin

from requests.exceptions import HTTPError

from javsp.web.base import get_html
from javsp.web.exceptions import *
from javsp.datatype import MovieInfo


logger = logging.getLogger(__name__)
BASE_URL = 'https://javfree.me'
SEARCH_URL = BASE_URL + '/?s={q}'


# 标题形如 "[HD][MIDE-980] 影片标题 演员", 开头可能有多个方括号块;
# 第一个通常是厂牌/规格标签(如 [HD]/[FHD]), 最后一个才是番号。
_re_leading = re.compile(r'^\s*((?:\[[A-Za-z0-9_-]+\]\s*)+)')
_re_brackets = re.compile(r'\[([A-Za-z0-9_-]+)\]')
# 番号需含数字且长度>=4, 以排除 HD/FHD/UHD 等规格标签
_re_number_like = re.compile(r'^[A-Za-z0-9-]{4,}$')
_re_has_digit = re.compile(r'[0-9]')
_re_ascii = re.compile(r'[A-Za-z]')


def _collapse(s):
    return ' '.join((s or '').split()).strip()


def _norm_code(v):
    """番号归一: 去横线 + 转大写, 用于忽略 'IPX-001' vs 'IPX001' 的格式差异"""
    return (v or '').replace('-', '').upper()


def _split_leading_brackets(text):
    """拆分标题前缀方括号块, 返回 (番号候选列表, 剥离后的标题正文)"""
    m = _re_leading.match(text or '')
    if not m:
        return [], (text or '').strip()
    cands = [g.upper() for g in _re_brackets.findall(m.group(1))]
    return cands, (text or '')[len(m.group(1)):].strip()


# 详情页 blockquote 内的元信息行前缀(逐行 <br> 分隔)。某行以这些前缀开头即认为它仍是
# 元信息而非简介正文 —— 用于「该条目只有元信息、没有简介」的判定。
_META_PREFIXES = ('発売日', '収録時間', '出演者', '監督', 'シリーズ', 'メーカー',
                   'レーベル', 'ジャンル', '品番', '収録', '配信', '制作', '形式')


def _split_paragraph_lines(p) -> list:
    """把 <p> 的内容按 <br> 切成若干行文本(行内标签取其全部文本)"""
    lines = []
    cur = p.text or ''
    for child in p:
        if child.tag == 'br':
            lines.append(cur)
            cur = ''
            cur += child.tail or ''
        else:
            cur += ''.join(child.itertext()) + (child.tail or '')
    lines.append(cur)
    return [' '.join(line.split()) for line in lines]


def _extract_plot(doc) -> str | None:
    """从详情页提取简介正文, 取不到返回 None

    实测结构(2026-11-25, javfree.me):
        <blockquote><p>発売日： ...<br>収録時間： ...<br>...<br>品番： <strong>xxx</strong><br>
        【简介正文】
        </p></blockquote>
    元信息逐行 <br> 分隔, 最后一行才是简介正文。

    只取 blockquote 里的 p 是有依据的: 含简介的条目把简介放在 blockquote 内;
    而元信息直接铺在 entry-content/p 下的条目, 实测其末行是空行或样本视频链接,
    本就没有简介正文 —— 那种条目判为「无简介」返回 None 才是对的
    (若放宽到 entry-content/p, 会把样本链接当简介写进 NFO)。
    另: 并非所有条目都有 blockquote, 缺失时静默返回 None, 不抛异常拖垮整条链路。
    """
    bqs = doc.xpath("//div[@class='entry-content']/blockquote")
    if not bqs:
        return None
    ps = bqs[0].xpath('.//p')
    if not ps:
        return None
    for line in reversed(_split_paragraph_lines(ps[0])):
        if not line:
            continue
        # 末行仍是元信息 -> 该条目没有简介
        if any(line.startswith(prefix) for prefix in _META_PREFIXES):
            return None
        # 末行是样本视频链接(纯 URL) -> 同样没有简介
        if line.startswith(('http://', 'https://')):
            return None
        return line
    return None


def parse_data(movie: MovieInfo):
    """从 javfree.me 抓取并解析指定番号的数据, 直接写入 movie

    Args:
        movie (MovieInfo): 要解析的影片信息, 解析后的信息直接更新到此变量内
    """
    code = (movie.dvdid or '').strip().upper()
    if not code:
        raise MovieNotFoundError(__name__, movie.dvdid)
    target = _norm_code(code)

    # 策略: 搜索结果页取首条番号匹配项, 进详情页解析
    try:
        html = get_html(SEARCH_URL.format(q=quote(code)))
    except HTTPError as e:
        if getattr(e.response, 'status_code', None) == 404:
            raise MovieNotFoundError(__name__, movie.dvdid)
        raise

    detail_url = None
    for art in html.xpath("//article[contains(concat(' ',normalize-space(@class),' '),' hentry ')]"):
        a = art.xpath(".//h2[@class='entry-title']/a")
        if not a:
            continue
        title_raw = (a[0].text_content() or '').strip()
        href = a[0].get('href')
        cands, _ = _split_leading_brackets(title_raw)
        # 忽略横线比较, 兼容 'IPX-001' 与 'IPX001' 两种写法
        if any(_norm_code(c) == target for c in cands):
            detail_url = urljoin(BASE_URL, href)
            break
    if not detail_url:
        raise MovieNotFoundError(__name__, movie.dvdid)

    try:
        d = get_html(detail_url)
    except HTTPError as e:
        if getattr(e.response, 'status_code', None) == 404:
            raise MovieNotFoundError(__name__, movie.dvdid)
        raise

    # 标题 + 番号(写入 title 时剥离前缀方括号; 不覆盖 movie.dvdid)
    h1 = d.xpath("//h1[@class='entry-title']//text()")
    if h1:
        raw = _collapse(''.join(h1))
        _cands, body = _split_leading_brackets(raw)
        movie.title = body or raw

    # 封面: 优先 og:image; 否则取详情页正文首图(cf.javfree.me CDN, 真实有效)。
    # 注意: 该站 cf.javfree.me/cover/500x333/<id>.jpg 路径实测返回 165 字节错误页,
    # 不可用, 故不硬编码 CDN 兜底, 改从正文 img 动态取(与 preview_pics 同源)。
    cover = d.xpath("//meta[@property='og:image']/@content")
    if not cover:
        cover = d.xpath("//div[@class='entry-content']//img/@src")
    if cover:
        movie.cover = _collapse(urljoin(detail_url, cover[0]))

    # 演员: rel="tag" 中**非 ASCII(纯日文)**的标签 —— 排除含英文的厂牌/系列标签
    # (javfree 的 Tags 段落把演员与厂牌/系列平铺, 结构上无法区分, 用 ASCII 启发式过滤)
    actors = []
    for a in d.xpath("//a[@rel='tag']"):
        t = (a.text_content() or '').strip()
        if t and not _re_ascii.search(t):
            actors.append(t)
    if actors:
        movie.actress = actors

    # 预览图: entry-content 内的 img(JS 动态填充, best-effort)
    pics = []
    for src in d.xpath("//div[@class='entry-content']//img/@src"):
        if src:
            pics.append(urljoin(detail_url, src))
    if pics:
        movie.preview_pics = pics

    # 简介(plot): 元信息逐行 <br>, 末行才是简介正文; 无 blockquote 时为 None
    movie.plot = _extract_plot(d)

    movie.url = detail_url

    # 有效性兜底: 核心字段全空视为无效响应(health 会判 invalid_response 而非 ok)
    if not (movie.title or movie.cover):
        raise MovieNotFoundError(__name__, movie.dvdid)


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
