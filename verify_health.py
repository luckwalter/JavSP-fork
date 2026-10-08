"""health.py 离线验证(不触网): 异常分类、脱敏、熔断状态机、跳过逻辑、展示辅助

熔断是本模块的核心, 必须覆盖完整状态迁移:
    closed --连续失败2次--> open --冷却到期--> half_open --成功--> closed
                                        half_open --失败--> open(冷却翻倍)

运行: venv312 下 `python verify_health.py`
"""
import os
import sys
import time
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests

import javsp.web.health as health
from javsp.web.exceptions import (
    CredentialError,
    MovieDuplicateError,
    MovieNotFoundError,
    SiteBlocked,
)

PASSED, FAILED = [], []


def check(label, cond, detail=''):
    if cond:
        PASSED.append(label)
        print(f'  [OK ] {label}')
    else:
        FAILED.append(f'{label} -> {detail}')
        print(f'  [FAIL] {label}  {detail}')


def resp(code):
    r = requests.Response()
    r.status_code = code
    return r


def mkmod(name, fn):
    """注册一个假的源模块"""
    m = types.ModuleType(f'javsp.web.{name}')
    m.parse_data = staticmethod(fn)
    sys.modules[f'javsp.web.{name}'] = m
    return m


def unmod(*names):
    for n in names:
        sys.modules.pop(f'javsp.web.{n}', None)


# ================================================================ 分类

def test_classify():
    print('\n===== 异常分类 =====')
    cases = [
        (MovieNotFoundError('m', 'X'), 'not_found'),
        (SiteBlocked('blocked'), 'blocked'),
        (CredentialError('need key'), 'credential_error'),
        (MovieDuplicateError('m', 'X', 2), 'duplicate'),
        (requests.exceptions.ReadTimeout('slow'), 'timeout'),
        (requests.exceptions.ConnectTimeout('ct'), 'timeout'),
        (requests.exceptions.SSLError('bad cert'), 'tls_error'),
        (requests.exceptions.ProxyError('proxy dead'), 'network_error'),
        (ValueError('boom'), 'error'),
    ]
    for exc, expect in cases:
        got = health._classify(exc)
        check(f'{type(exc).__name__} -> {expect}', got == expect, f'got {got!r}')


def test_classify_dns():
    print('\n===== DNS 需从 ConnectionError 中单独识别 =====')
    e = requests.exceptions.ConnectionError(
        "HTTPConnectionPool(host='x'): Max retries exceeded (Caused by NewConnectionError("
        "'HTTPSConnection: Failed to establish a new connection: [Errno -3] "
        "Temporary failure in name resolution'))")
    check('域名解析失败 -> dns_error', health._classify(e) == 'dns_error',
          f"got {health._classify(e)!r}")
    e2 = requests.exceptions.ConnectionError('Connection refused')
    check('连接被拒 -> network_error', health._classify(e2) == 'network_error',
          f"got {health._classify(e2)!r}")


def test_sanitize():
    print('\n===== 脱敏: 绝不泄露 URL/凭据 =====')
    raw = ("HTTPSConnectionPool(host='p', port=443): Max retries exceeded with url: /x?y=1 "
           "(Caused by ProxyError('Unable to connect to proxy', "
           "url='http://user:secretpass@192.0.2.3:3128')))")
    out = health._sanitize(raw)
    check('URL 已抹除', 'http://' not in out and 'https://' not in out, repr(out))
    check('代理账号密码未泄漏', 'secretpass' not in out and 'user:' not in out, repr(out))
    check('仍保留可读原因', 'Proxy' in out or 'proxy' in out, repr(out))
    check('已压成单行', '\n' not in out, repr(out[:60]))
    check('超长被截断<=180', len(health._sanitize('x' * 999)) <= 180,
          str(len(health._sanitize('x' * 999))))


# ================================================================ 熔断状态机

def test_breaker_trips_on_repeated_failure():
    print('\n===== 熔断: 连续失败达阈值才熔断(不误杀好源) =====')
    health.reset()
    mkmod('brk', lambda m: (_ for _ in ()).throw(
        requests.exceptions.ReadTimeout('t')))
    r1 = health.probe('brk')
    check('第1次失败不熔断(阈值=2, 防抖动误杀)',
          r1['breaker'] == health.STATE_CLOSED, f"got {r1['breaker']!r}")
    r2 = health.probe('brk')
    check('第2次失败才熔断', r2['breaker'] == health.STATE_OPEN, f"got {r2['breaker']!r}")
    check('熔断后累计 trips', r2['trips'] >= 1, str(r2.get('trips')))
    check('熔断有剩余冷却', r2['cooldown_remaining'] > 0, str(r2.get('cooldown_remaining')))
    unmod('brk')


def test_breaker_skips_scrape():
    print('\n===== 熔断后刮削应跳过该源 =====')
    health.reset()
    # 必须用**真实启用源**来测: 熔断只对启用源有意义(skipped_sources 遍历 active_sources)
    target = 'javlib' if 'javlib' in health.active_sources() else health.active_sources()[0]
    mkmod(target, lambda m: (_ for _ in ()).throw(
        requests.exceptions.ReadTimeout('t')))
    health.probe(target)
    health.probe(target)
    check(f'[{target}] 熔断中', health.is_tripped(target) is True, '')
    check(f'[{target}] allowlist 已剔除', target not in health.allowlist(), str(health.allowlist()))
    sk = health.skipped_sources()
    check(f'[{target}] skipped_sources 报告该源',
          any(r['source'] == target for r in sk), str([r['source'] for r in sk]))
    check('剩余冷却在递减', any(r['cooldown_remaining'] > 0 for r in sk),
          str([(r['source'], r['cooldown_remaining']) for r in sk]))
    unmod(target)


def test_breaker_cooldown_to_half_open():
    print('\n===== 冷却到期 -> 半开试探 =====')
    health.reset()
    mkmod('cool', lambda m: (_ for _ in ()).throw(
        requests.exceptions.ReadTimeout('t')))
    health.probe('cool')
    health.probe('cool')
    check('已熔断', health.is_tripped('cool') is True, '')
    with health._lock:
        rec = health._get('cool')
        rec['checked_at'] = time.time() - (health.COOLDOWN_BASE + 10)  # 假装冷却已过
    check('冷却到期后不再跳过', health.is_tripped('cool') is False, '')
    with health._lock:
        check('状态已转 half_open', health._get('cool')['breaker'] == health.STATE_HALF_OPEN,
              health._get('cool')['breaker'])
    unmod('cool')


def test_breaker_half_open_success_recovers():
    print('\n===== 半开试探成功 -> 恢复 closed =====')
    health.reset()
    flag = {'ok': False}

    def fn(m):
        if not flag['ok']:
            raise requests.exceptions.ReadTimeout('t')
        m.title = '有效标题'
    mkmod('rec', fn)
    health.probe('rec')
    health.probe('rec')
    check('已熔断', health.is_tripped('rec') is True, '')
    with health._lock:
        health._get('rec')['checked_at'] = time.time() - (health.COOLDOWN_BASE + 10)
    health.is_tripped('rec')      # 触发 half_open
    flag['ok'] = True
    r = health.probe('rec')
    check('试探成功 -> closed', r['breaker'] == health.STATE_CLOSED, f"got {r['breaker']!r}")
    check('状态回到 ok', r['status'] == 'ok', r['status'])
    check('失败计数清零', r['consecutive_failures'] == 0, str(r['consecutive_failures']))
    check('不再跳过', health.is_tripped('rec') is False, '')
    unmod('rec')


def test_breaker_half_open_failure_reopens():
    print('\n===== 半开试探失败 -> 重新熔断且冷却翻倍 =====')
    health.reset()
    mkmod('re', lambda m: (_ for _ in ()).throw(requests.exceptions.ReadTimeout('t')))
    health.probe('re')
    health.probe('re')
    with health._lock:
        health._get('re')['checked_at'] = time.time() - (health.COOLDOWN_BASE + 10)
    health.is_tripped('re')      # 转 half_open
    r = health.probe('re')
    check('试探失败 -> 重新 open', r['breaker'] == health.STATE_OPEN, f"got {r['breaker']!r}")
    check('失败计数累加', r['consecutive_failures'] >= 3, str(r['consecutive_failures']))
    check('冷却已翻倍(>=2倍基数)', r['cooldown_remaining'] >= health.COOLDOWN_BASE * 2 - 1,
          str(r['cooldown_remaining']))
    unmod('re')


def test_cooldown_exponential_capped():
    print('\n===== 冷却期指数增长且封顶 =====')
    v1 = health._cooldown_for(1)
    v2 = health._cooldown_for(2)
    v3 = health._cooldown_for(3)
    v9 = health._cooldown_for(9)
    check(f'第1次={health.COOLDOWN_BASE}s', v1 == health.COOLDOWN_BASE, str(v1))
    check('逐次翻倍', v2 == health.COOLDOWN_BASE * 2 and v3 == health.COOLDOWN_BASE * 4,
          f'{v2},{v3}')
    check('封顶不超过 COOLDOWN_MAX', v9 <= health.COOLDOWN_MAX, f'{v9} > {health.COOLDOWN_MAX}')


def test_not_found_never_trips():
    print('\n===== 关键: 未收录/内容异常 绝不熔断(否则好源被误杀) =====')
    health.reset()
    mkmod('nf', lambda m: (_ for _ in ()).throw(MovieNotFoundError('nf', m.dvdid)))
    for i in range(5):
        r = health.probe('nf')
    check('连续5次未收录仍不熔断', r['breaker'] == health.STATE_CLOSED, f"got {r['breaker']!r}")
    check('不会被跳过', health.is_tripped('nf') is False, '')
    check('状态记为 not_found', r['status'] == 'not_found', r['status'])
    unmod('nf')


def test_shell_page_is_invalid_not_ok():
    print('\n===== 死站返回 200 壳页: 必须判为 invalid_response 而非 ok =====')
    health.reset()
    mkmod('shell', lambda m: None)          # 不抛异常但什么都没抓到
    r = health.probe('shell')
    check('空壳返回 -> invalid_response', r['status'] == 'invalid_response', f"got {r['status']!r}")
    check('invalid_response 不熔断(仅标记内容异常)',
          r['breaker'] == health.STATE_CLOSED, f"got {r['breaker']!r}")
    # 标题就是番号本身, 也算无效
    mkmod('shell2', lambda m: setattr(m, 'title', 'IPX-001'))
    r2 = health.probe('shell2')
    check('标题仅等于番号 -> invalid_response', r2['status'] == 'invalid_response', r2['status'])
    unmod('shell', 'shell2')


def test_http_404_becomes_http_error():
    print('\n===== HTTP 404/403 应判为通道故障 =====')
    health.reset()
    mkmod('h404', lambda m: (_ for _ in ()).throw(
        requests.exceptions.HTTPError(response=resp(404))))
    r = health.probe('h404')
    check('404 -> http_error(而非 network_error)', r['status'] == 'http_error', f"got {r['status']!r}")
    check('记录状态码', r['last_status_code'] == 404, str(r['last_status_code']))
    check('reason 已脱敏', 'http://' not in (r['reason'] or ''), repr(r['reason']))
    mkmod('h403', lambda m: (_ for _ in ()).throw(
        requests.exceptions.HTTPError(response=resp(403))))
    r3 = health.probe('h403')
    check('403 -> http_error', r3['status'] == 'http_error', f"got {r3['status']!r}")
    unmod('h404', 'h403')


def test_site_blocked_detected():
    print('\n===== Cloudflare 拦截(SiteBlocked)可被识别 =====')
    health.reset()
    mkmod('cf', lambda m: (_ for _ in ()).throw(SiteBlocked('Just a moment...')))
    r = health.probe('cf')
    check('SiteBlocked -> blocked', r['status'] == 'blocked', f"got {r['status']!r}")
    check('blocked 属通道故障(会熔断)',
          r['status'] in health._TRIP_STATUSES, r['status'])
    unmod('cf')


# ================================================================ 现场观测

def test_passive_observation():
    print('\n===== 刮削现场被动观测(零额外请求) =====')
    health.reset()
    health.note_scrape_outcome('javdb', 'success')
    health.note_scrape_outcome('javdb', 'not_found')
    health.note_scrape_outcome('javdb', 'success')
    health.note_scrape_outcome('javlib', 'not_found')
    with health._lock:
        r = dict(health._get('javdb'))
        r2 = dict(health._get('javlib'))
    check('样本数累计', r['scrape_samples'] == 3, str(r['scrape_samples']))
    check('成功数累计', r['scrape_success'] == 2, str(r['scrape_success']))
    check('现场 not_found 不熔断', r['breaker'] == health.STATE_CLOSED, r['breaker'])
    check('good源命中率可算', r['scrape_samples'] > 0, '')
    check('另一源独立记录', r2['scrape_samples'] == 1, str(r2['scrape_samples']))
    ov = {x['source']: x for x in health.overview()}
    check('命中率百分比已算好', ov['javdb']['hit_rate'] is not None
          and 0 <= ov['javdb']['hit_rate'] <= 100, str(ov['javdb']['hit_rate']))


def test_passive_failure_can_trip():
    print('\n===== 现场连续通道故障也应能熔断(半开恢复靠它) =====')
    health.reset()
    health.note_scrape_outcome('javx', 'timeout')
    with health._lock:
        check('第1次未熔断', health._get('javx')['breaker'] == health.STATE_CLOSED, '')
    health.note_scrape_outcome('javx', 'timeout')
    with health._lock:
        check('第2次熔断', health._get('javx')['breaker'] == health.STATE_OPEN,
              health._get('javx')['breaker'])
    check('已被跳过', health.is_tripped('javx') is True, '')


def test_passive_success_recovers():
    print('\n===== 现场成功应恢复熔断的源 =====')
    health.reset()
    target = health.active_sources()[0]
    health.note_scrape_outcome(target, 'network_error')
    health.note_scrape_outcome(target, 'network_error')
    with health._lock:
        check(f'[{target}] 已熔断', health._get(target)['breaker'] == health.STATE_OPEN,
              health._get(target)['breaker'])
    health.note_scrape_outcome(target, 'success')
    with health._lock:
        r = health._get(target)
    check('现场成功 -> closed', r['breaker'] == health.STATE_CLOSED, r['breaker'])
    check('计数清零', r['consecutive_failures'] == 0, str(r['consecutive_failures']))
    check('不再跳过', health.is_tripped(target) is False, '')


# ================================================================ 覆盖度与展示

def test_covers_all_sources():
    print('\n===== 覆盖全部源(主人要求"都能监控起来") =====')
    actives = health.active_sources()
    ov = health.overview()
    known = health.known_sources()
    check('概览覆盖全部已注册源', len(ov) >= len(known), f'{len(ov)} vs {len(known)}')
    for s in ('javdbapi', 'javdatabase', 'javmenu', 'javdb', 'javbus', 'javlib', 'jav321'):
        check(f'{s} 在监控范围内', s in known, str(known))
    check('含未启用源(可提示用户可开)',
          len(known) > len(actives), f'{len(known)} vs {len(actives)}')
    check('每行带 active 标记', all('active' in r for r in ov), '')
    check('每行带 breaker 说明', all('breaker_text' in r for r in ov), '')
    check('每行带域名提示', all('domain' in r for r in ov), '')


def test_overview_sorting_and_cooldown_decay():
    print('\n===== 概览排序: 问题源排最前; 冷却数字随时间递减 =====')
    health.reset()
    with health._lock:
        health._get('javdb')['status'] = 'ok'
        health._get('javlib')['status'] = 'network_error'
        health._get('javbus')['status'] = 'blocked'
        health._get('javlib')['breaker'] = health.STATE_OPEN
        health._get('javlib')['consecutive_failures'] = 1
        health._get('javlib')['checked_at'] = time.time()
    ov = health.overview(include_inactive=False)
    names = [r['source'] for r in ov]
    check('blocked 排最前', names[0] == 'javbus', str(names))
    check('network_error 在 ok 之前', names.index('javlib') < names.index('javdb'), str(names))
    cd1 = next(r['cooldown_remaining'] for r in ov if r['source'] == 'javlib')
    time.sleep(1.2)
    ov2 = health.overview(include_inactive=False)
    cd2 = next(r['cooldown_remaining'] for r in ov2 if r['source'] == 'javlib')
    check(f'冷却随时间递减({cd1}->{cd2})', cd2 < cd1, f'{cd1} vs {cd2}')


def test_merge_into_scrape_sources():
    print('\n===== 并入刮削 sources(前端一次渲染) =====')
    health.reset()
    with health._lock:
        health._get('javdb')['status'] = 'ok'
        health._get('javlib')['status'] = 'timeout'
        health._get('javlib')['breaker'] = health.STATE_OPEN
        health._get('javlib')['consecutive_failures'] = 2
        health._get('javlib')['checked_at'] = time.time()
    srcs = {
        'javdb': {'dvdid': 'IPX-001', 'title': 'x', 'has_cover': True, 'contributed': True},
        'javlib': {'dvdid': 'IPX-001', 'title': None, 'contributed': False},
    }
    out = health.merge_into_scrape_sources(srcs)
    check('原有字段未被破坏',
          out['javdb']['title'] == 'x' and out['javdb']['has_cover'] is True, repr(out['javdb']))
    check('并入健康状态', out['javdb']['health_status'] == 'ok', repr(out['javdb']))
    check('并入可读文案', out['javlib']['health_text'].startswith('超时'), repr(out['javlib']))
    check('并入熔断标记', out['javlib']['breaker'] == 'open', repr(out['javlib']))
    check('入参未被就地修改', 'health_status' not in srcs['javdb'], '原对象被污染')
    check('非字典输入原样返回', health.merge_into_scrape_sources(None) is None, '')


def test_probe_all_resilience():
    print('\n===== probe_all: 单源失败不影响整体 =====')
    health.reset()
    mkmod('good_a', lambda m: setattr(m, 'title', 'ok标题'))
    mkmod('bad_b', lambda m: (_ for _ in ()).throw(RuntimeError('fail')))
    mkmod('bad_c', lambda m: (_ for _ in ()).throw(
        requests.exceptions.ConnectTimeout('t')))
    res = health.probe_all(['good_a', 'bad_b', 'bad_c'])
    got = {r['source']: r['status'] for r in res}
    check('好源正常', got.get('good_a') == 'ok', str(got))
    check('内部异常源标记 error', got.get('bad_b') == 'error', str(got))
    check('超时源标记 timeout', got.get('bad_c') == 'timeout', str(got))
    check('三源都有报告', len(res) == 3, str(len(res)))
    check('每个结果含展示字段',
          all('status_text' in r and 'breaker' in r for r in res), '')
    unmod('good_a', 'bad_b', 'bad_c')


def test_stale_guard():
    print('\n===== 防过期回填(sequence) =====')
    health.reset()
    s_old = health._begin_check('x')
    s_new = health._begin_check('x')
    ok_old = health._commit('x', s_old, 'ok', 'old', 1, 0, 'active')
    check('旧序列结果被拒绝', ok_old is False, f'returned {ok_old}')
    ok_new = health._commit('x', s_new, 'error', 'new', 1, 0, 'active')
    check('最新序列可提交', ok_new is True, f'returned {ok_new}')
    with health._lock:
        rec = dict(health._get('x'))
    check('档案为最新结果', rec['reason'] == 'new', repr(rec.get('reason')))


def test_reports():
    print('\n===== 运维报告(不含 URL/凭据) =====')
    health.reset()
    target = health.active_sources()[0]
    mkmod(target, lambda m: (_ for _ in ()).throw(
        requests.exceptions.ProxyError("url='http://u:pw@1.2.3.4:3128'")))
    health.probe(target)
    health.probe(target)
    s = health.summary_for_log()
    rep = health.format_report()
    check('摘要含正常/熔断计数', '正常' in s and '熔断' in s, repr(s))
    check(f'报告列出源 {target}', target in rep, rep[:200])
    check('报告标记熔断', '熔断' in rep, rep[:200])
    check('报告无凭据', 'pw@' not in rep and 'http://' not in rep, rep[:300])
    check('报告无 URL', '://' not in rep, rep[:300])
    unmod(target)


def test_unknown_source_not_skipped():
    print('\n===== 未知源不跳过(首次运行不该全跳过) =====')
    health.reset()
    check('从未探活过的源不被跳过', health.is_tripped('never_seen_src') is False, '')


def test_reset():
    print('\n===== reset 清空档案 =====')
    health.reset()
    mkmod('rs', lambda m: (_ for _ in ()).throw(requests.exceptions.ReadTimeout('t')))
    health.probe('rs')
    health.probe('rs')
    check('熔断中', health.is_tripped('rs') is True, '')
    health.reset()
    check('reset 后不再跳过', health.is_tripped('rs') is False, '')
    check('reset 后回到 unchecked',
          health.overview()[0]['status'] in ('unchecked',), '')
    unmod('rs')


def test_success_records_elapsed():
    print('\n===== 成功路径必须记录耗时(NAS 实测抓到的回归) =====')
    health.reset()

    def slow_ok(movie):
        time.sleep(0.25)          # 模拟真实网络耗时
        movie.title = '真实标题XYZ'
    mkmod('slowok', slow_ok)
    r = health.probe('slowok')
    check('成功 -> ok', r['status'] == 'ok', f"got {r['status']!r}")
    # 回归点: 曾因成功路径不记 elapsed 而恒为 0, 使前端"耗时"列对正常源失去意义
    check('成功源记录到真实耗时(>200ms)', r['elapsed_ms'] > 200,
          f"elapsed_ms={r['elapsed_ms']}")
    with health._lock:
        rec = dict(health._get('slowok'))
    check('档案中耗时也被更新', rec['elapsed_ms'] > 200, str(rec['elapsed_ms']))
    unmod('slowok')


if __name__ == '__main__':
    test_classify()
    test_classify_dns()
    test_sanitize()
    test_breaker_trips_on_repeated_failure()
    test_breaker_skips_scrape()
    test_breaker_cooldown_to_half_open()
    test_breaker_half_open_success_recovers()
    test_breaker_half_open_failure_reopens()
    test_cooldown_exponential_capped()
    test_not_found_never_trips()
    test_shell_page_is_invalid_not_ok()
    test_http_404_becomes_http_error()
    test_site_blocked_detected()
    test_passive_observation()
    test_passive_failure_can_trip()
    test_passive_success_recovers()
    test_covers_all_sources()
    test_overview_sorting_and_cooldown_decay()
    test_merge_into_scrape_sources()
    test_probe_all_resilience()
    test_stale_guard()
    test_reports()
    test_unknown_source_not_skipped()
    test_reset()
    test_success_records_elapsed()
    print(f'\n{"=" * 54}')
    print(f'通过 {len(PASSED)} 项, 失败 {len(FAILED)} 项')
    for f in FAILED:
        print(f'  FAIL: {f}')
    print('health.py 熔断验证全部通过 ✅' if not FAILED else '存在失败项 ❌')
    raise SystemExit(1 if FAILED else 0)