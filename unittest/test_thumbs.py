"""缩略图缓存模块的单元测试

覆盖要点:
- 文件名用 sha256 命名, 天然规避路径穿越与非法字符
- 只允许 http/https 协议(挡掉 file:// 等本地协议)
- 下载后用 Pillow 缩放到指定宽度, 并以 jpg 落盘
- 命中缓存不重复下载
- 清理按 mtime 判定超期; 手动清空只删缩略图
- 任何失败都降级为空串, 绝不抛异常影响主流程
"""
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from javsp import thumbs

# 构造一张纯色大图(400x600), 验证缩放逻辑
def _make_png_bytes(width=400, height=600, color=(200, 60, 30)):
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new('RGB', (width, height), color).save(buf, format='PNG')
    return buf.getvalue()


class _Handler(BaseHTTPRequestHandler):
    hits = 0

    def do_GET(self):
        type(self).hits += 1
        body = _make_png_bytes()
        self.send_response(200)
        self.send_header('Content-Type', 'image/png')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _start_server():
    srv = HTTPServer(('127.0.0.1', 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, srv.server_address[1]


def _tmp_thumbs_dir(monkeypatch=None):
    """把缓存目录指到临时目录, 避免污染真实缓存"""
    import tempfile
    d = tempfile.mkdtemp(prefix='thumbs_test_')
    thumbs._default_thumbs_dir = lambda: d
    return d


def test_cache_path_uses_sha256_and_no_traversal():
    """缓存文件名必须是 sha256, 不能把 URL 里的路径成分带进去"""
    url = 'https://evil.example.com/../../etc/passwd.jpg?a=1'
    path = thumbs.cache_path(url)
    name = os.path.basename(path)
    assert '..' not in name and '/' not in name and '\\' not in name
    assert name.endswith('.jpg')
    # 同一 URL 永远命中同一文件; 不同 URL 不同文件
    assert thumbs.cache_path(url) == path
    assert thumbs.cache_path(url + 'x') != path


def test_rejects_non_http_protocols():
    """file:// / data: 等本地协议必须被拒绝"""
    assert thumbs.ensure_thumb('file:///etc/passwd') == ''
    assert thumbs.ensure_thumb('data:image/png;base64,AAAA') == ''
    assert thumbs.ensure_thumb('') == ''
    assert thumbs.ensure_thumb(None) == ''


def test_shrinks_and_caches():
    """下载原图 -> 缩放到 THUMB_WIDTH -> jpg 落盘"""
    _tmp_thumbs_dir()
    srv, port = _start_server()
    try:
        url = f'http://127.0.0.1:{port}/cover.png'
        path = thumbs.ensure_thumb(url)
        assert path, '应成功取得缩略图'
        assert os.path.isfile(path)
        from PIL import Image
        with Image.open(path) as im:
            assert im.width == thumbs.THUMB_WIDTH, f'宽度应为 {thumbs.THUMB_WIDTH}, 实际 {im.width}'
            assert im.height < 600, '高度应按比例缩小'
            assert im.format == 'JPEG', '应存为 jpg'
    finally:
        srv.shutdown()


def test_cache_hit_does_not_redownload():
    """命中缓存不应再次请求网络"""
    _tmp_thumbs_dir()
    srv, port = _start_server()
    try:
        url = f'http://127.0.0.1:{port}/cover.png'
        _Handler.hits = 0
        p1 = thumbs.ensure_thumb(url)
        assert _Handler.hits == 1
        p2 = thumbs.ensure_thumb(url)
        assert p2 == p1
        assert _Handler.hits == 1, f'命中缓存不应再下载, 实际请求 {_Handler.hits} 次'
    finally:
        srv.shutdown()


def test_download_failure_degrades_gracefully():
    """下载失败必须降级为空串, 不抛异常"""
    _tmp_thumbs_dir()
    # 指向一个没有监听的端口
    assert thumbs.ensure_thumb('http://127.0.0.1:1/none.png') == ''


def test_stats_counts_and_bytes():
    """统计的张数与占用字节要准"""
    d = _tmp_thumbs_dir()
    for i in range(3):
        with open(os.path.join(d, f'a{i}.jpg'), 'wb') as f:
            f.write(b'x' * 100)
    st = thumbs.stats()
    assert st['count'] == 3
    assert st['bytes'] == 300
    assert st['dir'] == d


def test_cleanup_expired_by_mtime():
    """只清理超过保留天数的文件"""
    d = _tmp_thumbs_dir()
    old_p = os.path.join(d, 'old.jpg')
    new_p = os.path.join(d, 'new.jpg')
    for p in (old_p, new_p):
        with open(p, 'wb') as f:
            f.write(b'x' * 10)
    stale = time.time() - 10 * 86400
    os.utime(old_p, (stale, stale))
    removed = thumbs.cleanup_expired(7)
    assert removed == 1
    assert not os.path.exists(old_p), '超期文件应被删除'
    assert os.path.exists(new_p), '未超期文件应保留'


def test_cleanup_zero_days_disabled():
    """保留天数为 0 视为不清理"""
    d = _tmp_thumbs_dir()
    p = os.path.join(d, 'x.jpg')
    with open(p, 'wb') as f:
        f.write(b'x')
    assert thumbs.cleanup_expired(0) == 0
    assert os.path.exists(p)


def test_clear_all_only_removes_thumbs():
    """手动清空只删缩略图, 同目录下的其他文件不动"""
    d = _tmp_thumbs_dir()
    thumb_p = os.path.join(d, 'a.jpg')
    other_p = os.path.join(d, 'keep.txt')
    with open(thumb_p, 'wb') as f:
        f.write(b'x')
    with open(other_p, 'wb') as f:
        f.write(b'keep')
    removed = thumbs.clear_all()
    assert removed == 1
    assert not os.path.exists(thumb_p)
    assert os.path.exists(other_p), '非缩略图文件不应被删除'


def test_config_defaults():
    """配置默认值: 自动清理开、保留 7 天"""
    from javsp.config import Cfg
    cfg = Cfg()
    assert cfg.cache.auto_clean is True
    assert cfg.cache.retention_days == 7
