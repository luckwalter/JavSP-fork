"""多分片(CD)影片移动的健壮性测试(issue #6)

问题现象: 一部 3 分片的影片, 整理时只移动了第 1 个(命名 CD1), 其余分片原地不动。

根因定位结论: 移动实现(datatype.Movie.rename_files)在多分片分支里是"一错全停"
—— 任意一个分片移动抛异常(目标已存在/源文件被占用或失效)就会中断整个循环,
已成功的分片留在目标目录, 剩余分片留在源目录, 而失败原因只在异常栈里, 用户
完全看不到。标准场景(同目录同扩展名、体积均超阈值)下移动本身是正确的。

修复: 多分片逐个容错 —— 单片失败记录后继续处理其余分片, 失败明细经
move_failures 上报(organize_movie -> result['move_failures'] -> Web 端警告条)。
"""
import errno
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import javsp.datatype as dt
from javsp.datatype import Movie, MovieInfo


def _make_multifile_movie(root, count=3, avid='ABC-123'):
    """构造一部带 count 个分片的影片(文件已存在于 src 目录)"""
    src_dir = os.path.join(root, avid)
    save_dir = os.path.join(root, '#整理完成', 'Label', avid)
    os.makedirs(src_dir)
    os.makedirs(save_dir)
    files = []
    for i in range(1, count + 1):
        p = os.path.join(src_dir, f'{avid}-{i}.mp4')
        with open(p, 'wb') as f:
            f.write(b'\x00' * 1024)
        files.append(p)
    m = Movie(avid)
    m.files = files
    m.save_dir = save_dir
    m.basename = avid
    m.scan_root = os.path.abspath(root)
    m.info = MovieInfo(avid)
    return m, src_dir, save_dir, files


def test_all_slices_moved():
    """正常情况: 3 个分片全部移动, 无失败记录"""
    tmp = tempfile.mkdtemp(prefix='cd_ok_')
    try:
        m, src_dir, save_dir, files = _make_multifile_movie(tmp)
        m.rename_files(False)
        moved = sorted(os.listdir(save_dir))
        assert moved == ['ABC-123-CD1.mp4', 'ABC-123-CD2.mp4', 'ABC-123-CD3.mp4'], moved
        assert m.move_failures == [], m.move_failures
        assert len(m.new_paths) == 3
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_one_slice_failure_does_not_block_others():
    """第 2 个分片因目标已存在而失败时, 其余分片仍应完成移动(issue #6 核心)"""
    tmp = tempfile.mkdtemp(prefix='cd_partial_')
    try:
        m, src_dir, save_dir, files = _make_multifile_movie(tmp)
        # 预置 CD2 目标文件 -> move_file 会抛 FileExistsError
        with open(os.path.join(save_dir, 'ABC-123-CD2.mp4'), 'wb') as f:
            f.write(b'existing')
        m.rename_files(False)          # 不应抛异常
        moved = sorted(os.listdir(save_dir))
        assert 'ABC-123-CD1.mp4' in moved
        assert 'ABC-123-CD3.mp4' in moved, f'CD3 未被移动: {moved}'
        # 失败明细: 1 条, 指向 CD2 的源文件
        assert len(m.move_failures) == 1, m.move_failures
        fail = m.move_failures[0]
        assert fail['path'] == files[1]
        assert 'FileExistsError' in fail['reason']
        # 失败的那个分片仍留在源目录
        assert os.path.exists(files[1])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_single_file_failure_still_raises():
    """单文件影片移动失败时保持原语义(抛异常), 避免掩盖整部片的失败"""
    tmp = tempfile.mkdtemp(prefix='cd_single_')
    try:
        m, src_dir, save_dir, files = _make_multifile_movie(tmp, count=1)
        m.files = files
        with open(os.path.join(save_dir, 'ABC-123.mp4'), 'wb') as f:
            f.write(b'existing')
        try:
            m.rename_files(False)
        except FileExistsError:
            pass
        else:
            raise AssertionError('单文件移动失败应当抛出异常')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_move_failures_default_empty():
    """未移动时 move_failures 应为空列表(供上层安全读取)"""
    m = Movie('ABC-123')
    assert m.move_failures == []
