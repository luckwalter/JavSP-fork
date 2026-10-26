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

import json
import time

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
    workdir = os.path.join(tmp, 'helper_work')
    os.makedirs(workdir)
    script = u._helper_script(zip_path, exe_dir, os.path.join(exe_dir, 'JavSP.exe'), workdir)
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


# ---------------------------------------------------------------------------
# B-013: 升级失败时不得把旧版"藏进"主目录
# 现象: 升级失败后回滚, exe/配置/更新包全都不见了, 实际数据被移到了
#       <运行目录>/<备份名>/ 里 —— 用户以为文件被删了。
# 根因: `Move-Item -Path $bak -Destination $exeDir` 在目标目录已存在时,
#       PowerShell 会把备份移进该目录而不是替换它; 且失败后没有把
#       关键路径写进日志, 用户无从找回。
# 修复: 回滚前先把不完整的新目录挪到 <运行目录>.failed, 再把备份移回;
#       无论成败都把运行目录/旧版备份/不完整副本三个路径写进日志。
#       另加"枚举预检": 动数据之前先确认目录能枚举, 不可枚举则直接中止,
#       一个字节都不动(UNC/SMB 上深层目录常被拒绝枚举, 改名必然失败)。
# ---------------------------------------------------------------------------

def _helper_text(tmp, exe_dir=None, workdir=None):
    """生成 helper 脚本并返回其文本内容"""
    zip_path = os.path.join(tmp, 'JavSP-0.4.0-windows.zip')
    with open(zip_path, 'wb') as f:
        f.write(b'PK\x05\x06' + b'\x00' * 18)
    if exe_dir is None:
        exe_dir = os.path.join(tmp, 'JavSP')
    if workdir is None:
        workdir = os.path.join(tmp, 'helper_work')
    os.makedirs(workdir, exist_ok=True)
    script = u._helper_script(zip_path, exe_dir, os.path.join(exe_dir, 'JavSP.exe'), workdir)
    with open(script, 'rb') as f:
        return f.read().decode('utf-8-sig')


def test_rollback_does_not_nest_backup_into_exe_dir():
    """回滚不得把备份移进运行目录(那会让主目录看起来空无一物)"""
    text = _helper_text(tempfile.mkdtemp(prefix='b013_'))
    # 回滚必须先把不完整的新目录挪走
    assert '.failed' in text, '回滚前应把不完整的新目录挪到 <运行目录>.failed'
    assert "$failed = $exeDir + '.failed'" in text
    # 移走后再把备份原样移回(带 -Force, 避免被当成"移进目录")
    assert 'Move-Item -Path $bak -Destination $exeDir -Force' in text, '回滚移动备份必须带 -Force'
    # 绝不能再出现"无 -Force 的 Move-Item -Path $bak -Destination $exeDir"
    # (只看实际命令, 跳过以 # 开头的注释行)
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('#'):
            continue
        if 'Move-Item -Path $bak -Destination $exeDir' in stripped:
            assert '-Force' in stripped, f'回滚移动备份必须带 -Force: {stripped}'


def test_rollback_logs_paths_so_user_can_recover():
    """失败时必须把关键路径写进日志, 否则用户找不到数据"""
    text = _helper_text(tempfile.mkdtemp(prefix='b013_'))
    assert '路径提示' in text, '失败时需输出路径提示'
    assert '旧版备份:' in text, '需指出旧版备份位置'
    assert '不完整副本:' in text, '需指出不完整副本位置'


def test_precheck_blocks_upgrade_when_dir_not_enumerable():
    """动数据之前先做枚举预检: 不可枚举则中止, 不动任何文件"""
    text = _helper_text(tempfile.mkdtemp(prefix='b013_'))
    assert 'Get-ChildItem' in text, '需做目录枚举预检'
    assert '预检失败' in text, '预检失败需有明确日志'
    assert '已中止升级' in text, '预检失败须中止升级'
    assert '原文件未做任何改动' in text, '预检失败须声明未改动原文件'
    # 预检必须在 Move-Item 之前
    pre = text.index('预检')
    move = text.index('Move-Item -Path $exeDir -Destination $bak')
    assert pre < move, '预检必须早于目录改名'


# ---------------------------------------------------------------------------
# B-012: helper 的工作区必须与"待升级目录"彻底隔离
# 根因: helper 把脚本 + 日志 + 安装包放在 exe 同级的 update/ 内部, 且进程
#       工作目录继承自主进程 —— 打开的文件句柄与 CWD 让该目录自身处于
#       "使用中", Move-Item 改名备份必然失败(本地盘与 UNC 一视同仁)。
# ---------------------------------------------------------------------------

def test_helper_workdir_is_outside_exe_dir():
    """helper 工作区必须在系统临时目录, 绝不能落在 exe 目录内"""
    tmp = tempfile.mkdtemp(prefix='helper_wd_test_')
    exe_dir = os.path.join(tmp, 'JavSP')
    os.makedirs(exe_dir)
    work = u._helper_workdir()
    assert os.path.isdir(work)
    # 工作区既不在 exe 目录内, 也不是 exe 目录的子目录
    assert not u.is_path_within(work, exe_dir)
    assert not u.is_path_within(exe_dir, work)


def test_helper_script_not_written_into_exe_dir():
    """helper 脚本本身不得落在待升级目录内"""
    tmp = tempfile.mkdtemp(prefix='helper_path_test_')
    zip_path = os.path.join(tmp, 'JavSP-0.4.0-windows.zip')
    with open(zip_path, 'wb') as f:
        f.write(b'PK\x05\x06' + b'\x00' * 18)
    exe_dir = os.path.join(tmp, 'JavSP')
    os.makedirs(exe_dir)
    workdir = os.path.join(tmp, 'helper_work')
    os.makedirs(workdir)
    script = u._helper_script(zip_path, exe_dir, os.path.join(exe_dir, 'JavSP.exe'), workdir)
    assert not u.is_path_within(script, exe_dir), 'helper 脚本落在待升级目录内 -> 目录自身会被锁住'
    assert os.path.isfile(script)


def test_helper_log_path_not_in_exe_dir():
    """脚本内容里的日志路径必须指向工作区, 而非待升级目录"""
    tmp = tempfile.mkdtemp(prefix='helper_log_test_')
    zip_path = os.path.join(tmp, 'JavSP-0.4.0-windows.zip')
    with open(zip_path, 'wb') as f:
        f.write(b'PK\x05\x06' + b'\x00' * 18)
    exe_dir = os.path.join(tmp, 'JavSP')
    os.makedirs(exe_dir)
    workdir = os.path.join(tmp, 'helper_work')
    os.makedirs(workdir)
    script = u._helper_script(zip_path, exe_dir, os.path.join(exe_dir, 'JavSP.exe'), workdir)
    with open(script, 'rb') as f:
        text = f.read().decode('utf-8-sig')
    # 日志必须基于 $work(工作区), 不能是 Split-Path $zip 的同级(即待升级目录内)
    assert "$log = Join-Path $work 'apply_update.log'" in text, '日志必须写在工作区'
    assert 'Split-Path $zip' not in text, '日志不得跟随 zip 落在待升级目录内'


def test_stage_update_zip_moves_out_of_exe_dir():
    """安装包必须被搬离待升级目录"""
    tmp = tempfile.mkdtemp(prefix='stage_zip_test_')
    exe_dir = os.path.join(tmp, 'JavSP')
    upd = os.path.join(exe_dir, 'update')
    os.makedirs(upd)
    zip_path = os.path.join(upd, 'JavSP-0.4.0-windows.zip')
    with open(zip_path, 'wb') as f:
        f.write(b'PK\x05\x06' + b'\x00' * 18)
    workdir = os.path.join(tmp, 'helper_work')
    os.makedirs(workdir)
    staged = u._stage_update_zip(zip_path, workdir)
    assert not u.is_path_within(staged, exe_dir), '安装包仍在待升级目录内'
    assert os.path.isfile(staged)
    assert not os.path.isfile(zip_path), '原位置的安装包应被搬走(不是复制)'


def test_apply_windows_update_uses_workdir_cwd():
    """启动 helper 时必须显式指定 cwd=工作区, 不能继承主进程 CWD"""
    captured = {}

    class FakeProc:
        pid = 4242

    def fake_popen(cmd, **kwargs):
        captured['cmd'] = cmd
        captured['kwargs'] = kwargs
        return FakeProc()

    tmp = tempfile.mkdtemp(prefix='apply_wd_test_')
    exe_dir = os.path.join(tmp, 'JavSP')
    os.makedirs(exe_dir)
    upd = os.path.join(exe_dir, 'update')
    os.makedirs(upd)
    zip_path = os.path.join(upd, 'JavSP-0.4.0-windows.zip')
    with open(zip_path, 'wb') as f:
        f.write(b'PK\x05\x06' + b'\x00' * 18)

    orig_popen = u.subprocess.Popen
    orig_exe_dir = u._exe_dir
    orig_wait = u._wait_helper_started
    u.subprocess.Popen = fake_popen
    u._exe_dir = lambda: exe_dir
    u._wait_helper_started = lambda *a, **k: True
    try:
        res = u.apply_windows_update(zip_path)
    finally:
        u.subprocess.Popen = orig_popen
        u._exe_dir = orig_exe_dir
        u._wait_helper_started = orig_wait

    assert res['pid'] == 4242
    cwd = captured['kwargs'].get('cwd')
    assert cwd, '必须显式指定 cwd'
    assert not u.is_path_within(cwd, exe_dir), 'cwd 落在待升级目录内 -> 目录自身会被锁住'
    helper = captured['cmd'][-1]
    assert not u.is_path_within(helper, exe_dir), 'helper 脚本落在待升级目录内'



# ---------------------------------------------------------------------------
# v0.4.2+ 修复项回归
# ---------------------------------------------------------------------------

def test_is_path_within_case_insensitive():
    """B-009: Windows 路径大小写不敏感, 白名单比较必须大小写无关"""
    assert u.is_path_within(r'C:\JavSP\update\a.zip', r'C:\JavSP\update') is True
    assert u.is_path_within(r'c:\javsp\update\a.zip', r'C:\JavSP\UPDATE') is True
    assert u.is_path_within('/app/update/a.zip', '/app/update') is True


def test_is_path_within_rejects_escape():
    """安全边界: 目录自身、同级前缀欺骗、目录外一律拒绝"""
    assert u.is_path_within(r'C:\JavSP\update', r'C:\JavSP\update') is False       # 目录自身
    assert u.is_path_within(r'C:\JavSP\update_other\a.zip', r'C:\JavSP\update') is False  # 前缀欺骗
    assert u.is_path_within(r'C:\JavSP\a.zip', r'C:\JavSP\update') is False        # 父级
    assert u.is_path_within('/app/updateX/a.zip', '/app/update') is False


def test_sha256_file_matches_hashlib():
    """B-004: 分块哈希必须与一次性计算一致(含跨块场景)"""
    import hashlib
    with tempfile.TemporaryDirectory() as tmp:
        small = os.path.join(tmp, 'small.bin')
        with open(small, 'wb') as f:
            f.write(b'javsp' * 1000)
        big = os.path.join(tmp, 'big.bin')
        with open(big, 'wb') as f:
            for _ in range(300):
                f.write(os.urandom(8192))       # >2MB, 跨越默认 chunk
        for p in (small, big):
            assert u.sha256_file(p) == hashlib.sha256(open(p, 'rb').read()).hexdigest()


def test_verify_download_sha_mismatch_discards_file():
    """B-004: 哈希不符必须删文件 + 抛错, 绝不留损坏包在盘上"""
    with tempfile.TemporaryDirectory() as tmp:
        bad = os.path.join(tmp, 'bad.zip')
        with open(bad, 'wb') as f:
            f.write(b'broken')
        try:
            u._verify_download(bad, {'sha256': '0' * 64, 'size': 6})
            assert False, '哈希不符必须抛异常'
        except ValueError as e:
            assert 'SHA-256' in str(e)
        assert not os.path.exists(bad), '校验失败必须删除文件'


def test_verify_download_falls_back_to_size():
    """B-004: 无 sha256 时退化为大小校验; 无依据时放行(保持旧行为)"""
    with tempfile.TemporaryDirectory() as tmp:
        ok_file = os.path.join(tmp, 'ok.zip')
        with open(ok_file, 'wb') as f:
            f.write(b'abc')
        u._verify_download(ok_file, {'size': 3})            # 大小匹配 -> 通过
        assert os.path.exists(ok_file)
        try:
            u._verify_download(ok_file, {'size': 999})
            assert False, '大小不符必须抛异常'
        except ValueError:
            pass
        assert not os.path.exists(ok_file), '校验失败必须删除文件'

        keep = os.path.join(tmp, 'keep.zip')
        with open(keep, 'wb') as f:
            f.write(b'abc')
        u._verify_download(keep, None)                      # 无依据 -> 放行
        assert os.path.exists(keep)


def test_fetch_expected_checksum_parses_manifest(monkeypatch):
    """B-004: 从 sha256sum 格式的清单里解析出目标文件的哈希"""

    class FakeResp:
        status_code = 200
        text = ('aaaa  JavSP-0.4.2-windows.zip\n'
                'bbbb  javsp-fork-0.4.2-image.tar.gz\n')

        def raise_for_status(self):
            pass

    monkeypatch.setattr(u.requests, 'get', lambda *a, **k: FakeResp())
    release = {'assets': [{'name': 'javsp-fork-0.4.2.sha256', 'url': 'https://example.invalid/s'}]}
    assert u.fetch_expected_checksum(release, 'JavSP-0.4.2-windows.zip') == 'aaaa'
    assert u.fetch_expected_checksum(release, 'javsp-fork-0.4.2-image.tar.gz') == 'bbbb'
    assert u.fetch_expected_checksum(release, 'not-in-manifest.zip') == ''


def test_fetch_expected_checksum_degrades_when_missing(monkeypatch):
    """B-004: 清单缺失/下载失败必须返回空串降级, 不能抛异常阻断升级"""
    assert u.fetch_expected_checksum({'assets': []}, 'x.zip') == ''
    assert u.fetch_expected_checksum(None, 'x.zip') == ''

    def boom(*a, **k):
        raise OSError('network down')

    monkeypatch.setattr(u.requests, 'get', boom)
    release = {'assets': [{'name': 'javsp-fork-0.4.2.sha256', 'url': 'https://example.invalid/s'}]}
    assert u.fetch_expected_checksum(release, 'x.zip') == ''


# ---------------- B-006 · 高危更新端点的鉴权收紧(v0.4.2) ----------------

def _auth_snapshot():
    """保存 auth 模块的可变全局状态, 供用例结束后还原(避免污染同进程其他用例)"""
    from javsp import auth
    return dict(sessions=dict(auth._sessions), enabled=auth._auth_enabled,
                salt=auth._pwd_salt, digest=auth._pwd_digest)


def _auth_restore(snap):
    from javsp import auth
    auth._sessions.clear()
    auth._sessions.update(snap['sessions'])
    auth._auth_enabled = snap['enabled']
    auth._pwd_salt = snap['salt']
    auth._pwd_digest = snap['digest']


def test_has_valid_session_is_strict():
    """has_valid_session **不含**「未启用认证则放行」的短路 —— 这是它能当凭据用的前提

    对照 verify_token: 后者在未启用认证时返回 True(让所有既有接口维持免登录)。
    若两者语义一致, 高危端点的收紧就会形同虚设, 故此处显式断言二者不同。
    """
    import secrets as _sec
    import hashlib as _hl

    from javsp import auth
    snap = _auth_snapshot()
    try:
        auth._sessions.clear()
        auth._auth_enabled = False
        assert auth.has_valid_session(None) is False
        assert auth.has_valid_session('') is False
        assert auth.has_valid_session('garbage-token') is False
        assert auth.verify_token('garbage-token') is True   # 对照组: 放行

        # 真实登录后应能通过, 且换一个 token 就不通过
        auth._pwd_salt = _sec.token_bytes(16)
        auth._pwd_digest = _hl.pbkdf2_hmac(
            'sha256', b'batch2-pw', auth._pwd_salt, auth.PBKDF2_ITERATIONS)
        auth._auth_enabled = True
        res = auth.login('admin', 'batch2-pw')
        assert res['ok'] is True and res['token']
        assert auth.has_valid_session(res['token']) is True
        assert auth.has_valid_session('wrong-' + res['token']) is False
    finally:
        _auth_restore(snap)


def test_update_auth_mode_values():
    """模式取值: auto/on/off 合法, 非法值与空值一律退化为 auto(偏向更安全的一侧)"""
    from javsp import auth
    key = 'JAVSP_UPDATE_AUTH'
    backup = os.environ.pop(key, None)
    try:
        assert auth.update_auth_mode() == 'auto'
        for raw, want in (('on', 'on'), ('off', 'off'), ('AUTO', 'auto'),
                          ('bogus', 'auto'), ('', 'auto'), ('  ON  ', 'on')):
            os.environ[key] = raw
            assert auth.update_auth_mode() == want, f'{raw!r} -> {want}'
    finally:
        os.environ.pop(key, None)
        if backup is not None:
            os.environ[key] = backup


def test_is_loopback_covers_ipv4_mapped():
    """双栈监听下来源会写成 ::ffff:127.0.0.1, 必须识别为回环

    否则桌面版(pywebview 访问 127.0.0.1)会被误判成"远程请求", 一键升级被 401 拒掉。
    """
    from javsp.server import _is_loopback

    class _Req:
        def __init__(self, host):
            self.client = type('C', (), {'host': host})()

    for host, want in (('127.0.0.1', True), ('::1', True), ('localhost', True),
                       ('::ffff:127.0.0.1', True),
                       ('198.51.100.100', False), ('203.0.113.9', False), ('', False)):
        assert _is_loopback(_Req(host)) is want, f'{host!r} -> {want}'


def test_high_risk_update_endpoints_require_session():
    """auto 模式下: 远程调用高危端点必须 401; 回环放行; 非高危端点不受影响

    用手工构造的 ASGI scope 驱动中间件, 以便精确控制来源地址 —— TestClient 的
    默认来源地址是 'testclient'(非回环), 不能用来验证"回环放行"这一豁免路径。
    """
    import asyncio
    import secrets as _sec
    import time as _time

    from fastapi import Request
    from fastapi.responses import JSONResponse
    from javsp import auth
    from javsp.server import _AUTH_REQUIRED_ALWAYS, app, auth_middleware

    assert _AUTH_REQUIRED_ALWAYS == {'/api/update/download', '/api/update/apply'}

    async def _call_next(request):      # noqa: ARG001
        return JSONResponse({'passed': True})

    def drive(path, host, cookie=None):
        headers = [(b'cookie', cookie.encode('utf-8'))] if cookie else []
        scope = {'type': 'http', 'http_version': '1.1', 'method': 'POST',
                 'scheme': 'http', 'path': path, 'raw_path': path.encode(),
                 'query_string': b'', 'root_path': '', 'headers': headers,
                 'client': (host, 51234), 'server': ('testserver', 80)}
        return asyncio.run(auth_middleware(Request(scope), _call_next))

    snap = _auth_snapshot()
    backup = os.environ.pop('JAVSP_UPDATE_AUTH', None)
    try:
        # 1) 远程未登录 -> 拒绝
        r = drive('/api/update/apply', '203.0.113.9')
        assert r.status_code == 401, r.body
        assert 'JAVSP_AUTH_PASSWORD' in r.body.decode('utf-8', 'replace')

        # 2) 远程已登录 -> 放行(证明收紧的是凭据, 不是一刀切封来源)
        tok = _sec.token_urlsafe(32)
        auth._sessions[auth._token_hash(tok)] = {
            'created': _time.time(), 'expires': _time.time() + 3600, 'ttl': 3600}
        try:
            r = drive('/api/update/apply', '203.0.113.9',
                      cookie=f'{auth.SESSION_COOKIE}={tok}')
            body = json.loads(r.body.decode('utf-8'))
            assert r.status_code == 200 and body.get('passed') is True
        finally:
            auth._sessions.pop(auth._token_hash(tok), None)

        # 3) 回环 -> 放行(桌面版一键升级不受影响)
        r = drive('/api/update/apply', '127.0.0.1')
        assert r.status_code == 200, r.body

        # 4) 非高危端点不受收紧影响
        r = drive('/api/config', '203.0.113.9')
        assert r.status_code == 200, r.body

        # 5) off 模式 -> 放行
        os.environ['JAVSP_UPDATE_AUTH'] = 'off'
        r = drive('/api/update/apply', '203.0.113.9')
        assert r.status_code == 200, r.body
    finally:
        os.environ.pop('JAVSP_UPDATE_AUTH', None)
        if backup is not None:
            os.environ['JAVSP_UPDATE_AUTH'] = backup
        _auth_restore(snap)
