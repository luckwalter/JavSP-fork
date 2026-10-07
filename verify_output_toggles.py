# -*- coding: utf-8 -*-
"""验证「输出控制」开关：封面 / fanart / 剧照 / NFO 能否分别关闭

为什么要单独写这个脚本
----------------------
如果元数据最终交给 Jellyfin / Emby 自行管理，本项目再去下载高清封面（单张 8-10 MiB）、
裁剪 poster、抓剧照、写 NFO 全是重复劳动。因此需要能把每项产物单独关掉。

这里要守住的其实是三件事：

1. **关掉就不能生成**：不是「生成了再隐藏」，而是连下载那一步都别做
   （poster 与 fanart 同源，都关掉时才整次下载都不该发生）。
2. **别对外说谎**：被跳过的项目，`result` 里不能再给出那个不存在的文件路径，
   否则 Web 界面以为生成成功了。
3. **CLI 不能漂移**：`javsp/__main__.py` 有一份独立的整理流程，不走 core.organize_movie。
   历史上 Web 与 CLI 各改各的已经吃过亏（缺 UID 的时候后端新增字段导致只读到这里被修改），
   所以两边必须共用同一个判据 `core.output_enabled`。
"""
import os
import re
import ast
import sys
import copy
import json
import atexit
import shutil
import tempfile
import subprocess

PASS, FAIL = [], []


def check(name, cond, extra=''):
    (PASS if cond else FAIL).append(name)
    print(('PASS ' if cond else 'FAIL ') + name + (('  | ' + extra) if extra else ''))


def find_node():
    """定位 node（managed node 目录带版本号、会随环境升级漂移，不能写死）"""
    import glob
    base = os.path.expanduser('~/.workbuddy/binaries/node/versions')
    cands = sorted(glob.glob(os.path.join(base, '*/node.exe')), key=os.path.getmtime)
    if cands:
        return cands[-1]
    return shutil.which('node') or 'node'


PROJ = "C:/Users/luckw/WorkBuddy/2026-10-06-17-16-37/JavSP"
NODE = find_node()
sys.path.insert(0, PROJ)
os.chdir(PROJ)          # Cfg 需要能找到 config.yml

from PIL import Image

from javsp.config import Cfg
from javsp.config_io import diff_leaves
from javsp.config_reload import describe_runtime, apply_config_changes, reload_runtime_config
from javsp.datatype import Movie, MovieInfo
import javsp.core as core

SRC = os.path.join(PROJ, 'config.yml')
original = open(SRC, 'rb').read()
TMP = tempfile.mkdtemp(prefix='javsp_out_toggle_')

# ---------------------------------------------------------------- 假的下载实现
COVER_CALLS = []
FANART_CALLS = []


def fake_download_cover(covers, fanart_path, big_covers=[]):
    """伪造封面下载：本地造一张宽图，不联网"""
    COVER_CALLS.append(fanart_path)
    img = Image.new('RGB', (1200, 800), (70, 70, 70))
    for x in range(0, 520):            # 左侧放一块「主体」，方便观察裁剪
        for y in range(120, 700):
            img.putpixel((x, y), (210, 170, 150))
    img.save(fanart_path)
    return ('http://example.com/cover.jpg', fanart_path)


def fake_download(url, path):
    """伪造下载（剧照用）"""
    FANART_CALLS.append(url)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n' + b'0' * 64)
    return {'elapsed': 0.01, 'rate': 1024}


orig_cover_fn = core.download_cover
orig_dl_fn = core.download
orig_sleep = core.time.sleep
core.download_cover = fake_download_cover
core.download = fake_download
core.time.sleep = lambda _s: None      # 剧照下载间隔不必真等


def exists(p):
    """路径可能为 None（例如关掉 fanart 后 movie.fanart_file 会被置空），不能直接 os.path.exists"""
    return bool(p) and os.path.exists(p)


# 本脚本会真实改写仓库的 config.yml（开关走的就是 Web 保存那条路）。
# 一旦中途抛异常，脚本直接退出而下面的 finally 还没注册，仓库文件就会被留在中间状态
# ——开发过程中真的发生过（config.yml 停在 fanart.enabled=false），所以这里用 atexit 兜底。
_RESTORED = False


def restore():
    global _RESTORED
    if _RESTORED:
        return
    _RESTORED = True
    core.download_cover = orig_cover_fn
    core.download = orig_dl_fn
    core.time.sleep = orig_sleep
    open(SRC, 'wb').write(original)
    reload_runtime_config()
    shutil.rmtree(TMP, ignore_errors=True)


atexit.register(restore)

# ---------------------------------------------------------------- 测试数据
DATA_DIR = os.path.join(PROJ, 'unittest', 'data')
data_file = None
for f in sorted(os.listdir(DATA_DIR)):
    if not f.endswith('.json'):
        continue
    try:
        _i = MovieInfo(from_file=os.path.join(DATA_DIR, f))
    except Exception:
        continue
    if _i.title and _i.actress:
        data_file = f
        break
check('T0 找到可用于实跑的测试数据', data_file is not None, str(data_file))


def set_switches(poster=None, fanart=None, extrafanart=None, nfo=None):
    """把开关写回 config.yml 并热重载（走的是和 Web 保存完全相同的路径）"""
    cur = Cfg().model_dump(mode='json')
    m = copy.deepcopy(cur)
    if poster is not None:
        m['summarizer']['cover']['enabled'] = poster
    if fanart is not None:
        m['summarizer']['fanart']['enabled'] = fanart
    if extrafanart is not None:
        m['summarizer']['extra_fanarts']['enabled'] = extrafanart
    if nfo is not None:
        m['summarizer']['nfo']['enabled'] = nfo
    return apply_config_changes(SRC, diff_leaves(cur, m))


# 本脚本会真实改写仓库里的 config.yml。若上一次运行中途异常退出，文件可能停在中间状态，
# 那样不仅用例会集体误判，连「还原」都会还原到脏状态（开发时真实发生过：config.yml 停在
# fanart.enabled=false，下一次运行据此建立还原基准，于是越跑越歪）。这里先自愈再建立基准。
def _closed_items():
    s = Cfg().model_dump(mode='json')['summarizer']
    return [k for k, sec in (('poster', 'cover'), ('fanart', 'fanart'),
                             ('extrafanart', 'extra_fanarts'), ('nfo', 'nfo'))
            if s.get(sec, {}).get('enabled') is not True]


def ensure_baseline():
    global original
    before = _closed_items()
    if before:
        print(f'  注意：启动时配置项并非全启用（{before}，多为上次运行中断遗留），已先纠正为全启用')
        set_switches(poster=True, fanart=True, extrafanart=True, nfo=True)
        original = open(SRC, 'rb').read()
    return before


_before = ensure_baseline()
check('T0 基线为四项全启用（脏配置已自愈）', not _closed_items(), f'进入时已纠正: {_before}')


def build_movie(tag, with_pics=False):
    """造一部待整理的影片。move_files=False 时 save_dir 就是影片文件所在目录"""
    d = os.path.join(TMP, tag)
    os.makedirs(d, exist_ok=True)
    movie = Movie('ABC-123')
    info = MovieInfo(from_file=os.path.join(DATA_DIR, data_file))
    info.title = 'Toggle Test ' + tag
    info.actress = info.actress[:1] if info.actress else ['未知女优']
    info.score = None
    info.nfo_title = None
    info.preview_pics = ['http://example.com/p1.png'] if with_pics else []
    # covers/big_covers 平时由 info_summary 汇总时动态挂上，这里手工补（本脚本不跑抓取）
    info.covers = ['http://example.com/c1.jpg']
    info.big_covers = []
    movie.info = info
    movie.files = [os.path.join(d, 'ABC-123.mp4')]
    with open(movie.files[0], 'wb') as f:
        f.write(b'fake video')
    movie.scan_root = d
    return movie


def run_case(tag, with_pics=False, **sw):
    """按给定开关跑一次整理，返回 (result, 各产物路径)"""
    COVER_CALLS.clear()
    FANART_CALLS.clear()
    set_switches(**sw)
    movie = build_movie(tag, with_pics=with_pics)
    r = core.organize_movie(movie, translate=False, move_files=False)
    return r, movie


print('--- T1 开关判据与向后兼容 ---')
check('T1 output_enabled 支持四类输出',
      [core.output_enabled(k) for k in core.OUTPUT_TOGGLES] == [True] * 4,
      str(core.OUTPUT_TOGGLES))

# 旧 config.yml 没有这些键时必须仍然可用（默认全开），否则等于强制用户改配置
cur = Cfg().model_dump(mode='json')
stripped = copy.deepcopy(cur)
for sec in ('cover', 'fanart', 'nfo'):
    stripped['summarizer'][sec].pop('enabled', None)
try:
    validated = Cfg.model_validate(stripped)
    check('T1 缺 enabled 键的旧配置仍可被接受', True)
    check('T1 缺失时默认按启用处理', validated.summarizer.cover.enabled is True
          and validated.summarizer.fanart.enabled is True
          and validated.summarizer.nfo.enabled is True)
except Exception as e:
    check('T1 缺 enabled 键的旧配置仍可被接受', False, f'{type(e).__name__}: {e}')
    check('T1 缺失时默认按启用处理', False)

print('--- T2 CLI 与 Web 共用同一判据 ---')
cli_src = open(os.path.join(PROJ, 'javsp', '__main__.py'), encoding='utf-8').read()
core_src = open(os.path.join(PROJ, 'javsp', 'core.py'), encoding='utf-8').read()
check('T2 CLI 引入了 output_enabled', 'output_enabled' in cli_src)
check('T2 CLI 的封面分支走 output_enabled',
      "if output_enabled('poster'):" in cli_src and "if not output_enabled('fanart'):" in cli_src)
check('T2 CLI 的 NFO 分支走 output_enabled', "if output_enabled('nfo'):" in cli_src)
check('T2 CLI 的剧照分支走 output_enabled', "if output_enabled('extrafanart'):" in cli_src)
# CLI 里还有一份 total_step 计数，必须与实际的 check_step 次数同步，否则进度条不准
check('T2 CLI 步骤数随开关动态计算',
      'total_step = 3' in cli_src
      and re.search(r"if output_enabled\('poster'\):\s*\n\s*total_step \+= 1", cli_src) is not None
      and re.search(r"if output_enabled\('nfo'\):\s*\n\s*total_step \+= 1", cli_src) is not None,
      '封面与 NFO 各自对应一次 check_step')
check('T2 Web 侧同样使用 output_enabled', 'output_enabled(' in core_src)

print('--- T3 封面四种组合的实际落盘 ---')
cases = [
    # label,                     开关,                                          是否下载, poster, fanart, skipped 应含
    ('poster=on  fanart=on', dict(poster=True, fanart=True), True, True, True, []),
    ('poster=on  fanart=off', dict(poster=True, fanart=False), True, True, False, ['fanart']),
    ('poster=off fanart=on', dict(poster=False, fanart=True), True, False, True, ['poster']),
    ('poster=off fanart=off', dict(poster=False, fanart=False), False, False, False, ['cover']),
]
for i, (label, sw, need_dl, want_poster, want_fanart, want_skipped) in enumerate(cases):
    r, movie = run_case('t3_%d' % i, extrafanart=False, **sw)
    tag = 'T3 ' + label
    check(f'{tag} | 是否下载封面', (len(COVER_CALLS) > 0) == need_dl,
          f'期望下载={need_dl}, 实际调用 {len(COVER_CALLS)} 次')
    check(f'{tag} | poster 是否生成', exists(movie.poster_file) == want_poster, str(movie.poster_file))
    check(f'{tag} | fanart 是否生成', exists(movie.fanart_file) == want_fanart, str(movie.fanart_file))
    check(f'{tag} | result 如实反映 poster', (r.get('poster_file') is not None) == want_poster,
          str(r.get('poster_file')))
    check(f'{tag} | result 如实反映 fanart', (r.get('fanart_file') is not None) == want_fanart,
          str(r.get('fanart_file')))
    skipped = r.get('skipped') or []
    check(f'{tag} | skipped 恰含 {want_skipped}', all(k in skipped for k in want_skipped)
          and len([k for k in skipped if k != 'extrafanart']) == len(want_skipped), str(skipped))
    if not need_dl:
        check('T3 封面全关时一次下载都没发生（真正省掉无用功）', len(COVER_CALLS) == 0)
        check('T3 封面全关时不算失败', r.get('status') == 'ok', str(r.get('status')))

print('--- T4 NFO 开关 ---')
r, movie = run_case('t4_on', poster=False, fanart=False, extrafanart=False, nfo=True)
check('T4 NFO 开启时生成文件', os.path.exists(movie.nfo_file), str(movie.nfo_file))
check('T4 NFO 开启时 result 给出路径', r.get('nfo_file') is not None)
check('T4 NFO 开启时不记录在 skipped', 'nfo' not in (r.get('skipped') or []), str(r.get('skipped')))
nfo_text = open(movie.nfo_file, encoding='utf-8').read()
check('T4 NFO 内容完整（含 title 标签）', '<title>' in nfo_text)

r, movie = run_case('t4_off', poster=False, fanart=False, extrafanart=False, nfo=False)
check('T4 NFO 关闭时不生成文件', not os.path.exists(movie.nfo_file), str(movie.nfo_file))
check('T4 NFO 关闭时 result 不谎称已生成', r.get('nfo_file') is None, str(r.get('nfo_file')))
check('T4 NFO 关闭时记录在 skipped', 'nfo' in (r.get('skipped') or []), str(r.get('skipped')))

print('--- T5 剧照开关 ---')
r, movie = run_case('t5_on', with_pics=True, poster=False, fanart=False, extrafanart=True, nfo=False)
extra_dir = os.path.join(movie.save_dir, 'extrafanart')
check('T5 剧照开启时创建了目录', os.path.isdir(extra_dir), str(extra_dir))
check('T5 剧照开启时确实下载了', len(FANART_CALLS) >= 1, f'{len(FANART_CALLS)} 次')
check('T5 剧照开启时不记录在 skipped', 'extrafanart' not in (r.get('skipped') or []))

r, movie = run_case('t5_off', with_pics=True, poster=False, fanart=False, extrafanart=False, nfo=False)
extra_dir = os.path.join(movie.save_dir, 'extrafanart')
check('T5 剧照关闭时不创建目录', not os.path.isdir(extra_dir), str(extra_dir))
check('T5 剧照关闭时不下载', len(FANART_CALLS) == 0, f'{len(FANART_CALLS)} 次')
check('T5 剧照关闭时记录在 skipped', 'extrafanart' in (r.get('skipped') or []), str(r.get('skipped')))

print('--- T6 全关：只做重命名，不做任何多媒体产出 ---')
r, movie = run_case('t6_none', with_pics=True, poster=False, fanart=False, extrafanart=False, nfo=False)
saved = os.listdir(movie.save_dir)
check('T6 全关时目录里没有 poster/fanart/nfo',
      not any(n.startswith(('poster', 'fanart', 'movie.')) for n in saved), str(saved))
check('T6 全关时 skipped 记录了被跳过的项',
      all(k in (r.get('skipped') or []) for k in ('cover', 'extrafanart', 'nfo')),
      str(r.get('skipped')))
check('T6 全关时仍不算失败', r.get('status') == 'ok', str(r.get('status')))

print('--- T7 开关经 Web 保存路径写回后运行时跟随 ---')
r = set_switches(poster=False, fanart=True, extrafanart=False, nfo=True)
check('T7 写回成功', r.get('written', 0) >= 1, str(r.get('error') or ''))
check('T7 热重载成功', r.get('reloaded') is True)
rt = describe_runtime()['output']
check('T7 运行时 output 反映开关',
      rt == {'poster': False, 'fanart': True, 'extrafanart': False, 'nfo': True}, str(rt))

# 还原（保持全开），再确认运行时跟着回来
set_switches(poster=True, fanart=True, extrafanart=True, nfo=True)
check('T7 还原后运行时全为启用', all(describe_runtime()['output'].values()))

print('--- T8 前端决定是否提示的纯函数 ---')
vue_path = os.path.join(PROJ, 'frontend', 'src', 'App.vue')
vue = open(vue_path, 'r', encoding='utf-8').read()
FN_NAMES = ['readOutputToggles', 'skippedOutputNote']
parts = []
for fn in FN_NAMES:
    mm = re.search(r'function ' + fn + r'\(.*?\n\}', vue, re.S)
    if mm is None:
        check('T8 能提取到函数 ' + fn, False, 'App.vue 中未找到该函数')
        parts = []
        break
    parts.append(mm.group(0))
check('T8 能从 App.vue 提取到输出控制相关函数', len(parts) == len(FN_NAMES),
      f'{len(parts)}/{len(FN_NAMES)}')

if len(parts) == len(FN_NAMES):
    js_fn = '\n'.join(parts)
    js_case = r"""
const t = new Function(js_fn + '; return { readOutputToggles, skippedOutputNote };')();
const out = [];
function t8(name, cond, extra) { out.push({ name, cond: !!cond, extra: extra || '' }); }

t8('T8 空配置（旧版本）一律按启用处理',
   JSON.stringify(t.readOutputToggles({})) === JSON.stringify({poster:true,fanart:true,extrafanart:true,nfo:true}),
   JSON.stringify(t.readOutputToggles({})));
t8('T8 关掉 poster 只影响 poster',
   t.readOutputToggles({cover:{enabled:false}}).poster === false
   && t.readOutputToggles({cover:{enabled:false}}).fanart === true);
t8('T8 关掉 nfo 只影响 nfo',
   t.readOutputToggles({nfo:{enabled:false}}).nfo === false
   && t.readOutputToggles({nfo:{enabled:false}}).extrafanart === true);
t8('T8 传入 null 不报错且按启用处理', t.readOutputToggles(null).poster === true);
t8('T8 显式 true 被保留', t.readOutputToggles({fanart:{enabled:true}}).fanart === true);

t8('T8 没有跳过项时不提示', t.skippedOutputNote({}) === null);
t8('T8 result 为空时不提示', t.skippedOutputNote(null) === null);
const note = t.skippedOutputNote({ skipped: ['cover', 'nfo'] });
t8('T8 有跳过项时给出提示', typeof note === 'string' && note.indexOf('封面下载') >= 0, String(note));
t8('T8 提示里带上 NFO', typeof note === 'string' && note.indexOf('NFO') >= 0, String(note));
t8('T8 提示点明是设置导致的', typeof note === 'string' && note.indexOf('输出控制') >= 0, String(note));
console.log(JSON.stringify(out));
"""
    runner = os.path.join(tempfile.gettempdir(), 'javsp_out_toggle_vue.js')
    with open(runner, 'w', encoding='utf-8') as f:
        f.write('const js_fn = %s;\n%s' % (json.dumps(js_fn), js_case))
    p = subprocess.run([NODE, runner], capture_output=True, text=True, timeout=120)
    try:
        cases = json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        cases = []
        check('T8 node 用例执行失败', False, (p.stdout or '')[:300] + (p.stderr or '')[:300])
    for c in cases:
        check(c['name'], c['cond'], c['extra'])
    os.remove(runner)

# ---------------------------------------------------------------- 还原现场
restore()
check('T9 仓库 config.yml 已按字节还原', open(SRC, 'rb').read() == original)
check('T9 运行时输出开关已还原为全启用',
      all(describe_runtime()['output'].values()), str(describe_runtime()['output']))

print('---')
print(f'PASS {len(PASS)}  FAIL {len(FAIL)}')
if FAIL:
    print('FAILED:')
    for n in FAIL:
        print('  - ' + n)
else:
    print('ALL GREEN')
sys.exit(1 if FAIL else 0)
