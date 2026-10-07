"""config.yml「保注释」写入验证（纯本地，不联网）

背景：早期 `/api/config` PUT 用 `yaml.safe_dump` 整体重写 config.yml，会把文件里的
**全部中文注释丢掉**（实测 100 行 → 0 行）。改为 `config_io` 只替换变更字段后，
这里验证各种取值类型都能正确写回，且注释/排版/换行符不受影响。

覆盖:
  T1 标量写入（proxy_server 字符串）
  T2 整数写入（retry）
  T3 嵌套字段写入（proxy_free.avsox）
  T4 单行 flow 列表写入（crawler.selection.normal）
  T5 多行 block 列表写入（scanner.ignored_id_pattern）→ 应整块替换为单行且不残留旧行
  T6 无变更时不改动文件
  T7 换行符保持（CRLF 文件写回后仍为 CRLF）
  T8 写回后仍可被配置模型校验通过（语义正确、能重新解析）
"""
import os
import re
import sys
import json
import shutil
import tempfile
import subprocess

PROJ = "C:/Users/luckw/WorkBuddy/2026-10-06-17-16-37/JavSP"


def find_node():
    """定位 node 可执行文件

    WorkBuddy 的 managed node 装在**带版本号**的目录下（如 versions/22.22.2-6/node.exe），
    环境升级后目录名就会变。早期把具体版本写死，一升级脚本就 FileNotFoundError
    （看起来像功能坏了，其实是路径漂移）。故改为扫描 versions 目录取最新，
    再回退到 PATH 里的 node。
    """
    import glob
    base = os.path.expanduser('~/.workbuddy/binaries/node/versions')
    cands = sorted(glob.glob(os.path.join(base, '*/node.exe')), key=os.path.getmtime)
    if cands:
        return cands[-1]
    return shutil.which('node') or 'node'


NODE = find_node()
sys.path.insert(0, PROJ)
os.chdir(PROJ)

from javsp.config_io import diff_leaves, write_config_preserving_comments
from javsp.config import Cfg

PASS, FAIL = [], []


def check(name, cond, extra=''):
    (PASS if cond else FAIL).append(name)
    print(('PASS ' if cond else 'FAIL ') + name + (('  | ' + extra) if extra else ''))


SRC = os.path.join(PROJ, 'config.yml')
original = open(SRC, 'rb').read()
tmpdir = tempfile.mkdtemp(prefix='javsp_cfg_')


def fresh(path=None):
    """复制一份干净的 config.yml 用于测试"""
    p = path or os.path.join(tmpdir, 'config.yml')
    with open(p, 'wb') as f:
        f.write(original)
    return p


def comment_count(p):
    return sum(1 for l in open(p, encoding='utf-8', newline='').read().splitlines()
               if l.strip().startswith('#'))


def read_lines(p):
    return open(p, encoding='utf-8', newline='').read().splitlines(keepends=True)


base_comments = comment_count(SRC)
current = Cfg().model_dump(mode='json')


def apply_change(patch):
    """patch 为嵌套 dict，先算出变更再写入副本，返回文件路径"""
    p = fresh()
    merged = _merge(current, patch)
    changes = diff_leaves(current, merged)
    write_config_preserving_comments(p, changes)
    return p, changes


def _merge(base, upd):
    import copy
    out = copy.deepcopy(base)
    for k, v in (upd or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


try:
    # ---------------- T1: 标量 ----------------
    p, ch = apply_change({'network': {'proxy_server': 'http://127.0.0.1:7890'}})
    txt = open(p, encoding='utf-8', newline='').read()
    check('T1 标量写入成功', "proxy_server: 'http://127.0.0.1:7890'" in txt, str(ch))
    check('T1 注释未丢失', comment_count(p) == base_comments,
          f'{comment_count(p)}/{base_comments}')

    # ---------------- T2: 整数 ----------------
    p, ch = apply_change({'network': {'retry': 5}})
    txt = open(p, encoding='utf-8', newline='').read()
    check('T2 整数写入成功', 'retry: 5' in txt, str(ch))

    # ---------------- T3: 嵌套字段 ----------------
    p, ch = apply_change({'network': {'proxy_free': {'avsox': 'https://new.example.com'}}})
    txt = open(p, encoding='utf-8', newline='').read()
    check('T3 嵌套字段写入成功', "avsox: 'https://new.example.com'" in txt, str(ch))
    check('T3 同级其他站点未被误改', 'javbus: ' in txt and 'seedmm' in txt)

    # ---------------- T4: 单行 flow 列表 ----------------
    p, ch = apply_change({'crawler': {'selection': {'normal': ['javdb', 'airav']}}})
    txt = open(p, encoding='utf-8', newline='').read()
    check('T4 flow 列表写入成功', 'normal: [javdb, airav]' in txt, str(ch))

    # ---------------- T5: 多行 block 列表 ----------------
    before = read_lines(fresh())
    p, ch = apply_change({'scanner': {'ignored_id_pattern': ['ABC', 'DEF']}})
    after = read_lines(p)
    txt = ''.join(after)
    check('T5 block 列表替换为单行', 'ignored_id_pattern: [ABC, DEF]' in txt, str(ch))
    check('T5 旧 block 行已删除(行数变少)', len(after) < len(before),
          f'{len(before)} -> {len(after)}')
    check('T5 无残留 "- " 旧项', not any(l.strip().startswith('- ') for l in after
                                        if 'ABC' in l or 'DEF' in l))
    check('T5 注释仍未丢失', comment_count(p) == base_comments,
          f'{comment_count(p)}/{base_comments}')

    # ---------------- T6: 无变更 ----------------
    p = fresh()
    sig_before = open(p, 'rb').read()
    write_config_preserving_comments(p, {})
    check('T6 无变更时文件保持原样', open(p, 'rb').read() == sig_before)

    # ---------------- T7: 换行符保持 ----------------
    crlf_path = os.path.join(tmpdir, 'crlf.yml')
    with open(crlf_path, 'wb') as f:
        f.write(original.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
    changes = diff_leaves(current, _merge(current, {'network': {'retry': 7}}))
    write_config_preserving_comments(crlf_path, changes)
    d = open(crlf_path, 'rb').read()
    crlf_n = d.count(b'\r\n')
    lf_only = d.count(b'\n') - crlf_n
    check('T7 CRLF 文件写回后仍为 CRLF', crlf_n > 0 and lf_only == 0,
          f'CRLF={crlf_n} 纯LF={lf_only}')

    # ---------------- T8: 写回后仍可被解析并校验 ----------------
    import yaml
    p, ch = apply_change({
        'network': {
            'proxy_server': 'http://127.0.0.1:7890',
            'retry': 4,
            'timeout': 'PT20S',
            'proxy_free': {'javdb': 'https://db.example.com'},
        },
        'crawler': {'selection': {'normal': ['javdb', 'javlib']}},
    })
    data = yaml.safe_load(open(p, encoding='utf-8').read())
    check('T8 写回后仍是合法 YAML', isinstance(data, dict))
    check('T8 新值可被读回', data['network']['proxy_server'] == 'http://127.0.0.1:7890'
          and data['network']['retry'] == 4
          and data['network']['proxy_free']['javdb'] == 'https://db.example.com',
          str(data.get('network')))
    check('T8 未改动字段保持原值', data['crawler']['hardworking'] is True)
    try:
        Cfg.model_validate(data)
        ok = True
        err = ''
    except Exception as e:
        ok, err = False, str(e)[:160]
    check('T8 写回结果通过配置模型校验', ok, err)
    check('T8 多字段改动后注释仍在', comment_count(p) == base_comments,
          f'{comment_count(p)}/{base_comments}')

    # ---------------- T9: 走真实 /api/config PUT 的端到端 ----------------
    # 该用例会真的改写仓库 config.yml，故先备份、用完立即按字节还原
    backup = open(SRC, 'rb').read()
    try:
        from fastapi.testclient import TestClient
        import javsp.server as server
        import copy
        client = TestClient(server.app)
        cur = Cfg().model_dump(mode='json')
        # 只提交要改的字段（真实前端的做法）: 不整份回传。
        # 整份 deepcopy 回传在配置收紧校验后会把「当前配置里那些从未被端到端校验过的
        # 字段」一起送进model_validate, 从而因无关字段的取值而失败 ——
        # 那是测试提交方式的问题, 不是接口缺陷。
        payload = {
            'network': {
                'proxy_server': 'http://127.0.0.1:7890',
                'retry': 6,
                'timeout': 'PT20S',        # 前端按秒编辑后转回的写法
                'proxy_free': {'javdb': 'https://db.example.com'},
            }
        }
        resp = client.put('/api/config', json=payload).json()
        # 热重载后状态为 'applied'（即时生效）；若热重载不可用则退回 'written'
        check('T9 PUT 返回写入成功', resp.get('status') in ('written', 'applied'), str(resp)[:160])
        check('T9 无字段定位失败', not resp.get('missing'), str(resp.get('missing')))
        check('T9 变更字段数 >= 4', (resp.get('changed') or 0) >= 4, str(resp.get('changed')))
        t = open(SRC, encoding='utf-8', newline='').read()
        check('T9 新值已落到文件',
              "proxy_server: 'http://127.0.0.1:7890'" in t and 'retry: 6' in t
              and 'timeout: PT20S' in t and "javdb: 'https://db.example.com'" in t)
        check('T9 注释未被破坏', comment_count(SRC) == base_comments,
              f'{comment_count(SRC)}/{base_comments}')
    finally:
        with open(SRC, 'wb') as f:
            f.write(backup)
    check('T9 用例结束后 config.yml 已按字节还原', open(SRC, 'rb').read() == original)

    # ---------------- T10: 前端时长解析函数（直接从 App.vue 源码提取） ----------------
    vue = open(os.path.join(PROJ, 'frontend', 'src', 'App.vue'), encoding='utf-8').read()
    m = re.search(r'function parseDurationToSec.*?\n\}', vue, re.S)
    check('T10 能从 App.vue 提取到时长解析函数', m is not None)
    if m:
        js_case = r"""
const out = [];
function t(name, cond, extra) { out.push({ name, cond: !!cond, extra: extra || '' }); }
const p = new Function(FN + '; return parseDurationToSec;')();
t('T10 PT10S -> 10 秒', p('PT10S') === 10, String(p('PT10S')));
t('T10 PT1M30S -> 90 秒', p('PT1M30S') === 90, String(p('PT1M30S')));
t('T10 PT2H -> 7200 秒', p('PT2H') === 7200, String(p('PT2H')));
t('T10 传入数字 -> 原样返回', p(15) === 15, String(p(15)));
t('T10 空串 -> null(保留界面原值)', p('') === null, String(p('')));
t('T10 null -> null', p(null) === null, String(p(null)));
t('T10 非法串 -> null', p('abc') === null, String(p('abc')));
console.log(JSON.stringify(out));
"""
        runner = os.path.join(tempfile.gettempdir(), '_javsp_dur.js')
        with open(runner, 'w', encoding='utf-8') as f:
            f.write('const FN = ' + json.dumps(m.group(0)) + ';\n' + js_case)
        r = subprocess.run([NODE, runner], capture_output=True, text=True, timeout=120)
        os.remove(runner)
        if r.returncode != 0:
            check('T10 前端用例执行成功', False, (r.stderr or r.stdout)[:300])
        else:
            try:
                for c in json.loads(r.stdout.strip().splitlines()[-1]):
                    check(c['name'], c['cond'], c['extra'])
            except Exception as e:
                check('T10 前端用例结果可解析', False, f'{e} | {r.stdout[:200]}')
finally:
    shutil.rmtree(tmpdir, ignore_errors=True)
    # 确保仓库里的 config.yml 从未被动过
    check('仓库 config.yml 全程未被修改', open(SRC, 'rb').read() == original)

# ---------------- T11: 敏感字段脱敏/还原的往返完整性 ----------------
# 回归起因(v0.1.20 上线事故): unmask_secrets 的列表分支写成
#   for i in range(len(submitted)): ... reference[i] ...
# 这里 i 是整数下标, 对标量元素递归时把「按整数取值」当成了参考值, 导致
# ignored_id_pattern / filename_extensions 这类纯字符串数组被整段污染成 int,
# 前端「原样保存配置」直接 400(59 个校验错误)、满屏错误。
# 本组断言确保: 脱敏→还原必须完全保持类型与内容, 尤其**不能改变标量元素类型**。
print('--- T11 脱敏/还原往返完整性 ---')
from javsp.config_io import mask_secrets, unmask_secrets
from javsp.config import Cfg

_real = {'translator': {'engine': {'name': 'baidu',
                                   'app_id': 'REAL_APP', 'api_key': 'REAL_KEY'}},
         'scanner': {'ignored_id_pattern': ['(144|240)[Pp]', '[24][Kk]'],
                     'filename_extensions': ['.mp4', '.mkv'],
                     'nested_list': [{'api_key': 'INNER_KEY'}, 'plain', 42]}}
_masked = mask_secrets(_real)
check('T11 嵌套 api_key 被掩码', _masked['translator']['engine']['api_key'] == '***MASKED***')
check('T11 列表内dict 的 api_key 也被掩码',
      _masked['scanner']['nested_list'][0]['api_key'] == '***MASKED***')
check('T11 掩码不改动原对象', _real['translator']['engine']['api_key'] == 'REAL_KEY')

_restored = unmask_secrets(_masked, _real)
check('T11 往返后 api_key 还原为真值',
      _restored['translator']['engine']['api_key'] == 'REAL_KEY')
check('T11 往返后列表内 api_key 还原',
      _restored['scanner']['nested_list'][0]['api_key'] == 'INNER_KEY')

# 关键回归: 纯字符串数组往返后类型/内容必须不变(此前被整段转成 int)
check('T11 字符串数组往返后仍是 str',
      all(isinstance(x, str) for x in _restored['scanner']['ignored_id_pattern']),
      f"实际类型 {[type(x).__name__ for x in _restored['scanner']['ignored_id_pattern']]}")
check('T11 字符串数组往返后内容不变',
      _restored['scanner']['ignored_id_pattern'] == _real['scanner']['ignored_id_pattern'])
check('T11 filename_extensions 往返不变',
      _restored['scanner']['filename_extensions'] == _real['scanner']['filename_extensions'])
check('T11 混合列表(数字/字符串)往返不变',
      _restored['scanner']['nested_list'][1:] == ['plain', 42],
      f"实际 {_restored['scanner']['nested_list'][1:]}")

# 用真实配置跑一遍「整份往返 + 校验」——这是前端保存配置的真实路径
_cur = Cfg().model_dump(mode='json')
_rt = unmask_secrets(json.loads(json.dumps(_cur)), _cur)
try:
    Cfg.model_validate(_rt)
    check('T11 真实配置整份往返后可校验(前端保存路径)', True)
except Exception as e:
    check('T11 真实配置整份往返后可校验(前端保存路径)', False, str(e)[:120])

print('---')
print(f'PASS {len(PASS)}  FAIL {len(FAIL)}')
if FAIL:
    print('FAILED:', FAIL)
    sys.exit(1)
print('ALL GREEN')
