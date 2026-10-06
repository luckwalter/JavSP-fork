"""每站点贡献（sources）端到端验证（合成片源，屏蔽真实网络）

背景：v0.1.6 已把各站点抓取结果经 `_summarize_sources()` 透传到 SSE
（/api/scrape 的 result、/api/batch 的 movie_done），但前端长期未消费。
本脚本验证「后端确实吐数据」+「前端转换函数逻辑正确」两半，避免只有一半通。

前端函数的验证方式：直接从 frontend/src/App.vue 源码里正则提取
sourceRows / sourceSummary 两个函数求值后执行 —— 测的是真实源码而非副本，
源码改了测试用例自动跟着变，不会出现「测试与实现漂移」。

覆盖:
  T1 /api/batch 的 movie_done 事件携带 sources，且成功部结构正确
  T2 失败部（站点未收录）的 sources 各站点均无贡献
  T3 /api/scrape 的 result 事件同样携带 sources
  T4 前端 sourceRows 把对象转成带 contributed 标记的行
  T5 前端 sourceSummary 汇总为「有效/总数」，空数据降级不报错
"""
import os
import re
import sys
import json
import shutil
import tempfile
import subprocess

PROJ = "C:/Users/luckw/WorkBuddy/2026-10-06-17-16-37/JavSP"
NODE = "C:/Users/luckw/.workbuddy/binaries/node/versions/22.22.2-3/node.exe"
sys.path.insert(0, PROJ)
os.chdir(PROJ)          # Cfg 需能找到 config.yml

import javsp.core as core
import javsp.server as server
from javsp.config import Cfg
from javsp.datatype import MovieInfo

PASS, FAIL = [], []


def check(name, cond, extra=''):
    (PASS if cond else FAIL).append(name)
    print(('PASS ' if cond else 'FAIL ') + name + (('  | ' + extra) if extra else ''))


SIZE = 233 * 1024 * 1024          # 需 >= scanner.minimum_size(232MiB)
base = tempfile.mkdtemp(prefix='javsp_src_')
src = os.path.join(base, '影片源')
os.makedirs(src)
with open(os.path.join(src, 'ABP-123.mp4'), 'wb') as f:
    f.write(b'\0' * SIZE)
print(f'合成片源就绪: {src}')

# ---------------- mock: 爬虫（只让 ABP-123 成功） ----------------
core.import_crawlers()
patched = {}
for cid in Cfg().crawler.selection.normal:
    mod = sys.modules.get(f'javsp.web.{cid.value}')
    if not mod or not hasattr(mod, 'parse_data'):
        continue

    def make():
        def p(info):
            if getattr(info, 'dvdid', None) != 'ABP-123':
                return                       # 模拟未收录 -> 该部应计为失败
            info.title = '测试标题'
            info.actress = ['测试女优']
            info.genre = ['剧情']
            info.cover = 'http://example.com/cover.jpg'
            info.covers = ['http://example.com/cover.jpg']
            info.big_covers = []
            info.preview_pics = []
        return p

    patched[cid.value] = mod.parse_data
    mod.parse_data = make()

core.download_cover = lambda covers, fanart_path, big_covers=[]: ('http://example.com/cover.jpg', fanart_path)
core.process_poster = lambda movie: None
core.translate_movie_info = lambda info: None


def parse_sse(text):
    """把 SSE 文本流解析成事件字典列表"""
    evs = []
    for block in text.split('\n\n'):
        line = block.strip()
        if line.startswith('data: '):
            try:
                evs.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                pass
    return evs


try:
    from fastapi.testclient import TestClient
    client = TestClient(server.app)

    # ---------------- T1/T2: /api/batch 的 sources ----------------
    scan = client.post('/api/scan', json={'path': src}).json()
    guid = scan['movies'][0]['guid']
    events = parse_sse(client.post('/api/batch', json={'guids': [guid], 'organize': False}).text)
    done = next((e for e in events if e.get('type') == 'movie_done'), {})

    check('T1 movie_done 携带 sources 字段', 'sources' in done, str(sorted(done.keys())))
    s = done.get('sources') or {}
    check('T1 sources 为「站点名 -> 明细」字典且非空', isinstance(s, dict) and len(s) > 0, str(len(s)))

    first_key = next(iter(s), None)
    detail = s.get(first_key, {}) if first_key else {}
    need = {'dvdid', 'title', 'has_cover', 'has_genre', 'has_actress', 'uncensored', 'contributed'}
    check('T1 明细含约定的 7 个字段（含 contributed）', need.issubset(set(detail)), str(sorted(detail)))

    ok_detail = [v for v in s.values() if isinstance(v, dict) and v.get('contributed')]
    check('T1 成功部至少一个站点有实际贡献', len(ok_detail) >= 1, f'有贡献站点数={len(ok_detail)}')
    check('T1 成功部的封面/分类/女优标记为真',
          all([v.get('has_cover'), v.get('has_genre'), v.get('has_actress')] for v in ok_detail))

    # ---------------- T2: 失败部（站点未收录） ----------------
    # 回归守卫: dvdid 是「输入番号」, 站点未收录时它依然非空; 若拿它判贡献会导致
    # 所有站点永远显示「有贡献」(8/8), 使该列失去意义。故断言 contributed 全为 False。
    with open(os.path.join(src, 'SSIS-456.mp4'), 'wb') as f:
        f.write(b'\0' * SIZE)
    scan2 = client.post('/api/scan', json={'path': src}).json()
    bad_guid = next(m['guid'] for m in scan2['movies'] if m['dvdid'] == 'SSIS-456')
    ev2 = parse_sse(client.post('/api/batch', json={'guids': [bad_guid], 'organize': False}).text)
    done2 = next((e for e in ev2 if e.get('type') == 'movie_done'), {})
    s2 = done2.get('sources') or {}
    vals = [v for v in s2.values() if isinstance(v, dict)]
    if vals:
        none_ok = all(not v.get('contributed') for v in vals)
        check('T2 失败部各站点 contributed 均为 False', none_ok, str(s2)[:120])
        dvdid_nonempty = any(v.get('dvdid') for v in vals)
        check('T2 回归守卫: dvdid 非空但 contributed 仍为 False（证明未用 dvdid 判贡献）',
              dvdid_nonempty and none_ok,
              f'dvdid样例={vals[0].get("dvdid")!r} contributed样例={vals[0].get("contributed")!r}')
    else:
        check('T2 失败部无站点数据（前端应显示“无数据”而非报错）', True, 'sources 为空')

    # ---------------- T3: /api/scrape 的 result 也带 sources ----------------
    ev3 = parse_sse(client.post('/api/scrape', json={'avid': 'ABP-123'}).text)
    res = next((e for e in ev3 if e.get('type') == 'result'), {})
    check('T3 /api/scrape 的 result 携带 sources', bool(res.get('sources')), str(sorted(res.keys())))

finally:
    for cid, orig in patched.items():
        sys.modules[f'javsp.web.{cid}'].parse_data = orig
    shutil.rmtree(base, ignore_errors=True)
    print('临时片源已清理')

# ---------------- T4/T5: 前端转换函数（直接取 App.vue 源码求值） ----------------
vue_path = os.path.join(PROJ, 'frontend', 'src', 'App.vue')
vue = open(vue_path, 'r', encoding='utf-8').read()
m = re.search(r'function sourceRows.*?(?=\nfunction tagType)', vue, re.S)
check('T4 能从 App.vue 提取到前端转换函数源码', m is not None)
if m:
    js_fn = m.group(0)
    js_case = r"""
const t = new Function(js_fn + '; return { sourceRows, sourceSummary };')();
const out = [];
function t4(name, cond, extra) { out.push({ name, cond: !!cond, extra: extra || '' }); }

// 正常数据：两个站点，一个有完整贡献、一个空（dvdid 都非空，模拟「输入番号」的坑）
const s = {
  javdb: { dvdid: 'ABP-123', title: '标题A', has_cover: true, has_genre: true, has_actress: true, uncensored: false, contributed: true },
  airav: { dvdid: 'ABP-123', title: null, has_cover: false, has_genre: false, has_actress: false, uncensored: false, contributed: false },
};
const rows = t.sourceRows(s);
t4('T4 对象转成数组且站点名保留', rows.length === 2 && rows.map(r => r.site).join(',') === 'javdb,airav', JSON.stringify(rows.map(r => r.site)));
const jd = rows.find(r => r.site === 'javdb');
const av = rows.find(r => r.site === 'airav');
t4('T4 有数据站点 contributed=true', jd.contributed === true);
t4('T4 空数据站点 contributed=false（前端标“无贡献”）', av.contributed === false);
// 全空站点（连 dvdid 都没有）应整体降级为短横线，而不是 undefined
const empty = t.sourceRows({ z: {} })[0];
t4('T4 空字段降级为短横线而非 undefined',
   empty.dvdid === '-' && empty.title === '-' && av.title === '-',
   `${empty.dvdid}/${empty.title}`);
t4('T4 布尔字段规范化为 true/false', jd.has_cover === true && av.has_cover === false);
// 坑回归: 只有 dvdid(输入番号) 而无任何成果字段时, 不能算有贡献
const onlyDvdid = t.sourceRows({ x: { dvdid: 'ABP-123', title: null, has_cover: false, has_genre: false, has_actress: false } });
t4('T4 只有 dvdid 而无成果字段时 contributed=false', onlyDvdid[0].contributed === false, String(onlyDvdid[0].contributed));
// 后端未提供 contributed 时回退到本地判据（同样不能算 dvdid）
const legacy = t.sourceRows({ x: { dvdid: 'ABP-123', has_cover: false, has_genre: false, has_actress: false },
                              y: { dvdid: 'ABP-123', has_genre: true } });
t4('T4 后端缺 contributed 时按成果字段回退', legacy[0].contributed === false && legacy[1].contributed === true);

// 异常输入不应抛错
t4('T4 sources 为 null 时返回空数组', t.sourceRows(null).length === 0);
t4('T4 sources 为 undefined 时返回空数组', t.sourceRows(undefined).length === 0);
t4('T4 sources 为非法类型时不抛错', t.sourceRows('bad').length === 0);

// 汇总文本
t4('T5 汇总为「有效/总数」', t.sourceSummary(s) === '1/2 站点', t.sourceSummary(s));
t4('T5 空数据降级为“无数据”', t.sourceSummary(null) === '无数据', t.sourceSummary(null));
t4('T5 全部有贡献时为 2/2', t.sourceSummary({
  a: { dvdid: 'X', has_cover: true }, b: { dvdid: 'Y', has_cover: true }
}) === '2/2 站点');

console.log(JSON.stringify(out));
"""
    runner = os.path.join(base if os.path.exists(base) else tempfile.gettempdir(), '_t.js')
    os.makedirs(os.path.dirname(runner), exist_ok=True)
    with open(runner, 'w', encoding='utf-8') as f:
        f.write('const js_fn = ' + json.dumps(js_fn) + ';\n' + js_case)
    r = subprocess.run([NODE, runner], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        check('T4/T5 前端函数用例执行成功', False, (r.stderr or r.stdout)[:300])
    else:
        try:
            cases = json.loads(r.stdout.strip().splitlines()[-1])
            for c in cases:
                check(c['name'], c['cond'], c['extra'])
        except Exception as e:
            check('T4/T5 前端用例结果可解析', False, f'{e} | {r.stdout[:200]}')

print('---')
print(f'PASS {len(PASS)}  FAIL {len(FAIL)}')
if FAIL:
    print('FAILED:', FAIL)
    sys.exit(1)
print('ALL GREEN')
