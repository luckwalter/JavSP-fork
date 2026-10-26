"""上游遗留函数加固的单元测试(v0.4.3)

对应清单:
- B-008  `func.shutdown` 按平台给出正确的关机参数; 无权限/无命令时只提示,
         不让异常打断整理收尾(原本统一 `os.system('shutdown -s')`)
- B-008  `func.check_update` 取不到版本号时直接跳过(源码方式启动原本会因
         `meta.version('javsp')` 抛 PackageNotFoundError 崩掉 CLI 启动)
- B-007  `func.download_update` 解压前做 zip slip 校验(原本裸 `extractall()`)
"""
import os
import sys
import types
import zipfile

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from javsp import func


# ---------------------------------------------------------------- B-008 ---
def test_shutdown_command_per_platform(monkeypatch):
    """Windows 与类 Unix 的关机参数不同 —— 原本的 -s 在类 Unix 上不是有效选项"""
    monkeypatch.setattr(func.platform, 'system', lambda: 'Windows')
    assert func.system_shutdown_command() == ['shutdown', '/s', '/t', '0']
    monkeypatch.setattr(func.platform, 'system', lambda: 'Linux')
    assert func.system_shutdown_command() == ['shutdown', '-h', 'now']
    monkeypatch.setattr(func.platform, 'system', lambda: 'Darwin')
    assert func.system_shutdown_command() == ['shutdown', '-h', 'now']


def test_shutdown_failure_only_warns(monkeypatch, caplog):
    """普通用户无权关机时只降级为提示, 不能把异常抛到整理收尾流程里"""
    monkeypatch.setattr(func.time, 'sleep', lambda _s: None)
    monkeypatch.setattr(func.subprocess, 'run',
                        lambda _cmd, **_kw: types.SimpleNamespace(returncode=1, stderr='Need to be root\n'))
    with caplog.at_level('WARNING', logger='javsp.func'):
        func.shutdown(timeout=1)
    assert any('自动关机失败' in r.message for r in caplog.records)
    assert not any(r.levelname == 'ERROR' for r in caplog.records)


def test_check_update_skips_when_version_unknown(monkeypatch):
    """版本未知时跳过检查, 且绝不发起网络请求(原本会先崩在取版本这一步)"""
    monkeypatch.delattr(sys, 'javsp_version', raising=False)
    monkeypatch.setattr(func, 'get_version', lambda: func.FALLBACK_VERSION)

    def boom(*_a, **_kw):
        raise AssertionError('版本未知时不该发起网络请求')

    monkeypatch.setattr(func, 'request_get', boom)
    assert func.check_update(True, True) is None


def test_check_update_survives_network_error(monkeypatch, capsys):
    """网络不可达时打印提示后返回, 不抛异常(B-008 附带: 异常不能被吞成崩溃)"""
    monkeypatch.delattr(sys, 'javsp_version', raising=False)
    monkeypatch.setattr(func, 'get_version', lambda: '0.4.2')

    def boom(*_a, **_kw):
        raise OSError('network unreachable')

    monkeypatch.setattr(func, 'request_get', boom)
    func.check_update(True, False)
    out = capsys.readouterr().out
    assert '0.4.2' in out


# ---------------------------------------------------------------- B-007 ---
def _make_zip(path, members):
    with zipfile.ZipFile(str(path), 'w') as z:
        for name, content in members:
            z.writestr(name, content)


def test_safe_extract_normal(tmp_path):
    zip_path = tmp_path / 'ok.zip'
    _make_zip(zip_path, [('JavSP/readme.txt', 'hi'), ('JavSP/sub/x.txt', 'x')])
    dest = tmp_path / 'dest'
    dest.mkdir()
    func.safe_extract(str(zip_path), str(dest))
    assert (dest / 'JavSP' / 'readme.txt').read_text(encoding='utf-8') == 'hi'
    assert (dest / 'JavSP' / 'sub' / 'x.txt').exists()


@pytest.mark.parametrize('bad_member', ['../evil.txt', 'a/../../evil.txt', '/abs/evil.txt'])
def test_safe_extract_rejects_traversal(tmp_path, bad_member):
    """含路径穿越/绝对路径成员的包必须整体拒绝, 且**不留半个解压结果**在盘上"""
    zip_path = tmp_path / 'evil.zip'
    _make_zip(zip_path, [('good.txt', 'good'), (bad_member, 'evil')])
    dest = tmp_path / 'dest'
    dest.mkdir()
    outside = tmp_path / 'evil.txt'

    with pytest.raises(ValueError) as exc:
        func.safe_extract(str(zip_path), str(dest))

    assert '非法路径' in str(exc.value)
    assert not outside.exists()
    assert not (dest / 'good.txt').exists()      # 校验通过才解压, 不会留下半个结果


def test_download_update_refuses_source_run(monkeypatch, caplog):
    """源码方式运行时 sys.executable 是 Python 解释器 —— 绝不能"备份并替换"它"""
    monkeypatch.setattr(sys, 'frozen', False, raising=False)
    downloaded = []

    def spy(url, output_path, desc=None):
        downloaded.append(url)

    monkeypatch.setattr(func, 'download', spy)
    with caplog.at_level('WARNING', logger='javsp.func'):
        func.download_update({'assets': [{'name': 'x.zip', 'browser_download_url': 'http://x/x.zip'}]})

    assert downloaded == []
    assert any('不支持自动更新' in r.message for r in caplog.records)
