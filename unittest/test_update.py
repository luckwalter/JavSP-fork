"""软件更新模块的单元测试(v0.4.0)

覆盖要点:
- 版本比较必须用语义比较(字符串比较会把 '0.3.9' 判成比 '0.3.17' 新)
- 网络不可达时检查必须静默返回 None(不抛异常、不阻塞)
- Windows 资产挑选
- helper 脚本: 必须保留用户数据(config.yml 等)、必须清理旧版组件、
  必须以 UTF-8 BOM 落盘(PowerShell 5.1 对无 BOM 的 UTF-8 会按 ANSI 解码)
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from javsp import update as u


def test_is_newer_semantic():
    assert u.is_newer('v0.4.0', '0.3.17') is True
    assert u.is_newer('0.4.0', 'v0.3.17') is True
    assert u.is_newer('0.3.17', '0.3.17') is False
    # 字符串比较会判错的两个用例
    assert u.is_newer('0.3.9', '0.3.17') is False
    assert u.is_newer('0.3.10', '0.3.9') is True
    # 非法版本号不应触发提示
    assert u.is_newer('not-a-version', '0.3.17') is False
    assert u.is_newer('', '0.3.17') is False


def test_check_latest_silent_on_network_error(monkeypatch=None):
    """网络不可达时返回 None 且不抛异常 —— 绝不因查不到更新而打扰用户"""
    original = u._fetch_release

    def boom(timeout=3):
        raise OSError('network unreachable')

    u._fetch_release = boom
    u._cache.clear()
    try:
        assert u.check_latest(force=True) is None
    finally:
        u._fetch_release = original
        u._cache.clear()


def test_check_latest_detects_new_version():
    """有更新时返回完整信息(含资产与发布页)"""
    original = u._fetch_release
    original_local = u.local_version

    def fake_fetch(timeout=3):
        return {
            'tag_name': 'v9.9.9',
            'html_url': 'https://github.com/luckwalter/JavSP-fork/releases/tag/v9.9.9',
            'published_at': '2026-10-25T00:00:00Z',
            'body': 'notes',
            'assets': [
                {'name': 'javsp-fork-9.9.9-image.tar.gz', 'browser_download_url': 'http://x/img', 'size': 1},
                {'name': 'JavSP-9.9.9-windows.zip', 'browser_download_url': 'http://x/win.zip', 'size': 2},
            ],
        }

    u._fetch_release = fake_fetch
    u.local_version = lambda: '0.3.17'
    u._cache.clear()
    try:
        info = u.check_latest(force=True)
        assert info is not None
        assert info['latest'] == '9.9.9'
        assert info['current'] == '0.3.17'
        assert info['url'].endswith('/v9.9.9')
        assert len(info['assets']) == 2
    finally:
        u._fetch_release = original
        u.local_version = original_local
        u._cache.clear()


def test_select_windows_asset():
    release = {'assets': [
        {'name': 'javsp-fork-0.4.0-image.tar.gz', 'url': 'u1'},
        {'name': 'JavSP-0.4.0-windows.zip', 'url': 'u2'},
    ]}
    asset = u.select_windows_asset(release)
    assert asset and asset['url'] == 'u2'
    assert u.select_windows_asset({'assets': [{'name': 'x.tar.gz'}]}) is None
    assert u.select_windows_asset(None) is None


def test_docker_hint_mentions_load_and_restart():
    hint = u.docker_upgrade_hint('0.4.0')
    assert 'docker load' in hint and '0.4.0' in hint and 'restart' in hint


def test_keep_files_include_config_and_session():
    """用户数据必须被保留: 配置与会话文件"""
    assert 'config.yml' in u._KEEP_FILES
    assert '.sessions.json' in u._KEEP_FILES


def test_helper_script_content_and_bom():
    """helper 脚本: 含"保留用户数据/清理旧版/回滚"关键步骤, 且以 UTF-8 BOM 落盘"""
    tmp = tempfile.mkdtemp(prefix='helper_test_')
    zip_path = os.path.join(tmp, 'JavSP-0.4.0-windows.zip')
    with open(zip_path, 'wb') as f:
        f.write(b'PK\x05\x06' + b'\x00' * 18)
    exe_dir = os.path.join(tmp, 'JavSP')
    os.makedirs(exe_dir)
    script = u._helper_script(zip_path, exe_dir, os.path.join(exe_dir, 'JavSP.exe'))
    assert os.path.isfile(script)
    with open(script, 'rb') as f:
        raw = f.read()
    assert raw.startswith(b'\xef\xbb\xbf'), '必须以 UTF-8 BOM 落盘(PowerShell 5.1 否则按 ANSI 解码)'
    text = raw.decode('utf-8-sig')
    assert 'config.yml' in text, '必须保留 config.yml'
    assert '.sessions.json' in text, '必须保留会话文件'
    assert 'Move-Item' in text and '$bak' in text, '旧目录应改名备份(顺带清理旧版组件)'
    assert 'Remove-Item $bak' in text, '更老的备份应被清理, 只留最近一个'
    assert 'Expand-Archive' in text, '需用 PowerShell 解压 zip'
    assert 'Start-Process' in text, '升级后需启动新版本'
    assert '已回滚' in text or 'Move-Item -Path $bak -Destination $exeDir' in text, '失败需能回滚'
