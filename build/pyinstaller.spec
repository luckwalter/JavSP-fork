# -*- mode: python ; coding: utf-8 -*-
"""JavSP 桌面端 PyInstaller 构建定义 (onedir 形态, 随后压缩为 zip 分发)

产出: dist/JavSP/ 目录 (含 JavSP.exe 与所有依赖), 整体压缩为
      JavSP-<version>-windows.zip 即可直接分发给 Windows 用户 (exe 同级便携)。

形态: 默认 pywebview 原生窗口壳 (C/S); 加 --nogui 改用浏览器 (B/S)。
      两种形态数据均落在 exe 同级, 解压即用。

运行前需先构建前端:
    cd frontend && npm install && npm run build && cd ..
"""
import os
from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.dirname(os.path.abspath(SPECPATH))

# 爬虫子模块为运行时动态 import, PyInstaller 无法静态收集, 需显式列出
# uvicorn 在 desktop.py 函数体内部 import (第49行), 静态分析抓不到, 必须显式收集
hiddenimports = (
    collect_submodules('javsp.web')
    + collect_submodules('uvicorn')   # 桌面版 server 启动按需 import, 含全部子模块
    + ['webview',                     # pywebview (默认 GUI 形态按需 import)
       'javsp.server']                # server 模块经函数内 import 引入, 显式列出防漏收集
)

a = Analysis(
    [os.path.join(ROOT, 'javsp', 'desktop.py')],
    pathex=[ROOT],
    binaries=[],
    # 资源真实位于「项目根」的 image/ data/ 目录 (resource_path 以项目根为基准解析)
    datas=[
        (os.path.join(ROOT, 'frontend', 'dist'), 'frontend/dist'),
        (os.path.join(ROOT, 'config.yml'), '.'),
        (os.path.join(ROOT, 'pyproject.toml'), '.'),
        (os.path.join(ROOT, 'image'), 'image'),
        (os.path.join(ROOT, 'data'), 'data'),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'unittest', 'pydoc', 'doctest', 'pip', 'setuptools',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='JavSP',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,            # 保留控制台日志窗口, 便于排查爬虫/代理问题, 也提供 Ctrl+C 退出入口
    icon=os.path.join(ROOT, 'image', 'JavSP.ico'),
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='JavSP',
)
