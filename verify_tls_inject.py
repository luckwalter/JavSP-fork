"""验证 TLS 校验参数注入（v0.1.25）

## 背景

代理若做 TLS 解密(MITM), 目标站证书对该代理的 CA 而言是「未知签发者」, 默认校验必然
报 SSLCertVerificationError, 导致**所有**走代理的站点全挂。而本项目有 40+ 个请求点
(cloudscraper / requests.get/post/head / download / urlretrieve), 逐处加 `verify=` 易漏。

修法: 新增 `tls_verify()` 统一判定, 并在 `Request.__init__` 里用 `functools.partial`
**一次性绑定**到三个方法上。

本脚本验证:
1. `tls_verify()` 的三种取值语义(默认校验 / 自定义 CA 包 / 显式关闭);
2. **所有**请求点都带上了 verify 参数(源码扫描, 不抽样);
3. 未设环境变量时行为与改动前完全一致(默认校验开启) —— 不能为了修 MITM 而
   默认降低安全性;
4. 边界: CA 文件不存在时退回默认校验而非抛错。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = os.path.dirname(os.path.abspath(__file__))
BASE_PY = os.path.join(ROOT, 'javsp', 'web', 'base.py')

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


def test_tls_verify_semantics():
    from javsp.web import base
    section('A tls_verify() 三种取值')

    def _reset():
        """每个用例都从干净环境开始 —— 用例里会设置 JAVSP_CA_BUNDLE,
        若不在每个用例前清掉, 上一用例的临时文件路径会渗到下一用例造成假失败。"""
        os.environ.pop('JAVSP_CA_BUNDLE', None)
        os.environ.pop('JAVSP_TLS_VERIFY', None)

    import tempfile
    saved_b = os.environ.pop('JAVSP_CA_BUNDLE', None)
    saved_v = os.environ.pop('JAVSP_TLS_VERIFY', None)
    with tempfile.NamedTemporaryFile(suffix='.crt', delete=False) as f:
        f.write(b'-----BEGIN CERTIFICATE-----\n')
        ca = f.name
    try:
        # 1) 默认: 校验开启
        _reset()
        check('未设环境变量时返回 True(校验开启)',
              base.tls_verify() is True, repr(base.tls_verify()))

        # 2) 自定义 CA 包(存在的文件)
        _reset()
        os.environ['JAVSP_CA_BUNDLE'] = ca
        check('CA 文件存在时返回其路径', base.tls_verify() == ca, repr(base.tls_verify()))

        # 3) CA 文件不存在 -> 退回默认校验, 不抛错
        _reset()
        os.environ['JAVSP_CA_BUNDLE'] = '/no/such/ca.crt'
        check('CA 文件不存在时退回 True(不抛错)',
              base.tls_verify() is True, repr(base.tls_verify()))

        # 4) 显式关闭校验
        _reset()
        os.environ['JAVSP_TLS_VERIFY'] = '0'
        check('JAVSP_TLS_VERIFY=0 时返回 False', base.tls_verify() is False)
        _reset()
        os.environ['JAVSP_TLS_VERIFY'] = 'false'
        check('JAVSP_TLS_VERIFY=false 时返回 False', base.tls_verify() is False)
        _reset()
        os.environ['JAVSP_TLS_VERIFY'] = '1'
        check('JAVSP_TLS_VERIFY=1 时仍返回 True(不误关)',
              base.tls_verify() is True, repr(base.tls_verify()))

        # 5) CA 包优先于关闭开关(明确指定 CA 时不该被 VERIFY=0 覆盖成 False)
        _reset()
        os.environ['JAVSP_CA_BUNDLE'] = ca
        os.environ['JAVSP_TLS_VERIFY'] = '0'
        check('同时设置两者时 CA 包优先', base.tls_verify() == ca, repr(base.tls_verify()))

        # 6) 空白值不应被当成有效路径
        _reset()
        os.environ['JAVSP_CA_BUNDLE'] = '   '
        check('空白 CA 值被忽略(退回默认)', base.tls_verify() is True, repr(base.tls_verify()))
        _reset()
        os.environ['JAVSP_TLS_VERIFY'] = '   '
        check('空白 VERIFY 值被忽略(保持校验)',
              base.tls_verify() is True, repr(base.tls_verify()))
    finally:
        _reset()
        if saved_b is not None:
            os.environ['JAVSP_CA_BUNDLE'] = saved_b
        if saved_v is not None:
            os.environ['JAVSP_TLS_VERIFY'] = saved_v
        try:
            os.unlink(ca)
        except OSError:
            pass


def test_all_request_points_bound():
    section('B 所有请求点都带上 verify（源码扫描，不抽样）')
    src = open(BASE_PY, encoding='utf-8').read()

    # 1) Request.__init__ 里用 partial 统一绑定三个方法
    check('import partial', 'from functools import partial' in src)
    check('_verify = tls_verify() 已在 Request 内计算',
          re.search(r'_verify\s*=\s*tls_verify\(\)', src) is not None)
    for name in ('requests.get', 'requests.post', 'requests.head'):
        pat = rf'partial\(\s*{re.escape(name)}\s*,\s*verify=_verify\s*\)'
        check(f'{name} 用 partial 绑定 verify',
              re.search(pat, src) is not None, f'未找到 partial({name}, verify=_verify)')

    # 2) cloudscraper 分支也要绑(cloudscraper 走自己的 TLS 栈)
    n_scraper = len(re.findall(r'partial\(\s*self\.scraper\.\w+\s*,\s*verify=_verify\s*\)', src))
    check('cloudscraper 的 get/post/head 三处都绑定 verify', n_scraper == 3, f'实际 {n_scraper} 处')

    # 3) 模块级函数里的 requests 调用也要带 verify。
    # 逐个 `requests.xxx(` 起配对括号找完整调用(简单正则会在多行调用处截断, 造成误报)
    missing = []
    for m in re.finditer(r'\brequests\.(?:get|post)\s*\(', src):
        i = m.end() - 1
        depth = 0
        while i < len(src):
            if src[i] == '(':
                depth += 1
            elif src[i] == ')':
                depth -= 1
                if depth == 0:
                    break
            i += 1
        call = src[m.start():i + 1]
        if 'verify=tls_verify()' not in call:
            line = src[:m.start()].count('\n') + 1
            missing.append(f'line {line}: {call[:70]}')
    check('模块级 requests 调用全部带 verify', not missing, str(missing))

    # 4) 全项目不得出现裸 verify=False(那是我们刻意避免的降级)
    bad = []
    for root, _dirs, files in os.walk(os.path.join(ROOT, 'javsp')):
        for fn in files:
            if not fn.endswith('.py'):
                continue
            p = os.path.join(root, fn)
            t = open(p, encoding='utf-8').read()
            for m in re.finditer(r'verify\s*=\s*False', t):
                line = t[:m.start()].count('\n') + 1
                bad.append(f'{os.path.relpath(p, ROOT)}:{line}')
    check('全项目无硬编码 verify=False', not bad, str(bad))

    # 5) 除 4 处模块级调用外, 再确认 Request 内是 partial 绑定(而非裸 requests.get)
    n_verify = src.count('verify=tls_verify()')
    check('模块级调用点数量符合预期(4 处)', n_verify == 4, f'实际 {n_verify} 处')


def test_default_behavior_unchanged():
    section('C 默认行为与改动前一致（安全性未降低）')
    src = open(BASE_PY, encoding='utf-8').read()
    # tls_verify 在无环境变量时返回 True == requests 的默认 verify 行为
    check('默认分支显式 return True', '    return True' in src)
    check('默认分支不读 config.yml（无配置项被新增）',
          'JAVSP_CA_BUNDLE' not in open(os.path.join(ROOT, 'javsp', 'config.py'), encoding='utf-8').read(),
          '不应把证书配置写进用户 config.yml（属部署环境而非用户偏好）')


def test_runtime_binding():
    section('D 运行时真的把 verify 传下去了')
    # 实际构造 Request 并检查其绑定的可调用对象
    from javsp.web.base import Request
    import inspect
    r = Request(use_scraper=False)
    for attr in ('_Request__get', '_Request__post', '_Request__head'):
        fn = getattr(r, attr, None)
        ok = fn is not None and hasattr(fn, 'keywords') and 'verify' in fn.keywords
        check(f'{attr} 绑定了 verify', ok,
              f'类型={type(fn).__name__}')


if __name__ == '__main__':
    print('verify_tls_inject.py — TLS 校验参数注入验证')
    test_tls_verify_semantics()
    test_all_request_points_bound()
    test_default_behavior_unchanged()
    test_runtime_binding()
    print('\n' + '=' * 60)
    print(f'RESULT: PASS={PASS}  FAIL={FAIL}')
    if FAILURES:
        print('失败明细:')
        for f in FAILURES:
            print('  -', f)
    print('=' * 60)
    sys.exit(1 if FAIL else 0)
