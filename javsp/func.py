"""业务逻辑所需的或有一定通用性的函数"""
# 为了降低耦合度，也避免功能复杂后可能出现的循环导入的问题，这里尽量不导入项目内部的模块
# 如果需要获得配置信息，也应当由外部模块将配置项的值以参数的形式传入
import os
import re
import sys
import time
import shutil
import zipfile
import logging
import subprocess
import platform
from datetime import datetime
from packaging import version
from colorama import Style
from pathlib import Path
# 版本号与发布页地址收敛到单一数据源: 避免 CLI 这条路径再写一份仓库地址/版本逻辑
from javsp.version import get_version, FALLBACK_VERSION
from javsp.update import API_LATEST, RELEASES_PAGE

# 判断系统是否可以使用tk
USE_GUI = True
try:
    from tkinter import filedialog, Tk
except ImportError:
    USE_GUI = False

from javsp.web.base import *
from javsp.lib import re_escape, resource_path

from javsp.prompt import prompt

__all__ = ['select_folder', 'get_scan_dir', 'remove_trail_actor_in_title',
           'shutdown', 'system_shutdown_command', 'safe_extract', 'CLEAR_LINE',
           'check_update', 'split_by_punc']


CLEAR_LINE = '\r\x1b[K'
logger = logging.getLogger(__name__)


def select_folder(default_dir=''):
    """使用文件对话框提示用户选择一个文件夹"""
    if not USE_GUI:
        logger.error("无法打开窗口，请通过命令行的方式输入扫描路径")
        exit(1)
    window = Tk()
    window.withdraw()
    window.iconbitmap(resource_path('image/JavSP.ico'))
    path = filedialog.askdirectory(initialdir=default_dir)
    if path != '':
        return os.path.normpath(path)


def get_scan_dir(cfg_scan_dir: Path | None) -> str | None:
    """综合命令参数、配置文件等信息，返回要扫描影片的文件夹"""
    # 目前config模块负责处理来自命令行和来自文件的配置，cfg_scan_dir已经是综合了这两处后得到的结果
    if cfg_scan_dir:
        if cfg_scan_dir.exists():
            return str(cfg_scan_dir)
        else:
            logger.error(f"配置的待整理文件夹无效：'{cfg_scan_dir}'")
    else:
        if platform.system().lower() == 'windows':
            print('请选择要整理的文件夹：', end='')
            root = select_folder()
        else:
            root = prompt('请选择要整理的文件夹路径，必须是绝对路径: ', "要整理的文件夹")
        print(root)
        return root


def remove_trail_actor_in_title(title:str, actors:list) -> str:
    """寻找并移除标题尾部的女优名"""
    if not (actors and title):
        return title
    # 目前使用分隔符白名单来做检测（担心按Unicode范围匹配误伤太多），考虑尽可能多的分隔符
    delimiters = '-xX &·,;　＆・，；'
    actor_ls = [re_escape(i) for i in actors if i]
    pattern = f"^(.*?)([{delimiters}]{{1,3}}({'|'.join(actor_ls)}))+$"
    # 使用match而不是sub是为了将替换掉的部分写入日志
    match = re.match(pattern, title)
    if match:
        logger.debug(f"移除标题尾部的女优名: '{match.group(1)}' [{match.group(2)}]")
        return match.group(1)
    else:
        return title


def system_shutdown_command() -> list:
    """当前平台的关机命令(列表形式, 交给 subprocess 执行时不再经过 shell)

    上游统一用 `shutdown -s`: 那是 Windows 的关机参数, 类 Unix 下 `-s` 要么不是
    有效选项(多数发行版直接报错退出), 要么在少数 SysV 实现上被忽略而立即关机
    ——语义不可依赖, 故按平台分开。
    """
    if platform.system().lower() == 'windows':
        return ['shutdown', '/s', '/t', '0']
    # Linux/macOS: -h = halt(关机)
    return ['shutdown', '-h', 'now']


def shutdown(timeout=120):
    """关闭计算机"""
    try:
        for i in reversed(range(timeout)):
            print(CLEAR_LINE + f"JavSP整理完成，将在 {i} 秒后关机。按'Ctrl+C'取消", end='')
            time.sleep(1)
        logger.info('整理完成，自动关机')
        cmd = system_shutdown_command()
        try:
            # 参数均为常量且不经过 shell, 不存在命令注入面; 类 Unix 下普通用户通常
            # 无权关机, 此时降级为提示, 不让异常打断整理收尾
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                logger.warning('自动关机失败(可能缺少权限), 请手动关机: ' + (result.stderr or '').strip())
        except FileNotFoundError:
            logger.warning(f'当前平台没有可用的关机命令({cmd[0]}), 请手动关机')
    except KeyboardInterrupt:
        return


def utc2local(utc_str):
    """将UTC时间转换为本地时间"""
    # python不支持 ISO-8601 中的Z后缀
    now = time.time()
    offset = datetime.fromtimestamp(now) - datetime.utcfromtimestamp(now)
    utc_str = utc_str.replace('Z', '+00:00')
    utc_time = datetime.fromisoformat(utc_str)
    local_time = utc_time + offset
    return local_time


def get_actual_width(mix_str: str) -> int:
    """给定一个中英混合的字符串，返回实际的显示宽度"""
    width = len(mix_str)
    for c in mix_str:
        if u'\u4e00' <= c <= u'\u9fa5':
            width += 1
    return width


def align_center(mix_str: str, total_width: int) -> str:
    """给定一个中英混合的字符串，根据其实际显示宽度中心对齐"""
    actual_width = get_actual_width(mix_str)
    add_space = int((total_width - actual_width) / 2)
    aligned_str = ' ' * add_space + mix_str
    return aligned_str


# 枚举Unicode各平面内中日韩区块及拉丁字母区块内的所有标点符号
_punc = (
" 　",                      # spaces
"!\"#%&'()*,-./:;?@[\\]_{}", # (0x0, 0x7f), Basic Latin
"¡§«¶·»¿",                  # (0x80, 0xff), Latin-1 Supplement
";·",                       # (0x370, 0x3ff), Greek and Coptic
"‐‑‒–—―‖‗‘’‚‛“”„‟†‡•‣․‥…‧‰‱′″‴‵‶‷‸‹›※‼‽‾‿⁀⁁⁂⁃⁅⁆⁇⁈⁉⁊⁋⁌⁍⁎⁏⁐⁑⁓⁔⁕⁖⁗⁘⁙⁚⁛⁜⁝⁞",  # (0x2000, 0x206f), General Punctuation
"、。〃〈〉《》「」『』【】〔〕〖〗〘〙〚〛〜〝〞〟〰〽",                        # (0x3000, 0x303f), CJK Symbols and Punctuation
"゠・",                     # (0x30a0, 0x30ff), Katakana
"︐︑︒︓︔︕︖︗︘︙",      # (0xfe10, 0xfe1f), Vertical Forms
"︰︱︲︳︴︵︶︷︸︹︺︻︼︽︾︿﹀﹁﹂﹃﹄﹅﹆﹇﹈﹉﹊﹋﹌﹍﹎﹏",              # (0xfe30, 0xfe4f), CJK Compatibility Forms
# "﹐﹑﹒﹔﹕﹖﹗﹘﹙﹚﹛﹜﹝﹞﹟﹠﹡﹣﹨﹪﹫",                                  # (0xfe50, 0xfe6f), Small Form Variants
"！＂＃％＆＇（）＊，－．／：；？＠［＼］＿｛｝｟｠｡｢｣､･",                      # (0xff00, 0xffef), Halfwidth and Fullwidth Forms
)
_punc_pattern = re.compile('.*?[' + ''.join(_punc) + ']')
def split_by_punc(s):
    """将一个字符串按照Unicode标准中的标点符号进行分割"""
    iters = list(_punc_pattern.finditer(s))
    if iters:
        ls = [s[i.span()[0]: i.span()[1]] for i in iters]
        ls.append(s[iters[-1].span()[1]:])
    else:
        ls = [s]
    return ls


def check_update(allow_check=True, auto_update=True):
    """检查版本更新"""

    def print_header(title, info=[]):
        title_width = max([get_actual_width(i) for i in title])
        if info:
            info_width = max([get_actual_width(i) for i in info])
        else:
            info_width = 0
        terminal_width = shutil.get_terminal_size().columns
        display_width = min(max(title_width, info_width) + 6, terminal_width)
        print('=' * display_width)
        for line in title:
            print(align_center(line, display_width))
        if info:
            print('-' * display_width)
            for line in info:
                print(line)
        print('=' * display_width)
        print('')

    # 版本号: 优先用打包exe时由hook注入的 sys.javsp_version, 否则回落到单一版本源。
    # 上游这里直接 meta.version('javsp') —— 以源码方式启动时包并未安装, 会抛
    # PackageNotFoundError 把整个 CLI 启动流程带崩(B-008); 这里容错后跳过检查。
    local_version = getattr(sys, 'javsp_version', '') or ''
    if not local_version:
        try:
            local_version = get_version()
        except Exception as e:
            logger.debug('无法确定本地版本号: ' + repr(e))
            local_version = ''
    if not local_version or local_version == FALLBACK_VERSION:
        return
    # 检查更新(查本 fork 自己的 releases: 上游仓库的版本号对本 fork 没有意义)
    if allow_check:
        api_url = API_LATEST
        release_url = RELEASES_PAGE
        print('正在检查更新...', end='')
        try:
            data = request_get(api_url, timeout=3).json()
            latest_version = data['tag_name']
            release_time = utc2local(data['published_at'])
            release_date = release_time.isoformat().split('T')[0]
            if version.parse(local_version) < version.parse(latest_version):
                update_status = 'new_version'
            else:
                update_status = 'already_latest'
        except Exception as e:
            logger.debug('检查版本更新时出错: ' + repr(e))
            update_status = 'fail_to_check'
    else:
        update_status = 'disallow'
    # 根据检查更新的情况输出软件版本信息和更新信息
    print(CLEAR_LINE, end='')
    if update_status == 'disallow':
        title = f'Jav Scraper Package: {local_version}'
        print_header([title])
    elif update_status == 'already_latest':
        title = f'Jav Scraper Package: {local_version} (已是最新版)'
        print_header([title])
    elif update_status == 'fail_to_check':
        release_url_mirror = 'https://hub.fastgit.xyz/Yuukiy/JavSP/releases/latest'
        titles = [f'Jav Scraper Package: {local_version}']
        info = ['检查更新失败，请前往以下地址查看最新版本:', '  '+release_url,
                '如果你打不开上面的地址，也可以尝试访问镜像站点:', '  '+release_url_mirror]
        print_header(titles, info)
    elif update_status == 'new_version':
        titles = [f'Jav Scraper Package: {local_version}']
        titles.append(f'↓ 有新版本可下载: {latest_version} ↓')
        titles.append(release_url)
        # 提取changelog消息
        try:
            enable_msg_head = True
            lines = data['body'].splitlines()
            changelog = [f'更新时间: {release_date}']
            for line in lines:
                if line.startswith('## '):
                    enable_msg_head = False
                    changelog.append(Style.BRIGHT + line[3:] + Style.RESET_ALL)
                elif line.startswith('- '):
                    enable_msg_head = False
                    changelog.append(line)
                elif enable_msg_head:
                    changelog.append(line)
            print_header(titles, changelog)
        except:
            print_header(titles)
        # 尝试自动更新
        if auto_update:
            try:
                logger.info('尝试自动更新到新版本: ' + latest_version + " （按'Ctrl+C'取消）")
                download_update(data)
            except KeyboardInterrupt:
                logger.info('用户取消更新')
            except Exception as e:
                logger.warning('自动更新失败，请重启程序再试或者手动下载更新')
                logger.debug(e, exc_info=True)
            finally:
                print()     # 输出空行，作为新旧程序的分隔


def safe_extract(zip_path, dest='.'):
    """安全解压 zip: 拒绝任何会写到 dest 之外的成员(zip slip)

    上游直接 `extractall()`: 若包里出现 `../evil.exe` 或绝对路径这类成员名, 会
    被写到目标目录之外。这里**先全量校验、校验通过才解压**, 非法成员直接中止,
    不会在盘上留下半个解压结果。
    """
    dest = os.path.abspath(dest)
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        for member in zip_ref.infolist():
            if not member.filename:
                continue
            target = os.path.abspath(os.path.join(dest, member.filename))
            # normcase: Windows 文件系统大小写不敏感, 只比字符串会漏判
            target_n, dest_n = os.path.normcase(target), os.path.normcase(dest)
            if target_n != dest_n and not target_n.startswith(dest_n.rstrip(os.sep) + os.sep):
                raise ValueError(f'更新包内含非法路径, 已中止解压: {member.filename}')
        zip_ref.extractall(dest)


def download_update(rel_info):
    """下载版本更新

    Args:
        rel_info (json): 调用Github API得到的最新版的release信息
    """
    if not getattr(sys, 'frozen', False):
        # 源码方式运行时 sys.executable 指向 Python 解释器 —— 下面的"备份并替换
        # 可执行文件"会去重命名解释器本身, 必须在这里拦住(上游无此判断)
        logger.warning('以源码方式运行时不支持自动更新, 请自行前往发布页下载新版')
        return
    if rel_info.get('assets'):
        down_url = rel_info['assets'][0]['browser_download_url']
        asset_name = rel_info['assets'][0]['name']
        desc = '下载更新' if shutil.get_terminal_size().columns < 120 else '下载更新: '+asset_name
        download(down_url, asset_name, desc=desc)
        if os.path.exists(asset_name):
            # 备份原有的程序
            basepath, ext = os.path.splitext(sys.executable)
            backup_name = basepath + '_backup' + ext
            if os.path.exists(backup_name):
                os.remove(backup_name)
            os.rename(sys.executable, backup_name)
            # 解压下载的zip文件(带 zip slip 防护)
            safe_extract(asset_name)
            logger.info('更新完成，启动新版本程序...')
            args = [sys.executable] + sys.argv[1:]
            p = subprocess.Popen(args, start_new_session=True)
            p.wait()
            p.terminate()
            sys.exit(0)


if __name__ == "__main__":
    setattr(sys, 'javsp_version', 'v0')
    check_update()
