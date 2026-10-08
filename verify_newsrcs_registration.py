"""验证 v0.2.1 新增源已正确注册进配置与爬虫体系

检查四个层面(缺一层就可能在真实运行时静默失效):
    1. CrawlerID 枚举含 javdbapi/javdatabase (javmenu 原本就有)
    2. config.yml 的 selection.normal 默认列表含三个新源
    3. 三模块可被 core 的动态导入机制成功 import(不抛异常)
    4. 每个模块都暴露可调用的 parse_data(movie), 且签名接受一个参数

运行: venv312 下 `python verify_newsrcs_registration.py`
"""
import inspect
import os
import sys

# 允许从仓库根直接运行
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

NEW_SOURCES = ['javdbapi', 'javdatabase', 'javmenu']
PASSED = []
FAILED = []


def _check(label, cond, detail=''):
    if cond:
        PASSED.append(label)
        print(f'  [OK ] {label}')
    else:
        FAILED.append(f'{label} -> {detail}')
        print(f'  [FAIL] {label}  {detail}')


def test_enum():
    print('\n===== 1. CrawlerID 枚举 =====')
    from javsp.config import CrawlerID
    for name in NEW_SOURCES:
        _check(f'CrawlerID.{name} 存在', hasattr(CrawlerID, name),
               '枚举缺该成员, core 遍历 selection 时会导入失败')
    _check('CrawlerID.javmenu 复用原名(未新增 javmenu_com 之类)',
           CrawlerID.javmenu.value == 'javmenu', CrawlerID.javmenu.value)


def test_default_selection():
    print('\n===== 2. config.yml 默认 selection =====')
    from javsp.config import Cfg
    normal = Cfg().crawler.selection.normal
    names = [c.value for c in normal]
    print(f'  normal = {names}')
    for name in NEW_SOURCES:
        _check(f'selection.normal 含 {name}', name in names, f'当前 {names}')
    # 老源应仍在(未被误删)
    for old in ('javdb', 'javbus'):
        _check(f'老源 {old} 仍在列表(未被误删)', old in names, f'当前 {names}')
    _check('javdbapi 排在 javmenu 之后(新源做兜底, 不抢老源优先位)',
           names.index('javdbapi') > names.index('javdb'), f'当前 {names}')


def test_import():
    print('\n===== 3. 动态导入 + parse_data 可调用 =====')
    for name in NEW_SOURCES:
        full = 'javsp.web.' + name
        try:
            mod = __import__(full, fromlist=['parse_data'])
            import_err = ''
        except Exception as e:
            _check(f'{full} 可导入', False, f'{type(e).__name__}: {e}')
            continue
        _check(f'{full} 可导入', True)
        fn = getattr(mod, 'parse_data', None)
        _check(f'{full}.parse_data 存在', callable(fn), '缺失则 core 取不到解析函数')
        if callable(fn):
            sig = list(inspect.signature(fn).parameters)
            _check(f'{full}.parse_data 接受 1 个 movie 参数', len(sig) == 1, f'签名={sig}')
    print('\n  模拟 core.import_crawlers 的导入路径:')
    from javsp.core import Cfg
    unknown = []
    for _, mods in Cfg().crawler.selection.items():
        for n in mods:
            try:
                __import__('javsp.web.' + n)
            except Exception as e:
                unknown.append(f'{n}({type(e).__name__})')
    _check('core 遍历全部 selection 时无导入失败', not unknown, f'失败: {unknown}')


def test_excluded():
    print('\n===== 4. 未移植源的显式记录 =====')
    # minnanoav 经 NAS 实测无 movie-by-code 能力, 不应出现在任何 selection 里
    from javsp.config import Cfg
    names = [c.value for _t, lst in Cfg().crawler.selection.items() for c in lst]
    _check('minnanoav 未被塞进 selection(实测无 movie-by-code, 强行适配会造垃圾数据)',
           'minnanoav' not in names, f'当前 {names}')


if __name__ == '__main__':
    test_enum()
    test_default_selection()
    test_import()
    test_excluded()
    print(f'\n{"=" * 50}')
    print(f'通过 {len(PASSED)} 项, 失败 {len(FAILED)} 项')
    for f in FAILED:
        print(f'  FAIL: {f}')
    print('新源注册验证全部通过 ✅' if not FAILED else '存在失败项 ❌')
    raise SystemExit(1 if FAILED else 0)
