"""空目录清理的重试与失败上报测试(v0.3.16)

背景: 整理后源目录已空却没被删除(实测 NAS 上残留一个完全空的源目录)。
静态因素(代码版本/权限/配置)均已排除, 指向动态场景: 目录被短时占用
(如 Windows 桌面版经 SMB 整理 NAS 路径时资源管理器正停在该目录 -> EBUSY/EACCES)。
且原逻辑失败只写 debug 日志, 用户零感知。

修复: rmdir 重试 3 次(间隔 0.8s); 仍失败则记录 cleanup_failures ->
organize_movie 的 result['leftover_dirs'] -> Web 端提示用户手动删除。
"""
import errno
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import javsp.datatype as dt
from javsp.datatype import Movie, MovieInfo


def _make_movie(root, avid='HOMA-170'):
    src_dir = os.path.join(root, f'{avid} 北野未奈')
    save_dir = os.path.join(root, '#整理完成', 'h.m.p DORAMA', f'{avid} 北野未奈')
    os.makedirs(src_dir)
    os.makedirs(save_dir)
    src_file = os.path.join(src_dir, f'{avid}.mp4')
    with open(src_file, 'wb') as f:
        f.write(b'\x00' * 1024)
    m = Movie(avid)
    m.files = [src_file]
    m.save_dir = save_dir
    m.basename = f'{avid} 北野未奈'
    m.scan_root = os.path.abspath(root)
    m.info = MovieInfo(avid)
    return m, src_dir


def test_rmdir_retry_then_success():
    """前两次 EBUSY(模拟 SMB 占用), 第三次应成功删除且不记录失败"""
    tmp = tempfile.mkdtemp(prefix='retry_test_')
    try:
        m, src_dir = _make_movie(tmp)
        real_rmdir = os.rmdir
        attempts = {'n': 0}

        def flaky_rmdir(path):
            if path == os.path.realpath(src_dir):
                attempts['n'] += 1
                if attempts['n'] <= 2:
                    raise OSError(errno.EBUSY, 'Device or resource busy')
            return real_rmdir(path)

        dt.os.rmdir = flaky_rmdir
        try:
            m.rename_files(False)
        finally:
            dt.os.rmdir = real_rmdir
        assert attempts['n'] == 3, f'应重试到第 3 次, 实际 {attempts["n"]}'
        assert not os.path.isdir(src_dir), '重试后应删除成功'
        assert not m.cleanup_failures, '成功时不应记录失败'
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_rmdir_always_denied_records_failure():
    """持续 EACCES 时应保留目录、记录失败原因, 且不影响影片文件就位"""
    tmp = tempfile.mkdtemp(prefix='retry_test2_')
    try:
        m, src_dir = _make_movie(tmp, avid='ABC-123')
        real_rmdir = os.rmdir

        def always_fail(path):
            if path == os.path.realpath(src_dir):
                raise OSError(errno.EACCES, 'Permission denied')
            return real_rmdir(path)

        dt.os.rmdir = always_fail
        try:
            m.rename_files(False)     # 不应抛异常
        finally:
            dt.os.rmdir = real_rmdir
        assert os.path.isdir(src_dir), '删不掉时目录应保留'
        assert len(m.cleanup_failures) == 1
        assert 'Permission denied' in m.cleanup_failures[0]['reason']
        assert m.cleanup_failures[0]['path'] == os.path.realpath(src_dir)
        # 清理失败不影响主流程: 影片文件已就位
        moved = os.path.join(tmp, '#整理完成', 'h.m.p DORAMA', 'ABC-123 北野未奈', 'ABC-123 北野未奈.mp4')
        assert os.path.exists(moved), '影片文件应已移动到位'
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_cleanup_baseline_without_scan_root():
    """无 scan_root(CLI/任务重建)时也应清理源目录"""
    tmp = tempfile.mkdtemp(prefix='retry_test3_')
    try:
        m, src_dir = _make_movie(tmp, avid='XYZ-999')
        m.scan_root = None
        m.rename_files(False)
        assert not os.path.isdir(src_dir), '无 scan_root 时也应清理源目录'
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
