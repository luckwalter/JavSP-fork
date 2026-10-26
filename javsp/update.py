"""软件更新检查与 Windows 桌面版自动升级

背景: 上游 Yuukiy/JavSP 的 `javsp/func.py` 里有一套 CLI 版检查/自动更新
(`check_update` / `download_update`), 但有三点不能直接用:
1. 它查的是**上游**仓库的 releases(`Yuukiy/JavSP`), 对本 fork 无效;
2. CLI 打印式输出, 未接入 WebUI, 用户看不到;
3. 自动更新靠"重命名运行中的 exe"实现 —— Windows 不允许替换正在运行的可执行
   文件(文件锁), 该路径实际必然失败。

本模块重写为: 指向本 fork 自己的仓库 + 供 Web 端调用的接口 + "主进程退出后
再覆盖"的安全替换流程。

设计要点:
- 检查超时 3 秒(沿用上游), 失败**静默**返回 None —— 网络不可达时绝不拖慢启动;
- 走项目通用代理(`network.proxy_server`), 否则国内直连 GitHub API 会超时;
- 结果缓存 6 小时, 避免频繁请求;
- Windows 升级: 下载 zip → 生成 PowerShell helper(UTF-8 BOM, PowerShell 5.1
  对无 BOM 的 UTF-8 会按 ANSI 解码致中文路径乱码)→ 独立进程等待主进程退出
  → 旧目录改名备份 → 新版落地 → 保留 config.yml → 启动新版;
- Docker 部署无法替换自身镜像, 只给指引(load 命令 + 发布页), 由用户执行。
"""
import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

import requests

from javsp.version import get_version
from javsp.web.base import read_proxy, tls_verify

logger = logging.getLogger(__name__)


__all__ = ['check_latest', 'download_asset', 'select_windows_asset', 'is_newer',
           'local_version', 'is_frozen', 'is_container', 'apply_windows_update',
           'sha256_file', 'select_checksum_asset', 'fetch_expected_checksum',
           'is_path_within', 'RELEASES_PAGE', 'docker_upgrade_hint']


REPO_OWNER = 'luckwalter'
REPO_NAME = 'JavSP-fork'
API_LATEST = f'https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/latest'
RELEASES_PAGE = f'https://github.com/{REPO_OWNER}/{REPO_NAME}/releases/latest'
REPO_PAGE = f'https://github.com/{REPO_OWNER}/{REPO_NAME}'

# 检查更新的网络超时(秒)。沿用上游 func.check_update 的 3 秒: 网络不可达时
# 宁可放弃提示, 也不让用户在启动后干等。
CHECK_TIMEOUT = 3
# 下载更新包的超时(秒) —— 40MB 左右的 zip, 给足时间
DOWNLOAD_TIMEOUT = 900
# 抓取 sha256 清单的超时(秒)。清单只有几百字节, 但 GitHub 资产会 302 到
# objects.githubusercontent.com, 3 秒的检查超时实测不够(Read timed out),
# 会白白降级成大小校验, 故单独放宽。
CHECKSUM_TIMEOUT = 20
# 检查结果缓存时长(秒)
CACHE_TTL = 6 * 3600

_HEADERS = {'User-Agent': 'JavSP-fork', 'Accept': 'application/vnd.github+json'}

# 进程内缓存: {'checked_at': 时间戳, 'result': 结果字典或 None}
_cache = {}


def local_version() -> str:
    """当前程序版本号(单一版本源 pyproject.toml)"""
    return get_version()


def is_newer(latest: str, current: str) -> bool:
    """比较版本号: latest 是否比 current 新

    用 packaging.version 做语义比较 —— 直接比字符串会把 '0.3.9' 判成比
    '0.3.17' 新(字符串 '9' > '1')。无法解析的版本号一律视为"不新"，
    避免拿一个坏 tag 去提示用户升级。
    """
    from packaging import version
    try:
        return version.parse(_normalize_tag(latest)) > version.parse(_normalize_tag(current))
    except Exception:
        return False


def _normalize_tag(tag: str) -> str:
    """把 tag 名规整成版本号: 'v0.4.0' -> '0.4.0'"""
    return re.sub(r'^[vV]', '', (tag or '').strip())


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包的 exe 上(桌面版)"""
    return bool(getattr(sys, 'frozen', False))


def is_container() -> bool:
    """是否运行在容器里(Docker 部署)"""
    if os.path.exists('/.dockerenv'):
        return True
    try:
        with open('/proc/1/cgroup', 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        return 'docker' in content or 'kubepods' in content or 'containerd' in content
    except OSError:
        return False


def docker_upgrade_hint(version: str) -> str:
    """Docker 部署的升级指引(容器内无法替换自身镜像, 只能由用户执行)"""
    return (f'docker load -i javsp-fork-{version}-image.tar.gz && docker restart javsp')


def _fetch_release(timeout: int = CHECK_TIMEOUT) -> dict:
    """查询 GitHub 上本 fork 的最新 release; 失败抛异常由调用方消化"""
    proxies = read_proxy()
    # verify=tls_verify(): 与爬虫链路保持一致 —— 做 TLS 解密的代理(MITM)环境里,
    # 不传会直接 SSLCertVerificationError, 表现为"永远查不到新版本"且失败静默。
    r = requests.get(API_LATEST, headers=_HEADERS, proxies=proxies, timeout=timeout,
                     verify=tls_verify())
    r.raise_for_status()
    return r.json()


def check_latest(force: bool = False) -> dict:
    """检查是否有新版本

    返回 None 表示"无可用信息"(网络不可达/仓库无 release/当前已是最新)——
    调用方据此不提示任何东西。**绝不抛异常**, 也不长时间阻塞。

    返回字典结构:
        {'current': 本机版本, 'latest': 最新版本号, 'url': 发布页,
         'published_at': 发布时间, 'notes': 发布说明, 'assets': [资产列表]}
    """
    now = time.time()
    if not force and _cache.get('checked_at') and now - _cache['checked_at'] < CACHE_TTL:
        return _cache.get('result')

    current = local_version()
    result = None
    try:
        data = _fetch_release()
        latest_tag = data.get('tag_name') or ''
        latest = _normalize_tag(latest_tag)
        if latest and is_newer(latest_tag, current):
            assets = [{
                'name': a.get('name', ''),
                'url': a.get('browser_download_url', ''),
                'size': a.get('size', 0),
            } for a in data.get('assets', [])]
            result = {
                'current': current,
                'latest': latest,
                'tag': latest_tag,
                'url': data.get('html_url') or RELEASES_PAGE,
                'published_at': data.get('published_at', ''),
                'notes': (data.get('body') or '')[:8000],
                'assets': assets,
                'frozen': is_frozen(),
                'container': is_container(),
            }
    except Exception:
        # 网络不可达/限流/仓库无 release: 静默失败, 不打扰用户
        result = None
    _cache['checked_at'] = now
    _cache['result'] = result
    return result


def select_windows_asset(release: dict) -> dict:
    """从 release 资产里挑出 Windows 桌面版 zip"""
    if not release:
        return None
    for a in release.get('assets', []):
        name = (a.get('name') or '').lower()
        if name.endswith('.zip') and 'windows' in name:
            return a
    return None


def is_path_within(child: str, parent: str) -> bool:
    """child 是否位于 parent 目录之内(大小写无关)

    Windows 文件系统大小写不敏感, 但字符串 startswith 比较是大小写敏感的 ——
    盘符/目录名写法不一致时会把合法路径误判成非法(B-009)。这里统一 normcase
    后再比较; parent 自身不算"之内", 必须多出一层路径分隔。
    """
    child_n = os.path.normcase(os.path.abspath(child))
    parent_n = os.path.normcase(os.path.abspath(parent))
    if child_n == parent_n:
        return False
    return child_n.startswith(parent_n.rstrip(os.sep) + os.sep)


def sha256_file(path: str, chunk: int = 1 << 20) -> str:
    """分块计算文件 SHA-256(40MB 级的包也不占内存)"""
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(chunk), b''):
            h.update(block)
    return h.hexdigest()


def select_checksum_asset(release: dict) -> dict:
    """从 release 资产里挑出 sha256 清单文件"""
    if not release:
        return None
    for a in release.get('assets', []):
        if (a.get('name') or '').lower().endswith('.sha256'):
            return a
    return None


def fetch_expected_checksum(release: dict, asset_name: str, timeout: int = CHECKSUM_TIMEOUT) -> str:
    """取指定资产在发布清单里登记的 sha256; 取不到返回空串

    清单格式为 sha256sum 标准输出: `<hex>  <filename>`。
    清单缺失 / 下载失败 / 不含该文件时一律返回 '', 由调用方降级, 不抛异常 ——
    校验能力缺失不应直接阻断升级。
    """
    asset = select_checksum_asset(release)
    if not asset or not asset.get('url'):
        return ''
    try:
        proxies = read_proxy()
        r = requests.get(asset['url'], headers=_HEADERS, proxies=proxies,
                         timeout=timeout, verify=tls_verify())
        r.raise_for_status()
    except Exception as e:      # noqa: BLE001 清单只是增强校验, 失败应降级而非中断
        logger.warning(f'获取校验清单失败(降级为大小校验): {e}')
        return ''
    target = os.path.basename(asset_name).strip().lower()
    for line in r.text.splitlines():
        parts = line.split()
        # 兼容可能带 '*'(二进制模式)标记的 sha256sum 输出
        if len(parts) >= 2 and os.path.basename(parts[-1].strip('*')).lower() == target:
            return parts[0].strip()
    return ''


def download_asset(url: str, dest: str, progress_cb=None, expect: dict = None) -> str:
    """下载 release 资产到指定路径, 附带进度回调与完整性校验

    progress_cb(downloaded_bytes, total_bytes) —— total 为 0 时表示未知大小。
    走项目通用代理; GitHub 资产会 302 到 objects.githubusercontent.com,
    requests 默认跟随重定向, 代理同样生效。

    expect 为可选校验依据 {'sha256': 十六进制串, 'size': 字节数}:
      - 给了 sha256: 下载后计算比对, 不符则**删除该文件**并抛 ValueError
        (绝不把损坏/被篡改的包留在盘上等着被解压执行);
      - 只给了 size: 退化为字节数比对, 同样失败即删;
      - 两者都无: 记 warning 并放行, 保持旧行为(清单缺失不该阻断升级)。
    """
    proxies = read_proxy()
    with requests.get(url, headers=_HEADERS, proxies=proxies, timeout=DOWNLOAD_TIMEOUT,
                      stream=True, verify=tls_verify()) as r:
        r.raise_for_status()
        total = int(r.headers.get('Content-Length') or 0)
        done = 0
        os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
        with open(dest, 'wb') as f:
            for chunk in r.iter_content(chunk_size=256 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                done += len(chunk)
                if progress_cb:
                    try:
                        progress_cb(done, total)
                    except Exception:
                        pass

    _verify_download(dest, expect)
    return dest


def _verify_download(path: str, expect: dict = None) -> None:
    """校验已下载文件的完整性, 不符则删文件并抛 ValueError"""
    expect = expect or {}
    exp_sha = (expect.get('sha256') or '').strip().lower()
    exp_size = int(expect.get('size') or 0)
    got_size = os.path.getsize(path) if os.path.isfile(path) else -1

    def _discard():
        try:
            os.remove(path)
        except OSError:
            pass

    if exp_sha:
        got_sha = sha256_file(path)
        if got_sha != exp_sha:
            _discard()
            raise ValueError(f'更新包完整性校验失败(SHA-256 不符), 已删除: 期望 {exp_sha}, 实际 {got_sha}')
        logger.info(f'更新包 SHA-256 校验通过: {os.path.basename(path)}')
    elif exp_size > 0 and got_size != exp_size:
        _discard()
        raise ValueError(f'更新包完整性校验失败(大小不符), 已删除: 期望 {exp_size}, 实际 {got_size}')
    elif exp_size <= 0:
        logger.warning('更新包缺少校验依据(无 sha256 清单也无 size), 跳过完整性校验')


# --------------------------------------------------------------------------
# Windows 桌面版: 覆盖升级
# --------------------------------------------------------------------------

_HELPER_NAME = 'javsp_apply_update.ps1'

# helper 的"工作区"目录名 —— 必须位于**待升级目录之外**。
# 根因(B-012): 升级流程要把整个运行目录改名备份, 而 helper 若把脚本/日志/zip
# 放在该目录内部, 打开的文件句柄与进程工作目录会让目录自身处于"使用中",
# Move-Item 必然失败(本地盘与 UNC 一视同仁, 不是 SMB 锁的问题)。
# 故 helper 的脚本 + 日志 + 安装包 + 进程 CWD 全部搬到系统临时目录。
_HELPER_WORKDIR = 'javsp_update_helper'

# 升级时必须从旧目录保留回来的用户数据(存在于 exe 同级目录)。
# config.yml 是用户的配置(本地路径/代理/Ollama 端点等), .sessions.json 是登录
# 会话令牌哈希 —— 丢了会直接让用户"配置全丢 + 被登出", 故升级时整目录替换后
# 必须把它们拷回。其余 exe 同级的旧组件(_internal 里的旧模块等)随旧目录
# 整体删除, 不残留 —— 这正是"清理之前版本无用的组件"的落点。
_KEEP_FILES = ('config.yml', '.sessions.json', 'JavSP.log', 'run.log')


def _exe_dir() -> str:
    """当前 exe 所在目录(打包后为 JavSP.exe 所在目录)"""
    return os.path.dirname(os.path.abspath(sys.executable))


def _helper_workdir() -> str:
    """helper 的独立工作区(系统临时目录下), 刻意与待升级目录隔离"""
    d = os.path.join(tempfile.gettempdir(), _HELPER_WORKDIR)
    os.makedirs(d, exist_ok=True)
    return d


def _stage_update_zip(zip_path: str, workdir: str) -> str:
    """把安装包搬进 helper 工作区

    搬走是必须的: 安装包若留在待升级目录内, 该目录就含有 helper 正在使用的文件。
    跨盘移动(shutil.move 自动退化为 copy+delete)也能正常工作。
    """
    src = os.path.abspath(zip_path)
    dst = os.path.join(workdir, os.path.basename(src))
    if os.path.abspath(src) == os.path.abspath(dst):
        return dst
    if os.path.isfile(dst):
        try:
            os.remove(dst)
        except OSError:
            pass
    shutil.move(src, dst)
    return dst


def _helper_script(zip_path: str, exe_dir: str, new_exe: str, workdir: str) -> str:
    """生成 PowerShell helper 脚本内容

    用 PowerShell 而非 cmd: cmd 对 UTF-8 中文路径的处理不可靠, 且
    Expand-Archive(Win10 自带 PowerShell 5.1)解压 zip 比 cmd 可靠得多。
    脚本以 UTF-8 **带 BOM** 落盘 —— PowerShell 5.1 读取无 BOM 的 UTF-8 脚本会
    按 ANSI 解码, 中文路径会乱码导致找不到文件。

    流程: 等主进程退出 → 旧目录改名为备份(顺带清掉更老的备份) → 新版解压落地
    → 拷回用户数据 → 启动新版 → 清理临时文件(保留最近一个回退备份)。

    ⚠️ 脚本本体、日志与安装包都必须位于 **workdir**(系统临时目录), 绝不能
    放进待升级目录 —— 否则打开的文件句柄与进程工作目录会让该目录自身
    "正在使用中", Move-Item 改名必然失败(B-012)。
    """
    tmp_extract = os.path.join(workdir, 'javsp_update_extract')
    backup_dir = exe_dir + '.bak'
    keep_list = ','.join(f"'{n}'" for n in _KEEP_FILES)
    ps = f'''
$ErrorActionPreference = 'Stop'
$zip = '{zip_path}'
$exeDir = '{exe_dir}'
$tmp = '{tmp_extract}'
$bak = '{backup_dir}'
$newExe = '{new_exe}'
$work = '{workdir}'
$log = Join-Path $work 'apply_update.log'
$keep = @({keep_list})
function Log($m) {{ Add-Content -Path $log -Value ("[" + (Get-Date -Format 'HH:mm:ss') + "] " + $m) -Encoding UTF8 }}
try {{
    Log "开始应用更新"
    if (Test-Path $tmp) {{ Remove-Item $tmp -Recurse -Force }}
    New-Item -ItemType Directory -Path $tmp | Out-Null
    Expand-Archive -Path $zip -DestinationPath $tmp -Force
    Log "解压完成: $tmp"
    # 释放文件锁: pywebview 退出后常残留 msedgewebview2 孤儿进程, 仍锁着
    # _internal/webview 等文件; 网盘(UNC)路径下 SMB 锁更"粘", Move-Item 会
    # 卡在"另一个进程正在使用此文件"。先主动结束这些孤儿释放锁。
    try {{ Get-Process -Name msedgewebview2 -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue }} catch {{ }}
    Start-Sleep -Seconds 1
    # 只保留最近一个回退备份: 更老的直接清掉(避免多轮升级堆积旧组件)
    if (Test-Path $bak) {{ Remove-Item $bak -Recurse -Force }}
    # 把整个运行目录改名备份 —— 重试仍失败则多为目录自身被占用
    $moved = $false
    for ($i = 0; $i -lt 10; $i++) {{
        try {{
            Move-Item -Path $exeDir -Destination $bak -Force
            $moved = $true
            break
        }} catch {{
            Log ("Move-Item 重试 #" + ($i+1) + ": " + $_.Exception.Message)
            try {{ Get-Process -Name msedgewebview2 -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue }} catch {{ }}
            Start-Sleep -Seconds 2
        }}
    }}
    if (-not $moved) {{ throw ("多次重试仍无法移动旧目录(可能被占用): " + $exeDir) }}
    Copy-Item -Path $tmp -Destination $exeDir -Recurse -Force
    Log "新版已就位: $exeDir"
    # 拷回用户数据: 配置与会话不能丢
    foreach ($name in $keep) {{
        $old = Join-Path $bak $name
        $new = Join-Path $exeDir $name
        if ((Test-Path $old) -and -not (Test-Path $new)) {{
            Copy-Item -Path $old -Destination $new -Force
            Log "已保留 $name"
        }}
    }}
    try {{ Start-Process -FilePath $newExe }} catch {{ Log ("启动新版本失败(可手动启动): " + $_.Exception.Message) }}
    Log "已请求启动新版本"
    Start-Sleep -Seconds 3
    # 清理临时产物: 解压目录 / 下载的包 / 本脚本; 保留 $bak 作为最近回退版
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $zip -Force -ErrorAction SilentlyContinue
    Remove-Item $PSCommandPath -Force -ErrorAction SilentlyContinue
    # 清掉旧目录里遗留的安装包目录(新版不再往 exe 同级放 update/)
    Remove-Item (Join-Path $exeDir 'update') -Recurse -Force -ErrorAction SilentlyContinue
    Log "清理完成(旧版备份保留在: $bak)"
}} catch {{
    Log ("应用更新失败: " + $_.Exception.Message)
    # 回滚: 新目录不完整时换回旧目录
    if (-not (Test-Path (Join-Path $exeDir 'JavSP.exe')) -and (Test-Path $bak)) {{
        Move-Item -Path $bak -Destination $exeDir
        Log "已回滚到旧版本"
    }}
}}
'''
    path = os.path.join(workdir, _HELPER_NAME)
    with open(path, 'w', encoding='utf-8-sig', newline='\r\n') as f:
        f.write(ps)
    return path


def _wait_helper_started(helper: str, timeout: float = 2.0) -> bool:
    """观察 helper 是否真的跑起来(以它写入的第一行日志为准)

    PS 宿主在部分启动标志下会"起了进程却不执行脚本", 这里返回可诊断信号:
    False 说明根本没起来, 便于区分"已启动但失败"与"压根没启动"。
    """
    log = os.path.join(os.path.dirname(os.path.abspath(helper)), 'apply_update.log')
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.isfile(log):
            return True
        time.sleep(0.1)
    return os.path.isfile(log)


def apply_windows_update(zip_path: str) -> dict:
    """启动 helper 进程, 在主进程退出后覆盖升级并重启

    调用方需在返回后**立即退出主进程**(helper 会等待主进程消失)。

    返回 {'helper': 脚本路径, 'pid': helper 进程 PID}
    """
    exe_dir = _exe_dir()
    new_exe = os.path.join(exe_dir, 'JavSP.exe')
    # helper 的脚本/日志/安装包/工作目录全部搬到系统临时目录 —— 绝不能留在
    # 待升级目录内, 否则该目录自身"正在使用中", 改名备份必败(B-012)。
    workdir = _helper_workdir()
    staged_zip = _stage_update_zip(zip_path, workdir)
    helper = _helper_script(staged_zip, exe_dir, new_exe, workdir)
    # 实测坑(2026-10-26 修): DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP 下 PS 宿主
    # **不会执行脚本** —— Popen 正常返回 pid, 但脚本静默不运行, 连第一行日志都不写,
    # 表现为"点了立即升级, 程序关了却没升级"。矩阵实测: 无 flags / CREATE_NO_WINDOW
    # 均能执行, 上述组合必失败。故改用 CREATE_NO_WINDOW(隐藏控制台且能真正执行)。
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)
    proc = subprocess.Popen(
        ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', helper],
        creationflags=flags, close_fds=True, cwd=workdir,
    )
    started = _wait_helper_started(helper)
    return {'helper': helper, 'pid': proc.pid, 'started': started}
