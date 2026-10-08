"""从 javmenu.com 抓取影片元数据 (移植自 JavBoss internal/jav/javmenu)

==== 替换说明(依据主人「允许用新实现替换原 .py 并说明范围与理由」的授权) ====
范围:
    本文件整体重写。原 `javmenu.py` 抓取 `mrzyx.xyz`(一个已死站, 自 v0.1.28 起
    在受限出口下优雅降级为 MovieNotFoundError 空过, 不产生任何数据)。本次改为抓取
    `javmenu.com` —— JavBoss 探针证实该站在 NAS 受限出口下可达并可取数
    (/IPX-001 返回 16 万字节真页)。
理由:
    1) mrzyx.xyz 已失效, 保留只会增加一个永远空过的源, 无收益;
    2) javmenu.com 是同功能的活跃站点(详情页结构 `div.card.rounded div.card-body`
       + 中文标签行), 直接复用其解析逻辑即可恢复一个真实可取的源;
    3) 模块名 `javmenu` 与配置 `CrawlerID.javmenu` 保持不变, 因此**不需要改动任何注册
       逻辑**, 仅就地替换实现 —— 属「同名替换」而非「新增模块」, 风险最低。
    解析结构与原死站(lxml xpath 取 `col-md-9`/`card-body` 老结构)完全不同, 故为重写。

实现来源(合规): 解析逻辑移植自 github.com/Solr159/JavBoss 的 `internal/jav/javmenu`(MIT)。

与本项目规范的对接: 复用 base.get_html(lxml, 现取代理/TLS 不固化) + parse_data 接口 +
找不到抛 MovieNotFoundError(优雅降级)。

【已知上游站点数据缺陷(实测, 勿误判为解析 bug)】
    1. 「系列」字段内容不可靠: SSIS-001 真实页该行填的是剧情梗概
       ('一ヶ月間の禁欲の果てに彼女の親友と僕が浮気SEX...'), 而 IPX-001 真实页干脆没有该行。
       JavBoss 亦原样取用, 本实现保持一致 —— 不额外加启发式过滤, 以免把真实系列名误删。
       已在 verify_newsrcs_real.py 中固化该事实, 供后续决策。
    2. 「img.lazy」在真实页是占位图 loading_3_green_dot.gif, 封面必须靠
       og:image / video@poster 兜住(本实现已按此优先级)。
    3. 真实页还存在「更新於」「導演」「在線看」等行, 现有标签映射忽略即可(无对应 MovieInfo 字段)。
"""
import re
import logging
from urllib.parse import urljoin

from requests.exceptions import HTTPError

from javsp.web.base import get_html
from javsp.web.exceptions import *
from javsp.datatype import MovieInfo


logger = logging.getLogger(__name__)
BASE_URL = 'https://javmenu.com'
# JavBoss 用 url.PathEscape(ToUpper(code)); 英文/数字番号 PathEscape 后不变, 直接拼
DETAIL_URL = BASE_URL + '/{code}/'


def _collapse(s):
    return ' '.join((s or '').split()).strip()


def _norm_label(s):
    """归一标签: 去首尾空白、去冒号、去所有空白后小写 (对应 JavBoss normalizeJavMenuLabel)"""
    s = (s or '').replace('：', ':').replace(':', '')
    s = re.sub(r'\s+', '', s)
    return s.lower()


def _clean_title(title, code):
    """清理标题: 去掉 '免費AV在線看'/'免费AV在线看' 广告后缀, 去番号前缀, 去 ' | ' 之后部分"""
    title = (title or '').replace('免費AV在線看', '').replace('免费AV在线看', '')
    title = title.strip()
    if code and title.startswith(code):
        title = title[len(code):].strip()
    title = re.sub(r'(?i)^[a-z]{2,8}[-_ ]?\d{2,6}[a-z]{0,3}\s+', '', title)
    if ' | ' in title:
        title = title.split(' | ')[0].strip()
    return title.strip()


def parse_data(movie: MovieInfo):
    """从 javmenu.com 抓取并解析指定番号的数据, 直接写入 movie

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

    # 定位影片资料卡片: 含「影片資料」文本的 card-body
    cards = html.xpath(
        "//div[contains(@class,'card') and contains(@class,'rounded')]"
        "//div[contains(@class,'card-body')]")
    target = None
    for c in cards:
        if '影片資料' in c.text_content():
            target = c
            break
    if target is None and cards:
        target = cards[0]
    if target is None:
        raise MovieNotFoundError(__name__, movie.dvdid)

    title = ''
    date = studio = series = runtime = ''
    tags = []
    actors = []
    for row in target.xpath("./div"):
        spans = row.xpath("./span")
        if not spans:
            continue
        label_raw = spans[0].text_content()
        label = _norm_label(label_raw)
        if not label:
            continue
        # 值 = 整行文本去掉标签文本(对应 JavBoss: TrimPrefix(整行文本, 标签文本) + TrimSpace)
        value = _collapse(_collapse(row.text_content()).replace(_collapse(label_raw), '', 1))
        anchors = row.xpath(".//a")
        anchor_text = _collapse(anchors[0].text_content()) if anchors else ''

        if label in ('番號', '番号', '識別碼', '识别码'):
            pass  # 不覆盖 movie.dvdid(见下)
        elif label in ('發佈於', '发布于', '發行日期', '发行日期', '発売日', 'releasedate'):
            if not date:
                date = value
        elif label in ('時長', '时长', '長度', '长度', 'duration', 'runtime'):
            if not runtime:
                # 实测真实页为 '時長: 160分鐘', 需抽纯分钟数
                # (本项目 duration 惯例为纯数字字符串, 见 javdb/javbus/avsox 等源)
                m = re.search(r'\d+', value)
                runtime = m.group(0) if m else ''
        elif label in ('出版', '發行', '发行', '片商', '製作商', '制作商', 'studio', 'maker', 'publisher'):
            if not studio:
                studio = anchor_text or value
        elif label in ('系列', 'series'):
            if not series:
                series = anchor_text or value
        elif label in ('類別', '类别', '主題', '主题', 'genre', 'genres', 'tags'):
            if not tags:
                tags = [_collapse(t) for t in row.xpath(".//a[contains(@class,'genre')]/text()")]
        elif label in ('女優', '女优', '演員', '演员', 'actress', 'actor', 'actors'):
            if not actors:
                actors = [_collapse(t) for t in row.xpath(".//a[contains(@class,'actress')]/text()")]

    # 标题: 先取 h1, 失败回退 <title>
    h1 = html.xpath("//h1/text()")
    if h1:
        title = _clean_title(h1[0], code)
    if not title:
        t = html.xpath("//title/text()")
        if t:
            title = _clean_title(t[0], code)

    if not (title or studio or series or date or runtime or tags or actors):
        raise MovieNotFoundError(__name__, movie.dvdid)

    # 不覆盖 movie.dvdid: core 靠输入番号关联各源结果(见 v0.1.x 教训)
    movie.url = url
    # 封面: 尽力而为(JavBoss 的 javmenu 未抽封面, 这里补 og:image / video poster / 懒加载图)
    cover = html.xpath("//meta[@property='og:image']/@content")
    if not cover:
        cover = html.xpath("//div[contains(@class,'single-video')]//video/@poster")
    if not cover:
        cover = html.xpath(
            "//img[contains(@class,'lazy')]/@data-src | //img[contains(@class,'lazy')]/@src")
    if cover:
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
