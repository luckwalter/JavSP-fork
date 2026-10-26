"""从fanza抓取数据"""
import re
import json
import logging
from typing import Dict, List


from javsp.web.base import Request, resp2html
from javsp.web.exceptions import *
from javsp.config import Cfg
from javsp.datatype import MovieInfo


logger = logging.getLogger(__name__)
base_url = 'https://www.dmm.co.jp'
# 初始化Request实例（要求携带已通过R18认证的cookies，否则会被重定向到认证页面）
request = Request()
request.cookies = {'age_check_done': '1'}
request.headers['Accept-Language'] = 'ja,en-US;q=0.9'


_PRODUCT_PRIORITY = {'digital': 10, 'mono': 5, 'monthly': 2, 'rental': 1}
_TYPE_PRIORITY = {'videoa': 10, 'anime': 8, 'nikkatsu': 6, 'doujin': 4, 'dvd': 3, 'ppr': 2, 'paradisetv': 1}

# 站点改版后已无法静态解析的页面: monthly(月額見放題, type 为 standard/premium)
# 改成前端渲染, 取不到任何字段; 留着只会浪费请求并在解析时抛异常。
_UNSUPPORTED_PRODUCTS = {'monthly'}
_UNSUPPORTED_TYPES = {'standard', 'premium', 'paradisetv'}


def sort_search_result(result: List[Dict]):
    """排序搜索结果"""
    scores = {i['url']:(_PRODUCT_PRIORITY.get(i['product'], 0), _TYPE_PRIORITY.get(i['type'], 0)) for i in result}
    sorted_result = sorted(result, key=lambda x:scores[x['url']], reverse=True)
    return sorted_result


def _normalize_cid(cid: str) -> str:
    """规范化 cid 以便匹配站点给出的各种变体

    搜索结果里的 cid 与查询用 cid 常常并不字面相等: 查 'ipx00120' 会得到
    'ipx00120bod'(BOD 蓝光) / 'ipx120' / '7ipx120'; 查 'ipx00535' 会得到 'ipx535'。
    老实现要求字面相等, 于是明明搜到了却判"未收录"。这里抽掉数字的前导零再比较。
    """
    cid = (cid or '').lower().strip()
    m = re.match(r'^([a-z]+?)(\d+)', cid)
    if not m:
        return cid
    return f'{m.group(1)}{int(m.group(2))}'


def get_urls_of_cid(cid: str) -> List[Dict]:
    """搜索cid可能的影片URL"""
    target = cid
    r = request.get(f"https://www.dmm.co.jp/search/?redirect=1&enc=UTF-8&category=&searchstr={cid}&commit.x=0&commit.y=0")
    if r.status_code == 404:
        raise MovieNotFoundError(__name__, cid)
    r.raise_for_status()
    html = resp2html_wrapper(r)
    # 2026-10: 搜索页改版, 老结构 //ul[@id='list'] 已取不到任何条目(实测 0 个),
    # 改为直接取所有带 cid= 的链接; 老结构保留为优先分支, 站点回滚时仍可用。
    result = html.xpath("//ul[@id='list']/li/div/p/a/@href")
    if not result:
        result = html.xpath("//a[contains(@href, 'cid=')]/@href")
    parsed_result = {}
    for url in result:
        items = url.split('/')
        type_, found_cid, product = None, None, None
        for i, part in enumerate(items):
            if part == '-':
                product, type_ = items[i-2], items[i-1]
            elif part.startswith('cid='):
                found_cid = part[4:]
                new_url = '/'.join(i for i in items if not i.startswith('?')) + '/'
                parsed_result.setdefault(found_cid, []).append({'product': product, 'type': type_, 'url': new_url})
                break
    # 注意: 老实现在这里用循环变量 cid 覆盖入参, 导致下面比较的是"最后一个结果的 cid"
    # 也不能"精确匹配优先"——实测查 ipx00120 时 monthly 恰好字面相等、mono 却是
    # ipx00120bod/ipx120 等变体, 只取精确匹配会**整组漏掉 mono**(唯一可解析的入口)。
    matched = list(parsed_result.get(target, []))
    norm_target = _normalize_cid(target)
    for k, v in parsed_result.items():
        if k == target:
            continue
        if _normalize_cid(k) == norm_target:
            matched.extend(v)
    # 剔除已无法静态解析的页面
    matched = [i for i in matched
               if i.get('product') not in _UNSUPPORTED_PRODUCTS
               and i.get('type') not in _UNSUPPORTED_TYPES]
    if not matched:
        if len(result) > 0:
            logger.debug(f"Unknown URL in search result: " + ', '.join(result))
        raise MovieNotFoundError(__name__, target)
    sorted_result = sort_search_result(matched)
    return sorted_result


def resp2html_wrapper(resp):
    html = resp2html(resp)
    if 'not available in your region' in html.text_content():
        raise SiteBlocked('FANZA不允许从当前IP所在地区访问，请检查你的网络和代理服务器设置')
    elif '/login/' in resp.url:
        raise SiteBlocked('FANZA要求当前IP登录账号才可访问，请尝试更换为日本IP')
    return html


def _search_and_parse(movie: MovieInfo):
    """走搜索页拿到候选详情页, 逐个尝试解析直到成功

    ⚠️ 不能"跳过所有候选后静默返回": 那样上层会以为抓取成功、写入一条全空的影片
    (实测 fanza 就出现过「解析 OK 但 title/actress 全空」)。走不到成功就必须报错,
    且要报成站点结构问题(WebsiteError)而不是"未收录", 便于监控正确归类。
    """
    urls = get_urls_of_cid(movie.cid)
    last_exc = None
    for d in urls:
        func_name = f"parse_{d['type']}_page"
        if func_name in globals():
            parse_func = globals()[func_name]
        else:
            logger.debug(f"不知道怎么解析 fanza {d['type']} 的页面: {d['url']}")
            continue
        r = request.get(d['url'])
        html = resp2html_wrapper(r)
        try:
            parse_func(movie, html)
            movie.url = d['url']
            return
        except Exception as e:
            last_exc = e
            logger.debug(f"Fail to parse {d['url']}", exc_info=True)
    types = [d.get('type') for d in urls]
    if last_exc is not None:
        logger.warning(f"在fanza查找到的cid={movie.cid}的影片页面均解析失败")
        raise WebsiteError(f'FANZA: cid={movie.cid} 的候选页面均无法解析(类型: {types})') from last_exc
    raise WebsiteError(f'FANZA: cid={movie.cid} 没有可解析的页面类型: {types}')


def parse_data(movie: MovieInfo):
    """解析指定番号的影片数据"""
    # 没有 cid 时不要硬往下走: 空 cid 拼出的 URL 拿到的是非详情页, 后面 xpath 取标题
    # 会抛 IndexError, 被渠道监控误判成"内部错误"(会累计熔断) —— 实际只是查了个寂寞。
    if not (getattr(movie, 'cid', '') or '').strip():
        raise MovieNotFoundError(__name__, getattr(movie, 'dvdid', '') or '(空 cid)')
    default_url = f'{base_url}/digital/videoa/-/detail/=/cid={movie.cid}/'
    r0 = request.get(default_url, delay_raise=True)
    if r0.status_code == 404:
        _search_and_parse(movie)
        return
    html = resp2html_wrapper(r0)
    # 2026-10 改版: digital/videoa 详情已 301 迁到 video.dmm.co.jp, 且那里是纯前端
    # 渲染的 Next.js 壳(无 title/meta/hreview, 业务数据在客户端二次请求里)。此时不能
    # 再按"无此商品"报未收录(实测会持续误报 fanza 未收录), 而要回退到搜索页拿
    # 仍为服务端渲染的通販(mono/dvd)页解析。
    if not html.xpath("//div[@class='hreview']/h1/text()"):
        _search_and_parse(movie)
        return
    parse_videoa_page(movie, html)
    movie.url = default_url


def parse_videoa_page(movie: MovieInfo, html):
    """解析AV影片的页面布局"""
    title = html.xpath("//div[@class='hreview']/h1/text()")[0]
    # 注意: 浏览器在渲染时会自动加上了'tbody'字段，但是原始html网页中并没有，因此xpath解析时还是要按原始网页的来
    container = html.xpath("//table[@class='mg-b12']/tr/td")[0]
    cover = container.xpath("//div[@id='sample-video']/a/@href")[0]
    # 采用'配信開始日'作为发布日期: https://www.zhihu.com/question/57513172/answer/153219083
    date_tag = container.xpath("//td[text()='配信開始日：']/following-sibling::td/text()")
    if date_tag:
        movie.publish_date = date_tag[0].strip().replace('/', '-')
    duration_str = container.xpath("//td[text()='収録時間：']/following-sibling::td/text()")[0].strip()
    match = re.search(r'\d+', duration_str)
    if match:
        movie.duration = match.group(0)
    # 女优、导演、系列：字段不存在时，匹配将得到空列表。暂未发现有名字不显示在a标签中的情况
    actress = container.xpath("//span[@id='performer']/a/text()")
    director_tag = container.xpath("//td[text()='監督：']/following-sibling::td/a/text()")
    if director_tag:
        movie.director = director_tag[0].strip()
    serial_tag = container.xpath("//td[text()='シリーズ：']/following-sibling::td/a/text()")
    if serial_tag:
        movie.serial = serial_tag[0].strip()
    producer_tag = container.xpath("//td[text()='メーカー：']/following-sibling::td/a/text()")
    if producer_tag:
        movie.producer = producer_tag[0].strip()
    # label: 大意是某个系列策划用同样的番号，例如ABS打头的番号label是'ABSOLUTELY PERFECT'，暂时用不到
    # label_tag = container.xpath("//td[text()='レーベル：']/following-sibling::td/a/text()")
    # if label_tag:
    #     label = label_tag[0].strip()
    # fanza会把促销信息也写进genre……因此要根据tag指向的链接类型进行筛选
    genre_tags = container.xpath("//td[text()='ジャンル：']/following-sibling::td/a[contains(@href,'?keyword=') or contains(@href,'article=keyword')]")
    genre, genre_id = [], []
    for tag in genre_tags:
        genre.append(tag.text.strip())
        genre_id.append(tag.get('href').split('=')[-1].strip('/'))
    cid = container.xpath("//td[text()='品番：']/following-sibling::td/text()")[0].strip()
    plot = container.xpath("//div[contains(@class, 'mg-b20 lh4')]/text()")[0].strip()
    preview_pics = container.xpath("//a[@name='sample-image']/img/@src")
    score_tag = container.xpath("//p[@class='d-review__average']/strong/text()")
    if score_tag:
        # 用 [\d.]+ 而非 \d+ : FANZA 这里的评分带一位小数(形如'4.5'), 用 \d+ 会截断成'4'
        match = re.search(r'[\d.]+', score_tag[0].strip())
        if match:
            score = float(match.group()) * 2     # 站点为5分制, 换算到10分制
            movie.score = f'{score:.2f}'
    else:
        # 部分影片没有打分区, 只有星星图片, 文件名形如 00/05/.../50(即5分制的十倍值)
        # 必须与上面的分支一样换算到10分制并保持字符串类型, 否则写入 nfo 后 Jellyfin 会显示 45 这种越界值
        score_img = container.xpath("//td[text()='平均評価：']/following-sibling::td/img/@src")[0]
        score = int(score_img.split('/')[-1].split('.')[0]) / 5
        movie.score = f'{score:.2f}'
    
    if Cfg().crawler.hardworking:
        # 预览视频是动态加载的，不在静态网页中
        video_url = f'{base_url}/service/digitalapi/-/html5_player/=/cid={movie.cid}'
        html2 = request.get_html(video_url)
        # 目前用到js脚本的地方不多，所以不使用专门的js求值模块，先用正则提取文本然后用json解析数据
        script = html2.xpath("//script[contains(text(),'getElementById(\"dmmplayer\")')]/text()")[0].strip()
        match = re.search(r'\{.*\}', script)
        # 主要是为了捕捉json.loads的异常，但是也借助try-except判断是否正则表达式是否匹配
        try:
            data = json.loads(match.group())
            video_url = data.get('src')
            if video_url and video_url.startswith('//'):
                video_url = 'https:' + video_url
            movie.preview_video = video_url
        except Exception as e:
            logger.debug('解析视频地址时异常: ' + repr(e))

    movie.cid = cid
    movie.title = title
    movie.cover = cover
    movie.actress = actress
    movie.genre = genre
    movie.genre_id = genre_id
    movie.plot = plot
    movie.preview_pics = preview_pics
    movie.uncensored = False    # 服务器在日本且面向日本国内公开发售，不会包含无码片


def parse_anime_page(movie: MovieInfo, html):
    """解析动画影片的页面布局"""
    title = html.xpath("//h1[@id='title']/text()")[0]
    container = html.xpath("//table[@class='mg-b12']/tr/td")[0]
    cover = container.xpath("//img[@name='package-image']/@src")[0]
    date_str = container.xpath("//td[text()='発売日：']/following-sibling::td/text()")[0].strip()
    publish_date = date_str.replace('/', '-')
    duration_tag = container.xpath("//td[text()='収録時間：']/following-sibling::td/text()")
    if duration_tag:
        movie.duration = duration_tag[0].strip().replace('分', '')
    serial_tag = container.xpath("//td[text()='シリーズ：']/following-sibling::td/a/text()")
    if serial_tag:
        movie.serial = serial_tag[0].strip()
    producer_tag = container.xpath("//td[text()='メーカー：']/following-sibling::td/a/text()")
    if producer_tag:
        movie.producer = producer_tag[0].strip()
    genre_tags = container.xpath("//td[text()='ジャンル：']/following-sibling::td/a[contains(@href,'article=keyword')]")
    genre, genre_id = [], []
    for tag in genre_tags:
        genre.append(tag.text.strip())
        genre_id.append(tag.get('href').split('=')[-1].strip('/'))
    cid = container.xpath("//td[text()='品番：']/following-sibling::td/text()")[0].strip()
    plot = container.xpath("//div[@class='mg-b20 lh4']/p")[0].text_content().strip()
    preview_pics = container.xpath("//a[@name='sample-image']/img/@data-lazy")
    score_img = container.xpath("//td[text()='平均評価：']/following-sibling::td/img/@src")[0]
    score = int(score_img.split('/')[-1].split('.')[0]) # 00, 05 ... 50

    movie.cid = cid
    movie.title = title
    movie.cover = cover
    movie.publish_date = publish_date
    movie.genre = genre
    movie.genre_id = genre_id
    movie.plot = plot
    movie.score = f'{score/5:.2f}'  # 转换为10分制
    movie.preview_pics = preview_pics
    movie.uncensored = False    # 服务器在日本且面向日本国内公开发售，不会包含无码片


# 商品形态后缀(不属于影片标题本身): 通販页标题常带"（ブルーレイディスク）（BOD）"等
_MEDIA_SUFFIX_RE = re.compile(
    r'\s*[（(](?:ブルーレイディスク|Blu-?ray(?:\s*Disc)?|DVD|BOD|DOD)[）)]$', re.I)


def parse_mono_dvd_page(movie: MovieInfo, html):
    """解析 FANZA 通販(mono/dvd)页面布局

    2026-10 改版后: 配信页 digital/videoa 已 301 迁到 video.dmm.co.jp, 那里是纯前端
    渲染的 Next.js 壳(无 title/meta/正文, 业务数据在客户端二次请求里), 静态解析彻底
    取不到东西; 而通販 mono/dvd 仍是服务端渲染, 成为当前唯一可用入口。
    实测(ipx00120bod / ipx535)可取字段: 标题、品番、女优、収録時間、発売日、
    メーカー、レーベル、シリーズ、監督、ジャンル、封面、样本图、评分。
    """
    # 不能只取 h1/text() 的第一个节点: 实测 ipx535 的标题被拆成多个文本节点,
    # 取 [0] 会丢掉后半段("！！アナルもヒクヒク 桜空もも")。拼接全部文本节点。
    h1_nodes = html.xpath("//div[@class='hreview']/h1")
    title = ''.join(h1_nodes[0].itertext()).strip() if h1_nodes else ''
    if not title:
        raise WebsiteError('FANZA: 通販页未取到标题, 页面结构可能又变了')
    # 去掉"（ブルーレイディスク）（BOD）"这类商品形态后缀, 只保留影片标题
    while True:
        stripped = _MEDIA_SUFFIX_RE.sub('', title)
        if stripped == title:
            break
        title = stripped.strip()

    actress = [i.strip() for i in html.xpath("//span[@id='performer']/a/text()") if i.strip()]
    # 通販页用'発売日'(没有'配信開始日')
    date_tag = html.xpath("//td[text()='発売日：']/following-sibling::td/text()")
    if date_tag:
        movie.publish_date = date_tag[0].strip().replace('/', '-')
    duration_tag = html.xpath("//td[text()='収録時間：']/following-sibling::td/text()")
    if duration_tag:
        match = re.search(r'\d+', duration_tag[0])
        if match:
            movie.duration = match.group(0)
    director_tag = html.xpath("//td[text()='監督：']/following-sibling::td/a/text()")
    if director_tag:
        movie.director = director_tag[0].strip()
    serial_tag = html.xpath("//td[text()='シリーズ：']/following-sibling::td/a/text()")
    if serial_tag:
        movie.serial = serial_tag[0].strip()
    producer_tag = html.xpath("//td[text()='メーカー：']/following-sibling::td/a/text()")
    if producer_tag:
        movie.producer = producer_tag[0].strip()
    genre_tags = html.xpath("//td[contains(text(),'ジャンル')]/following-sibling::td//a[contains(@href,'article=keyword')]")
    genre, genre_id = [], []
    for tag in genre_tags:
        genre.append((tag.text or '').strip())
        genre_id.append((tag.get('href') or '').split('=')[-1].strip('/'))
    cid = html.xpath("//td[text()='品番：']/following-sibling::td/text()")[0].strip()
    # 剧情: 容器里混着促销/公告块, 只取 p.mg-b20 的**直接文本节点**(借此排除内嵌的
    # 广告 a/说明 span), 再去掉"20xx年x月x日発売の商品です"这类前缀
    plot_nodes = html.xpath("//div[contains(@class,'mg-b20 lh4')]/p[@class='mg-b20']")
    plot = ''
    if plot_nodes:
        parts = [t.strip() for t in plot_nodes[0].xpath('text()') if t.strip()]
        plot = re.sub(r'^\d{4}年\d{1,2}月\d{1,2}日発売の商品です\s*', '', ' '.join(parts)).strip()
    cover_tag = html.xpath("//img[@name='package-image']/@src")
    # 样本图: @src 往往是占位图(dummy_ps.gif), 真图在 @data-lazy
    preview_pics = [i for i in html.xpath("//a[@name='sample-image']/img/@data-lazy") if 'dummy' not in i]
    if not preview_pics:
        preview_pics = [i for i in html.xpath("//a[@name='sample-image']/img/@src") if 'dummy' not in i]
    score_tag = html.xpath("//p[@class='d-review__average']/strong/text()")
    if score_tag:
        match = re.search(r'[\d.]+', score_tag[0].strip())
        if match:
            movie.score = f'{float(match.group()) * 2:.2f}'
    else:
        score_img = html.xpath("//td[text()='平均評価：']/following-sibling::td/img/@src")
        if score_img:
            # 文件名形如 00/05/45/50(5分制的十倍值), 换算到10分制
            movie.score = f'{int(score_img[0].split("/")[-1].split(".")[0]) / 5:.2f}'

    movie.cid = cid
    movie.title = title
    movie.cover = cover_tag[0] if cover_tag else None
    movie.actress = actress
    movie.genre = genre
    movie.genre_id = genre_id
    movie.plot = plot
    movie.preview_pics = preview_pics
    movie.uncensored = False    # 服务器在日本且面向日本国内公开发售，不会包含无码片




if __name__ == "__main__":
    import pretty_errors
    pretty_errors.configure(display_link=True)
    logger.root.handlers[1].level = logging.DEBUG

    movie = MovieInfo(cid='d_aisoft3356')
    try:
        parse_data(movie)
        print(movie)
    except CrawlerError as e:
        logger.error(e, exc_info=1)
