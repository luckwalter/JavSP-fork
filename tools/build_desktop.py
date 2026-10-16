#!/usr/bin/env python3
"""JavSP 桌面端构建脚本：前端 build -> PyInstaller 打包 -> 压缩为可分发的 zip

用法:
    python tools/build_desktop.py                # 完整构建 (含前端 build)
    python tools/build_desktop.py --no-frontend  # 跳过前端 build (dist 已存在时)

产物: dist/JavSP/ 目录 -> dist/JavSP-<version>-windows.zip
前置: 需在已安装项目依赖的 Python 环境中运行 (pip install -e . pyinstaller)
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = os.path.join(ROOT, 'build', 'pyinstaller.spec')
DIST_DIR = os.path.join(ROOT, 'dist', 'JavSP')
FRONTEND_DIR = os.path.join(ROOT, 'frontend')


def read_version():
    with open(os.path.join(ROOT, 'pyproject.toml'), encoding='utf-8') as f:
        m = re.search(r'^version\s*=\s*["\']([^"\']+)', f.read(), re.M)
    if not m:
        raise RuntimeError('在 pyproject.toml 中未找到 version')
    return m.group(1)


def run(cmd, cwd=None):
    print('>>', ' '.join(cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


def build_frontend():
    if not shutil.which('npm'):
        raise RuntimeError('未找到 npm，无法构建前端；或改用 --no-frontend 并提供已构建的 frontend/dist')
    run(['npm', 'install'], cwd=FRONTEND_DIR)
    run(['npm', 'run', 'build'], cwd=FRONTEND_DIR)


def build_pyinstaller():
    run([sys.executable, '-m', 'PyInstaller', 'build/pyinstaller.spec', '--noconfirm', '--clean'], cwd=ROOT)


def package_zip(version):
    if not os.path.isdir(DIST_DIR):
        raise RuntimeError(f'未找到打包产物: {DIST_DIR}')
    zip_name = os.path.join(ROOT, 'dist', f'JavSP-{version}-windows.zip')
    if os.path.exists(zip_name):
        os.remove(zip_name)
    with zipfile.ZipFile(zip_name, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(DIST_DIR):
            for fn in files:
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, DIST_DIR)
                zf.write(full, rel)
    print(f'已生成分发包: {zip_name}')
    return zip_name


def main():
    ap = argparse.ArgumentParser(description='JavSP 桌面端构建')
    ap.add_argument('--no-frontend', action='store_true',
                    help='跳过前端 build (frontend/dist 已存在)')
    args = ap.parse_args()

    if not args.no_frontend:
        build_frontend()
    elif not os.path.isdir(os.path.join(FRONTEND_DIR, 'dist')):
        raise RuntimeError('frontend/dist 不存在，不能用 --no-frontend')

    build_pyinstaller()
    ver = read_version()
    zip_path = package_zip(ver)
    print('构建完成:', zip_path)


if __name__ == '__main__':
    main()
