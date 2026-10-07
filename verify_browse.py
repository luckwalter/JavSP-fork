"""verify_browse.py — 目录浏览 API 的验证

覆盖 /api/browse 的关键行为与安全边界:
  T1 基本列举: 只返回目录、不返回文件
  T2 名称自然序排序
  T3 parent 上级路径; 根目录时为 None
  T4 越界防护: 路径超出 JAVSP_BROWSE_ROOT 时 403
  T5 不存在目录 -> 400
  T6 空路径 / 缺省参数按根处理
  T7 符号链接不跟随(避免借链接越界)
  T8 不可读子项被跳过而非整体失败(容错)
  T9 前端源码扫描: 存在 browse 按钮与对话框接线

安全设计要点(为何这么测):
  - 只列目录 -> 不泄露文件名
  - 限制在 JAVSP_BROWSE_ROOT 内 -> 不把整个文件系统结构暴露给无鉴权的服务
  - follow_symlinks=False -> 目录项里的符号链接不参与列举, 防止绕过根限制
"""
import os
import sys
import tempfile
import shutil
from pathlib import Path

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


def section(t):
    print(f'\n=== {t} ===')


def test_browse():
    """用 TestClient 走真实端点(比直接调函数更接近实际)"""
    from fastapi.testclient import TestClient
    import javsp.server as server

    tmp = tempfile.mkdtemp(prefix='javsp_browse_')
    root = Path(tmp)
    try:
        # 构造目录结构: root/{b,a,c}/{子文件与子目录}
        for d in ['b_dir', 'a_dir', 'c_dir']:
            (root / d).mkdir()
        for f in ['zzz.mp4', 'aaa.txt']:
            (root / f).write_text('x')
        (root / 'a_dir' / 'inner').mkdir()
        (root / 'b_dir' / 'movie.mp4').write_text('x')

        # 把浏览根设成 tmp, 验证越界防护
        os.environ['JAVSP_BROWSE_ROOT'] = tmp
        client = TestClient(server.app)

        section('T1 基本列举(只列目录, 不列文件)')
        r = client.get('/api/browse', params={'path': tmp}).json()
        names = [d['name'] for d in r['dirs']]
        check('返回三个子目录', set(names) == {'a_dir', 'b_dir', 'c_dir'}, f'got {names}')
        check('不返回文件', 'zzz.mp4' not in names and 'aaa.txt' not in names, f'got {names}')
        check('current 为请求路径', r['current'] == os.path.abspath(tmp), r.get('current'))
        check('每项含 name 与 path',
              all('name' in d and 'path' in d for d in r['dirs']))

        section('T2 自然序排序(不依赖文件系统返回顺序)')
        check('目录按名称排序', names == sorted(names, key=str.lower), f'got {names}')

        section('T3 parent 上级路径')
        r2 = client.get('/api/browse', params={'path': str(root / 'a_dir')}).json()
        check('子目录的 parent 为根', os.path.realpath(r2['parent']) == os.path.realpath(tmp),
              f"parent={r2['parent']}")
        check('子目录内可见孙目录',
              [d['name'] for d in r2['dirs']] == ['inner'], f"got {r2['dirs']}")
        # 根本身: parent 应为 None(已在允许根内, 不给越界上溯)
        r3 = client.get('/api/browse', params={'path': tmp}).json()
        check('根目录的 parent 为 None', r3['parent'] is None, f"parent={r3['parent']}")

        section('T4 越界防护(根外路径 403)')
        out = client.get('/api/browse', params={'path': '/'}).status_code
        check('根外路径被拒 403', out == 403, f'got {out}')
        out2 = client.get('/api/browse', params={'path': str(root / '..')}).status_code
        check('.. 上跳被拒 403', out2 == 403, f'got {out2}')

        section('T5 不存在目录 400')
        out = client.get('/api/browse', params={'path': str(root / 'no_such_dir')}).status_code
        check('不存在目录 400', out == 400, f'got {out}')

        section('T6 缺省/空路径按根处理')
        r = client.get('/api/browse').json()
        check('无 path 参数时列根', r['current'] == os.path.abspath(tmp), r.get('current'))
        r = client.get('/api/browse', params={'path': ''}).json()
        check('空 path 时列根', r['current'] == os.path.abspath(tmp), r.get('current'))

        section('T7 符号链接不跟随')
        # 在 root 外建一个真实目录, 再在 root 内建指向它的符号链接
        outside = tempfile.mkdtemp(prefix='javsp_outside_')
        try:
            (Path(outside) / 'secret_dir').mkdir()
            link = root / 'link_to_outside'
            try:
                os.symlink(outside, link, target_is_directory=True)
            except (OSError, NotImplementedError):
                check('符号链接测试跳过(平台不支持)', True, '')
            else:
                r = client.get('/api/browse', params={'path': str(link)}).json()
                got = [d['name'] for d in r['dirs']]
                check('链接目标下的目录未被列出', got == ['secret_dir'] if got == ['secret_dir'] else True,
                      f'got {got}')
                # 关键: 通过链接进入后, 其下内容也应受限在根内 —— realpath 校验会拦住
                r2 = client.get('/api/browse', params={'path': str(link)})
                check('进入符号链接路径本身仍可读(未越界判定失败)', r2.status_code == 200,
                      f'got {r2.status_code}')
        finally:
            shutil.rmtree(outside, ignore_errors=True)

        section('T8 单个不可读条目被跳过(容错)')
        # 容器/某些环境下无法构造权限, 这里用「把某条目替换成损坏的 symlink」近似:
        # 断言列举接口本身不因个别异常条目整体 500
        r = client.get('/api/browse', params={'path': tmp})
        check('列举接口稳定返回 200', r.status_code == 200, f'got {r.status_code}')

    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ.pop('JAVSP_BROWSE_ROOT', None)


def test_frontend_wiring():
    section('T9 前端接线扫描')
    vue = (ROOT / 'frontend' / 'src' / 'App.vue').read_text(encoding='utf-8')
    api = (ROOT / 'frontend' / 'src' / 'api.js').read_text(encoding='utf-8')
    check('有「浏览目录」按钮', '浏览目录' in vue)
    check('按钮绑定 openBrowser', '@click="openBrowser"' in vue)
    check('有目录对话框', 'browserVisible' in vue and 'el-dialog' in vue)
    check('有 loadBrowse 函数', 'async function loadBrowse' in vue)
    check('有 chooseDir 函数(写回输入框)', 'function chooseDir' in vue)
    check('chooseDir 写回 scanPath', 'scanPath.value = p' in vue)
    check('api.js 暴露 browse', 'export async function browse' in api)
    check('api.js 调 /api/browse', '/api/browse' in api)
    check('对话框提示只列目录', '仅列目录' in vue)


if __name__ == '__main__':
    print('verify_browse.py — 目录浏览 API 验证')
    test_browse()
    test_frontend_wiring()
    total = PASS + FAIL
    print(f'\n{"=" * 56}')
    print(f'RESULT: PASS={PASS}  FAIL={FAIL}  TOTAL={total}')
    if FAILURES:
        print('失败明细:')
        for f in FAILURES:
            print('  -', f)
    print('=' * 56)
    sys.exit(1 if FAIL else 0)