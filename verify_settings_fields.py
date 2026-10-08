"""校验前端设置页绑定的配置字段与 config.yml 真实结构一致

背景: 设置页曾按"看起来合理"的猜测写字段名(output_dir / gen_cover / translate_title),
而config.yml 实际是 path.output_folder_pattern / cover.enabled / fields.title ——
写错会导致 PUT /api/config 静默不生效或写坏配置。本脚本从真实 config.yml 与
SettingsView.vue 双向解析, 断言所有绑定路径都存在。

运行: venv312 下 `python verify_settings_fields.py`
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import yaml

PASSED, FAILED = [], []
HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(HERE, 'config.yml')
VIEW = os.path.join(HERE, 'frontend', 'src', 'views', 'SettingsView.vue')


def check(label, cond, detail=''):
    if cond:
        PASSED.append(label)
        print(f'  [OK ] {label}')
    else:
        FAILED.append(f'{label} -> {detail}')
        print(f'  [FAIL] {label}  {detail}')


MISSING = object()   # 哨兵: 区分"键不存在"与"值为 null"(null 是合法配置, 如未设代理)


def dig(d, path):
    """按 'a.b.c' 取值。**键存在但值为 None 时返回 None(合法)**,
    键不存在时返回 MISSING 哨兵 —— 不能用 None 判定不存在:
    proxy_server / translator.engine 默认为 null 是正常状态。"""
    cur = d
    for k in path.split('.'):
        if not isinstance(cur, dict) or k not in cur:
            return MISSING
        cur = cur[k]
    return cur


def main():
    cfg = yaml.safe_load(io.open(CFG, encoding='utf-8').read())
    src = io.open(VIEW, encoding='utf-8').read()

    print('\n===== 前端绑定的配置路径必须真实存在 =====')
    # 提取所有 v-model="cfg.xxx.yyy" / v-model="cfg.xxx"
    binds = sorted(set(re.findall(r'v-model="cfg\.([A-Za-z0-9_.]+)"', src)))
    print(f'  共发现 {len(binds)} 个绑定')
    missing = []
    for b in binds:
        v = dig(cfg, b)
        ok = v is not MISSING
        check(f'cfg.{b}', ok, 'config.yml 中不存在该路径')
        if not ok:
            missing.append(b)

    print('\n===== 关键字段的形态(不只是存在, 类型也要对) =====')
    def g(path):
        v = dig(cfg, path)
        return None if v is MISSING else v

    check('minimum_size 是字符串(带单位)',
          isinstance(g('scanner.minimum_size'), str),
          f"实际类型 {type(g('scanner.minimum_size')).__name__}")
    check('network.timeout 是 ISO8601 字符串',
          isinstance(g('network.timeout'), str)
          and str(g('network.timeout')).startswith('PT'),
          f"实际 {g('network.timeout')!r}")
    check('network.retry 是数字', isinstance(g('network.retry'), int), '')
    check('proxy_server 允许为 null(未设代理)',
          g('network.proxy_server') is None or isinstance(g('network.proxy_server'), str),
          f"实际 {g('network.proxy_server')!r}")
    check('translator.engine 允许为 null(不翻译)',
          g('translator.engine') is None or isinstance(g('translator.engine'), str),
          f"实际 {g('translator.engine')!r}")
    check('summarizer.path.output_folder_pattern 存在(非 output_dir)',
          g('summarizer.path.output_folder_pattern') is not None, '')
    check('summarizer.path.basename_pattern 存在(非 output_file)',
          g('summarizer.path.basename_pattern') is not None, '')
    for k in ('cover', 'fanart', 'extra_fanarts', 'nfo'):
        check(f'summarizer.{k}.enabled 是布尔',
              isinstance(g(f'summarizer.{k}.enabled'), bool), '')
    for k in ('title', 'plot'):
        check(f'translator.fields.{k} 是布尔',
              isinstance(g(f'translator.fields.{k}'), bool), '')

    print('\n===== 不得出现旧版那些猜错的字段名 =====')
    for wrong in ('cfg.summarizer.output_dir', 'cfg.summarizer.output_file',
                  'cfg.summarizer.gen_cover', 'cfg.summarizer.gen_fanart',
                  'cfg.summarizer.gen_nfo', 'cfg.translator.translate_title',
                  'cfg.translator.translate_plot', 'cfg.scanner.minimum_size_mb'):
        check(f'未使用错误字段 {wrong}', wrong not in src, '')

    print('\n===== 破坏性设置需有明确说明 =====')
    check('移动文件有说明文字', '不动原文件' in src, '')
    check('API 密钥提示用环境变量', 'JAVSP_TRANSLATOR.ENGINE.API_KEY' in src, '')

    print(f'\n{"=" * 54}')
    print(f'通过 {len(PASSED)} 项, 失败 {len(FAILED)} 项')
    for f in FAILED:
        print(f'  FAIL: {f}')
    if missing:
        print('\n缺失路径: ' + ', '.join(missing))
    print('设置页字段对齐校验通过 ✅' if not FAILED else '存在不一致 ❌')
    raise SystemExit(1 if FAILED else 0)


if __name__ == '__main__':
    main()
