"""守护 App.vue 的 crawlerSites 与 javsp.config.CrawlerID 枚举保持一致

背景: 设置页的源清单是枚举的**手工副本**(前端无法 import python 枚举), 天然会漂移。
实测 v0.2.1 就漏过 javdbapi/javdatabase —— 后端已注册, 设置页却勾不到。
本脚本从真实源码双向解析并断言一致, 漂移即失败。

运行: venv312 下 `python verify_crawler_sites_sync.py`
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

APP_VUE = os.path.join(HERE, 'frontend', 'src', 'App.vue')
PASSED = []
FAILED = []


def _check(label, cond, detail=''):
    if cond:
        PASSED.append(label)
        print(f'  [OK ] {label}')
    else:
        FAILED.append(f'{label} -> {detail}')
        print(f'  [FAIL] {label}  {detail}')


def get_enum_ids():
    """从 config.py 真实源码解析 CrawlerID 成员(不依赖运行时, 与前端解析方式对称)"""
    with open(os.path.join(HERE, 'javsp', 'config.py'), encoding='utf-8') as f:
        src = f.read()
    m = re.search(r'class CrawlerID\(str, Enum\):(.*?)(?=\n\nclass |\n\ndef )', src, re.S)
    if not m:
        raise AssertionError('未能在 config.py 中定位 CrawlerID 枚举')
    return set(re.findall(r'^\s{4}(\w+)\s*=\s*[\'"]', m.group(1), re.M))


def get_frontend_ids():
    """从 App.vue 真实源码解析 crawlerSites 数组(排除注释行)"""
    with open(APP_VUE, encoding='utf-8') as f:
        src = f.read()
    m = re.search(r'const crawlerSites\s*=\s*\[(.*?)\]', src, re.S)
    if not m:
        raise AssertionError('未能在 App.vue 中定位 crawlerSites')
    body = '\n'.join(line for line in m.group(1).splitlines()
                     if not line.strip().startswith('//'))
    return set(re.findall(r'[\'"]([\w]+)[\'"]', body))


def main():
    enum_ids = get_enum_ids()
    fe_ids = get_frontend_ids()
    print(f'\nCrawlerID 枚举({len(enum_ids)}): {sorted(enum_ids)}')
    print(f'App.vue 清单({len(fe_ids)}): {sorted(fe_ids)}\n')

    missing = enum_ids - fe_ids
    extra = fe_ids - enum_ids
    _check('前端未漏源(枚举 ⊆ 前端)', not missing, f'前端缺少: {sorted(missing)}')
    _check('前端无幽灵源(前端 ⊆ 枚举)', not extra, f'前端多出: {sorted(extra)}')
    _check('两处数量一致', len(enum_ids) == len(fe_ids),
           f'枚举 {len(enum_ids)} vs 前端 {len(fe_ids)}')

    # 反向校验: 解析结果必须等于运行时枚举, 防止正则解析漂移
    from javsp.config import CrawlerID
    runtime = {c.value for c in CrawlerID}
    _check('源码解析结果与运行时枚举一致', runtime == enum_ids,
           f'运行时多出 {sorted(runtime - enum_ids)}, 源码多出 {sorted(enum_ids - runtime)}')

    print(f'\n{"=" * 50}')
    print(f'通过 {len(PASSED)} 项, 失败 {len(FAILED)} 项')
    for f in FAILED:
        print(f'  FAIL: {f}')
    print('前端源清单与枚举一致 ✅' if not FAILED else '存在漂移 ❌')
    raise SystemExit(1 if FAILED else 0)


if __name__ == '__main__':
    main()
