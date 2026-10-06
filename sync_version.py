"""发版同步工具：把 pyproject.toml 的 version（单一版本源）同步到各处引用

覆盖三处（此前只同步了 package.json，导致 README 徽章与 package-lock 长期漂移）：
  1. frontend/package.json        -> version
  2. frontend/package-lock.json   -> version 与 packages[""].version
  3. README.md                    -> 版本徽章 `badge/version-x.y.z-blue.svg` 与「当前版本：**x.y.z**」

背景：曾出现 pyproject 已是 0.1.8，而 README 徽章 / package-lock 仍停在 0.1.1。
本脚本把这三处纳入机械同步，避免靠人记忆。

注意：前端页面显示的版本号**不来自** package.json，而是运行时从后端 /api/health 拉取，
所以这里的同步属于工程卫生（便于打包/排查），不同步也不影响运行时正确性。

用法：
    python sync_version.py            # 执行同步（写入上述文件）
    python sync_version.py --check    # 只校验，有任何一处不一致则以退出码 1 报错（不写入）
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PYPROJECT = os.path.join(ROOT, 'pyproject.toml')
PKG_JSON = os.path.join(ROOT, 'frontend', 'package.json')
PKG_LOCK = os.path.join(ROOT, 'frontend', 'package-lock.json')
README = os.path.join(ROOT, 'README.md')

_VERSION_RE = re.compile(r'^version\s*=\s*["\']([^"\']+)["\']', re.M)
# README 中的版本徽章: ![Version](https://img.shields.io/badge/version-0.1.8-blue.svg)
_BADGE_RE = re.compile(r'(badge/version-)([0-9][0-9.]*)(-blue\.svg)')
# README 中的「当前版本：**0.1.8**」
_CURRENT_RE = re.compile(r'(当前版本：\*\*)([0-9][0-9.]*)(\*\*)')


def read_pyproject_version():
    with open(PYPROJECT, 'r', encoding='utf-8') as f:
        m = _VERSION_RE.search(f.read())
    if not m:
        raise RuntimeError(f'在 {PYPROJECT} 中未找到 version 字段')
    return m.group(1)


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
    if not badge and not cur:
        return None, '未找到版本徽章或「当前版本」标记'
    found = {badge.group(2) if badge else None, cur.group(2) if cur else None}
    got = found.pop() if len(found) == 1 else '/'.join(sorted(i for i in found if i))
    return got, None


def sync_readme(ver):
    # newline='' 关闭换行转换，保持仓库原有的 LF（否则整份 README 都会变成 CRLF 改动）
    with open(README, 'r', encoding='utf-8', newline='') as f:
        text = f.read()
    text = _BADGE_RE.sub(lambda m: m.group(1) + ver + m.group(3), text)
    text = _CURRENT_RE.sub(lambda m: m.group(1) + ver + m.group(3), text)
    with open(README, 'w', encoding='utf-8', newline='') as f:
        f.write(text)


TARGETS = [
    ('frontend/package.json', check_package_json, sync_package_json),
    ('frontend/package-lock.json', check_package_lock, sync_package_lock),
    ('README.md', check_readme, sync_readme),
]


def main():
    check_only = '--check' in sys.argv
    ver = read_pyproject_version()
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
