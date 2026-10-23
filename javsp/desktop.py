"""JavSP 桌面端入口 (PyWebView 窗口壳 + 自动开浏览器 双形态)

将 FastAPI 后端与前端打包为单机桌面应用, 提供两种使用形态:
- 默认 (C/S / GUI): 用 pywebview 开一个原生窗口壳加载 http://127.0.0.1:8000, 关闭窗口即退出
- --nogui (B/S): 改用默认浏览器打开 WebUI, 关闭命令行窗口即退出

打包 (PyInstaller, 产出 onedir 目录, 随后压缩为 zip 直接分发):
    cd frontend && npm install && npm run build && cd ..
    pyinstaller build/pyinstaller.spec
    # 产物在 dist/JavSP/, 整体压缩为 JavSP-<version>-windows.zip 即可

数据目录: 全部落在可执行文件同级 (便携版, 解压即用, 不写系统目录)。
"""
import os
import sys
import time
import threading
import logging
import webbrowser
import urllib.request

logger = logging.getLogger('javsp.desktop')


def _resolve_host_port():
    host = os.getenv('JAVSP_HOST', '127.0.0.1')
    try:
        port = int(os.getenv('JAVSP_PORT', '8000'))
    except ValueError:
        port = 8000
    return host, port


def _wait_until_ready(url: str, timeout: float = 25.0) -> bool:
    """轮询 /api/health, 直到返回 200 或服务启动超时"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


def _run_server(host: str, port: int):
    import uvicorn
    from javsp.server import app
    config = uvicorn.Config(app, host=host, port=port, log_level='info', access_log=False)
    server = uvicorn.Server(config)
    server.run()


def _configure_logging():
    """frozen 下把日志同时落盘到 exe 同级 JavSP.log, 方便无控制台时排查"""
    if getattr(sys, 'frozen', False):
        log_path = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), 'JavSP.log')
        handler = logging.FileHandler(log_path, encoding='utf-8')
        handler.setFormatter(logging.Formatter(
            '%(asctime)s [%(levelname)s] %(name)s: %(message)s'))
        root = logging.getLogger()
        if not any(getattr(h, 'baseFilename', '') == log_path for h in root.handlers):
            root.addHandler(handler)
            root.setLevel(logging.INFO)


def _webview2_version() -> str:
    """检测 Microsoft Edge WebView2 Runtime 是否安装, 返回版本号; 未装返回空串"""
    if not sys.platform.startswith('win'):
        return 'n/a'
    import winreg
    key = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'  # WebView2 Runtime 客户端 GUID
    paths = (
        (winreg.HKEY_CURRENT_USER, rf'SOFTWARE\Microsoft\EdgeUpdate\Clients\{key}'),
        (winreg.HKEY_LOCAL_MACHINE, rf'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{key}'),
        (winreg.HKEY_LOCAL_MACHINE, rf'SOFTWARE\Microsoft\EdgeUpdate\Clients\{key}'),
    )
    for hive, path in paths:
        try:
            with winreg.OpenKey(hive, path) as k:
                pv, _ = winreg.QueryValueEx(k, 'pv')
                if pv:
                    return str(pv)
        except Exception:
            continue
    return ''


def _netfx_release() -> int:
    """检测 .NET Framework 4.x 的 Release 号; 未找到返回 0"""
    if not sys.platform.startswith('win'):
        return 0
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r'SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full') as k:
            v, _ = winreg.QueryValueEx(k, 'Release')
            return int(v)
    except Exception:
        return 0


def _check_gui_env():
    """检查窗口模式所需的浏览器内核环境, 返回 (是否可用, 失败原因)。

    pywebview 在缺少 WebView2 Runtime 或 .NET Framework 过旧时, 会**静默**回退到
    IE 内核 (MSHTML)。我们的前端是 Vue3/ES2020 产物, 在 IE 下必然白屏,
    用户只能看到一个空白窗口。因此这里提前拦住并给出明确原因。
    """
    if not sys.platform.startswith('win'):
        return True, ''
    wv2 = _webview2_version()
    if not wv2:
        return False, ('未检测到 Microsoft Edge WebView2 Runtime。\n\n'
                       '缺少该运行时时窗口内核会退化为 IE, 导致界面空白。\n'
                       '请安装: https://developer.microsoft.com/microsoft-edge/webview2/\n'
                       '或改用浏览器模式 (命令行加 --nogui)。')
    release = _netfx_release()
    if release and release < 461808:  # .NET Framework 4.7.2
        return False, (f'.NET Framework 版本过低 (Release {release}, 需要 4.7.2 及以上)。\n\n'
                       '请升级 .NET Framework 后重试, 或改用浏览器模式 (命令行加 --nogui)。')
    logger.info(f'窗口环境检查通过: WebView2 Runtime {wv2}, .NET Release {release or "未知"}')
    return True, ''


def _show_error_dialog(message: str) -> None:
    """弹出系统消息框提示用户; 失败则静默忽略 (日志已记录)"""
    if not sys.platform.startswith('win'):
        return
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, 'JavSP', 0x10)  # MB_ICONERROR
    except Exception:
        pass


def main(gui: bool = True):
    _configure_logging()
    host, port = _resolve_host_port()
    url = f'http://{host}:{port}'

    # 后台线程跑 FastAPI, 主线程负责 UI / 浏览器
    t = threading.Thread(target=_run_server, args=(host, port), daemon=True)
    t.start()

    logger.info(f'正在启动 JavSP 服务 ({url}) ...')
    if not _wait_until_ready(f'{url}/api/health', timeout=30):
        logger.error('服务在限定时间内未能就绪, 请查看上方日志 / JavSP.log')
        sys.exit(1)

    if gui:
        ok, reason = _check_gui_env()
        if not ok:
            logger.error(f'窗口模式不可用: {reason}')
            _show_error_dialog(reason + '\n\n已自动切换到浏览器模式。')
            gui = False
        else:
            try:
                # 明确要求使用 Chromium 内核 (pywebview 5.x 在缺 WebView2 时会静默回退 IE)
                os.environ['PYWEBVIEW_GUI'] = 'edgechromium'
                import webview
                # 打开 pywebview 调试日志, 便于把渲染层报错写进 JavSP.log
                logging.getLogger('pywebview').setLevel(logging.DEBUG)
                logger.info('以窗口形态启动 (pywebview)')
                webview.create_window(
                    'JavSP',
                    url,
                    width=1200,
                    height=820,
                    min_size=(960, 640),
                )
                webview.start()
            except Exception as e:
                logger.exception(f'窗口模式启动失败: {e}')
                _show_error_dialog(f'窗口模式启动失败: {e}\n\n已自动切换到浏览器模式。')
                gui = False

    if not gui:
        logger.info(f'已在浏览器打开 {url}；关闭命令行窗口即可退出')
        try:
            webbrowser.open(url)
        except Exception as e:
            logger.warning(f'无法自动打开浏览器 ({e})，请手动访问 {url}')
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info('收到退出信号, 正在关闭 ...')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(prog='JavSP', description='JavSP 桌面端')
    parser.add_argument('--nogui', action='store_true',
                        help='改用默认浏览器打开 WebUI (默认使用 pywebview 原生窗口壳)')
    parser.add_argument('--host', default=None, help='监听地址 (默认 127.0.0.1)')
    parser.add_argument('--port', default=None, help='监听端口 (默认 8000)')
    args = parser.parse_args()
    if args.host:
        os.environ['JAVSP_HOST'] = args.host
    if args.port:
        os.environ['JAVSP_PORT'] = args.port
    main(gui=not args.nogui)
