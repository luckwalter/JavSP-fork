"""verify_task_store.py — TASKS 内存缓存回收策略验证

覆盖 P1-3（长跑内存无界增长）的修复：
  T1  dict 兼容性——TaskStore 必须能无缝顶替普通 dict（这是接入零改动的前提）
  T2  基础读写/删除语义
  T3  TTL 过期回收
  T4  访问刷新时间戳（持续操作不会被误回收）
  T5  容量上限 + 最旧优先淘汰
  T6  活跃任务保护（scrape/organize 期间不回收，且异常路径也必须解除）
  T7  边界：ttl<=0 / max<=0 关闭对应策略；空 store
  T8  线程安全（并发写入 + 断言无异常、最终计数正确）
  T9  server.py 源码扫描——TASKS 确实用 TaskStore、organize/batch 确实加了 mark_active
  T10 stats() 观测字段

运行: python verify_task_store.py
"""
import re
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

PASS = 0
FAIL = 0
FAILURES = []


def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  [PASS] {name}')
    else:
        FAIL += 1
        FAILURES.append(f'{name} {detail}')
        print(f'  [FAIL] {name} {detail}')


def section(title):
    print(f'\n=== {title} ===')


# ---------------------------------------------------------------- T1-T8
def test_task_store():
    from javsp.task_store import TaskStore

    section('T1 dict 兼容性(接入零改动的前提)')
    s = TaskStore(ttl=100, max_tasks=100)
    # server.py 里的既有用法逐条模拟
    s['g1'] = {'v': 1}
    s['g2'] = {'v': 2}
    check('__setitem__ + __getitem__', s['g1'] == {'v': 1})
    check('get 命中', s.get('g1') == {'v': 1})
    check('get 未命中返回 default', s.get('nope', 'D') == 'D')
    check('__contains__ True', 'g1' in s)
    check('__contains__ False', 'nope' not in s)
    check('values() 长度', len(s.values()) == 2)
    check('values() 可迭代', sorted(m['v'] for m in s.values()) == [1, 2])
    check('keys()', sorted(s.keys()) == ['g1', 'g2'])
    check('items()', dict(s.items())['g2'] == {'v': 2})
    check('__len__', len(s) == 2)
    check('__iter__', sorted(iter(s)) == ['g1', 'g2'])
    # 未命中 KeyError 语义与 dict 一致
    try:
        s['missing']
        check('缺失键抛 KeyError', False, '未抛出')
    except KeyError:
        check('缺失键抛 KeyError', True)

    section('T2 基础语义')
    check('重复赋值覆盖而非新增', (s.__setitem__('g1', {'v': 9}) or len(s)) == 2)
    check('覆盖后值正确', s['g1'] == {'v': 9})

    section('T3 TTL 过期回收')
    s = TaskStore(ttl=0.15, max_tasks=1000)
    s['old'] = 1
    time.sleep(0.25)
    evicted = s.evict_expired()
    check('过期条目被回收', 'old' not in s and 'old' in evicted, f'evicted={evicted}')
    check('回收后计数为 0', len(s) == 0)
    # 未过期不应被动到
    s['fresh'] = 2
    check('未过期条目保留', s.evict_expired() == [] and 'fresh' in s)
    # 写入时顺带触发 TTL 清理（正常路径，不依赖显式调用）
    s2 = TaskStore(ttl=0.15, max_tasks=1000)
    s2['a'] = 1
    time.sleep(0.25)
    s2['b'] = 2
    check('写入触发过期清理', 'a' not in s2, 'a 未被顺带清理')

    section('T4 访问刷新时间戳')
    s = TaskStore(ttl=0.3, max_tasks=1000)
    s['live'] = 1
    # 在 TTL 窗口内反复访问，模拟用户持续操作某影片
    for _ in range(4):
        time.sleep(0.1)
        _ = s['live']
    check('持续访问的条目不被回收', 'live' in s, '被误回收了')
    check('get 同样刷新', (s.get('live') is not None))

    section('T5 容量上限 + 最旧优先淘汰')
    s = TaskStore(ttl=0, max_tasks=3)
    for i in range(5):
        s[f'k{i}'] = i
        time.sleep(0.02)   # 保证时间戳有先后顺序
    check('超容量后收敛到上限', len(s) == 3, f'实际 {len(s)}')
    check('保留的是最新的三个', sorted(s.keys()) == ['k2', 'k3', 'k4'], f"实际 {sorted(s.keys())}")

    section('T6 活跃任务保护')
    s = TaskStore(ttl=0, max_tasks=2)
    s['a'] = 1
    s['b'] = 2
    with s.mark_active('a'):
        check('活跃期 is_active', s.is_active('a'))
        s['c'] = 3   # 触发容量淘汰，a 正在处理必须保住
        check('活跃条目在容量淘汰中存活', 'a' in s, '活跃条目被误删')
        check('非活跃的 b 被淘汰', 'b' not in s)
    check('退出后不再活跃', not s.is_active('a'))

    # 异常路径必须解除保护，否则条目永久不被回收
    s = TaskStore(ttl=0, max_tasks=1)
    s['x'] = 1
    try:
        with s.mark_active('x'):
            raise RuntimeError('模拟处理失败')
    except RuntimeError:
        pass
    check('异常路径解除活跃保护', not s.is_active('x'))
    s['y'] = 2   # 触发淘汰，此时 x 已非活跃应可被淘汰
    check('异常后条目可被正常回收', 'x' not in s, '异常后 x 仍被永久保护')

    # TTL 同样跳过活跃条目
    s = TaskStore(ttl=0.1, max_tasks=100)
    s['hold'] = 1
    with s.mark_active('hold'):
        time.sleep(0.2)
        check('TTL 清理跳过活跃条目', s.evict_expired() == [] and 'hold' in s)
    check('退出后可被 TTL 回收', s.evict_expired() == ['hold'])

    section('T7 边界')
    s = TaskStore(ttl=0, max_tasks=0)   # 两项策略都关闭
    for i in range(50):
        s[f'k{i}'] = i
    check('ttl<=0 且 max<=0 时不回收', len(s) == 50)
    check('evict_expired 在 ttl<=0 时返回空', s.evict_expired() == [])
    empty = TaskStore()
    check('空 store len', len(empty) == 0)
    check('空 store values', empty.values() == [])
    check('空 store evict', empty.evict_expired() == [])

    section('T8 线程安全')
    s = TaskStore(ttl=0, max_tasks=500)
    errors = []

    def worker(base):
        try:
            for i in range(40):
                s[f'g{base * 100 + i}'] = i
                _ = s.values()
                _ = len(s)
                if i % 10 == 0:
                    s.get('g0')
        except Exception as e:      # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check('并发写入无异常', not errors, f'异常={errors[:3]}')
    check('并发后计数正确(8*40=320, 上限500不淘汰)', len(s) == 320, f'实际 {len(s)}')

    section('T10 stats 观测')
    s = TaskStore(ttl=60, max_tasks=9)
    s['a'] = 1
    st = s.stats()
    check('stats.count 正确', st['count'] == 1)
    check('stats 暴露 ttl/max', st['ttl_seconds'] == 60 and st['max_tasks'] == 9)
    check('stats.active 字段存在', 'active' in st and st['active'] == 0)
    with s.mark_active('a'):
        check('stats.active 反映活跃数', s.stats()['active'] == 1)


# ---------------------------------------------------------------- T9
def test_server_integration():
    section('T9 server.py 接入扫描(防漂移)')
    src = (ROOT / 'javsp' / 'server.py').read_text(encoding='utf-8')

    check('server 导入 TaskStore', 'from javsp.task_store import TaskStore' in src)
    check('TASKS 用 TaskStore 实例化',
          re.search(r'TASKS\s*=\s*TaskStore\(', src) is not None)
    check('不再用普通 dict 初始化 TASKS',
          re.search(r'TASKS\s*:\s*Dict\[str,\s*Movie\]\s*=\s*\{\}', src) is None)
    # organize / batch 都必须加活跃保护，否则长任务会被回收导致落盘失败
    check('organize 加了 mark_active', 'with TASKS.mark_active(req.guid):' in src)
    check('batch 加了 mark_active', 'mark_active(*[m.guid for m in movies])' in src)
    # 未用导入清理
    check('typing 未用的 Dict 已移除',
          re.search(r'from typing import[^\n]*\bDict\b', src) is None)


if __name__ == '__main__':
    print('verify_task_store.py — TASKS 内存缓存回收策略验证')
    test_task_store()
    test_server_integration()
    total = PASS + FAIL
    print(f'\n{"=" * 56}')
    print(f'RESULT: PASS={PASS}  FAIL={FAIL}  TOTAL={total}')
    if FAILURES:
        print('失败明细:')
        for f in FAILURES:
            print('  -', f)
    print('=' * 56)
    sys.exit(1 if FAIL else 0)