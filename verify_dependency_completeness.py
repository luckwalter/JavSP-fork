"""verify_dependency_completeness.py — 运行时依赖声明完整性扫描（P3-7）

背景
----
`packaging` 曾长期「运行时用到但 pyproject.toml 未声明」：它只作为 dev 组 `cx-freeze`
的**传递依赖**存在于 lock 里，`poetry install --only main` 把 dev 组排除后，干净 venv
里就缺包 → 容器启动即崩（v0.1.16 修复）。本地之所以长期没暴露，是因为 pip / poetry
自身就携带 `packaging`，恰好掩盖了「未声明」。

这类 bug 的特征是：**本地 pip install -e . 侥幸存活，只有干净部署环境才炸**。
所以需要一道机械检查，把「import 了但没在 main 组声明」的包全部找出来。

做法
----
1. 用 `ast` 解析 `javsp/` 下**全部** `.py`（含 `web/` 下 30+ 爬虫模块），提取顶层
   import 名——比 grep 精确：能识别 `from X import`、别名、条件导入，也能避免
   把注释/字符串里的名字误当 import。
2. 剔除标准库（`sys.stdlib_module_names`）与项目内模块（`javsp`）。
3. 与 `pyproject.toml` 的 `[tool.poetry.dependencies]` 比对。**难点是包名 ≠ import 名**：
   `pillow`→`PIL`、`pycryptodome`→`Crypto`、`python-multipart`→`multipart`、
   `pretty-errors`→`pretty_errors`、`lxml[html-clean]`→`lxml`、`pywin32`→`win32crypt`。
   故维护一张映射表，未命中映射的按「包名原样 / 下划线化」两种形式兜底。
4. 报出「import 了但未声明」的包；已声明的额外验证其在 main 组确实存在。

注意：本脚本**只做静态报告**，是否「声明」是判定依据；它不能证明运行时环境真能装上，
那部分由 `poetry install --only main` 在干净 venv 里的实跑验证。
"""
import ast
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).parent
PKG_DIR = ROOT / 'javsp'

# PyPI 包名 → import 名 的非直觉映射（其余按原名 / 下划线化兜底）
IMPORT_ALIASES = {
    'pillow': 'PIL',
    'pycryptodome': 'Crypto',
    'python-multipart': 'multipart',
    'pretty-errors': 'pretty_errors',
    'pywin32': 'win32crypt',# 仅 win32 分支使用
    'pywin32-ctypes': 'win32ctypes',
    'python-dotenv': 'dotenv',      # confz 的传递依赖，一般不直接 import
    'typing-extensions': 'typing_extensions',
    'pydantic-core': 'pydantic_core',
    'pywebview': 'webview',         # 提供的是 import 名 webview
    'pydantic': 'pydantic',
}

# 仅在特定平台/条件下使用、允许「未声明」的包（不属于通用运行时依赖）
OPTIONAL_OK = {
    'win32crypt',   # pywin32，pyproject 里已用 markers 限定 sys_platform=='win32'
    'win32api',
    'win32con',
}


def declared_packages() -> set:
    """从 pyproject.toml 读 main 组声明的包名"""
    with open(ROOT / 'pyproject.toml', 'rb') as f:
        data = tomllib.load(f)
    return set(data.get('tool', {}).get('poetry', {}).get('dependencies', {}).keys())


def declared_import_names(declared: set) -> set:
    """把声明的包名映射成 import 名空间"""
    names = set()
    for pkg in declared:
        if pkg in IMPORT_ALIASES:
            names.add(IMPORT_ALIASES[pkg])
        else:
            names.add(pkg)
            names.add(pkg.replace('-', '_'))
    return names


def imported_top_level() -> set:
    """用 ast 扫描 javsp/ 下全部 .py，收集顶层 import 名（跳过 javsp 自身）"""
    names = set()
    for py in PKG_DIR.rglob('*.py'):
        try:
            tree = ast.parse(py.read_text(encoding='utf-8'), filename=str(py))
        except SyntaxError as e:
            print(f'[WARN] 解析失败 {py}: {e}')
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    names.add(a.name.split('.')[0])
            elif isinstance(node, ast.ImportFrom):
                # node.level > 0 表示相对导入(from .x / from ..x)，跳过
                if node.level and node.level > 0:
                    continue
                if node.module:
                    names.add(node.module.split('.')[0])
    return names


def main() -> int:
    print('verify_dependency_completeness.py — 运行时依赖声明完整性扫描\n')

    declared = declared_packages()
    allowed = declared_import_names(declared)
    imported = imported_top_level()

    stdlib = set(sys.stdlib_module_names)
    internal = {'javsp'}
    external = sorted(n for n in imported
                      if n not in stdlib and n not in internal)

    print(f'声明的main 依赖包（{len(declared)} 个）：')
    for p in sorted(declared):
        print(f'  - {p}')
    print()

    print(f'代码中 import 的外部顶层模块（{len(external)} 个）：')
    print(f'  {", ".join(external)}')
    print()

    # 关键断言：import 了但既未声明、又不在允许清单里
    missing = [n for n in external
               if n not in allowed and n not in OPTIONAL_OK]

    print('=' * 60)
    if missing:
        print(f'发现 {len(missing)} 个「代码 import 了但 pyproject main 组未声明」的包：')
        for n in missing:
            # 反查是哪些文件用了它
            users = []
            for py in PKG_DIR.rglob('*.py'):
                try:
                    tree = ast.parse(py.read_text(encoding='utf-8'), filename=str(py))
                except SyntaxError:
                    continue
                hit = False
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        if any(a.name.split('.')[0] == n for a in node.names):
                            hit = True
                    elif isinstance(node, ast.ImportFrom):
                        if (not node.level) and node.module and \
                                node.module.split('.')[0] == n:
                            hit = True
                if hit:
                    users.append(py.relative_to(ROOT).as_posix())
            print(f'  [!] {n}')
            for u in sorted(users)[:6]:
                print(f'        用于 {u}')
        print('=' * 60)
        print('结论：这些包需补进 pyproject.toml 的 main 依赖组，'
              '否则干净环境（poetry install --only main / Docker 镜像）缺包崩溃。')
        return 1

    print('结论：所有 import 的外部包均已在 main 组声明（或属允许的平台限定项）。')
    print('=' * 60)

    # 附加检查：声明了但代码里没用到（提示冗余，不算失败）
    unused = []
    for pkg in sorted(declared):
        names = {IMPORT_ALIASES.get(pkg, pkg), pkg.replace('-', '_')}
        if pkg == 'pywin32':
            names |= {'win32crypt', 'win32api', 'win32con'}
        if not (names & imported):
            unused.append(pkg)
    if unused:
        print(f'\n提示：声明了但本轮未在代码中 import 到的包（可能是 CLI/桌面端/可选路径使用）：')
        for p in unused:
            print(f'  - {p}')
    return 0


if __name__ == '__main__':
    sys.exit(main())