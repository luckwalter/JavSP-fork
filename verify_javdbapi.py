"""验证 javsp/web/javdbapi.py 的解析逻辑(不联网, mock 请求与配置)

专测: 字段映射正确性 + 未找到抛 MovieNotFoundError。真实网络/字段格式由 NAS 容器实测覆盖。
"""
import sys
from unittest import mock

sys.path.insert(0, 'JavSP')


# ---- 用 mock 顶掉配置与代理/TLS 依赖, 专注验证解析逻辑 ----
class _Timeout:
    def total_seconds(self): return 15
class _Network:
    proxy_server = None
    timeout = _Timeout()
    retry = 3
class _Cfg:
    network = _Network()
_CFG = _Cfg()

import javsp.web.javdbapi as api
api.Cfg = lambda: _CFG
api.read_proxy = lambda: {}
api.tls_verify = lambda: True

from javsp.datatype import MovieInfo
from javsp.web.exceptions import MovieNotFoundError


class _Resp:
    def __init__(self, payload): self._p = payload; self.status_code = 200
    def json(self): return self._p


MOVIE = {
    "id": "A72m", "number": "IPX-001",
    "origin_title": "刺激您五感的三上悠亜頂級自慰協助",
    "title": "三上悠亜 頂級自慰協助",
    "maker_name": "IDEAPOCKET", "series_name": "",
    "release_date": "2016-04-13", "duration": "120",
    "cover_url": "https://blob.javdb.com/cover/A72m.jpg", "type": "0",
    "tags": [{"name": "劇情"}, {"name": "単体作品"}],
    "actors": [{"id": "x1", "name": "三上悠亜", "gender": "0"},
               {"id": "x2", "name": "male-actor", "gender": "1"}],
    "preview_images": [{"thumb_url": "https://blob.javdb.com/t/A72m.jpg",
                        "large_url": "https://blob.javdb.com/l/A72m.jpg"}],
}
SEARCH_OK = {"success": 1, "data": {"movies": [MOVIE]}}
DETAIL_OK = {"success": 1, "data": MOVIE}
SEARCH_EMPTY = {"success": 1, "data": {"movies": []}}


def _run(search_payload, detail_payload, dvdid):
    def fake_get(url, *a, **k):
        if '/search' in url:
            return _Resp(search_payload)
        return _Resp(detail_payload)
    with mock.patch.object(api.requests, 'get', side_effect=fake_get):
        movie = MovieInfo(dvdid)
        api.parse_data(movie)
        return movie


def main():
    # 1) 正常映射
    mv = _run(SEARCH_OK, DETAIL_OK, 'IPX-001')
    assert mv.title == '刺激您五感的三上悠亜頂級自慰協助', mv.title
    assert mv.cover == 'https://blob.javdb.com/cover/A72m.jpg', mv.cover
    assert mv.actress == ['三上悠亜'], mv.actress
    assert mv.genre == ['劇情', '単体作品'], mv.genre
    assert mv.producer == 'IDEAPOCKET', mv.producer
    assert mv.serial is None, mv.serial
    assert mv.publish_date == '2016-04-13', mv.publish_date
    assert mv.duration == '120', mv.duration
    assert mv.uncensored is False, mv.uncensored
    assert mv.url == 'https://javdb.com/movies/A72m', mv.url
    assert mv.preview_pics == ['https://blob.javdb.com/l/A72m.jpg'], mv.preview_pics
    assert mv.dvdid == 'IPX-001', '不应覆盖输入番号'
    print('[1] 字段映射 OK')

    # 2) 相对 cover_url 应拼 BASE_URL
    rel = dict(MOVIE); rel['cover_url'] = '/samples/A72m.jpg'
    mv2 = _run({"success": 1, "data": {"movies": [rel]}}, {"success": 1, "data": rel}, 'IPX-001')
    assert mv2.cover == 'https://jdforrepam.com/samples/A72m.jpg', mv2.cover
    print('[2] 相对 cover_url 拼 BASE_URL OK')

    # 3) 无码 type=1
    unc = dict(MOVIE); unc['type'] = '1'
    mv3 = _run({"success": 1, "data": {"movies": [unc]}}, {"success": 1, "data": unc}, 'IPX-001')
    assert mv3.uncensored is True, mv3.uncensored
    print('[3] type=1 -> uncensored OK')

    # 4) 搜索无结果 -> MovieNotFoundError
    try:
        _run(SEARCH_EMPTY, DETAIL_OK, 'NOPE-001')
        print('[4] FAIL: 应抛 MovieNotFoundError')
        raise SystemExit(1)
    except MovieNotFoundError:
        print('[4] 搜索无结果 -> MovieNotFoundError OK')

    # 5) FC2 归一: 输入 FC2-xxx 查询去 PPV (仅 search 请求带 q, 需归一)
    def fake_get_fc2(url, *a, **k):
        if '/search' in url:
            assert k.get('params', {}).get('q') == 'FC2-123456', f'查询应归一为 FC2-123456, 实际: {k.get("params")}'
        return _Resp({"success": 1, "data": {"movies": [
            {"id": "fc1", "number": "FC2-123456", "title": "t", "cover_url": "https://x/c.jpg"}]}})
    with mock.patch.object(api.requests, 'get', side_effect=fake_get_fc2):
        api.parse_data(MovieInfo('FC2-PPV-123456'))
    print('[5] FC2 查询归一去 PPV OK')

    print('\n全部通过 ✅')


if __name__ == '__main__':
    main()
