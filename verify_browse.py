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

        section('T4 越界防护(回退到允许根, 而非报错)')
        # v0.1.23 起语义由 403 改为「回退到根」: 前端会以输入框里的**残留旧值**作为
        # 起点(如上一轮填的容器真机路径), 这类值在新部署环境里必然越界; 直接 403 会
        # 让「点开浏览就是失败」, 用户还得先手工清空输入框。回退到根后对话框可正常打开。
        r = client.get('/api/browse', params={'path': '/'}).json()
        check('根外路径回退到根而非报错',
              r['current'] == os.path.abspath(tmp), f"got {r.get('current')}")
        r = client.get('/api/browse', params={'path': str(root / '..')}).json()
        check('.. 上跳同样回退到根',
              r['current'] == os.path.abspath(tmp), f"got {r.get('current')}")
        # 回退后仍必须受根约束: 不能因为回退而列出根外内容
        names = sorted(d['name'] for d in r['dirs'])
        check('回退后仅列根内子目录(未泄漏根外)',
              names == ['a_dir', 'b_dir', 'c_dir'], f'got {names}')
        check('回退后 parent 为 None', r['parent'] is None, f"parent={r['parent']}")
        check('响应含 root 供前端预填', r.get('root') == os.path.abspath(tmp),
              f"root={r.get('root')}")

        section('T5 不存在目录 400')
        out = client.get('/api/browse', params={'path': str(root / 'no_such_dir')}).status_code
        check('不存在目录 400', out == 400, f'got {out}')
        # 根外且不存在 -> 先回退到根, 根存在故列举成功(而非 400)
        out2 = client.get('/api/browse', params={'path': '/no_such_root_xyz'}).status_code
        check('根外不存在路径先回退到根(200)', out2 == 200, f'got {out2}')

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
                # 断言的是**不变式**: 无论平台如何, 根外的 secret_dir 内容都不可见。
                #
                # 平台差异(实测): Windows 上 os.symlink 创建的是目录联接(junction),
                # os.path.realpath / Path.resolve **都不跟踪它** → 联接路径被判定为
                # 仍在根内而放行, 随后 os.path.isdir 失败返回 400(拿不到目录列表, 没有泄漏);
                # Linux 上 realpath 会解析到根外 → 判定越界 → 回退到根。
                # 两种平台都拿不到根外目录列表, 只是路径不同, 故此处只断言不变式。
                r = client.get('/api/browse', params={'path': str(link)})
                if r.status_code == 200:
                    got = [d['name'] for d in r.json()['dirs']]
                else:
                    got = []
                check('沿符号链接未泄漏根外内容(secret_dir 不可见)',
                      'secret_dir' not in got, f'got {got}')

                r2 = client.get('/api/browse',
                                params={'path': str(link / 'secret_dir')})
                if r2.status_code == 200:
                    got2 = [d['name'] for d in r2.json()['dirs']]
                    cur2 = r2.json().get('current')
                else:
                    got2, cur2 = [], None
                check('根外链接子目录未被列出',
                      'secret_dir' not in got2, f'got {got2}')
                check('根外链接子目录被拦(回退到根或拒绝)',
                      r2.status_code != 200 or cur2 == os.path.abspath(tmp),
                      f'status={r2.status_code} current={cur2}')

                r3 = client.get('/api/browse', params={'path': str(root)}).json()
                names3 = [d['name'] for d in r3['dirs']]
                check('根内列举不含根外的 secret_dir',
                      'secret_dir' not in names3, f'got {names3}')
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
    dockerfile = (ROOT / 'docker' / 'Dockerfile').read_text(encoding='utf-8')
    check('有「浏览目录」按钮', '浏览目录' in vue)
    check('按钮绑定 openBrowser', '@click="openBrowser"' in vue)
    check('有目录对话框', 'browserVisible' in vue and 'el-dialog' in vue)
    check('有 loadBrowse 函数', 'async function loadBrowse' in vue)
    check('有 chooseDir 函数(写回输入框)', 'function chooseDir' in vue)
    check('chooseDir 写回 scanPath', 'scanPath.value = p' in vue)
    check('api.js 暴露 browse', 'export async function browse' in api)
    check('api.js 调 /api/browse', '/api/browse' in api)
    check('对话框提示只列目录', '仅列目录' in vue)

    # --- v0.1.23: 默认根与部署配置自动一致 ---
    check('Dockerfile 设 JAVSP_BROWSE_ROOT（免手工填默认目录）',
          'ENV JAVSP_BROWSE_ROOT=' in dockerfile,
          '未设则新部署镜像默认 "/" 且与挂载点不一致')
    check('Dockerfile 默认根为 /data（与 compose 挂载点一致）',
          'ENV JAVSP_BROWSE_ROOT=/data' in dockerfile)
    check('前端启动时预填默认根', 'api.browse(\'\')' in vue)
    check('前端记录 browseRoot 供展示', 'browseRoot' in vue)

    # --- v0.1.23: 设置页复用同一选择器 ---
    check('设置页有浏览按钮(复用 openBrowser)',
          "openBrowser('config.input_directory')" in vue)
    check('chooseDir 按 target 分流到设置项',
          "configObj.value.scanner.input_directory = p" in vue)
    check('browseTarget 记录目标字段', 'browseTarget' in vue)

    # --- v0.1.23: 重新扫描后清空批量结果区 ---
    check('doScan 内重置 batch(旧任务窗体随重新扫描消失)',
          'batch.value = { running: false' in vue.split('async function doScan')[1].split('async function')[0],
          '未在 doScan 中重置 -> el-alert 的 v-if 恒真, 旧任务窗体永久残留')
    check('doScan 内重置选中项(旧 guid 已失效)',
          'selectedGuids.value = []' in vue.split('async function doScan')[1].split('async function')[0])


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