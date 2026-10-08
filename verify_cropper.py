# -*- coding: utf-8 -*-
"""验证「封面 AI 裁剪」整条链路

为什么要单独写这个脚本
----------------------
README 里曾写着「基于 AI 人体分析裁剪素人等非常规封面的海报」，而实际情况是：

1. 实现早已从「百度人体分析」换成本地 slimeface **人脸检测**，措辞过时；
2. `config.yml` 里 `crop.engine: null`，**默认并不启用**；
3. 最隐蔽的一点：`SlimefaceCropper` 原先是**裸 except** 静默回退默认裁剪，
   失败原因（含「没检测到人脸」这类完全正常的情况）一点痕迹都不留 ——
   于是「配置开了 AI 裁剪」和「真的用了 AI 裁剪」根本无法区分。

覆盖内容：开关能否写回 config.yml（保留注释）、裁剪器四种走法是否如实上报、
process_poster 是否把结果带回给调用方、运行时/接口能否观察到状态、
以及前端那几个决定是否提示的纯函数。
"""
import os
import re
import sys
import json
import shutil
import tempfile
import subprocess
from types import SimpleNamespace

import yaml


def find_node():
    """定位 node（managed node 目录带版本号、会随环境升级漂移，不能写死）"""
    import glob
    base = os.path.expanduser('~/.workbuddy/binaries/node/versions')
    cands = sorted(glob.glob(os.path.join(base, '*/node.exe')), key=os.path.getmtime)
    if cands:
        return cands[-1]
    return shutil.which('node') or 'node'


PROJ = os.path.dirname(os.path.abspath(__file__))
NODE = find_node()
sys.path.insert(0, PROJ)
os.chdir(PROJ)          # Cfg 需要能找到 config.yml

from PIL import Image
import slimeface as slime_mod

from javsp.config import Cfg
from javsp.config_io import diff_leaves, write_config_preserving_comments
from javsp.config_reload import describe_runtime, apply_config_changes, reload_runtime_config
from javsp.cropper import get_cropper
from javsp.cropper.interface import DefaultCropper
from javsp.cropper.slimeface_crop import SlimefaceCropper
import javsp.core as core

PASS, FAIL = [], []


def check(name, cond, extra=''):
    (PASS if cond else FAIL).append(name)
    print(('PASS ' if cond else 'FAIL ') + name + (('  | ' + extra) if extra else ''))


SRC = os.path.join(PROJ, 'config.yml')
original = open(SRC, 'rb').read()
tmpdir = tempfile.mkdtemp(prefix='javsp_crop_')


def fresh(path=None):
    p = path or os.path.join(tmpdir, 'config.yml')
    with open(p, 'wb') as f:
        f.write(original)
    return p


def comment_count(p):
    return sum(1 for l in open(p, encoding='utf-8', newline='').read().splitlines()
               if l.strip().startswith('#'))


def read_text(p):
    return open(p, encoding='utf-8', newline='').read()


base_comments = comment_count(SRC)
current = Cfg().model_dump(mode='json')

# 仓库 config.yml 中 engine 那一行的原文（用于把标量形态改写成嵌套块做反向测试）
ENGINE_LINE_SRC = '      engine: null # null表示禁用图像剪裁'


# ---------------- T1: dict 写回为 flow mapping ----------------
p = fresh()
before = comment_count(p)
n, missing = write_config_preserving_comments(
    p, {('summarizer', 'cover', 'crop', 'engine'): {'name': 'slimeface'}})
text = read_text(p)
check('T1 engine: null -> flow mapping', 'engine: {name: slimeface}' in text,
      repr([l for l in text.splitlines() if 'engine:' in l and '#' not in l.split('engine:')[0]][:1]))
check('T1 无字段定位失败', missing == [], str(missing))
check('T1 注释未丢失', comment_count(p) == before, f'{before} -> {comment_count(p)}')
loaded = yaml.safe_load(text)
check('T1 写回后仍是合法 YAML 且读回嵌套对象',
      loaded['summarizer']['cover']['crop']['engine'] == {'name': 'slimeface'},
      str(loaded['summarizer']['cover']['crop']['engine']))
check('T1 同级其它字段未被误改', loaded['summarizer']['cover']['basename_pattern'] == 'poster')
try:
    Cfg.model_validate(loaded)
    check('T1 能通过配置模型校验', True)
except Exception as e:
    check('T1 能通过配置模型校验', False, str(e))


# ---------------- T2: 嵌套块收敛为标量 ----------------
p = fresh()
t = read_text(p)
check('T2 源文件中存在 engine 行（脚本锚点）', ENGINE_LINE_SRC in t,
      '若失败请同步更新 ENGINE_LINE_SRC')
t_block = t.replace(ENGINE_LINE_SRC, '      engine:\n        name: slimeface')
open(p, 'w', encoding='utf-8', newline='').write(t_block)
n2, missing2 = write_config_preserving_comments(
    p, {('summarizer', 'cover', 'crop', 'engine'): None})
text2 = read_text(p)
check('T2 嵌套块收敛为 engine: null', 'engine: null' in text2)
loaded2 = yaml.safe_load(text2)
check('T2 YAML 解析后 engine 为 None',
      loaded2['summarizer']['cover']['crop']['engine'] is None,
      str(loaded2['summarizer']['cover']['crop']['engine']))
check('T2 旧子行已无残留',
      'name: slimeface' not in loaded2['summarizer']['cover']['crop']['engine']
      if loaded2['summarizer']['cover']['crop']['engine'] is not None else True)
check('T2 注释未丢失', comment_count(p) == before, f'{before} -> {comment_count(p)}')


# ---------------- T3: diff_leaves 能识别出「标量 -> 嵌套」是变更 ----------------
merged = json.loads(json.dumps(current))
merged['summarizer']['cover']['crop']['engine'] = {'name': 'slimeface'}
changes = diff_leaves(current, merged)
check('T3 diff 能识别 engine 从 None 变为 dict',
      ('summarizer', 'cover', 'crop', 'engine') in changes
      and changes[('summarizer', 'cover', 'crop', 'engine')] == {'name': 'slimeface'},
      str(changes.get(('summarizer', 'cover', 'crop', 'engine'))))


# ---------------- T4: 裁剪器四种走法是否如实上报 ----------------
# 构图要点：必须用「宽图 + 主体偏左」。宽图下默认裁剪固定取最右侧，
# 只有人脸不在右边时 AI 才会把它往左挪；若拿纯色图测，任何裁剪结果都一样。
imgdir = tempfile.mkdtemp(prefix='javsp_img_')
fanart = os.path.join(imgdir, 'fanart.jpg')
poster = os.path.join(imgdir, 'poster.jpg')
_rgb = Image.new('RGB', (2000, 1300), (30, 30, 30))
for _x in range(300, 700):
    for _y in range(300, 800):
        _rgb.putpixel((_x, _y), (200, 160, 140))
_rgb.save(fanart)
fanart_img = Image.open(fanart)
_default_output = list(DefaultCropper().crop(fanart_img).getdata())

orig_detect = slime_mod.detectRGB
orig_module = sys.modules.get('slimeface')
try:
    def run_slime(detect_impl):
        slime_mod.detectRGB = detect_impl
        c = SlimefaceCropper()
        return c, c.crop(fanart_img)

    def same_as_default(im):
        return list(im.getdata()) == _default_output

    c_ok, out_ok = run_slime(lambda w, h, b: [(500, 400, 150, 150, 0.98)])
    check('T4 检测到人脸时上报 applied=True', c_ok.last_status['applied'] is True,
          str(c_ok.last_status))
    check('T4 AI 裁剪结果与默认不同（否则等于没生效）', not same_as_default(out_ok))

    c_none, out_none = run_slime(lambda w, h, b: [])
    check('T4 未检测到人脸时上报回退', c_none.last_status['applied'] is False)
    check('T4 回退原因写明是没检测到人脸',
          '未从封面中检测到人脸' in (c_none.last_status['reason'] or ''),
          str(c_none.last_status['reason']))
    check('T4 回退时结果等于默认裁剪', same_as_default(out_none))

    def boom(w, h, b):
        raise RuntimeError('boom')
    c_err, out_err = run_slime(boom)
    check('T4 检测异常时上报回退', c_err.last_status['applied'] is False)
    check('T4 回退原因带上异常信息',
          '人脸检测出错' in (c_err.last_status['reason'] or ''),
          str(c_err.last_status['reason']))

    sys.modules['slimeface'] = None          # 模拟依赖缺失
    c_imp = SlimefaceCropper()
    out_imp = c_imp.crop(fanart_img)
    check('T4 依赖缺失时上报回退', c_imp.last_status['applied'] is False)
    check('T4 依赖缺失原因明确',
          'slimeface' in (c_imp.last_status['reason'] or ''),
          str(c_imp.last_status['reason']))
    check('T4 依赖缺失不至于让裁剪崩掉', same_as_default(out_imp))
finally:
    sys.modules['slimeface'] = orig_module
    slime_mod.detectRGB = orig_detect

_default_cropper = DefaultCropper()
_default_cropped = _default_cropper.crop(fanart_img)
check('T4 默认裁剪器把自己这次裁剪标为已应用',
      _default_cropped is not None and _default_cropper.last_status['applied'] is True,
      str(_default_cropper.last_status))
check('T4 get_cropper(None) 仍返回默认裁剪器',
      type(get_cropper(None)).__name__ == 'DefaultCropper')


# ---------------- T5/T6: process_poster 是否如实回报裁剪方式 ----------------
movie = SimpleNamespace(
    info=SimpleNamespace(uncensored=True, label='ABC-123'),
    data_src='javdb',
    fanart_file=fanart,
    poster_file=poster,
    hard_sub=False,
    uncensored=False,
)

check('T5 默认配置下 crop.engine 为 None', Cfg().summarizer.cover.crop.engine is None)
try:
    crop_default = core.process_poster(movie)
except Exception as e:
    crop_default = None
    check('T5 默认裁剪不报错', False, f'{type(e).__name__}: {e}')
if crop_default is not None:
    check('T5 默认裁剪不报错', True)
check('T5 未启用引擎时返回 engine=None', crop_default['engine'] is None,
      str(crop_default))
# 语义统一：没启用 AI 时 applied 必须是 False，否则界面会误判成「AI 已生效」
check('T5 未启用 AI 时 applied=False', crop_default['applied'] is False, str(crop_default))
check('T5 未启用时给出原因说明', '默认居中裁剪' in (crop_default['reason'] or ''),
      str(crop_default['reason']))
check('T5 poster 文件已生成', os.path.exists(poster))

try:
    # ---- 真实改动仓库 config.yml 并热重载（验证「开关一开就生效」）----
    res = apply_config_changes(
        SRC, {('summarizer', 'cover', 'crop', 'engine'): {'name': 'slimeface'}})
    check('T6 写入并热重载成功', res['reloaded'] and res['missing'] == [], str(res))
    check('T6 运行时 engine 已切换',
          describe_runtime()['cover_crop']['engine'] == 'slimeface',
          str(describe_runtime()['cover_crop']))
    check('T6 运行时报告已启用', describe_runtime()['cover_crop']['enabled'] is True)
    check('T6 运行时给出依赖可用性', isinstance(describe_runtime()['cover_crop']['available'], bool))

    slime_mod.detectRGB = lambda w, h, b: [(500, 400, 150, 150, 0.98)]
    crop_ai = core.process_poster(movie)
    check('T6 process_poster 返回引擎名', crop_ai['engine'] == 'slimeface', str(crop_ai))
    check('T6 AI 生效时 applied=True', crop_ai['applied'] is True, str(crop_ai))

    slime_mod.detectRGB = lambda w, h, b: []
    crop_fb = core.process_poster(movie)
    check('T6 检测不到人脸时如实上报未生效',
          crop_fb['applied'] is False and '未从封面中检测到人脸' in (crop_fb['reason'] or ''),
          str(crop_fb))
    check('T6 回退时仍带出引擎名（便于界面提示）', crop_fb['engine'] == 'slimeface')

    # 整理结果里也应有 crop 字段（Web 侧据此提示用户）
    import inspect
    src = inspect.getsource(core.organize_movie)
    check('T6 organize_movie 已把裁剪结果放进 result', "'crop'" in src or '"crop"' in src)

    # ---- T7: 接口端到端开关 ----
    # T6 跑完后配置已停在「开启」态，若直接 PUT 同值会因无差异返回 unchanged。
    # 先复位到基线，保证 T7 自身从「关闭 → 开启 → 关闭」完整可复现。
    open(SRC, 'wb').write(original)
    reload_runtime_config()
    check('T7 基线已复位为未启用', describe_runtime()['cover_crop']['engine'] is None)

    from fastapi.testclient import TestClient
    import javsp.server as server
    client = TestClient(server.app)
    r_on = client.put('/api/config', json={
        'summarizer': {'cover': {'crop': {'engine': {'name': 'slimeface'}}}}})
    j_on = r_on.json() if r_on.headers.get('content-type', '').startswith('application/json') else {}
    check('T7 PUT 开启裁剪返回成功', r_on.status_code == 200 and j_on.get('status') == 'applied',
          str(j_on)[:160])
    check('T7 文件写回为 flow mapping', 'engine: {name: slimeface}' in read_text(SRC))
    check('T7 运行时随之生效', describe_runtime()['cover_crop']['engine'] == 'slimeface')

    r_off = client.put('/api/config', json={
        'summarizer': {'cover': {'crop': {'engine': None}}}})
    j_off = r_off.json() if r_off.headers.get('content-type', '').startswith('application/json') else {}
    check('T7 PUT 关闭裁剪返回成功', r_off.status_code == 200 and j_off.get('status') == 'applied',
          str(j_off)[:160])
    check('T7 文件回到 engine: null', 'engine: null' in read_text(SRC))
    check('T7 运行时 engine 变回 None', describe_runtime()['cover_crop']['engine'] is None)
    check('T7 运行时 enabled 变回 False', describe_runtime()['cover_crop']['enabled'] is False)
    rt = client.get('/api/config/runtime').json()
    check('T7 /api/config/runtime 返回 cover_crop 段',
          'cover_crop' in (rt.get('runtime') or {}), str(list((rt.get('runtime') or {}).keys())))
finally:
    slime_mod.detectRGB = orig_detect
    open(SRC, 'wb').write(original)
    reload_runtime_config()
    shutil.rmtree(imgdir, ignore_errors=True)
    check('T8 仓库 config.yml 已按字节还原', open(SRC, 'rb').read() == original)
    check('T8 运行时已回到初始配置', describe_runtime()['cover_crop']['engine'] is None)

shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------- T9: 前端判断是否提示的那几个纯函数 ----------------
vue_path = os.path.join(PROJ, 'frontend', 'src', 'App.vue')
vue = open(vue_path, 'r', encoding='utf-8').read()
# 逐个**按函数**提取（不能用一个大正则抓区间：这几个函数定义之间还夹着
# `const batch = ref(...)` 之类的声明，整段求值会因为 ref 未定义而报错）
FN_NAMES = ['readCropEnabled', 'buildCropEngine', 'isCropAvailable', 'cropResultNote']
parts = []
for fn in FN_NAMES:
    mm = re.search(r'function ' + fn + r'\(.*?\n\}', vue, re.S)
    if mm is None:
        check('T9 能提取到函数 ' + fn, False, 'App.vue 中未找到该函数')
        parts = []
        break
    parts.append(mm.group(0))
check('T9 能从 App.vue 提取到裁剪相关函数', len(parts) == len(FN_NAMES),
      f'{len(parts)}/{len(FN_NAMES)}')
if parts:
    js_fn = '\n\n'.join(parts)
    js_case = r"""
const t = new Function(js_fn + '; return { readCropEnabled, buildCropEngine, isCropAvailable, cropResultNote };')();
const out = [];
function t9(name, cond, extra) { out.push({ name, cond: !!cond, extra: extra || '' }); }

t9('T9 引擎有 name 视为开启', t.readCropEnabled({ engine: { name: 'slimeface' } }) === true);
t9('T9 engine 为 null 视为关闭', t.readCropEnabled({ engine: null }) === false);
t9('T9 缺 engine 字段视为关闭', t.readCropEnabled({}) === false);
t9('T9 整个 crop 为 null 不报错', t.readCropEnabled(null) === false);

t9('T9 开启时构造出 {name} 载荷', t.buildCropEngine(true) !== null && t.buildCropEngine(true).name === 'slimeface');
t9('T9 关闭时构造 null（写回为 engine: null）', t.buildCropEngine(false) === null);

t9('T9 依赖可用判定为真', t.isCropAvailable({ cover_crop: { available: true } }) === true);
t9('T9 依赖缺失判定为假', t.isCropAvailable({ cover_crop: { available: false } }) === false);
t9('T9 运行时为 null 时不报错', t.isCropAvailable(null) === false);

t9('T9 未开启 AI 裁剪时不提示', t.cropResultNote({ engine: null, applied: false }) === null);
t9('T9 未传 crop 时不提示', t.cropResultNote(null) === null);
const okNote = t.cropResultNote({ engine: 'slimeface', applied: true, reason: null });
t9('T9 AI 生效时给出正面提示', typeof okNote === 'string' && okNote.indexOf('人脸检测') >= 0, String(okNote));
const badNote = t.cropResultNote({ engine: 'slimeface', applied: false, reason: '未从封面中检测到人脸' });
t9('T9 AI 未生效时明确点出未生效', typeof badNote === 'string' && badNote.indexOf('未生效') >= 0, String(badNote));
t9('T9 未生效时带上具体原因', typeof badNote === 'string' && badNote.indexOf('未从封面中检测到人脸') >= 0, String(badNote));
console.log(JSON.stringify(out));
"""
    runner = os.path.join(tempfile.gettempdir(), 'javsp_crop_vue_test.js')
    with open(runner, 'w', encoding='utf-8') as f:
        f.write('const js_fn = %s;\n%s' % (json.dumps(js_fn), js_case))
    r = subprocess.run([NODE, runner], capture_output=True, text=True, timeout=120)
    try:
        cases = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        cases = []
        check('T9 node 用例执行失败', False, (r.stdout or '')[:300] + (r.stderr or '')[:300])
    for c in cases:
        check(c['name'], c['cond'], c['extra'])
    os.remove(runner)

print('---')
print(f'PASS {len(PASS)}  FAIL {len(FAIL)}')
if FAIL:
    print('FAILED:')
    for n in FAIL:
        print('  - ' + n)
else:
    print('ALL GREEN')
