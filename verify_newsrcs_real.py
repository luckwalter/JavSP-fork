"""用真实抓取的样本 HTML 离线验证两个新源的解析逻辑(不触网)

样本来自 NAS 容器经 squid 实抓(javdatabase_IPX-001 / javmenu_IPX-001 / javmenu_SSIS-001),
落盘在 samples/。用真实页而非合成页验证, 才能暴露选择器偏差(教训: 合成样本可能与真实结构不一致)。

运行: venv312 下 `python verify_newsrcs_real.py`
"""
import os
import lxml.html

import javsp.web.javdatabase as javdatabase
import javsp.web.javmenu as javmenu
from javsp.datatype import MovieInfo
from javsp.web.exceptions import MovieNotFoundError

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = os.path.join(HERE, 'samples')

PASSED = []
FAILED = []


def _load(name):
    path = os.path.join(SAMPLES, name)
    if not os.path.exists(path):
        raise SystemExit(
            f'缺少样本: {path}\n'
            f'样本不入 git(见 .gitignore), 需先抓取:\n'
            f'  1) 在 NAS 容器内经 squid 抓取并落盘到 /data/.javsp_samples/samples\n'
            f'  2) 本地执行: python ../nas_fetch_samples_drive.py\n'
            f'若网络不可达, 可改跑 verify_javdatabase.py / verify_javmenu.py(合成样本离线验证)')
    with open(path, encoding='utf-8', newline='') as f:
        text = f.read()
    doc = lxml.html.fromstring(text)
    doc.make_links_absolute('https://invalid.local/', resolve_base_href=True)
    return doc


def _mock(mod, name, base):
    doc = _load(name)
    url = base

    def _get_html(u, *a, **kw):
        d = lxml.html.fromstring(open(os.path.join(SAMPLES, name), encoding='utf-8',
                                      newline='').read())
        d.make_links_absolute(u, resolve_base_href=True)
        return d

    mod.get_html = _get_html
    return doc


def _check(label, cond, detail=''):
    if cond:
        PASSED.append(label)
        print(f'  [OK ] {label}')
    else:
        FAILED.append(f'{label} -> {detail}')
        print(f'  [FAIL] {label}  {detail}')


def test_javdatabase_real():
    print('\n===== javdatabase.com 真实样本 (IPX-001) =====')
    _mock(javdatabase, 'javdatabase_IPX-001.html', 'https://www.javdatabase.com/movies/IPX-001/')
    m = MovieInfo('IPX-001')
    javdatabase.parse_data(m)
    print(f'  title       = {m.title!r}')
    print(f'  producer    = {m.producer!r}')
    print(f'  serial      = {m.serial!r}')
    print(f'  date        = {m.publish_date!r}')
    print(f'  duration    = {m.duration!r}')
    print(f'  genre       = {m.genre!r}')
    print(f'  actress     = {m.actress!r}')
    print(f'  cover       = {m.cover!r}')
    print(f'  url         = {m.url!r}')
    _check('dvdid 未被覆盖', m.dvdid == 'IPX-001', f'got {m.dvdid!r}')
    _check('标题非空', bool(m.title), 'title 空')
    _check('标题不含站点后缀', 'JAVDatabase' not in (m.title or ''), repr(m.title))
    _check('日期格式 YYYY-MM-DD',
           bool(m.publish_date) and len(m.publish_date) == 10 and m.publish_date[4] == '-',
           repr(m.publish_date))
    _check('duration 为纯数字分钟', (m.duration or '').isdigit(), repr(m.duration))
    _check('producer 非空', bool(m.producer), 'producer 空')
    _check('genre 为非空列表且元素无空串',
           bool(m.genre) and all(g for g in m.genre), repr(m.genre))
    _check('actress 为非空列表且元素无空串',
           bool(m.actress) and all(a for a in m.actress), repr(m.actress))
    _check('cover 为绝对 http(s) URL',
           (m.cover or '').startswith('http'), repr(m.cover))
    _check('url 指向详情页', m.url.endswith('/movies/IPX-001/'), repr(m.url))


def test_javmenu_real():
    for name, code in (('javmenu_IPX-001.html', 'IPX-001'),
                       ('javmenu_SSIS-001.html', 'SSIS-001')):
        print(f'\n===== javmenu.com 真实样本 ({code}) =====')
        _mock(javmenu, name, f'https://javmenu.com/{code}/')
        m = MovieInfo(code)
        javmenu.parse_data(m)
        print(f'  title       = {m.title!r}')
        print(f'  producer    = {m.producer!r}')
        print(f'  serial      = {m.serial!r}')
        print(f'  date        = {m.publish_date!r}')
        print(f'  duration    = {m.duration!r}')
        print(f'  genre       = {m.genre!r}')
        print(f'  actress     = {m.actress!r}')
        print(f'  cover       = {m.cover!r}')
        print(f'  url         = {m.url!r}')
        _check(f'[{code}] dvdid 未被覆盖', m.dvdid == code, f'got {m.dvdid!r}')
        _check(f'[{code}] 标题非空', bool(m.title), 'title 空')
        _check(f'[{code}] 标题无广告后缀',
               '免費AV在線看' not in (m.title or '') and '免费AV在线看' not in (m.title or ''),
               repr(m.title))
        _check(f'[{code}] 标题不以番号开头', not (m.title or '').startswith(code),
               repr(m.title))
        _check(f'[{code}] 标题不含站点后缀竖线',
               ' | ' not in (m.title or ''), repr(m.title))
        _check(f'[{code}] 日期格式正确',
               bool(m.publish_date) and len(m.publish_date) == 10 and m.publish_date[4] == '-',
               repr(m.publish_date))
        _check(f'[{code}] duration 为纯数字分钟', (m.duration or '').isdigit(), repr(m.duration))
        _check(f'[{code}] genre 列表非空且无空串',
               bool(m.genre) and all(g for g in m.genre), repr(m.genre))
        _check(f'[{code}] actress 列表非空且无空串',
               bool(m.actress) and all(a for a in m.actress), repr(m.actress))
        _check(f'[{code}] cover 为绝对 URL 且非占位图',
               (m.cover or '').startswith('http') and 'loading' not in (m.cover or ''),
               repr(m.cover))
        _check(f'[{code}] url 指向详情页', m.url.endswith(f'/{code}/'), repr(m.url))


def test_javmenu_cover_not_placeholder():
    """真实页 img.lazy 是 loading_3_green_dot.gif 占位图 —— 必须由 og:image/video poster 兜住"""
    print('\n===== javmenu 封面占位图防护 =====')
    doc = _load('javmenu_IPX-001.html')
    lazy = doc.xpath("//img[contains(@class,'lazy')]/@data-src | //img[contains(@class,'lazy')]/@src")
    og = doc.xpath("//meta[@property='og:image']/@content")
    vp = doc.xpath("//video/@poster")
    print(f'  img.lazy 首个 = {lazy[:1]}')
    print(f'  og:image      = {og[:1]}')
    print(f'  video poster  = {vp[:1]}')
    _check('确实存在 loading 占位图(说明该风险真实)', any('loading' in u for u in lazy),
           repr(lazy[:1]))
    _check('og:image 可用', bool(og) and 'loading' not in og[0], repr(og[:1]))


def test_javmenu_known_site_defects():
    """固化 javmenu 的上游站点数据缺陷, 避免后续误判为解析 bug 而乱改实现"""
    print('\n===== javmenu 已知站点缺陷(固化事实, 非解析 bug) =====')
    _mock(javmenu, 'javmenu_SSIS-001.html', 'https://javmenu.com/SSIS-001/')
    m1 = MovieInfo('SSIS-001')
    javmenu.parse_data(m1)
    # 缺陷1: SSIS-001 的「系列」被站点填成剧情梗概(长句), 而非真系列名
    _check('SSIS-001 站点把剧情梗概填进「系列」(已知缺陷, 照实取用)',
           bool(m1.serial) and len(m1.serial) > 30, f'serial={m1.serial!r}')

    _mock(javmenu, 'javmenu_IPX-001.html', 'https://javmenu.com/IPX-001/')
    m2 = MovieInfo('IPX-001')
    javmenu.parse_data(m2)
    # 缺陷2: IPX-001 真实页没有「系列」行 -> serial 应为空(None), 而非编造
    _check('IPX-001 无「系列」行时 serial 留空(不编造)', not m2.serial, f'serial={m2.serial!r}')
    # 对照: 其余字段两片都应正常取到, 说明缺陷只限「系列」
    _check('IPX-001 其余字段正常(缺陷仅限系列)', bool(m2.producer) and bool(m2.genre),
           f'producer={m2.producer!r} genre={m2.genre!r}')


def test_javdatabase_known_site_defects():
    """固化 javdatabase 的上游数据错配(女优名与内容 ID 不对应)"""
    print('\n===== javdatabase 已知站点缺陷(固化事实) =====')
    _mock(javdatabase, 'javdatabase_IPX-001.html', 'https://www.javdatabase.com/movies/IPX-001/')
    m = MovieInfo('IPX-001')
    javdatabase.parse_data(m)
    print(f'  javdatabase 女优 = {m.actress!r}   javmenu 女优 = 见上(田淵正浩/梅田吉雄/...)')
    # javdatabase 的 IPX-001 页把女优写成 'Rui Negoto'(与 javmenu 的日文名不一致, 系站点侧数据错配)
    _check('javdatabase 女优字段为站点侧错配值(已知缺陷, 照实取用)',
           m.actress == ['Rui Negoto'], f'actress={m.actress!r}')


def test_javdatabase_404_real():
    """真实站 javdatabase 对 ABP-123 返回 404 —— 用探针实测事实构造降级验证"""
    print('\n===== javdatabase 404 降级 (真实站 ABP-123 实测 404) =====')
    import requests

    def _raise(u):
        r = requests.Response()
        r.status_code = 404
        raise requests.exceptions.HTTPError(response=r)

    javdatabase.get_html = _raise
    try:
        javdatabase.parse_data(MovieInfo('ABP-123'))
    except MovieNotFoundError:
        _check('404 转 MovieNotFoundError(优雅降级)', True)
        return
    _check('404 转 MovieNotFoundError(优雅降级)', False, '未抛异常')


if __name__ == '__main__':
    test_javdatabase_real()
    test_javmenu_real()
    test_javmenu_cover_not_placeholder()
    test_javmenu_known_site_defects()
    test_javdatabase_known_site_defects()
    test_javdatabase_404_real()
    print(f'\n{"=" * 50}')
    print(f'通过 {len(PASSED)} 项, 失败 {len(FAILED)} 项')
    for f in FAILED:
        print(f'  FAIL: {f}')
    print('真实样本验证全部通过 ✅' if not FAILED else '存在失败项 ❌')
    raise SystemExit(1 if FAILED else 0)
