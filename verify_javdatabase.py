"""javdatabase.py 的离线解析验证 (不触网, 用合成 HTML 断言成功路径)

对照 javdbapi 的验证标准: 必须覆盖成功分支(教训#1), 同时验证 404 与空数据两条降级路径。
运行: venv312 下 `python verify_javdatabase.py`
"""
import sys
import lxml.html
from urllib.parse import urljoin

import javsp.web.javdatabase as javdatabase
from javsp.web.javdatabase import parse_data
from javsp.datatype import MovieInfo
from javsp.web.exceptions import MovieNotFoundError


SAMPLE = """<html><head>
<meta property="og:image" content="/covers/ipx001.jpg">
<title>IPX-001 - JAVDatabase</title>
</head><body>
<div class="movietable">
  <div class="col-md-10 col-lg-10 col-xxl-10 col-8">
    <p class="mb-1"><b>Title:</b> IPX-001 アイドルのような美少女</p>
    <p class="mb-1"><b>DVD ID:</b> IPX-001</p>
    <p class="mb-1"><b>Release Date:</b> 2020-01-10</p>
    <p class="mb-1"><b>Runtime:</b> 120 (HD: 120) min.</p>
    <p class="mb-1"><b>Studio:</b> <a href="/studio/ideapocket">IdeaPocket</a></p>
    <p class="mb-1"><b>Series:</b> <a href="/series/xxx">美少女シリーズ</a></p>
    <p class="mb-1"><b>Genre:</b> <a href="/g/1">Slider</a> <a href="/g/2">ギャル</a></p>
    <p class="mb-1"><b>Idol Actress:</b> <a href="/idol/1">波多野結衣</a></p>
  </div>
</div>
</body></html>"""

URL = 'https://www.javdatabase.com/movies/IPX-001/'


def _fake_html(html_str, base=URL):
    doc = lxml.html.fromstring(html_str)
    doc.make_links_absolute(base, resolve_base_href=True)
    return doc


def test_success():
    javdatabase.get_html = lambda u: _fake_html(SAMPLE, base=u)
    m = MovieInfo('IPX-001')
    parse_data(m)
    assert m.title == 'IPX-001 アイドルのような美少女', f'title={m.title!r}'
    # 不覆盖输入番号
    assert m.dvdid == 'IPX-001', f'dvdid 被改写: {m.dvdid!r}'
    assert m.cover == urljoin(URL, '/covers/ipx001.jpg'), f'cover={m.cover!r}'
    assert m.publish_date == '2020-01-10', f'date={m.publish_date!r}'
    # 时长按项目惯例存纯分钟数字(真实页为 'Runtime: 160 (HD: 160) min.', 需抽数字)
    assert m.duration == '120', f'duration={m.duration!r}'
    assert m.producer == 'IdeaPocket', f'producer={m.producer!r}'
    assert m.serial == '美少女シリーズ', f'serial={m.serial!r}'
    assert m.genre == ['Slider', 'ギャル'], f'genre={m.genre!r}'
    assert m.actress == ['波多野結衣'], f'actress={m.actress!r}'
    assert m.url == URL, f'url={m.url!r}'
    print('[OK] test_success: 字段映射正确, 封面相对路径已拼绝对, dvdid 未被覆盖')


def test_404():
    import requests
    def _raise(u):
        r = requests.Response()
        r.status_code = 404
        raise requests.exceptions.HTTPError(response=r)
    javdatabase.get_html = _raise
    m = MovieInfo('NOPE-999')
    try:
        parse_data(m)
    except MovieNotFoundError:
        print('[OK] test_404: 404 转 MovieNotFoundError')
        return
    raise AssertionError('404 未抛 MovieNotFoundError')


def test_empty():
    javdatabase.get_html = lambda u: _fake_html("<html><body><div class='col-md-10 col-xxl-10'></div></body></html>", base=u)
    m = MovieInfo('EMPTY-000')
    try:
        parse_data(m)
    except MovieNotFoundError:
        print('[OK] test_empty: 无字段页转 MovieNotFoundError')
        return
    raise AssertionError('空数据未抛 MovieNotFoundError')


if __name__ == '__main__':
    test_success()
    test_404()
    test_empty()
    print('\n全部 javdatabase 验证通过 ✅')
