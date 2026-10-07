"""verify_path_traversal.py — 整理落盘路径穿越防御验证（P2-6）

威胁模型: 影片元数据（番号 / 女优名 / 标题）来自**外部站点爬虫**，属不可信输入。
若这些字段未经净化就进入输出目录模板（output_folder_pattern，形如
`#整理完成/{actress}/...`）或文件名模板，落盘时就可能通过 `../` 逃出扫描根目录，
把NFO / 封面 / 甚至影片本身写到扫描根之外 —— 在 NAS 上意味着可覆盖共享目录里的
任意文件。

本脚本分两部分：
  A. 纯函数层——直接对 `replace_illegal_chars`喂恶意载荷，验证净化是否彻底。
  B. 端到端层——构造带恶意外部数据的 Movie，跑真实 `generate_names` +
     `organize_movie`，断言所有落盘路径都在扫描根内（不联网、封面/NFO 用桩函数）。

设计取自项目既有教训：外网输入一律视为不可信，且断言必须能真正失败
（不能用 `or True` 恒真式）。
"""
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

PASS = 0
FAIL = 0
FAILURES = []


def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  [PASS] {name}')
    else:
        FAIL += 1
        FAILURES.append(f'{name} {detail}')
        print(f'  [FAIL] {name} {detail}')


def section(title):
    print(f'\n=== {title} ===')


#恶意载荷库：覆盖各种平台与绕过手法
PAYLOADS = [
    ('..', '纯父目录'),
    ('../', '父目录+分隔符'),
    ('../../../../../../etc', '多层上跳'),
    ('..\\..\\windows', 'Windows 反斜杠上跳'),
    ('....//....//etc', '多点绕过(>2)'),
    ('./../../x', '带当前目录的上跳'),
    ('foo/../../bar', '中间夹带'),
    ('..;/', '分号结尾'),
    ('%2e%2e/', 'URL 编码(信息: Python 不会解码, 属无效载荷但需确认无害)'),
    ('a/b', '单分隔符'),
    ('a\\b', '单反斜杠'),
    ('/etc/passwd', '绝对路径'),
    ('C:\\Windows\\System32', 'Windows 绝对路径'),
    ('\\x00', 'NUL 字节'),
]


def test_replace_illegal_chars():
    from javsp.file import replace_illegal_chars

    section('A1 分隔符全部被中和(任何平台)')
    for payload, desc in PAYLOADS:
        out = replace_illegal_chars(payload)
        has_sep = ('/' in out) or ('\\' in out)
        check(f'分隔符已替换: {desc}', not has_sep,
              f'payload={payload!r} out={out!r} 仍含分隔符')

    section('A2 父目录标记(..)被消解')
    for payload, desc in PAYLOADS:
        if '..' not in payload and '....' not in payload:
            continue
        out = replace_illegal_chars(payload)
        check(f'无连续英文点: {desc}', '..' not in out,
              f'payload={payload!r} out={out!r} 仍含 ..')

    section('A3 净化后不含任何路径分隔符或上跳结构(全载荷)')
    for payload, desc in PAYLOADS:
        out = replace_illegal_chars(payload)
        bad = [t for t in ('/', '\\') if t in out]
        if '..' in out:
            bad.append('..')
        check(f'净化彻底: {desc}', not bad, f'out={out!r} 残留={bad}')


def _in_root(root: str, path: str) -> bool:
    """path 是否位于 root 之内（含相等）——用 realpath 消解符号链接后比较"""
    try:
        rp = os.path.realpath(path)
        rr = os.path.realpath(root)
        return rp == rr or rp.startswith(rr + os.sep)
    except Exception:      # noqa: BLE001
        return False


def test_end_to_end():
    from javsp.config import Cfg
    from javsp.datatype import Movie, MovieInfo
    from javsp.core import generate_names, organize_movie

    # 桩掉会联网/下载的部分：封面与剧照下载
    def fake_download_cover(*a, **k):
        return None
    def fake_process_poster(*a, **k):
        return {'engine': 'default', 'applied': False, 'reason': 'test'}
    def fake_write_nfo(*a, **k):
        return None

    payloads = [
        '../../../../ESCAPED',
        '..\\..\\ESCAPED',
        'ACCT/../../../../ESCAPED',
        'x/../../ESCAPED',
    ]

    for payload in payloads:
        section(f'B 端到端 落盘边界: {payload!r}')
        tmp = tempfile.mkdtemp(prefix='javsp_traverse_')
        # 造一个够大的假影片文件，让 scan/organize 能识别（organize 依赖 movie.files）
        fpath = Path(tmp) / 'ESCAPED-100.mp4'
        try:
            fpath.write_bytes(b'\x00' * 4096)
        except OSError:
            shutil.rmtree(tmp, ignore_errors=True)
            continue
        try:
            # 真实影片对象：番号用恶意载荷，标题/女优也带载荷
            movie = Movie('ESCAPED-100')
            movie.files = [str(fpath)]
            movie.scan_root = tmp
            info = MovieInfo('ESCAPED-100')
            info.title = payload
            info.rawtitle = payload
            info.actress = [payload]
            info.plot = payload
            info.label = payload
            info.nfo_title = payload
            # covers / big_covers 由爬虫运行时附加（MovieInfo.__init__ 只声明核心字段），
            # organize_movie 会读它们；这里给空列表并靠桩 download_cover 短路，避免联网。
            info.covers = []
            info.big_covers = []
            movie.info = info

            with mock.patch('javsp.core.download_cover', fake_download_cover), \
                 mock.patch('javsp.core.process_poster', fake_process_poster), \
                 mock.patch('javsp.core.write_nfo', fake_write_nfo):
                generate_names(movie)
                result = organize_movie(movie)

            # 关键断言：所有落盘路径必须在扫描根内
            for attr in ('save_dir', 'nfo_file', 'fanart_file', 'poster_file'):
                p = getattr(movie, attr, None)
                if not p:
                    continue
                check(f'{attr} 未逃出扫描根', _in_root(tmp, str(p)),
                      f'{attr}={p}')
            # 结果里的路径同样要查
            for key in ('nfo_file', 'fanart_file', 'poster_file'):
                p = (result or {}).get(key)
                if not p:
                    continue
                check(f'result.{key} 未逃出扫描根', _in_root(tmp, str(p)),
                      f'{key}={p}')
            # 并且：外部路径上不该真的产生文件
            check('扫描根之外未创建 ESCAPED 目录',
                  not any('ESCAPED' in p for p in
                          os.listdir(os.path.dirname(tmp))),
                  '逃逸目录被发现')
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def test_scan_root_missing_stays_relative():
    """回归守卫：_root_save_dir 的边界收敛语义

    - 无 scan_root（CLI 旧路径）：相对路径原样返回，交给 CLI 的 os.chdir 解析 → 语义不变
    - 有 scan_root：相对路径锚定到扫描根
    - 越界收敛：无论绝对路径还是带.. 的相对路径，都不允许逃出扫描根
      （绝对路径配置同样受约束—— 否则无鉴权的 PUT /api/config 就能写到任意位置）
    """
    from javsp.core import _root_save_dir
    from javsp.datatype import Movie
    import tempfile, shutil

    section('C _root_save_dir 语义回归')
    m = Movie('ABC-123')
    # 无 scan_root → 原样返回（CLI 依赖 os.chdir 解析，不干预）
    out = _root_save_dir('#整理完成/x', m)
    check('无 scan_root 时保持相对', out == '#整理完成/x', f'got {out!r}')
    # 无 scan_root + 绝对路径 → 同样不干预（CLI 显式指定时用户自己负责）
    out = _root_save_dir('/abs/target', m)
    check('无 scan_root 时绝对路径不干预', out == '/abs/target', f'got {out!r}')

    # 以下均有 scan_root，需收敛
    tmp = tempfile.mkdtemp(prefix='javsp_root_')
    try:
        m.scan_root = tmp
        out = _root_save_dir('#整理完成/x', m)
        check('有 scan_root 时锚定',
              out == os.path.join(tmp, '#整理完成/x'), f'got {out!r}')

        out = _root_save_dir('../evil', m)
        check('相对路径带 .. 被收敛回根内',
              os.path.realpath(out).startswith(os.path.realpath(tmp) + os.sep)
              or os.path.realpath(out) == os.path.realpath(tmp),
              f'逃逸了: {out!r}')

        out = _root_save_dir(os.path.join(tmp, '..', '..', 'evil2'), m)
        check('绝对路径含 .. 被收敛回根内',
              os.path.realpath(out).startswith(os.path.realpath(tmp) + os.sep)
              or os.path.realpath(out) == os.path.realpath(tmp),
              f'逃逸了: {out!r}')

        # 完全无关的绝对路径 → 收敛到扫描根下的 basename
        out = _root_save_dir('/etc/cron.d/x', m)
        check('无关绝对路径被收敛到根内',
              os.path.realpath(out).startswith(os.path.realpath(tmp) + os.sep),
              f'逃逸了: {out!r}')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    print('verify_path_traversal.py — 整理落盘路径穿越防御验证')
    test_replace_illegal_chars()
    test_end_to_end()
    test_scan_root_missing_stays_relative()
    total = PASS + FAIL
    print(f'\n{"=" * 56}')
    print(f'RESULT: PASS={PASS}  FAIL={FAIL}  TOTAL={total}')
    if FAILURES:
        print('失败明细:')
        for f in FAILURES:
            print('  -', f)
    print('=' * 56)
    sys.exit(1 if FAIL else 0)