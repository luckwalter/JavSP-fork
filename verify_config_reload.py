"""验证「配置保存后即时生效（热重载）」

覆盖两条主线：
1. 正常路径 —— 改代理/超时后，运行时配置对象与**各爬虫出口**都立即跟随
   （爬虫的 request 实例在 import 时就固化了代理，是本功能的重点与难点）
2. 失败路径 —— 写入非法配置时，文件与运行时都要能自动回滚，且服务不能被打瘫

说明：本脚本会临时改写仓库里的 config.yml，结束后按字节还原并校验。
"""
import os
import sys
import shutil
import tempfile
import importlib

PROJ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJ)
os.chdir(PROJ)

from javsp.config import Cfg
from javsp.config_io import diff_leaves
from javsp.config_reload import (
    apply_config_changes, reload_runtime_config, describe_runtime, _refresh_crawler_requests,
)
from javsp.web.base import read_proxy

SRC = os.path.join(PROJ, 'config.yml')
TMP = os.path.join(tempfile.gettempdir(), 'javsp_reload_test')
os.makedirs(TMP, exist_ok=True)

_PASSED = 0
_FAILED = 0


def _norm(u):
    """pydantic 的 Url 会把 'http://h:9999' 规范化成 'http://h:9999/'，比较时忽略尾斜杠"""
    return (u or '').rstrip('/')


def check(name, ok, detail=''):
    global _PASSED, _FAILED
    if ok:
        _PASSED += 1
        print(f'  PASS  {name}' + (f'  ({detail})' if detail else ''))
    else:
        _FAILED += 1
        print(f'  FAIL  {name}' + (f'  ({detail})' if detail else ''))


# 预先加载爬虫模块（模块级 request 实例即热重载要刷新的目标）
CRAWLERS = ['airav', 'javlib', 'javdb', 'fanza', 'javmenu', 'mgstage']
loaded = []
for name in CRAWLERS:
    try:
        importlib.import_module(f'javsp.web.{name}')
        loaded.append(name)
    except Exception as e:
        print(f'  （跳过爬虫 {name}，导入失败：{e}）')
print(f'已加载爬虫模块用于验证: {loaded}\n')

original = open(SRC, 'rb').read()
orig_runtime = describe_runtime()
ORIG_PROXY = orig_runtime['proxy_server']

try:
    # ---------------- T1: 正常热重载 —— 运行时配置对象立即跟随 ----------------
    print('--- T1 运行时配置对象热重载 ---')
    new_proxy = 'http://127.0.0.1:9999'
    cur = Cfg().model_dump(mode='json')
    m = {k: v for k, v in cur.items()}
    m['network'] = dict(cur['network'])
    m['network']['proxy_server'] = new_proxy
    m['network']['retry'] = (cur['network'].get('retry') or 3) + 1 if cur['network'].get('retry', 0) < 9 else 1
    changes = diff_leaves(cur, m)
    res = apply_config_changes(SRC, changes)
    check('T1 写入成功', res['written'] >= 1, f"written={res['written']}")
    check('T1 热重载标记为真', res['reloaded'] is True, str(res['error'] or ''))
    after = describe_runtime()
    check('T1 运行时代理已变为新值', _norm(after['proxy_server']) == _norm(new_proxy),
          f"{ORIG_PROXY} -> {after['proxy_server']}")
    check('T1 运行时 retry 已变为新值', after['retry'] == m['network']['retry'],
          f"retry={after['retry']}")
    check('T1 未触发回滚', res['rolled_back'] is False)

    # ---------------- T2: 爬虫出口（本功能的重点） ----------------
    print('\n--- T2 爬虫模块级 Request 出口刷新 ---')
    craw = after['crawlers']
    check('T2 至少刷新了 1 个爬虫出口', len(res['refreshed']) >= 1, str(res['refreshed']))
    want = dict(read_proxy())
    same = [n for n in craw if craw[n]['proxies'] == want and want]
    check('T2 所有已加载爬虫的代理均跟随新值', len(same) == len(craw),
          f'{len(same)}/{len(craw)} 一致，期望代理={want}')
    # 超时下限必须保留：airav 20s、javlib 5s，不能被全局 timeout 冲掉
    gtimeout = after['timeout']
    if 'airav' in craw:
        check('T2 airav 保留 20s 超时下限', craw['airav']['timeout'] >= 20,
              f"airav.timeout={craw['airav']['timeout']} (全局 {gtimeout})")
    if 'javlib' in craw:
        check('T2 javlib 保留 5s 超时下限', craw['javlib']['timeout'] >= 5,
              f"javlib.timeout={craw['javlib']['timeout']} (全局 {gtimeout})")
    others = [n for n in craw if n not in ('airav', 'javlib')]
    if others:
        ok = all(abs(craw[n]['timeout'] - gtimeout) < 0.001 for n in others)
        check('T2 其余爬虫超时等于全局配置', ok,
              f"{[(n, craw[n]['timeout']) for n in others]} vs 全局 {gtimeout}")

    # ---------------- T3: 保存后注释仍在（热重载不能顺手把注释弄丢） ----------------
    print('\n--- T3 写入后文件完整性 ---')
    text = open(SRC, encoding='utf-8', newline='').read()
    n_comment = sum(1 for l in text.splitlines() if l.strip().startswith('#'))
    n_orig_comment = sum(1 for l in original.decode('utf-8').splitlines()
                         if l.strip().startswith('#'))
    check('T3 注释行数未减少', n_comment >= n_orig_comment,
          f'{n_orig_comment} -> {n_comment}')
    check('T3 文件仍是合法 YAML 且含顶层字段',
          all(k + ':' in text for k in ('scanner', 'network', 'crawler')))

    # ---------------- T4: 失败路径 —— 非法配置必须回滚 ----------------
    print('\n--- T4 非法配置的回滚保护 ---')
    before_bad = open(SRC, 'rb').read()
    runtime_before_bad = describe_runtime()
    bad = {('network', 'proxy_server'): '这不是一个合法的URL'}
    res_bad = apply_config_changes(SRC, bad)
    check('T4 重载失败被正确识别', res_bad['reloaded'] is False,
          str(res_bad['error'])[:60])
    check('T4 文件已回滚（字节一致）', open(SRC, 'rb').read() == before_bad)
    rt_bad = describe_runtime()
    check('T4 运行时配置回到旧值', rt_bad['proxy_server'] == runtime_before_bad['proxy_server'],
          f"{rt_bad['proxy_server']}")
    try:
        Cfg()                      # 服务不能被打瘫：Cfg() 必须仍可正常调用
        check('T4 重载失败后 Cfg() 仍可用（服务未被打瘫）', True)
    except Exception as e:
        check('T4 重载失败后 Cfg() 仍可用（服务未被打瘫）', False, str(e)[:80])

    # ---------------- T5: 真实 API 端到端 ----------------
    print('\n--- T5 接口端到端（PUT /api/config → GET /api/config/runtime） ---')
    from fastapi.testclient import TestClient
    import javsp.server as srv
    client = TestClient(srv.app)
    r0 = client.get('/api/config/runtime')
    check('T5 运行时接口可访问', r0.status_code == 200 and r0.json().get('status') == 'ok')
    api_proxy = 'http://127.0.0.1:8888'
    cur2 = Cfg().model_dump(mode='json')
    payload = {'network': dict(cur2['network'])}
    payload['network']['proxy_server'] = api_proxy
    r1 = client.put('/api/config', json=payload)
    body = r1.json()
    check('T5 PUT 返回 200', r1.status_code == 200, str(body)[:120])
    check('T5 返回状态为已生效', body.get('status') == 'applied', str(body.get('status')))
    r2 = client.get('/api/config/runtime').json()
    check('T5 运行时接口读到新代理（即时生效实证）',
          _norm((r2.get('runtime') or {}).get('proxy_server')) == _norm(api_proxy),
          str((r2.get('runtime') or {}).get('proxy_server')))
    crawlers_now = (r2.get('runtime') or {}).get('crawlers') or {}
    check('T5 接口显示爬虫出口已刷新',
          all(v['proxies'] == dict(read_proxy()) for v in crawlers_now.values()) and bool(crawlers_now),
          f'{len(crawlers_now)} 个爬虫')

    # ---------------- T6: 无变更时不写文件 ----------------
    print('\n--- T6 无变更时不写文件 ---')
    snapshot = open(SRC, 'rb').read()
    r3 = apply_config_changes(SRC, {})
    check('T6 空变更不写文件', r3['written'] == 0 and open(SRC, 'rb').read() == snapshot)

finally:
    shutil.rmtree(TMP, ignore_errors=True)
    # 还原仓库 config.yml 与运行时配置
    open(SRC, 'wb').write(original)
    reload_runtime_config()
    restored = open(SRC, 'rb').read() == original
    print()
    check('仓库 config.yml 已按字节还原', restored)
    check('运行时配置已还原（代理回到初始值）',
          describe_runtime()['proxy_server'] == ORIG_PROXY,
          f"{describe_runtime()['proxy_server']} vs {ORIG_PROXY}")

print()
# ---------------------------------------------------------------- 读写同源
print('--- T 写回路径与读取路径同源 ---')


def test_config_path_follows_read_source():
    """PUT 写回的路径必须与 Cfg 实际读取的路径一致

    原实现硬编码 `<包目录>/config.yml`, 但 confz 支持 `-c/--config` 指定其它文件。
    二者不一致时表现为「PUT 返回 applied + reloaded=true, 但 GET 读到的还是旧值」
    —— 写 A 读 B, 且容器重建后改动全丢。容器化部署通常把配置挂到 `/etc/javsp/config.yml`
    再用 `-c` 指定, 正好命中这个坑(NAS 实测)。
    """
    import re as _re
    src = open(os.path.join(PROJ, 'javsp', 'server.py'), encoding='utf-8').read()
    check('server.py 定义了 _config_file_path()', 'def _config_file_path()' in src)
    check('_config_file_path 从 confz 的 config source 取路径',
          _re.search(r'def _config_file_path\(\).*?get_config_source\(\)', src, _re.S) is not None)
    check('写回调用已改用 _config_file_path()', 'cfg_path = _config_file_path()' in src)
    check('不再硬编码 <包目录>/config.yml 作为写回路径',
          _re.search(r'cfg_path = os\.path\.join\(\s*os\.path\.dirname\(os\.path\.dirname\(',
                     src) is None,
          '旧的硬编码路径又回来了')

    # 行为级: 解析结果应指向真实的 config.yml, 且与 confz 实际使用的 FileSource 一致
    try:
        from javsp.config import get_config_source
        srcs = [str(getattr(s, 'file', '')) for s in get_config_source()]
        check('confz 至少有一个 FileSource', any(srcs), str(srcs))
        from javsp.server import _config_file_path
        p = _config_file_path()
        check('_config_file_path() 返回存在的文件', os.path.isfile(p), p)
        check('与 confz 的 FileSource 路径一致', p in srcs, f'{p} vs {srcs}')
    except Exception as e:      # noqa: BLE001
        check('_config_file_path() 可调用', False, f'{type(e).__name__}: {e}')


test_config_path_follows_read_source()

print()
print(f'{"ALL GREEN" if _FAILED == 0 else "HAS FAILURE"}  PASS={_PASSED}  FAIL={_FAILED}')
sys.exit(1 if _FAILED else 0)
