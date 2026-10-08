"""确认 confz 嵌套环境变量的正确分隔符(点号 vs 双下划线), 并据此修正文档

背景: README 旧文写「双下划线表示嵌套层级」, 但 confz 2.x 的 EnvSource.nested_separator
默认值是 '.' 且本项目从未改过-> 双下划线写法**静默失效**(读出来是 None, 不报错)。
这是典型的「配错了却以为生效」陷阱, 必须实证后修文档。
"""
import os
import subprocess
import sys
import tempfile

PY = sys.executable

# 用 RFC 5737 文档专用地址段(192.0.2.0/24)作占位: 它不会被路由到任何真实网络,
# 既能验证"值是否真的被读取到", 又不会把内网地址结构带进开源仓库。
FAKE_PROXY = 'http://192.0.2.1:3128'

CASES = {
    'double_underscore': {'JAVSP_NETWORK__PROXY_SERVER': FAKE_PROXY},
    'dot': {'JAVSP_NETWORK.PROXY_SERVER': FAKE_PROXY},
    'none': {},
}

SNIPPET = (
    "import sys; sys.path.insert(0, {repo!r})\n"
    "from javsp.config import Cfg\n"
    "from javsp.web.base import read_proxy\n"
    "print('  proxy_server =', repr(Cfg().network.proxy_server))\n"
    "print('  read_proxy() =', repr(read_proxy()))\n"
)

REPO = r'C:\Users\luckw\WorkBuddy\2026-10-06-17-16-37\JavSP'


def main():
    results = {}
    with tempfile.TemporaryDirectory() as td:
        script = os.path.join(td, 'probe.py')
        with open(script, 'w', encoding='utf-8') as f:
            f.write(SNIPPET.format(repo=REPO))
        for label, env_extra in CASES.items():
            env = {k: v for k, v in os.environ.items() if not k.startswith('JAVSP_')}
            env.update(env_extra)
            print(f'\n[{label}] 注入 {list(env_extra) or "(无)"}')
            p = subprocess.run([PY, script], env=env, capture_output=True, text=True)
            print(p.stdout.rstrip())
            if p.stderr.strip():
                print('  STDERR:', p.stderr.strip()[:300])
            results[label] = p.stdout

    print(f'\n{"=" * 60}')
    ok_dot = 'proxy_server = None' not in results.get('dot', '')
    ok_du = 'proxy_server = None' not in results.get('double_underscore', '')
    print(f'双下划线写法生效? {ok_du}')
    print(f'点号写法生效?     {ok_dot}')
    if ok_dot and not ok_du:
        print('\n结论: **点号(. )才是正确写法, 双下划线静默失效** -> README:251 的说法是错的, 需修正')
    elif ok_du:
        print('\n结论: 双下划线生效(与 confz 默认值不符, 需进一步核实原因)')
    else:
        print('\n结论: 两种写法都未生效, 需换思路排查')


if __name__ == '__main__':
    main()
