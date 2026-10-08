"""渠道监控 API 验证: /api/channels 与 /api/channels/check, 及 sources 健康注入

用 FastAPI TestClient 打真实路由, 覆盖序列化契约(前端依赖这些字段名)。

运行: venv312 下 `python verify_channels_api.py`
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient

import javsp.web.health as health
from javsp.server import app, _sources_with_health

PASSED, FAILED = [], []


def check(label, cond, detail=''):
    if cond:
        PASSED.append(label)
        print(f'  [OK ] {label}')
    else:
        FAILED.append(f'{label} -> {detail}')
        print(f'  [FAIL] {label}  {detail}')


class FakeMovie:
    sources = {
        'javdb': {'dvdid': 'IPX-001', 'title': 't', 'has_cover': True,
                  'has_genre': True, 'has_actress': True, 'contributed': True},
        'javlib': {'dvdid': 'IPX-001', 'title': None,
                   'contributed': False},
    }


def test_channels_api():
    print('\n===== GET /api/channels =====')
    health.reset()
    with TestClient(app) as c:
        r = c.get('/api/channels')
        check('返回 200', r.status_code == 200, str(r.status_code))
        d = r.json()
        check('含 sources 列表', isinstance(d.get('sources'), list), str(type(d.get('sources'))))
        check('含 summary', isinstance(d.get('summary'), dict), '')
        names = [s['source'] for s in d['sources']]
        check('覆盖新移植的三源',
              all(x in names for x in ('javdbapi', 'javdatabase', 'javmenu')), str(names[:8]))
        check('覆盖全部已注册源', len(names) >= len(health.known_sources()),
              f"{len(names)} vs {len(health.known_sources())}")
        s = d['summary']
        for k in ('total', 'active', 'ok', 'tripped', 'unchecked', 'skipped_names'):
            check(f'summary 含 {k}', k in s, str(list(s.keys())))
        check('给出探活周期', d.get('probe_interval') == health.PROBE_INTERVAL,
              str(d.get('probe_interval')))
        check('给出熔断阈值', d.get('threshold') == health.FAILURE_THRESHOLD,
              str(d.get('threshold')))
        # 前端渲染必需字段
        row = d['sources'][0]
        for k in ('source', 'status', 'status_text', 'breaker', 'breaker_text',
                  'active', 'domain', 'severity', 'elapsed_ms'):
            check(f'每行含 {k}', k in row, str(list(row.keys())))


def test_tripped_shown():
    print('\n===== 熔断源在 API 中明确标出 =====')
    health.reset()
    target = health.active_sources()[0]
    with health._lock:
        r = health._get(target)
        r['breaker'] = health.STATE_OPEN
        r['consecutive_failures'] = 2
        r['status'] = 'network_error'
        r['checked_at'] = __import__('time').time()
    with TestClient(app) as c:
        d = c.get('/api/channels').json()
    row = next(s for s in d['sources'] if s['source'] == target)
    check('熔断状态为 open', row['breaker'] == 'open', row['breaker'])
    check('有中文说明', row['breaker_text'] == '已熔断(跳过)', row['breaker_text'])
    check('summary 计入 tripped', d['summary']['tripped'] >= 1, str(d['summary']))
    check('summary 列出跳过的源名', target in d['summary']['skipped_names'],
          str(d['summary']['skipped_names']))
    check('剩余冷却 > 0', row['cooldown_remaining'] > 0, str(row['cooldown_remaining']))


def test_sources_injection():
    print('\n===== 刮削 sources 已注入健康信息 =====')
    health.reset()
    with health._lock:
        health._get('javdb')['status'] = 'ok'
        health._get('javlib')['status'] = 'timeout'
    out = _sources_with_health(FakeMovie())
    check('原字段保留', out['javdb']['title'] == 't', repr(out['javdb']))
    check('并入健康状态', out['javdb']['health_status'] == 'ok', repr(out['javdb']))
    check('并入熔断状态', out['javlib']['breaker'] == health.STATE_CLOSED, repr(out['javlib']))
    check('前端可直接渲染', 'health_text' in out['javlib'], str(list(out['javlib'].keys())))


def test_sources_injection_resilient():
    print('\n===== 监控故障时退化为原始摘要(不能拖垮刮削) =====')

    class Bad:
        sources = {'javdb': {'title': 't'}}

    orig = health.merge_into_scrape_sources
    try:
        import javsp.server as srv

        def boom(src):
            raise RuntimeError('模拟监控内部错误')
        srv.channel_health.merge_into_scrape_sources = boom
        out = srv._sources_with_health(Bad())
        check('监控异常时仍返回原始摘要', out == Bad.sources, repr(out))
    finally:
        import javsp.server as srv
        srv.channel_health.merge_into_scrape_sources = orig


def test_check_api():
    print('\n===== POST /api/channels/check (主动探活) =====')
    health.reset()
    with TestClient(app) as c:
        r = c.post('/api/channels/check', json=None)
        check('返回 200', r.status_code == 200, f'{r.status_code} {r.text[:200]}')
        d = r.json()
        check('含 sources', isinstance(d.get('sources'), list), '')
        check('含摘要', isinstance(d.get('summary'), str), str(type(d.get('summary'))))
        if d['sources']:
            row = d['sources'][0]
            check('每行有 status/breaker/elapsed',
                  all(k in row for k in ('status', 'breaker', 'elapsed_ms')),
                  str(list(row.keys())))
            check('summary 不含 URL', '://' not in d['summary'], d['summary'])


def test_check_api_single_source():
    print('\n===== POST /api/channels/check 指定单个源 =====')
    health.reset()
    with TestClient(app) as c:
        r = c.post('/api/channels/check', json=['javdbapi'])
        d = r.json()
        check('仅返回指定源', [s['source'] for s in d['sources']] == ['javdbapi'],
              str([s['source'] for s in d['sources']]))


if __name__ == '__main__':
    test_channels_api()
    test_tripped_shown()
    test_sources_injection()
    test_sources_injection_resilient()
    test_check_api()
    test_check_api_single_source()
    print(f'\n{"=" * 54}')
    print(f'通过 {len(PASSED)} 项, 失败 {len(FAILED)} 项')
    for f in FAILED:
        print(f'  FAIL: {f}')
    print('渠道 API 验证全部通过 ✅' if not FAILED else '存在失败项 ❌')
    raise SystemExit(1 if FAILED else 0)