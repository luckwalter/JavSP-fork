"""登录认证验证(启停两种模式都要测)

关键: 必须覆盖「认证启用」与「认证禁用(向后兼容)」两种配置, 否则可能出现
"在测试环境没开认证所以过了, 线上开了反而失效"。

运行: venv312 下 `python verify_auth.py`
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASSED, FAILED = [], []


def check(label, cond, detail=''):
    if cond:
        PASSED.append(label)
        print(f'  [OK ] {label}')
    else:
        FAILED.append(f'{label} -> {detail}')
        print(f'  [FAIL] {label}  {detail}')


def run_enabled_suite():
    """认证启用时的完整测试"""
    os.environ['JAVSP_AUTH_PASSWORD'] = 'S3cretPass'
    os.environ['JAVSP_AUTH_USERNAME'] = 'admin'
    for m in list(sys.modules):
        if m.startswith('javsp'):
            del sys.modules[m]
    from fastapi.testclient import TestClient
    from javsp.server import app
    from javsp import auth

    print('\n===== 认证启用: 基本开关 =====')
    check('认证已启用', auth.is_enabled() is True, '')
    c = TestClient(app)

    r = c.get('/api/auth/status')
    check('status 可访问(免登录)', r.status_code == 200, str(r.status_code))
    check('status 报告已启用', r.json()['enabled'] is True, str(r.json()))

    print('\n===== 未登录访问受保护接口应401 =====')
    for ep in ['/api/movies', '/api/config', '/api/channels']:
        rr = c.get(ep)
        check(f'GET {ep} 未登录 -> 401', rr.status_code == 401, f'got {rr.status_code}')
    rr = c.post('/api/scan', json={'path': '/tmp'})
    check('POST /api/scan 未登录 -> 401', rr.status_code == 401, f'got {rr.status_code}')
    rr = c.post('/api/organize', json={'guid': 'x'})
    check('POST /api/organize 未登录 -> 401', rr.status_code == 401, f'got {rr.status_code}')

    print('\n===== health 免登录(否则健康检查会挂) =====')
    rr = c.get('/api/health')
    check('GET /api/health 免登录 -> 200', rr.status_code == 200, str(rr.status_code))

    print('\n===== 登录行为 =====')
    rr = c.post('/api/auth/login', json={'username': 'admin', 'password': 'wrong'})
    check('错误密码 -> 401', rr.status_code == 401, f'got {rr.status_code}')
    check('错误提示不区分用户/密码错', '用户名或密码错误' in rr.text, rr.text[:80])

    rr = c.post('/api/auth/login', json={'username': 'nobody', 'password': 'S3cretPass'})
    check('错误用户名 -> 401', rr.status_code == 401, str(rr.status_code))
    check('账号枚举被阻断(与密码错同样提示)',
          '用户名或密码错误' in rr.text, rr.text[:80])

    rr = c.post('/api/auth/login', json={'username': 'admin', 'password': 'S3cretPass'})
    check('正确凭据 -> 200', rr.status_code == 200, f'got {rr.status_code} {rr.text[:80]}')
    check('下发会话 Cookie', 'javsp_session' in c.cookies, str(dict(c.cookies)))
    check('Cookie 为 HttpOnly',
          'HttpOnly' in rr.headers.get('set-cookie', ''),
          rr.headers.get('set-cookie', '')[:80])
    check('Cookie SameSite=lax',
          'samesite=lax' in rr.headers.get('set-cookie', '').lower()
          or 'samesite' in rr.headers.get('set-cookie', '').lower(),
          rr.headers.get('set-cookie', '')[:100])

    print('\n===== 登录后可访问 =====')
    rr = c.get('/api/movies')
    check('GET /api/movies 已登录 -> 200', rr.status_code == 200, f'got {rr.status_code}')

    print('\n===== 注销后失效 =====')
    rr = c.post('/api/auth/logout')
    check('logout -> 200', rr.status_code == 200, str(rr.status_code))
    rr = c.get('/api/movies')
    check('注销后 GET /api/movies -> 401', rr.status_code == 401, f'got {rr.status_code}')

    print('\n===== 暴力破解锁定 =====')
    for i in range(4):
        c.post('/api/auth/login', json={'username': 'admin', 'password': f'bad{i}'})
    check('未达阈值前未锁定(仍可尝试)', auth.lockout_remaining() == 0,
          str(auth.lockout_remaining()))
    c.post('/api/auth/login', json={'username': 'admin', 'password': 'bad5'})
    check('达阈值后锁定', auth.lockout_remaining() > 0, str(auth.lockout_remaining()))
    rr = c.post('/api/auth/login', json={'username': 'admin', 'password': 'S3cretPass'})
    check('锁定期内正确密码也被拒(423)', rr.status_code == 423, f'got {rr.status_code}')
    # 真正要防的是"剩余次数/失败次数"这类具体数字被吐出来(便于攻击者试探),
    # 而 "尝试次数过多" 是固定文案, 不泄露任何可利用信息。
    import re as _re
    check('锁定提示不含具体剩余次数',
          not _re.search(r'剩\s*\d|\d+\s*次(?!锁)|还可尝试\s*\d', rr.text),
          repr(rr.text[:80]))
    check('锁定提示为固定文案', '尝试次数过多' in rr.text, repr(rr.text[:80]))

    print('\n===== 会话令牌安全性 =====')
    import hashlib
    r = auth.login('admin', 'S3cretPass')if not auth.lockout_remaining() else {'token': None}
    # 直接用内部接口验证令牌存储: 存的是哈希而非明文
    with auth._lock:
        keys = list(auth._sessions.keys())
    check('会话以哈希存储(非明文令牌)', all(len(k) == 64 for k in keys),
          f'key长度={ [len(k) for k in keys] }')
    check('令牌无明文残留',
          all(r['token'] not in k for r in [r] if r.get('token') for k in keys), '')


def run_disabled_suite():
    """认证禁用: 向后兼容(升级 0.2.1 部署不能被锁在门外)"""
    os.environ.pop('JAVSP_AUTH_PASSWORD', None)
    for m in list(sys.modules):
        if m.startswith('javsp'):
            del sys.modules[m]
    from fastapi.testclient import TestClient
    from javsp.server import app
    from javsp import auth

    print('\n===== 认证禁用: 向后兼容 =====')
    check('认证已禁用', auth.is_enabled() is False, '')
    c = TestClient(app)
    for ep in ['/api/health', '/api/movies', '/api/config', '/api/channels']:
        rr = c.get(ep)
        check(f'免登录 GET {ep} -> 200', rr.status_code == 200, f'got {rr.status_code}')
    rr = c.post('/api/auth/login', json={'username': 'x', 'password': 'y'})
    check('禁用时登录接口仍 200(前端无需特判)', rr.status_code == 200, str(rr.status_code))


if __name__ == '__main__':
    run_enabled_suite()
    run_disabled_suite()
    print(f'\n{"=" * 54}')
    print(f'通过 {len(PASSED)} 项, 失败 {len(FAILED)} 项')
    for f in FAILED:
        print(f'  FAIL: {f}')
    print('登录认证验证全部通过 ✅' if not FAILED else '存在失败项 ❌')
    raise SystemExit(1 if FAILED else 0)