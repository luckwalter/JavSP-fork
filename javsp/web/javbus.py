"""从JavBus抓取数据"""
import logging

from javsp.config import Cfg
from javsp.web.base import *
from javsp.web.exceptions import *
from javsp.func import *
from javsp.datatype import MovieInfo, GenreMap


logger = logging.getLogger(__name__)
genre_map = GenreMap('data/genre_javbus.csv')
permanent_url = 'https://www.javbus.com'
base_url = permanent_url


def _resolve_javbus_cookies() -> dict:
    """解析 javbus 渠道配置里的浏览器 cookie（随 config.yml 持久化, 热重载即生效）

    从 Cfg().crawler.cookies['javbus'] 读取 Cookie-Editor 导出数组([{name, value}]),
    转成 requests 可用的 {name: value} 字典。未配置或读取失败时回退到默认 age=verified,
    仍尽力取数。
    """
    try:
        arr = (Cfg().crawler.cookies or {}).get('javbus') or []
        cookies = {str(c['name']): str(c['value'])
                   for c in arr if isinstance(c, dict) and c.get('name')}
        if cookies:
            return cookies
    except Exception:
        logger.debug('读取 javbus cookie 配置失败, 使用默认 age cookie')
    return {'age': 'verified'}


def parse_data(movie: MovieInfo):
    """从网页抓取并解析指定番号的数据
    Args:
        movie (MovieInfo): 要解析的影片信息，解析后的信息直接更新到此变量内
    """
    url = f'{base_url}/{movie.dvdid}'
    # JavBus 对未通过年龄验证的访客, 会把番号详情页 302 到年龄验证页(driver-verify);
    # 该页有 .container 外壳但无影片数据, 继续按影片页解析会 IndexError 崩溃。
    # 注入年龄验证 cookie(值 'verified' 为服务端确认后下发的固定值, 约 30 天有效):
    # 带此 cookie 的请求服务端直接放行, 等效浏览器通过年龄验证(实测可拿到真实详情页)。
    resp = request_get(url, cookies=_resolve_javbus_cookies(), delay_raise=True)
    html = resp2html(resp)
    page_title = html.xpath('/html/head/title/text()')
    if page_title and page_title[0].startswith('404 Page Not Found!'):
        raise MovieNotFoundError(__name__, movie.dvdid)
    # 年龄验证墙兜底(极端情况下 cookie 未生效仍被 302 到 driver-verify):
    # 该页 title 含 'Age Verification', 直接判该源未取得数据, 不崩溃。
    if page_title and 'Age Verification' in page_title[0]:
        raise MovieNotFoundError(__name__, movie.dvdid)
    if not html.xpath("//div[@class='container']"):
        raise MovieNotFoundError(__name__, movie.dvdid)
    container = html.xpath("//div[@class='container']")[0]
    title = container.xpath("h3/text()")
    if not title:
        raise MovieNotFoundError(__name__, movie.dvdid)
    title = title[0]
    cover = container.xpath("//a[@class='bigImage']/img/@src")[0]
    preview_pics = container.xpath("//div[@id='sample-waterfall']/a/@href")
    info = container.xpath("//div[@class='col-md-3 info']")[0]
    dvdid = info.xpath("p/span[text()='識別碼:']")[0].getnext().text
    publish_date = info.xpath("p/span[text()='發行日期:']")[0].tail.strip()
    duration = info.xpath("p/span[text()='長度:']")[0].tail.replace('分鐘', '').strip()
    director_tag = info.xpath("p/span[text()='導演:']")
    if director_tag:    # xpath没有匹配时将得到空列表
        movie.director = director_tag[0].getnext().text.strip()
    producer_tag = info.xpath("p/span[text()='製作商:']")
    if producer_tag:
        text = producer_tag[0].getnext().text
        if text:
            movie.producer = text.strip()
    publisher_tag = info.xpath("p/span[text()='發行商:']")
    if publisher_tag:
        movie.publisher = publisher_tag[0].getnext().text.strip()
    serial_tag = info.xpath("p/span[text()='系列:']")
    if serial_tag:
        movie.serial = serial_tag[0].getnext().text
    # genre, genre_id
    genre_tags = info.xpath("//span[@class='genre']/label/a")
    genre, genre_id = [], []
    for tag in genre_tags:
        tag_url = tag.get('href')
        pre_id = tag_url.split('/')[-1]
        genre.append(tag.text)
        if 'uncensored' in tag_url:
            movie.uncensored = True
            genre_id.append('uncensored-' + pre_id)
        else:
            movie.uncensored = False
            genre_id.append(pre_id)
    # JavBus的磁力链接是依赖js脚本加载的，无法通过静态网页来解析
    # actress, actress_pics
    actress, actress_pics = [], {}
    actress_tags = html.xpath("//a[@class='avatar-box']/div/img")
    for tag in actress_tags:
        name = tag.get('title')
        pic_url = tag.get('src')
        actress.append(name)
        if not pic_url.endswith('nowprinting.gif'):     # 略过默认的头像
            actress_pics[name] = pic_url
    # 整理数据并更新movie的相应属性
    movie.url = f'{permanent_url}/{movie.dvdid}'
    movie.dvdid = dvdid
    movie.title = title.replace(dvdid, '').strip()
    movie.cover = cover
    movie.preview_pics = preview_pics
    if publish_date != '0000-00-00':    # 丢弃无效的发布日期
        movie.publish_date = publish_date
    movie.duration = duration if int(duration) else None
    movie.genre = genre
    movie.genre_id = genre_id
    movie.actress = actress
    movie.actress_pics = actress_pics


def parse_clean_data(movie: MovieInfo):
    """解析指定番号的影片数据并进行清洗"""
    parse_data(movie)
    movie.genre_norm = genre_map.map(movie.genre_id)
    movie.genre_id = None   # 没有别的地方需要再用到，清空genre id（暗示已经完成转换）


if __name__ == "__main__":
    import pretty_errors
    pretty_errors.configure(display_link=True)
    logger.root.handlers[1].level = logging.DEBUG

    movie = MovieInfo('NANP-030')
    try:
        parse_clean_data(movie)
        print(movie)
    except CrawlerError as e:
        logger.error(e, exc_info=1)
