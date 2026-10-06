"""把 pyproject.toml 的 version(单一版本源)同步到 frontend/package.json

背景: 前端 package.json 的 version 曾长期停留在 0.1.1, 与后端版本漂移。
本脚本把 pyproject.toml 的 version 写入 frontend/package.json, 保证发版时两边一致。

注意: 前端页面显示的版本号**不来自** package.json, 而是启动时从后端 /api/health 拉取,
所以 package.json 的版本只是工程卫生(便于打包/排查), 不同步也不影响运行时正确性。

用法:
    python sync_version.py            # 执行同步(写入 frontend/package.json)
    python sync_version.py --check    # 只校验是否已同步, 不同步则以退出码 1 报错(不写入)
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PYPROJECT = os.path.join(ROOT, 'pyproject.toml')
PKG_JSON = os.path.join(ROOT, 'frontend', 'package.json')

_VERSION_RE = re.compile(r'^version\s*=\s*["\']([^"\']+)["\']', re.M)


def read_pyproject_version():
    with open(PYPROJECT, 'r', encoding='utf-8') as f:
        m = _VERSION_RE.search(f.read())
    if not m:
        raise RuntimeError(f'在 {PYPROJECT} 中未找到 version 字段')
    return m.group(1)


def read_package_version():
    if not os.path.isfile(PKG_JSON):
        return None
    with open(PKG_JSON, 'r', encoding='utf-8') as f:
        return json.load(f).get('version')


def write_package_version(ver):
    with open(PKG_JSON, 'r', encoding='utf-8') as f:
        data = json.load(f)
    data['version'] = ver
    with open(PKG_JSON, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write('\n')


def main():
    check_only = '--check' in sys.argv
    pyv = read_pyproject_version()
    fev = read_package_version()

    if pyv == fev:
        print(f'OK  版本已同步: pyproject.toml = frontend/package.json = {pyv}')
        return 0

    if check_only:
        print(f'FAIL 版本未同步: pyproject.toml = {pyv}, frontend/package.json = {fev}')
        return 1

    write_package_version(pyv)
    print(f'DONE 已同步: frontend/package.json {fev} -> {pyv}(源: pyproject.toml)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
