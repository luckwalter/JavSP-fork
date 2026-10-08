"""core 与 health 集成验证: 熔断是否真的让刮削变快(而非只是打个标记)

核心价值主张验证: 熔断后刮削耗时应显著下降, 且被跳过的源仍出现在 sources 里(标为已跳过),
用户才知道"源被熔断了"而不是"程序漏了源"。

运行: venv312 下 `python verify_health_integration.py`
"""
import os
import sys
import time
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests

import javsp.core as core
import javsp.web.health as health
from javsp.config import Cfg, CrawlerID
from javsp.datatype import Movie
from javsp.web import health as health_mod

PASSED, FAILED = [], []


def check(label, cond, detail=''):
    if cond:
        PASSED.append(label)
        print(f'  [OK ] {label}')
    else:
        FAILED.append(f'{label} -> {detail}')
        print(f'  [FAIL] {label}  {detail}')


def mkmod(name, fn):
    m = types.ModuleType(f'javsp.web.{name}')
    m.parse_data = staticmethod(fn)
    sys.modules[f'javsp.web.{name}'] = m
    return m


def unmod(*names):
    for n in names:
        sys.modules.pop(f'javsp.web.{n}', None)


def fake_movie():
    return Movie('/data/test/IPX-001.mp4')


def test_summary_reports_skipped():
    print('\n===== sources 中被跳过的源仍可见(标为已跳过) =====')
    health.reset()
    target = health.active_sources()[0]
    with health._lock:
        rec = health._get(target)
        rec['breaker'] = health.STATE_OPEN
        rec['consecutive_failures'] = 2
        rec['status'] = 'network_error'
        rec['checked_at'] = time.time()
    check(f'[{target}] 处于熔断', health.is_tripped(target) is True, '')
    # 用真实 MovieInfo(与并行刮削里 all_info 的实际形态一致: 被跳过的源是空 MovieInfo)
    from javsp.datatype import MovieInfo
    mv = fake_movie()
    all_info = {name: MovieInfo(mv) for name in health.active_sources()}
    summary = core._summarize_sources(all_info)
    check('sources 仍含被跳过的源', target in summary, str(list(summary.keys())[:5]))
    check('被跳过源 contributed=False',
          summary[target]['contributed'] is False, repr(summary[target]))
    merged = health_mod.merge_into_scrape_sources(summary)
    row = merged[target]
    check('前端可读到 breaker 状态', row.get('breaker') == health.STATE_OPEN, repr(row))
    check('前端可读到已跳过标记', row.get('skipped') is True, repr(row))


def test_scrape_skips_tripped():
    print('\n===== 刮削时真的跳过熔断源(且不发起请求) =====')
    health.reset()
    target = health.active_sources()[0]
    calls = {'n': 0}

    def _slow_fail(movie):
        calls['n'] += 1
        raise requests.exceptions.ReadTimeout('t')

    mkmod(target, _slow_fail)
    # 制造熔断
    health.probe(target)
    health.probe(target)
    check(f'[{target}] 已熔断', health.is_tripped(target) is True, '')
    calls['n'] = 0                      # 归零, 只看熔断后的实际请求数

    events = []
    t0 = time.perf_counter()
    core.parallel_crawler(fake_movie(), progress_cb=lambda n, s: events.append((n, s)))
    elapsed = time.perf_counter() - t0

    check(f'[{target}] 熔断后未发起任何请求', calls['n'] == 0, f"实际调用 {calls['n']} 次")
    check(f'[{target}] 收到 skipped 事件',
          any(n == f'javsp.web.{target}' and s == 'skipped' for n, s in events),
          str([e for e in events if target in str(e)][:3]))
    check('刮削未整体崩溃(正常返回)', elapsed < 60, f'耗时 {elapsed:.1f}s')
    unmod(target)


def test_elapsed_saved():
    print('\n===== 熔断确实省时间(量化核心价值) =====')
    health.reset()
    # 构造"死源": 每次调用都耗 0.6s 后超时。CFG retry 默认为 3
    dead = health.active_sources()[0]

    def _dead(movie):
        time.sleep(0.6)
        raise requests.exceptions.ReadTimeout('slow-dead')

    mkmod(dead, _dead)
    cfg = Cfg()

    # 第一次: 源尚未熔断, 会完整跑 retry 次重试 => 慢。
    # 注意单次刮削只记 1 次失败(而非 retry 次), 故需连续两次刮削才够熔断阈值=2 ——
    # 这是刻意设计: 一次刮削失败可能只是抖动, 立刻熔断会误杀好源。
    t0 = time.perf_counter()
    core.parallel_crawler(fake_movie())
    before = time.perf_counter() - t0
    ov = {r['source']: r for r in health.overview(include_inactive=False)}
    b1 = ov[dead]['breaker']
    check('单次刮削失败后尚未熔断(阈值=2, 防误杀)', b1 == health.STATE_CLOSED, b1)

    # 第二次: 累计够失败次数 => 熔断
    core.parallel_crawler(fake_movie())
    ov = {r['source']: r for r in health.overview(include_inactive=False)}
    breaker_after = ov[dead]['breaker']
    check('连续两次刮削后熔断', breaker_after == health.STATE_OPEN, breaker_after)

    # 第三次: 已熔断, 应立即跳过 => 快
    t0 = time.perf_counter()
    core.parallel_crawler(fake_movie())
    after = time.perf_counter() - t0

    print(f'     未熔断耗时={before:.2f}s  熔断后耗时={after:.2f}s  '
          f'({dead} breaker={breaker_after})')
    check(f'熔断后明显更快({before:.2f}s -> {after:.2f}s)', after < before * 0.5,
          f'before={before:.2f} after={after:.2f}')
    unmod(dead)


def test_success_not_broken():
    print('\n===== 正常源不受熔断影响(回归防护) =====')
    health.reset()
    target = health.active_sources()[0]

    def _ok(movie):
        movie.title = '有效标题ABC'
        movie.cover = 'http://x/1.jpg'
        movie.genre = ['分类']

    mkmod(target, _ok)
    events = []
    core.parallel_crawler(fake_movie(), progress_cb=lambda n, s: events.append((n, s)))
    check(f'[{target}] 收到 success 事件',
          any(n == f'javsp.web.{target}' and s == 'success' for n, s in events),
          str([e for e in events if target in str(e)][:3]))
    with health._lock:
        rec = health._get(target)
    check('档案记为 ok', rec['status'] == 'ok', rec['status'])
    check('未熔断', rec['breaker'] == health.STATE_CLOSED, rec['breaker'])
    check('现场样本已累计', rec['scrape_samples'] >= 1, str(rec['scrape_samples']))
    unmod(target)


def test_notify_records_health():
    print('\n===== _notify 把刮削结果汇入健康档案 =====')
    health.reset()
    core._notify(None, 'airav', 'success')
    with health._lock:
        rec = dict(health._get('airav'))
    check('成功计入现场样本', rec['scrape_samples'] == 1, str(rec['scrape_samples']))
    check('成功计入成功数', rec['scrape_success'] == 1, str(rec['scrape_success']))
    core._notify(None, 'airav', 'not_found')
    with health._lock:
        rec = dict(health._get('airav'))
    check('未收录也计入样本', rec['scrape_samples'] == 2, str(rec['scrape_samples']))
    check('未收录不算成功', rec['scrape_success'] == 1, str(rec['scrape_success']))
    core._notify(None, 'airav', 'network_error')
    core._notify(None, 'airav', 'network_error')
    check('现场连续网络故障可熔断', health.is_tripped('airav') is True, '')
    core._notify(None, 'airav', 'success')
    check('现场成功可恢复', health.is_tripped('airav') is False, '')


if __name__ == '__main__':
    test_summary_reports_skipped()
    test_scrape_skips_tripped()
    test_elapsed_saved()
    test_success_not_broken()
    test_notify_records_health()
    print(f'\n{"=" * 54}')
    print(f'通过 {len(PASSED)} 项, 失败 {len(FAILED)} 项')
    for f in FAILED:
        print(f'  FAIL: {f}')
    print('熔断集成验证全部通过 ✅' if not FAILED else '存在失败项 ❌')
    raise SystemExit(1 if FAILED else 0)