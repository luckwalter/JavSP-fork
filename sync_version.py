"""发版同步工具：把 pyproject.toml 的 version（单一版本源）同步到各处引用

覆盖三处（此前只同步了 package.json，导致 README 徽章与 package-lock 长期漂移）：
  1. frontend/package.json        -> version
  2. frontend/package-lock.json   -> version 与 packages[""].version
  3. README.md                    -> 版本徽章 `badge/version-x.y.z-blue.svg` 与「当前版本：**x.y.z**」

背景：曾出现 pyproject 已是 0.1.8，而 README 徽章 / package-lock 仍停在 0.1.1。
本脚本把这三处纳入机械同步，避免靠人记忆。

==== 跨环境同步(2026-11 新增) ====
**新增 `--sync-target <path>`**: 把某个目标环境里的 pyproject.toml 也对齐到当前版本。
起因: 2026-10-11 实测踩坑 —— 部署脚本只 `docker cp` 了 *.py, **漏掉 pyproject.toml**,
容器里的版本因此长期停在 0.2.9(界面右下角显示旧版本), 而仓库已是 0.2.11。
这类漂移不会被本脚本默认检查发现, 所以提供显式的跨环境同步入口。

用法:
    python sync_version.py                     # 执行同步（写入上述文件）
    python sync_version.py --check             # 只校验，有任何一处不一致则以退出码 1 报错（不写入）
    python sync_version.py --check-target <path>   # 校验目标环境的 pyproject.toml
    python sync_version.py --sync-target <path>    # 把目标环境的 pyproject.toml 对齐到当前版本
"""
import argparse
import json
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PYPROJECT = os.path.join(ROOT, 'pyproject.toml')
PKG_JSON = os.path.join(ROOT, 'frontend', 'package.json')
PKG_LOCK = os.path.join(ROOT, 'frontend', 'package-lock.json')
README = os.path.join(ROOT, 'README.md')

_VERSION_RE = re.compile(r'^version\s*=\s*["\']([^"\']+)["\']', re.M)
# README 中的版本徽章: ![Version](https://img.shields.io/badge/version-0.1.8-blue.svg)
_BADGE_RE = re.compile(r'(badge/version-)([0-9][0-9.]*)(-blue\.svg)')
# README 中的「当前版本」标记。兼容两种写法:
#   `当前版本：**0.3.3**` 与 `**当前版本：0.3.3**`
# (0.3.2 起 README 实际是后者, 旧正则只匹配前者 —— 静默失配导致该处从 0.3.2
#  起漂移, 且 check 被徽章的值洗白成 OK, 2026-10-17 全库版本审查发现)
_CURRENT_RE = re.compile(r'((?:\*\*)?当前版本[：:]\s*(?:\*\*)?)([0-9][0-9.]*)')


def read_pyproject_version():
    with open(PYPROJECT, 'r', encoding='utf-8') as f:
        m = _VERSION_RE.search(f.read())
    if not m:
        raise RuntimeError(f'在 {PYPROJECT} 中未找到 version 字段')
    return m.group(1)


def sync_target_env(path: str, ver: str, dry_run: bool) -> int:
    """校验/同步目标环境的 pyproject.toml 版本

    path 可以是 pyproject.toml 文件本身, 也可以是包含它的目录。
    """
    target = path if path.endswith('.toml') else os.path.join(path, 'pyproject.toml')
    if not os.path.isfile(target):
        print(f'FAIL 目标环境不存在: {target}')
        return 2
    with open(target, 'r', encoding='utf-8', newline='') as f:
        text = f.read()
    m = _VERSION_RE.search(text)
    got = m.group(1) if m else '(未找到 version 字段)'
    if got == ver:
        print(f'OK   目标环境 {target} = {ver}')
        return 0
    print(f'OUT  目标环境 {target} = {got} (仓库为 {ver})')
    if dry_run:
        return 1
    # 只替换 version 行, 保留其余内容与原换行符
    new_text, n = _VERSION_RE.subn(lambda mm: f'version = "{ver}"', text, count=1)
    if n != 1:
        print(f'FAIL 未能定位 version 行, 请人工检查 {target}')
        return 2
    shutil.copyfile(target, target + '.bak.syncver')
    with open(target, 'w', encoding='utf-8', newline='') as f:
        f.write(new_text)
    print(f'DONE 目标环境 {target} -> {ver} (已备份 {os.path.basename(target)}.bak.syncver)')
    return 0


def _detect_newline(path, default='\n'):
    """探测文件当前使用的换行符

    Windows 上以默认文本模式写文件会把 LF 一律转成 CRLF；仓库里这些文件原本是 LF，
    一次同步就会让 README / package-lock 产生「整文件重写」的 diff 噪声，真实改动被淹没。
    故写入前探测原换行符并原样保留。
    """
    try:
        with open(path, 'rb') as f:
            head = f.read(65536)
    except OSError:
        return default
    crlf = head.count(b'\r\n')
    lf = head.count(b'\n') - crlf
    return '\r\n' if crlf > lf else '\n'


def _dump_json(path, data):
    nl = _detect_newline(path)
    text = json.dumps(data, indent=2, ensure_ascii=False) + '\n'
    if nl != '\n':
        text = text.replace('\n', nl)
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(text)


def check_package_json(ver):
    if not os.path.isfile(PKG_JSON):
        return None, '文件不存在'
    with open(PKG_JSON, 'r', encoding='utf-8') as f:
        return json.load(f).get('version'), None


def sync_package_json(ver):
    with open(PKG_JSON, 'r', encoding='utf-8') as f:
        data = json.load(f)
    data['version'] = ver
    _dump_json(PKG_JSON, data)


def check_package_lock(ver):
    if not os.path.isfile(PKG_LOCK):
        return None, '文件不存在'
    with open(PKG_LOCK, 'r', encoding='utf-8') as f:
        data = json.load(f)
    root_v = data.get('version')
    pkg_v = (data.get('packages', {}).get('', {}) or {}).get('version')
    # 两处必须一致且都等于目标版本
    return (root_v if root_v == pkg_v == ver else f'{root_v}/{pkg_v}'), None


def sync_package_lock(ver):
    with open(PKG_LOCK, 'r', encoding='utf-8') as f:
        data = json.load(f)
    data['version'] = ver
    if '' in data.get('packages', {}):
        data['packages']['']['version'] = ver
    _dump_json(PKG_LOCK, data)


def check_readme(ver):
    if not os.path.isfile(README):
        return None, '文件不存在'
    with open(README, 'r', encoding='utf-8') as f:
        text = f.read()
    badge = _BADGE_RE.search(text)
    cur = _CURRENT_RE.search(text)
    # 两个标记都必须存在: 任一缺失都说明同步面破损(如 README 改了写法),
    # 必须显式报错。旧实现把未匹配的标记过滤成 None 后「有一处就放行」,
    # 导致「当前版本」处从 0.3.2 起静默漂移而 check 仍报 OK(2026-10-17 审查)
    if not badge:
        return None, '未找到版本徽章(badge/version-x.y.z-blue.svg), README 写法可能已变'
    if not cur:
        return None, '未找到「当前版本」标记, README 写法可能已变'
    if badge.group(2) != cur.group(2):
        # 两处不一致: 如实回报, 让 sync 修复
        return f'{badge.group(2)}/{cur.group(2)}', None
    return badge.group(2), None


def sync_readme(ver):
    # newline='' 关闭换行转换，保持仓库原有的 LF（否则整份 README 都会变成 CRLF 改动）
    with open(README, 'r', encoding='utf-8', newline='') as f:
        text = f.read()
    text = _BADGE_RE.sub(lambda m: m.group(1) + ver + m.group(3), text)
    text = _CURRENT_RE.sub(lambda m: m.group(1) + ver, text)
    with open(README, 'w', encoding='utf-8', newline='') as f:
        f.write(text)


TARGETS = [
    ('frontend/package.json', check_package_json, sync_package_json),
    ('frontend/package-lock.json', check_package_lock, sync_package_lock),
    ('README.md', check_readme, sync_readme),
]


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument('--check', action='store_true',
                    help='只校验仓库内各文件，有不一致则以退出码 1 报错（不写入）')
    ap.add_argument('--check-target', metavar='PATH',
                    help='只校验目标环境（目录或 pyproject.toml 路径）的版本')
    ap.add_argument('--sync-target', metavar='PATH',
                    help='把目标环境的 pyproject.toml 对齐到当前版本')
    args = ap.parse_args()

    ver = read_pyproject_version()

    # 跨环境模式：只处理目标环境，不做仓库内同步
    if args.check_target or args.sync_target:
        path = args.sync_target or args.check_target
        return sync_target_env(path, ver, dry_run=bool(args.check_target))

    check_only = args.check
    print(f'单一版本源 pyproject.toml = {ver}')

    stale = []
    for label, checker, _sync in TARGETS:
        got, err = checker(ver)
        if err:
            print(f'SKIP {label}: {err}')
            continue
        if got == ver:
            print(f'OK   {label} = {ver}')
        else:
            print(f'OUT  {label} = {got}')
            stale.append(label)

    if not stale:
        print(f'全部同步：{ver}')
        return 0

    if check_only:
        print(f'FAIL 未同步: {", ".join(stale)}')
        return 1

    for label, _checker, syncer in TARGETS:
        if label in stale:
            syncer(ver)
            print(f'DONE {label} -> {ver}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
