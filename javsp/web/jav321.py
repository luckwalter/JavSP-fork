"""从jav321抓取数据

jav321 是第三方中文聚合站, 全盘镜像 DMM/FANZA 的商品页数据: 影片页 cid 直接
沿用 DMM Content ID(品番小写去横线、数字左侧补零到 5 位, 如 IPX-001 ->
ipx00001); 封面图更是直接 hotlink pics.dmm.co.jp(实测 IPX-001 的封面即
http://pics.dmm.co.jp/digital/video/ipx00001/ipx00001pl.jpg), 因此常被
当作 DMM 的免代理替代源(mdcx 等项目即采用 dmm -> jav321 兜底链)。
2026-10-21 实测: 站点已恢复开放(2024 年曾整体宕机), 但服务器 TLS 证书
已过期, requests 默认严格校验会报 SSLCertVerificationError。
"""
import re
import logging


from javsp.web.base import post_html
from javsp.web.exceptions import *
from javsp.datatype import MovieInfo


logger = logging.getLogger(__name__)
base_url = 'https://www.jav321.com'


def parse_data(movie: MovieInfo):
    """解析指定番号的影片数据

    支持两种入参: dvdid(普通番号, 如 IPX-001)或 cid(DMM Content ID, 如
    ipx00001, 来自 'cid:xxx' 手动指定或 cid 维度刮削)。jav321 搜索接口对两种
    形式返回同一影片页(2026-10-21 实测 sn=IPX-001 与 sn=ipx00001 字段一致),
    故共用作战: 优先 dvdid, 缺失时退 cid。
    """
    query = movie.dvdid or movie.cid
    if not query:
        raise MovieNotFoundError(__name__, 'dvdid 与 cid 均为空')
    html = post_html(f'{base_url}/search', data={'sn': query})
    page_url = html.xpath("//ul[@class='dropdown-menu']/li/a/@href")[0]
    # cid 是 DMM 的概念(Content ID: 品番小写去横线、数字补足 5 位), jav321
    # 镜像 DMM 商品数据时直接沿用, 所以这里拿到的就是 DMM 风格 cid。
    # 非 DMM 体系的番号(如 MGSTAGE 345SIMM-542)此处的 cid 是 jav321 自己
    # 编造的, 不对应任何 DMM 商品。
    cid = page_url.split('/')[-1]   # /video/ipx00177
    # 如果从URL匹配到的cid是'search'，说明还停留在搜索页面，找不到这部影片
    if cid == 'search':
        raise MovieNotFoundError(__name__, query)
    title = html.xpath("//div[@class='panel-heading']/h3/text()")[0]
    info = html.xpath("//div[@class='col-md-9']")[0]
    # jav321的不同信息字段间没有明显分隔，只能通过url来匹配目标标签
    company_tags = info.xpath("a[contains(@href,'/company/')]/text()")
    if company_tags:
        movie.producer = company_tags[0]
    # actress, actress_pics
    # jav321现在连女优信息都没有了，首页通过女优栏跳转过去也全是空白
    actress, actress_pics = [], {}
    actress_tags = html.xpath("//div[@class='thumbnail']/a[contains(@href,'/star/')]/img")
    for tag in actress_tags:
        name = tag.tail.strip()
        pic_url = tag.get('src')
        actress.append(name)
        # jav321的女优头像完全是应付了事：即使女优实际没有头像，也会有一个看起来像模像样的url，
        # 因而无法通过url判断女优头像图片是否有效。有其他选择时最好不要使用jav321的女优头像数据
        actress_pics[name] = pic_url
    # genre, genre_id
    genre_tags = info.xpath("a[contains(@href,'/genre/')]")
    genre, genre_id = [], []
    for tag in genre_tags:
        genre.append(tag.text)
        genre_id.append(tag.get('href').split('/')[-2]) # genre/4025/1
    dvdid = info.xpath("b[text()='品番']")[0].tail.replace(': ', '').upper()
    publish_date = info.xpath("b[text()='配信開始日']")[0].tail.replace(': ', '')
    duration_div = info.xpath("b[text()='収録時間']")
    if duration_div:
        match = re.search(r'\d+', duration_div[0].tail)
        if match:
            movie.duration = match.group(0)
    # 仅部分影片有评分且评分只能粗略到星级而没有分数，要通过星级的图片来判断，如'/img/35.gif'表示3.5星
    score_tag = info.xpath("//b[text()='平均評価']/following-sibling::img/@data-original")
    if score_tag:
        score = int(score_tag[0][5:7])/5   # /10*2
        movie.score = str(score)
    serial_tag = info.xpath("a[contains(@href,'/series/')]/text()")
    if serial_tag:
        movie.serial = serial_tag[0]
    preview_video_tag = info.xpath("//video/source/@src")
    if preview_video_tag:
        movie.preview_video = preview_video_tag[0]
    plot_tag = info.xpath("//div[@class='panel-body']/div[@class='row']/div[@class='col-md-12']/text()")
    if plot_tag:
        movie.plot = plot_tag[0]
    preview_pics = html.xpath("//div[@class='col-xs-12 col-md-12']/p/a/img[@class='img-responsive']/@src")
    if len(preview_pics) == 0:
        # 尝试搜索另一种布局下的封面，需要使用onerror过滤掉明明没有封面时网站往里面塞的默认URL
        preview_pics = html.xpath("//div/div/div[@class='col-md-3']/img[@onerror and @class='img-responsive']/@src")
    # 有的图片链接里有多个//，网站质量堪忧……
    preview_pics = [i[:8] + i[8:].replace('//', '/') for i in preview_pics]
    # 磁力和ed2k链接是依赖js脚本加载的，无法通过静态网页来解析

    movie.url = page_url
    movie.cid = cid
    movie.dvdid = dvdid
    movie.title = title
    movie.actress = actress
    movie.actress_pics = actress_pics
    movie.genre = genre
    movie.genre_id = genre_id
    movie.publish_date = publish_date
    # preview_pics的第一张图始终是封面，剩下的才是预览图
    if len(preview_pics) > 0:
        movie.cover = preview_pics[0]
        movie.preview_pics = preview_pics[1:]


if __name__ == "__main__":
    import pretty_errors
    pretty_errors.configure(display_link=True)
    logger.root.handlers[1].level = logging.DEBUG

    movie = MovieInfo('SCUTE-1177')
    try:
        parse_data(movie)
        print(movie)
    except CrawlerError as e:
        logger.error(e, exc_info=1)
