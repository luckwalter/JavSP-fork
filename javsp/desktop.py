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
        import webview
        logger.info('以窗口形态启动 (pywebview)')
        webview.create_window(
            'JavSP',
            url,
            width=1200,
            height=820,
            min_size=(960, 640),
        )
        webview.start()
    else:
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
