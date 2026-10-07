"""验证刮削后空目录清理(问题1)

原实现只 os.rmdir 最内层一层, 嵌套目录(<根>/<分类>/<影片>/xxx.mp4)清理后
<分类> 会残留空壳目录。本次改为向上递归清理。

**本脚本只操作临时目录, 不触碰真实库目录**; 且刻意验证「不该删的绝不删」——
误删用户目录树是不可逆事故, 因此边界断言比功能断言更重要。
"""
import os
import sys
import shutil
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from javsp.datatype import Movie, _is_within
from javsp.datatype import MovieInfo

PASS = 0
FAIL = 0


def attach_info(m, dvdid):
    """rename_files 依赖 self.basename(实例属性, 正常由 generate_names 填) 与
    self.save_dir。测试里直接给 basename, 不必走整套命名模板。"""
    m.basename = dvdid
    return m


def check(desc, cond, extra=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'[PASS] {desc}')
    else:
        FAIL += 1
        print(f'[FAIL] {desc} {extra}')


def section(t):
    print(f'\n--- {t} ---')


def make_movie(root, files, dvdid='ABC-123'):
    """在 root 下造一个含指定文件的 Movie"""
    m = attach_info(Movie(dvdid), dvdid)
    m.files = list(files)
    return m


# --------------------------------------------------------------------------
section('A 递归向上清理（核心修复）')

tmp = tempfile.mkdtemp(prefix='emptyclean_')
try:
    # 结构: root/分类/影片/xxx.mp4
    root = os.path.join(tmp, 'scanroot')
    cat = os.path.join(root, '分类A')
    movie = os.path.join(cat, 'ABC-123')
    os.makedirs(movie)
    f1 = os.path.join(movie, 'ABC-123.mp4')
    open(f1, 'wb').write(b'x' * 100)

    m = make_movie(root, [f1])
    m.scan_root = root
    # save_dir 必须存在且不同, 否则移动会失败
    save = os.path.join(root, '#整理完成')
    os.makedirs(save, exist_ok=True)
    m.save_dir = save

    m.rename_files(False)

    check('影片目录已删除', not os.path.exists(movie))
    # 原实现只删最内层, 这里就是回归点: 父目录也应变空被清理
    check('上级分类目录也被清理（原本会残留）', not os.path.exists(cat),
          f'仍存在: {cat}')
    check('扫描根本身保留（边界）', os.path.isdir(root))
    check('整理输出目录保留', os.path.isdir(save))
    check('文件已移动到输出目录',
          os.path.exists(os.path.join(save, m.basename + '.mp4')))
finally:
    shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
section('B 非空目录绝不能删')

tmp = tempfile.mkdtemp(prefix='emptykeep_')
try:
    root = os.path.join(tmp, 'scanroot')
    # 分类下有另一部片子 → 分类目录非空, 必须保留
    cat = os.path.join(root, '分类B')
    movie1 = os.path.join(cat, 'AAA-111')
    movie2 = os.path.join(cat, 'BBB-222')
    os.makedirs(movie1)
    os.makedirs(movie2)
    f1 = os.path.join(movie1, 'AAA-111.mp4')
    f2 = os.path.join(movie2, 'BBB-222.mp4')
    open(f1, 'wb').write(b'x' * 100)
    open(f2, 'wb').write(b'x' * 100)

    save = os.path.join(root, '#整理完成')
    os.makedirs(save, exist_ok=True)
    m = make_movie(root, [f1])
    m.scan_root = root
    m.save_dir = save
    m.rename_files(False)

    check('已处理的影片目录删除', not os.path.exists(movie1))
    check('兄弟影片目录保留', os.path.isdir(movie2))
    check('非空分类目录保留（关键）', os.path.isdir(cat),
          f'误删了非空目录 {cat}')
    check('兄弟文件仍在', os.path.exists(f2))
finally:
    shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
section('C 边界保护：绝不越界删除')

tmp = tempfile.mkdtemp(prefix='emptybound_')
try:
    root = os.path.join(tmp, 'scanroot')
    movie = os.path.join(root, 'CCC-333')
    os.makedirs(movie)
    f = os.path.join(movie, 'CCC-333.mp4')
    open(f, 'wb').write(b'x' * 100)
    save = os.path.join(root, '#整理完成')
    os.makedirs(save, exist_ok=True)

    m = make_movie(root, [f])
    m.scan_root = root
    m.save_dir = save
    m.rename_files(False)

    # scan_root 之外的祖先目录(tmp)此刻已空, 但绝不能被删
    check('影片目录删除', not os.path.exists(movie))
    check('scan_root 保留', os.path.isdir(root))
    check('scan_root 的父目录未被波及（边界生效）', os.path.isdir(tmp))
finally:
    shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
section('D 无 scan_root（CLI 路径）仍能安全清理')

tmp = tempfile.mkdtemp(prefix='emptycli_')
try:
    # CLI 没有 scan_root 字段, 只有黑名单兜底
    movie = os.path.join(tmp, '深层', '子目录', 'DDD-444')
    os.makedirs(movie)
    f = os.path.join(movie, 'DDD-444.mp4')
    open(f, 'wb').write(b'x' * 100)
    save = os.path.join(tmp, 'out')
    os.makedirs(save, exist_ok=True)

    m = attach_info(Movie('DDD-444'), 'DDD-444')
    m.files = [f]
    m.save_dir = save
    # 故意不设 scan_root
    m.rename_files(False)

    check('影片目录删除', not os.path.exists(movie))
    check('空的中间目录也一并清理', not os.path.exists(os.path.join(tmp, '深层', '子目录')))
    check('临时根目录未被删（黑名单/自保护）', os.path.isdir(tmp))
    check('文件已移动', os.path.exists(os.path.join(save, m.basename + '.mp4')))
finally:
    shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
section('E 隐藏目录不参与清理')

tmp = tempfile.mkdtemp(prefix='emptyhid_')
try:
    # .hidden 分类下的影片移走后, .hidden 目录本身不应被删(可能是系统/回收站目录)
    root = os.path.join(tmp, 'scanroot')
    hid = os.path.join(root, '.hidden')
    movie = os.path.join(hid, 'EEE-555')
    os.makedirs(movie)
    f = os.path.join(movie, 'EEE-555.mp4')
    open(f, 'wb').write(b'x' * 100)
    save = os.path.join(root, 'out')
    os.makedirs(save, exist_ok=True)

    m = make_movie(root, [f])
    m.scan_root = root
    m.save_dir = save
    m.rename_files(False)

    check('影片目录删除', not os.path.exists(movie))
    check('隐藏目录 .hidden 被保留（不做隐藏目录清理）', os.path.isdir(hid))
finally:
    shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
section('F 多文件(CD1/CD2)场景')

tmp = tempfile.mkdtemp(prefix='emptycd_')
try:
    root = os.path.join(tmp, 'scanroot')
    movie = os.path.join(root, '分类C', 'FFF-666')
    os.makedirs(movie)
    f1 = os.path.join(movie, 'FFF-666-cd1.mp4')
    f2 = os.path.join(movie, 'FFF-666-cd2.mp4')
    open(f1, 'wb').write(b'x' * 100)
    open(f2, 'wb').write(b'x' * 100)
    save = os.path.join(root, 'out')
    os.makedirs(save, exist_ok=True)

    m = make_movie(root, [f1, f2])
    m.scan_root = root
    m.save_dir = save
    m.rename_files(False)

    check('多文件影片目录清空并删除', not os.path.exists(movie))
    check('分类目录一并清理', not os.path.exists(os.path.join(root, '分类C')))
    check('CD1 已移动', os.path.exists(os.path.join(save, m.basename + '-CD1.mp4')))
    check('CD2 已移动', os.path.exists(os.path.join(save, m.basename + '-CD2.mp4')))
finally:
    shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
section('G _is_within 判定')

check('子路径在根内', _is_within(os.path.join('/data', 'a', 'b'), '/data'))
check('根本身在根内', _is_within('/data', '/data'))
check('兄弟路径不在根内', not _is_within('/database', '/data'))
check('前缀相同但非子路径', not _is_within('/data2', '/data'))


# --------------------------------------------------------------------------
section('H 极端边界：家目录/文件系统根绝不删除')

# 直接对家目录本身调用清理(构造场景: 用户把影片直接放在家目录下刮削)
old_home = os.environ.get('USERPROFILE') or os.path.expanduser('~')
if old_home and os.path.isdir(old_home):
    m = Movie('GGG-777')
    m.basename = 'GGG-777'
    # 用一个绝对不存在的路径触发 realpath 判定, 但把 home 作为 start_dir 的保护对象
    # 实际做法: 直接调用内部方法观察它不会误删 home
    m._cleanup_empty_dirs(old_home, stop_at=None)
    check('家目录未被清理方法删除', os.path.isdir(old_home))
else:
    print('[SKIP] 无 home 目录')

# start_dir 是 home 下的空目录时: 该空目录会被删(设计如此), 但**更上层**的祖先
# 必须保留 —— 靠层数上限兜住, 这是防止一路删到目录树顶端的关键。
tmp = tempfile.mkdtemp(prefix='emptyhome_')
try:
    home_sim = os.path.join(tmp, 'home')
    empty = os.path.join(home_sim, 'empty')
    os.makedirs(empty)
    m = Movie('HHH-888')
    m.basename = 'HHH-888'
    m._cleanup_empty_dirs(empty, stop_at=None)
    check('空子目录已清理', not os.path.exists(empty))
    # 允许 home_sim 被清掉(它就是「残留空分类目录」, 该删), 但临时根必须保留
    check('临时根保留（层数上限生效）', os.path.isdir(tmp))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# CWD 本身不得被清理
tmp = tempfile.mkdtemp(prefix='emptycwd_')
try:
    cwd_dir = os.path.join(tmp, 'cwdlike')
    os.makedirs(cwd_dir)
    old_cwd = os.getcwd()
    os.chdir(cwd_dir)
    try:
        m = Movie('III-999')
        m.basename = 'III-999'
        m._cleanup_empty_dirs(cwd_dir, stop_at=None)
        check('当前工作目录未被清理', os.path.isdir(cwd_dir))
    finally:
        os.chdir(old_cwd)
finally:
    shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
section('I NAS 缩略图缓存目录(.@__thumb / @eaDir)不算内容')

# 这是 v0.1.23 在 NAS 上的实际失败场景: 刮削后源目录只剩 '.@__thumb'
# (QNAP 缩略图缓存, 内含 s100/s800/s2000/default 前缀的几十 KB 缩略图副本),
# 于是 os.listdir 认为非空 -> 目录永远清不掉。
tmp = tempfile.mkdtemp(prefix='emptythumb_')
try:
    root = os.path.join(tmp, 'scanroot')
    cat = os.path.join(root, '分类X')          # 上层分类目录
    movie = os.path.join(cat, 'TST-001')       # 影片目录
    os.makedirs(movie)
    f = os.path.join(movie, 'TST-001.mp4')
    open(f, 'wb').write(b'x' * 100)

    # 模拟 QNAP 在影片目录里生成缩略图缓存
    thumb = os.path.join(movie, '.@__thumb')
    os.makedirs(thumb)
    open(os.path.join(thumb, 's100TST-001.mp4'), 'wb').write(b'x' * 25)
    open(os.path.join(thumb, 'defaultTST-001.mp4'), 'wb').write(b'x' * 25)

    save = os.path.join(root, 'out')
    os.makedirs(save, exist_ok=True)

    m = make_movie(root, [f])
    m.scan_root = root
    m.save_dir = save
    m.rename_files(False)

    check('影片目录(仅剩缩略图缓存)已删除', not os.path.exists(movie),
          f'仍存在: {movie}')
    check('上层分类目录也一并清理', not os.path.exists(cat), f'仍存在: {cat}')
    check('扫描根保留', os.path.isdir(root))
    check('影片本体已移走', os.path.exists(os.path.join(save, m.basename + '.mp4')))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

tmp = tempfile.mkdtemp(prefix='emptyeadir_')
try:
    # 群晖的 @eaDir(非隐藏, 但同为缩略图缓存) 也应被忽略
    root = os.path.join(tmp, 'scanroot')
    movie = os.path.join(root, 'TST-002')
    os.makedirs(movie)
    f = os.path.join(movie, 'TST-002.mp4')
    open(f, 'wb').write(b'x' * 100)
    os.makedirs(os.path.join(movie, '@eaDir'))
    open(os.path.join(movie, '@eaDir', 'TST-002_SES.jpg'), 'wb').write(b'x' * 10)
    save = os.path.join(root, 'out')
    os.makedirs(save, exist_ok=True)

    m = make_movie(root, [f])
    m.scan_root = root
    m.save_dir = save
    m.rename_files(False)

    check('含 @eaDir 的影片目录已删除', not os.path.exists(movie), f'仍存在: {movie}')
    check('扫描根保留', os.path.isdir(root))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

tmp = tempfile.mkdtemp(prefix='emptythumbkeep_')
try:
    # 关键反向: 目录里除缩略图缓存外还有**不会被移动的文件**时, 判定「有效非空」-> 必须保留。
    # 构造: 影片本体在 movie/ 下(会被移动), 另有一个 extra/ 子目录含未被移动的文件。
    root = os.path.join(tmp, 'scanroot')
    movie = os.path.join(root, 'TST-003')
    extra = os.path.join(movie, 'extra')        # 不在 self.files 里 -> 不会被移动
    os.makedirs(extra)
    keep_file = os.path.join(extra, 'note.txt')
    open(keep_file, 'w').write('x')
    os.makedirs(os.path.join(movie, '.@__thumb'))
    open(os.path.join(movie, '.@__thumb', 's100TST-003.mp4'), 'wb').write(b'x' * 25)
    src = os.path.join(movie, 'TST-003.mp4')
    open(src, 'wb').write(b'x' * 100)
    save = os.path.join(root, 'out')
    os.makedirs(save, exist_ok=True)

    m = make_movie(root, [src])
    m.scan_root = root
    m.save_dir = save
    m.rename_files(False)

    # movie 里剩 extra/(含 note.txt) + .@__thumb -> 「有效非空」必须保留
    check('有效非空的影片目录未被误删', os.path.isdir(movie), f'{movie} 不应被删')
    check('未被移动的真实文件仍在', os.path.exists(keep_file))
    check('影片本体已移到输出目录', os.path.exists(os.path.join(save, m.basename + '.mp4')))
    check('扫描根保留', os.path.isdir(root))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

tmp = tempfile.mkdtemp(prefix='emptydotgit_')
try:
    # .git / .Trash 这类隐藏目录仍必须被保护, 不能当缓存删掉
    root = os.path.join(tmp, 'scanroot')
    movie = os.path.join(root, 'TST-004')
    os.makedirs(movie)
    f = os.path.join(movie, 'TST-004.mp4')
    open(f, 'wb').write(b'x' * 100)
    os.makedirs(os.path.join(movie, '.Trash-1000'))
    open(os.path.join(movie, '.Trash-1000', 'x.bin'), 'wb').write(b'x' * 10)
    save = os.path.join(root, 'out')
    os.makedirs(save, exist_ok=True)

    m = make_movie(root, [f])
    m.scan_root = root
    m.save_dir = save
    m.rename_files(False)

    check('.Trash-1000 未被当作缓存删除', os.path.isdir(os.path.join(movie, '.Trash-1000')))
    check('含 .Trash 的影片目录被保留', os.path.isdir(movie))
    check('扫描根保留', os.path.isdir(root))
finally:
    shutil.rmtree(tmp, ignore_errors=True)


print('\n' + '=' * 60)
print(f'RESULT: {PASS} passed, {FAIL} failed')
print('=' * 60)
sys.exit(1 if FAIL else 0)
