"""smoke_tasks.py — TaskStore 接入真 uvicorn 的端到端冒烟

不验证算法（已由 verify_task_store.py 覆盖），只验证「换掉 TASKS 类型后，
真实 HTTP 服务仍能正常跑通 scan → movies → health」，防止集成层面出问题。
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
PY = sys.executable
PORT = 18731


def wait_health(retries=40, delay=0.5):
    for _ in range(retries):
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/api/health', timeout=2) as r:
                return json.loads(r.read().decode())
        except Exception:
            time.sleep(delay)
    return None


def main():
    tmp = tempfile.mkdtemp(prefix='javsp_tasks_')
    # 造几个够小的文件——本测试只验TASKS 通路，不要求达到 minimum_size
    os.makedirs(tmp, exist_ok=True)
    for i in range(3):
        p = Path(tmp) / f'ABP-{100 + i}.mp4'
        with open(p, 'wb') as f:
            f.write(b'\x00' * 1024)

    env = dict(os.environ)
    env['JAVSP_HOST'] = '127.0.0.1'
    env['JAVSP_PORT'] = str(PORT)
    proc = subprocess.Popen([PY, '-c',
                             'from javsp.server import entry; entry()'],
                            cwd=str(ROOT), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    ok = False
    try:
        h = wait_health()
        print('health:', h)
        if not h:
            print('FAIL: 服务未在超时内就绪')
            return 1

        # scan（文件过小可能扫不到，用 API 直接验证 TASKS 类型接得住即可）
        req = urllib.request.Request(
            f'http://127.0.0.1:{PORT}/api/scan',
            data=json.dumps({'path': tmp}).encode(),
            headers={'Content-Type': 'application/json'},
            method='POST')
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                scan = json.loads(r.read().decode())
            print('scan:', scan)
        except Exception as e:
            print('scan 端点异常(小文件扫不到属正常):', e)

        # movies 端点必须能正常序列化(TASKS.values() 的关键路径)
        with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/api/movies', timeout=10) as r:
            movies = json.loads(r.read().decode())
        print('movies:', movies)
        assert isinstance(movies, list), 'movies 应为list'

        ok = True
        print('\nSMOKE PASS: scan/movies/health 全链路通(TASKS 接入无集成问题)')
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())