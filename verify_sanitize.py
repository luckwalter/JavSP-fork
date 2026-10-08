"""脱敏专项验证: requests 异常常嵌「无 scheme 的 URL」(如 'with url: //IPX-001')

这类 URL 不会被 `scheme://` 正则命中, 可能把 token/路径泄漏到日志与前端。
运行: venv312 下 `python verify_sanitize.py`
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import javsp.web.health as health

PASSED, FAILED = [], []


def check(label, cond, detail=''):
    if cond:
        PASSED.append(label)
        print(f'  [OK ] {label}')
    else:
        FAILED.append(f'{label} -> {detail}')
        print(f'  [FAIL] {label}  {detail}')


CASES = [
    ('带 scheme', "HTTPSConnectionPool(host='h', port=443): url: https://u:pw@h/p?token=abc"),
    ('无 scheme(requests 常见)', "Max retries exceeded with url: //IPX-001?token=SECRET"),
    ('纯路径', "GET /a/b/c failed, path=/secret/xyz"),
    ('代理凭据', "url='http://user:pass@192.0.2.2:3128'"),
]

print('\n===== 脱敏: 无 scheme 的 URL 也须处理 =====')
for name, raw in CASES:
    out = health._sanitize(raw, limit=200)
    print(f'  [{name}]')
    print(f'     IN : {raw[:88]}')
    print(f'     OUT: {out[:88]}')
    check(f'[{name}] 不含 scheme URL', '://' not in out, repr(out[:100]))
    check(f'[{name}] 不含明文凭据',
          'pw@' not in out and 'SECRET' not in out and 'abc' not in out, repr(out[:100]))

print('\n===== 长度与格式 =====')
long = 'x' * 999
check('超长被截断', len(health._sanitize(long)) <= 180, str(len(health._sanitize(long))))
multi = 'line1\nline2\r\nline3'
check('换行被压平', '\n' not in health._sanitize(multi) and '\r' not in health._sanitize(multi),
      repr(health._sanitize(multi)))
check('空输入安全', health._sanitize('') == '' and health._sanitize(None) == '', '')

print(f'\n{"=" * 50}')
print(f'通过 {len(PASSED)} 项, 失败 {len(FAILED)} 项')
for f in FAILED:
    print(f'  FAIL: {f}')
print('脱敏验证全部通过 ✅' if not FAILED else '存在失败项 ❌')
raise SystemExit(1 if FAILED else 0)