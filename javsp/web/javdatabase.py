"""从 javdatabase.com 抓取影片元数据 (移植自 JavBoss internal/jav/javdatabase)

为什么加它:
    JavSP-fork 在 NAS 受限出口下原多源(javlib/javbus/javdb 网站)已废或被 Cloudflare 拦截,
    JavBoss 研究证实 javdatabase.com 在该出口下可达并可取数(探针 /movies/IPX-001/ 返回 11 万字节真页)。

实现来源(合规):
    解析逻辑移植自 github.com/Solr159/JavBoss 的 `internal/jav/javdatabase`(MIT)。本文件为独立
    Python 实现, 仅复用其「标签 -> 字段」的映射与取值顺序, 不复制 Go 代码。

与本项目规范的对接:
    - 复用 `javsp.web.base.get_html()`: 它内部每次调用现取 `read_proxy()` / `tls_verify()`, 不模块级
      固化(符合 v0.1.12 热重载教训); 解析用 lxml, 与 jav321/javbus 等现有源一致。
    - 对齐 `parse_data(movie)` 接口, 直接写入 MovieInfo, 由 core 动态发现。
    - 找不到影片时抛 `MovieNotFoundError`, 由 core 干净跳过(优雅降级约定, 见 v0.1.28)。
"""
import re
import logging
from urllib.parse import urljoin

from requests.exceptions import HTTPError

from javsp.web.base import get_html
from javsp.web.exceptions import *
from javsp.datatype import MovieInfo


logger = logging.getLogger(__name__)
BASE_URL = 'https://www.javdatabase.com'
DETAIL_URL = BASE_URL + '/movies/{code}/'


def _collapse(s):
    return ' '.join((s or '').split()).strip()


def _norm_label(s):
    """归一标签: 小写、冒号转空格、非字母数字统一成空格、压缩空白 (对应 JavBoss normalizeLabel)"""
    s = (s or '').lower().strip()
    s = s.replace('：', ':')
    s = re.sub(r'[^a-z0-9:]+', ' ', s)
    return _collapse(s)


def _has(label, *tokens):
    return any(t in label for t in tokens)


def _value_after(b):
    """取 <b> 标签之后、遇到首个 <br> 为止的纯文本 (对应 JavBoss collectValueAfterBold)"""
    if b is None:
        return ''
    parts = [b.tail or '']
    for sib in b.itersiblings():
        if not isinstance(sib.tag, str):
            continue
        if sib.tag.lower() == 'br':
            break
        parts.append(sib.text or '')
        parts.append(sib.tail or '')
    return _collapse(''.join(parts))


def _first_anchor_text(node):
    a = node.xpath(".//a[1]")
    if a:
        return _collapse(a[0].text_content())
    return ''


def _anchor_texts(node):
    return [_collapse(t) for t in node.xpath(".//a/text()") if _collapse(t)]


def _clean_page_title(title):
    """从 <title> 取标题: 去掉 ' - JAVDatabase' 等站点后缀, 再取最后一个 ' - ' 之后的部分"""
    title = _collapse(title)
    for suffix in ('- JAVDatabase', '- JavDatabase', '- JAVDatabase.com', '- JavDatabase.com'):
        if title.endswith(suffix):
            title = title[:-len(suffix)].strip()
    if ' - ' in title:
        title = title.rsplit(' - ', 1)[-1].strip()
    return title


def parse_data(movie: MovieInfo):
    """从 javdatabase.com 抓取并解析指定番号的数据, 直接写入 movie

    Args:
        movie (MovieInfo): 要解析的影片信息, 解析后的信息直接更新到此变量内
    """
    code = (movie.dvdid or '').strip()
    if not code:
        raise MovieNotFoundError(__name__, movie.dvdid)

    url = DETAIL_URL.format(code=code.upper())
    try:
        html = get_html(url)
    except HTTPError as e:
        if getattr(e.response, 'status_code', None) == 404:
            raise MovieNotFoundError(__name__, movie.dvdid)
        raise

    # 定位影片信息列: 优先 movietable 内, 回退到任意 col-md-10 + col-xxl-10
    cols = html.xpath(
        "//div[contains(@class,'movietable')]"
        "//div[contains(@class,'col-md-10') and contains(@class,'col-xxl-10')]")
    if not cols:
        cols = html.xpath(
            "//div[contains(@class,'col-md-10') and contains(@class,'col-xxl-10')]")
    if not cols:
        raise MovieNotFoundError(__name__, movie.dvdid)
    column = cols[0]

    title = ''
    date = studio = series = runtime = ''
    tags = []
    actors = []
    for p in column.xpath(".//p[contains(concat(' ',normalize-space(@class),' '),' mb-1 ')]"):
        bs = p.xpath("b[1]")
        if not bs:
            continue
        b = bs[0]
        label = _norm_label(b.text_content())
        if not label:
            continue
        if _has(label, 'title'):
            if not title:
                title = _value_after(b)
        elif _has(label, 'release date', 'released', 'date'):
            if not date:
                date = _value_after(b)
        elif _has(label, 'runtime', 'duration'):
            if not runtime:
                # 实测真实页为 'Runtime: 160 (HD: 160) min.', 需抽纯分钟数
                # (本项目 duration 惯例为纯数字字符串, 见 fanza/avsox/jav321 等源)
                m = re.search(r'\d+', _value_after(b))
                runtime = m.group(0) if m else ''
        elif _has(label, 'studio'):
            if not studio:
                studio = _first_anchor_text(p) or _value_after(b)
        elif _has(label, 'series'):
            if not series:
                series = _first_anchor_text(p) or _value_after(b)
        elif _has(label, 'genre'):
            if not tags:
                tags = _anchor_texts(p)
        elif _has(label, 'idol', 'actress', 'actor'):
            if not actors:
                actors = _anchor_texts(p)

    # 标题回退: <title> 去掉站点后缀
    if not title:
        t = html.xpath("//title/text()")
        if t:
            title = _clean_page_title(t[0])

    if not (title or studio or series or date or runtime or tags or actors):
        raise MovieNotFoundError(__name__, movie.dvdid)

    # 注意: 不覆盖 movie.dvdid。core 以输入番号关联各源结果, 改写会破坏合并(见 v0.1.x 教训)
    movie.url = url
    cover = html.xpath("//meta[@property='og:image']/@content")
    if not cover:
        cover = html.xpath(
            "//img[contains(@class,'poster')]/@src | //img[contains(@class,'cover')]/@src")
    if cover:
        # meta[og:image] 的 content 是相对路径(与 JavBoss ResolveURL 一致), 用页面 URL 拼绝对地址
        movie.cover = _collapse(urljoin(url, cover[0]))
    if title:
        movie.title = title
    if studio:
        movie.producer = studio
    if series:
        movie.serial = series
    if date:
        movie.publish_date = date
    if runtime:
        movie.duration = runtime
    if tags:
        movie.genre = tags
    if actors:
        movie.actress = actors


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
