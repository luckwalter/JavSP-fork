"""会话持久化验证: 模拟进程/容器重启后, 已登录的浏览器是否还能用

背景(v0.2.2 实测事故): 会话只存内存, 容器一重启全丢, 而浏览器 Cookie 还在,
于是每个请求都被判 401 —— 表现为「操作一会儿就提示未登录」「点什么都没反应」。

这里的"重启"用**独立子进程**模拟(而不是同进程内重新 import), 因为只有真正
换了一个 Python 进程, 才能证明状态确实跨进程存活。
每个用例单独开进程, 用各自的会话文件目录隔离, 互不干扰。
"""
import json
import os
import subprocess
import sys
import tempfile

PY = sys.executable
HERE = os.path.dirname(os.path.abspath(__file__))
AUTH = os.path.join(HERE, 'javsp', 'auth.py')

PASS = 0
FAIL = 0


def check(name, cond, extra=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  ✓ {name}')
    else:
        FAIL += 1
        print(f'  ✗ {name}  {extra}')


def run_snippet(code, env_extra):
    """在独立子进程里执行一段代码, 返回 (stdout, returncode)"""
    env = dict(os.environ)
    env['JAVSP_AUTH_PASSWORD'] = 'testpass'
    env['JAVSP_AUTH_USERNAME'] = 'admin'
    env.update(env_extra)
    r = subprocess.run([PY, '-c', code], capture_output=True, text=True, env=env, cwd=HERE)
    return r.stdout.strip(), r.returncode, r.stderr.strip()


LOADER = '''
import importlib.util, os, json
spec = importlib.util.spec_from_file_location('javsp_auth', r'{auth}')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
'''.replace('{auth}', AUTH.replace('\\', '/'))


def main():
    print('=== 1. 会话跨进程(模拟容器重启)保持有效 ===')
    with tempfile.TemporaryDirectory() as d:
        env = {'JAVSP_SESSION_FILE': os.path.join(d, 's.json')}
        token, rc, err = run_snippet(
            LOADER + '''
r = m.login('admin', 'testpass', remember=True)
print(r['token'])
''', env)
        check('登录成功并拿到令牌', rc == 0 and len(token) > 20, f'token={token[:16]} rc={rc} err={err[:200]}')

        check('会话文件已落盘', os.path.exists(env['JAVSP_SESSION_FILE']), env['JAVSP_SESSION_FILE'])

        # 全新的进程 = 模拟容器重启
        out, rc, err = run_snippet(
            LOADER + '''
print(m.verify_token('{t}'))
'''.replace('{t}', token), env)
        check('重启后旧令牌仍然有效(关键)', out == 'True', f'got={out} rc={rc} err={err[:200]}')

        # 未登录过的进程拿别的令牌应无效
        out, rc, _ = run_snippet(
            LOADER + '''
print(m.verify_token('bogus-token'))
''', env)
        check('伪造令牌仍被拒绝', out == 'False', f'got={out}')

    print('\n=== 2. 注销后重启也失效(不能"复活") ===')
    with tempfile.TemporaryDirectory() as d:
        env = {'JAVSP_SESSION_FILE': os.path.join(d, 's.json')}
        token, rc, _ = run_snippet(LOADER + '''
r = m.login('admin', 'testpass', remember=True)
print(r['token'])
''', env)
        out, rc, _ = run_snippet(
            LOADER + '''
m.logout('{t}')
print('ok')
'''.replace('{t}', token), env)
        check('注销执行成功', out == 'ok')
        out, rc, _ = run_snippet(
            LOADER + '''
print(m.verify_token('{t}'))
'''.replace('{t}', token), env)
        check('注销后重启仍无效', out == 'False', f'got={out}')

    print('\n=== 3. 落盘内容不含令牌明文 ===')
    with tempfile.TemporaryDirectory() as d:
        env = {'JAVSP_SESSION_FILE': os.path.join(d, 's.json')}
        token, rc, _ = run_snippet(LOADER + '''
r = m.login('admin', 'testpass', remember=True)
print(r['token'])
''', env)
        raw = open(env['JAVSP_SESSION_FILE'], encoding='utf-8').read()
        check('文件里没有令牌明文', token not in raw, '会话文件泄露了可用 Cookie')
        data = json.loads(raw)
        keys = list(data['sessions'].keys())
        check('存的是 64 位哈希(sha256 hex)', len(keys[0]) == 64, f'key={keys[0][:20]}')

    print('\n=== 4. 过期会话不会被恢复 ===')
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, 's.json')
        # 手工造一条已过期的会话
        json.dump({'sessions': {'a' * 64: {'created': 1.0, 'expires': 2.0, 'ttl': 3600}}},
                  open(p, 'w', encoding='utf-8'))
        out, rc, _ = run_snippet(
            LOADER + '''
print(len(m._sessions))
''', {'JAVSP_SESSION_FILE': p})
        check('过期会话被丢弃', out == '0', f'剩余={out}')

    print('\n=== 5. 滑动续期不会把有效期越滚越大 ===')
    with tempfile.TemporaryDirectory() as d:
        env = {'JAVSP_SESSION_FILE': os.path.join(d, 's.json')}
        out, rc, err = run_snippet(
            LOADER + '''
r = m.login('admin', 'testpass', remember=True)
t = r['token']
import time
s = m._sessions[m._token_hash(t)]
ttl = s['ttl']
# 手动把 expires 推到"只剩一点点", 强制触发续期
s['expires'] = time.time() + ttl * 0.1
m.verify_token(t)
after = m._sessions[m._token_hash(t)]['expires'] - time.time()
# 再触发一次
s2 = m._sessions[m._token_hash(t)]
s2['expires'] = time.time() + ttl * 0.1
m.verify_token(t)
after2 = m._sessions[m._token_hash(t)]['expires'] - time.time()
print(f'{ttl}|{after}|{after2}')
''', env)
        if rc == 0 and '|' in out:
            ttl, a1, a2 = [float(x) for x in out.split('|')]
            check('续期后有效期≈原 TTL(不膨胀)', abs(a1 - ttl) < 5 and abs(a2 - ttl) < 5,
                  f'ttl={ttl} after1={a1} after2={a2}')
        else:
            check('续期后有效期≈原 TTL(不膨胀)', False, f'out={out} err={err[:200]}')

    print('\n=== 6. 会话目录可随配置文件目录指定(读写同源) ===')
    with tempfile.TemporaryDirectory() as d:
        env = {'JAVSP_SESSION_FILE': os.path.join(d, 's.json')}
        token, rc, _ = run_snippet(LOADER + '''
r = m.login('admin', 'testpass', remember=True)
print(r['token'])
''', env)
        # 换个目录再验证: 若实现写成"读 A 写 B", 这里必然失效
        with tempfile.TemporaryDirectory() as d2:
            out, rc, _ = run_snippet(
                LOADER + '''
print(m.verify_token('{t}'))
'''.replace('{t}', token), {'JAVSP_SESSION_FILE': os.path.join(d2, 's.json')})
            check('换目录后旧会话不可见(路径生效)', out == 'False', f'got={out}')

    print(f'\n结果: {PASS} 通过, {FAIL} 失败')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
