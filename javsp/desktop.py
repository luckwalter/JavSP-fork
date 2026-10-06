"""JavSP 桌面端入口 (PyWebView)

将 FastAPI 后端与前端打包为单机桌面应用:
1. 在后台线程启动 FastAPI 服务(复用 javsp.server.app)
2. 用 pywebview 打开本地窗口加载 http://127.0.0.1:8000

打包示例(npx cxfreeze 或 pyinstaller):
    pyinstaller --noconsole --onefile javsp/desktop.py
"""
import os
import time
import threading
import logging

logger = logging.getLogger('javsp.desktop')


def start_server():
    import uvicorn
    from javsp.server import app
    uvicorn.run(app, host='127.0.0.1', port=8000, log_level='info')


def main():
    # 先启动后端服务
    t = threading.Thread(target=start_server, daemon=True)
    t.start()
    # 等待服务就绪
    time.sleep(2)

    import webview
    webview.create_window(
        'JavSP',
        'http://127.0.0.1:8000',
        width=1200,
        height=820,
        min_size=(960, 640),
    )
    webview.start()


if __name__ == '__main__':
    main()
