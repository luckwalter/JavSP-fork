"""javmenu.py (javmenu.com 版) 的离线解析验证 (不触网, 合成 HTML 断言成功路径)

替换死站 mrzyx.xyz 后, 必须验证新解析逻辑的成功分支(教训#1), 以及 404 / 空数据降级。
运行: venv312 下 `python verify_javmenu.py`
"""
import lxml.html
from urllib.parse import urljoin

import javsp.web.javmenu as javmenu
from javsp.web.javmenu import parse_data
from javsp.datatype import MovieInfo
from javsp.web.exceptions import MovieNotFoundError


SAMPLE = """<html><head>
<meta property="og:image" content="/pics/ipx001.jpg">
<title>IPX-001 タイトル | JAV MENU</title>
</head><body>
<div class="card rounded">
  <div class="card-body">
    <h2>影片資料</h2>
    <div><span>番號:</span> IPX-001</div>
    <div><span>發佈於:</span> 2020-01-10</div>
    <div><span>時長:</span> 120分鐘</div>
    <div><span>片商:</span> <a href="/studio/1">IdeaPocket</a></div>
    <div><span>系列:</span> <a href="/series/1">美少女</a></div>
    <div><span>類別:</span> <a class="genre" href="/g/1">Slider</a> <a class="genre" href="/g/2">ギャル</a></div>
    <div><span>女優:</span> <a class="actress" href="/a/1">波多野結衣</a></div>
  </div>
</div>
</body></html>"""

H1_SAMPLE = """<html><head><meta property="og:image" content="/pics/x.jpg"></head><body>
<div class="card rounded"><div class="card-body"><h2>影片資料</h2>
<div><span>番號:</span> SSIS-001</div>
<div><span>女優:</span> <a class="actress" href="/a/9">三上悠亜</a></div>
</div></div>
<h1>SSIS-001 三上悠亜の美少女 免費AV在線看</h1>
</body></html>"""

URL = 'https://javmenu.com/IPX-001/'


def _fake_html(html_str, base=URL):
    doc = lxml.html.fromstring(html_str)
    doc.make_links_absolute(base, resolve_base_href=True)
    return doc


def test_success():
    javmenu.get_html = lambda u: _fake_html(SAMPLE, base=u)
    m = MovieInfo('IPX-001')
    parse_data(m)
    # 无 h1 → 回退 <title>: 去番号前缀 'IPX-001 ' + 截 ' | ' 之后站点名(对齐 JavBoss cleanJavMenuTitle)
    assert m.title == 'タイトル', f'title={m.title!r}'
    assert m.dvdid == 'IPX-001', f'dvdid 被改写: {m.dvdid!r}'
    assert m.cover == urljoin(URL, '/pics/ipx001.jpg'), f'cover={m.cover!r}'
    assert m.publish_date == '2020-01-10', f'date={m.publish_date!r}'
    # 时长按项目惯例存纯分钟数字(真实页为 '時長: 160分鐘', 需抽数字)
    assert m.duration == '120', f'duration={m.duration!r}'
    assert m.producer == 'IdeaPocket', f'producer={m.producer!r}'
    assert m.serial == '美少女', f'serial={m.serial!r}'
    assert m.genre == ['Slider', 'ギャル'], f'genre={m.genre!r}'
    assert m.actress == ['波多野結衣'], f'actress={m.actress!r}'
    assert m.url == URL, f'url={m.url!r}'
    print('[OK] test_success: javmenu.com 字段映射正确, 封面相对路径拼绝对, dvdid 未覆盖')


def test_title_from_h1():
    javmenu.get_html = lambda u: _fake_html(H1_SAMPLE, base='https://javmenu.com/SSIS-001/')
    m = MovieInfo('SSIS-001')
    parse_data(m)
    # 标题应去掉 '免費AV在線看' 广告后缀与番号前缀
    assert m.title == '三上悠亜の美少女', f'title={m.title!r}'
    assert m.actress == ['三上悠亜'], f'actress={m.actress!r}'
    print('[OK] test_title_from_h1: h1 标题去广告后缀与番号前缀正确')


def test_404():
    import requests
    def _raise(u):
        r = requests.Response()
        r.status_code = 404
        raise requests.exceptions.HTTPError(response=r)
    javmenu.get_html = _raise
    m = MovieInfo('NOPE-999')
    try:
        parse_data(m)
    except MovieNotFoundError:
        print('[OK] test_404: 404 转 MovieNotFoundError')
        return
    raise AssertionError('404 未抛 MovieNotFoundError')


def test_empty():
    javmenu.get_html = lambda u: _fake_html(
        "<html><body><div class='card rounded'><div class='card-body'><h2>其他</h2></div></div></body></html>",
        base=u)
    m = MovieInfo('EMPTY-000')
    try:
        parse_data(m)
    except MovieNotFoundError:
        print('[OK] test_empty: 无影片资料卡片转 MovieNotFoundError')
        return
    raise AssertionError('空数据未抛 MovieNotFoundError')


if __name__ == '__main__':
    test_success()
    test_title_from_h1()
    test_404()
    test_empty()
    print('\n全部 javmenu 验证通过 ✅')
