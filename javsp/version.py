"""统一版本号读取 —— 单一版本源为 pyproject.toml 的 `version` 字段

读取优先级:
1. 仓库根 `pyproject.toml`(源码 / editable install 场景, 权威)
2. `importlib.metadata.version('javsp')`(正式打包安装场景, 此时 pyproject.toml 不在包内)
3. `'0.0.0'` 兜底

为什么不用已安装元数据优先: editable install 的元数据是**安装时快照**, 改了 pyproject.toml 后
`meta.version()` 仍返回旧值(曾出现 pyproject 已是 0.1.6、运行时仍报 0.1.3 的漂移)。
故源码场景下以 pyproject.toml 为准, 仅在其不存在时回落到元数据。

为什么不用 tomllib: 项目支持 Python 3.10, 而 `tomllib` 从 3.11 才进标准库;
这里只需取一行 version, 正则即可, 保持零依赖。
"""
import os
import re
import logging

logger = logging.getLogger(__name__)

# 匹配 `version = "x.y.z"`(行首, 允许单引号)
_VERSION_RE = re.compile(r'^version\s*=\s*["\']([^"\']+)["\']', re.M)

_FALLBACK = '0.0.0'


def _from_pyproject():
    """从仓库根 pyproject.toml 读取 version, 读不到返回 None"""
    try:
        pkg_dir = os.path.dirname(os.path.abspath(__file__))   # .../JavSP/javsp
        root = os.path.dirname(pkg_dir)                        # .../JavSP
        path = os.path.join(root, 'pyproject.toml')
        if not os.path.isfile(path):
            return None
        with open(path, 'r', encoding='utf-8') as f:
            m = _VERSION_RE.search(f.read())
        return m.group(1) if m else None
    except Exception as e:
        logger.debug(f'从 pyproject.toml 读取版本号失败: {e}')
        return None


def _from_metadata():
    """从已安装的分发元数据读取 version, 读不到返回 None"""
    try:
        import importlib.metadata as meta
        return meta.version('javsp')
    except Exception:
        return None


def get_version() -> str:
    """返回当前版本号(单一版本源: pyproject.toml, 元数据兜底)"""
    return _from_pyproject() or _from_metadata() or _FALLBACK


__version__ = get_version()
