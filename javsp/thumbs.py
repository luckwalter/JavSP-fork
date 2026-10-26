"""缩略图缓存: 结果审阅页展示"每个渠道各自的封面小图"

为什么需要
----------
审阅页原本只有渠道名与字段贡献, 无法直观判断"哪个源给的封面更好看"。本模块把
各站自己的封面拉成小尺寸缩略图供前端并排展示。

关键设计
--------
1. **缓存目录**
   - 容器形态: 优先 ``/data/.javsp_cache/thumbs``。``/data`` 是媒体库挂载点,
     用**点开头**的隐藏目录, Jellyfin / Emby / Kodi 默认跳过, 不会污染媒体库索引;
     该目录随共享目录持久化, 容器重建后缓存仍在。
   - 桌面版: exe 同级 ``cache/thumbs``(便携, 整包升级不覆盖)。
   - 均可通过配置 ``cache.thumbs_dir`` 显式覆盖(留空=自动判定)。
   - 判定失败(目录不可写)时回退到系统临时目录, 功能不中断。
2. **文件名**: ``sha256(url)`` 前 20 位 + 扩展名 —— 天然规避路径穿越与非法字符,
   同一 URL 永远命中同一文件。
3. **缩放**: 各站封面原图普遍 800px 以上(DMM 图床等), 直接缓存原图浪费空间。
   用 Pillow(项目已有依赖)缩到宽 ``THUMB_WIDTH``, 存 jpg, 单张约 5~15 KB。
4. **失败降级**: 下载/解码/写盘任何一步失败, 一律返回 None(前端显示占位),
   **绝不影响刮削主流程**; 缩略图是锦上添花, 不是必需品。
5. **安全边界**: 仅允许 http/https; 响应体上限 5MB; 超时 10s; 代理与证书校验
   复用项目统一的 ``read_proxy()`` / ``tls_verify()``。
"""
import hashlib
import io
import logging
import os
import shutil
import sys
import tempfile
import time
from urllib.parse import urlparse

import requests

from javsp.config import Cfg

logger = logging.getLogger(__name__)

# 缩略图宽度(px)。竖版封面按原始比例缩放, 约 120x180。
THUMB_WIDTH = 120
# 原始图片下载上限(字节), 防止异常大响应吃满内存
MAX_BYTES = 5 * 1024 * 1024
# 下载超时(秒)
TIMEOUT = 10
# 缓存文件扩展名
_EXT = '.jpg'
# 容器形态的媒体库挂载点(与 docker/Dockerfile 的 VOLUME 一致)
_CONTAINER_DATA = '/data'
# 容器形态的缓存子目录(点开头, 媒体服务器默认跳过)
_CONTAINER_SUBDIR = os.path.join('.javsp_cache', 'thumbs')


def _config():
    """取当前配置(单例); 取不到时返回 None, 由调用方走默认值"""
    try:
        return Cfg()
    except Exception as e:  # 配置异常不应让缩略图功能整体不可用
        logger.debug('读取配置失败, 使用默认缓存策略: %s', e)
        return None


def _default_thumbs_dir() -> str:
    """自动判定缓存目录

    容器形态: /data/.javsp_cache/thumbs (可写时)
    桌面版  : exe 同级 cache/thumbs
    兜底    : 系统临时目录
    """
    # 容器形态: /data 存在且可写 -> 放媒体库挂载点下的隐藏目录(随共享持久化)
    data_dir = _CONTAINER_DATA
    if os.path.isdir(data_dir) and os.access(data_dir, os.W_OK):
        return os.path.join(data_dir, _CONTAINER_SUBDIR)
    # 桌面版: exe 同级
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(os.path.abspath(sys.executable))
        return os.path.join(base, 'cache', 'thumbs')
    # 源码运行: 项目根下的 cache/thumbs
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'cache', 'thumbs')


def thumbs_dir() -> str:
    """返回缓存目录路径(不保证已存在, 由调用方按需创建)

    优先级: 配置 cache.thumbs_dir > 自动判定。显式配置不可写时回退自动判定,
    避免因一个写错的配置项导致整个功能报错。
    """
    cfg = _config()
    custom = ''
    if cfg is not None:
        try:
            custom = (getattr(cfg.cache, 'thumbs_dir', '') or '').strip()
        except Exception:
            custom = ''
    path = custom or _default_thumbs_dir()
    try:
        os.makedirs(path, exist_ok=True)
        return path
    except OSError as e:
        fallback = os.path.join(tempfile.gettempdir(), 'javsp_thumbs')
        logger.warning('缓存目录不可用(%s: %s), 回退到 %s', path, e, fallback)
        os.makedirs(fallback, exist_ok=True)
        return fallback


def cache_path(url: str) -> str:
    """URL -> 缓存文件路径(sha256 命名, 天然规避路径穿越)"""
    digest = hashlib.sha256(url.encode('utf-8', 'replace')).hexdigest()[:20]
    return os.path.join(thumbs_dir(), digest + _EXT)


def _is_allowed(url: str) -> bool:
    """仅允许 http/https, 挡掉 file:// / data: 等本地协议"""
    try:
        scheme = (urlparse(url).scheme or '').lower()
    except Exception:
        return False
    return scheme in ('http', 'https')


def _shrink(data: bytes) -> bytes:
    """用 Pillow 缩到 THUMB_WIDTH 宽的 jpg; 失败(非图片/缺 Pillow)时原样返回"""
    try:
        from PIL import Image
    except ImportError:
        logger.warning('Pillow 不可用, 缩略图将存原图')
        return data
    try:
        with Image.open(io.BytesIO(data)) as im:
            im = im.convert('RGB')
            if im.width > THUMB_WIDTH:
                ratio = THUMB_WIDTH / im.width
                im = im.resize((THUMB_WIDTH, max(1, int(im.height * ratio))), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format='JPEG', quality=80, optimize=True)
            return buf.getvalue()
    except Exception as e:
        logger.debug('缩略图缩放失败, 存原图: %s', e)
        return data


def ensure_thumb(url: str) -> str:
    """取缩略图: 命中缓存直接返回路径; 未命中则下载+缩放+落盘

    返回空串表示无可用缩略图(失败已降级, 不抛异常)。
    """
    if not url or not isinstance(url, str):
        return ''
    url = url.strip()
    if not url or not _is_allowed(url):
        return ''

    path = cache_path(url)
    if os.path.isfile(path) and os.path.getsize(path) > 0:
        return path

    # 代理与证书校验统一走项目配置, 避免与主链路行为不一致
    proxies = {}
    verify = True
    try:
        from javsp.web.base import read_proxy, tls_verify
        proxies = read_proxy()
        verify = tls_verify()
    except Exception:
        pass

    try:
        with requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, proxies=proxies,
                          timeout=TIMEOUT, stream=True, verify=verify) as r:
            r.raise_for_status()
            buf = io.BytesIO()
            for chunk in r.iter_content(64 * 1024):
                if not chunk:
                    continue
                buf.write(chunk)
                if buf.tell() > MAX_BYTES:
                    logger.debug('缩略图响应过大, 放弃: %s', url)
                    return ''
            raw = buf.getvalue()
    except Exception as e:
        logger.debug('下载缩略图失败: %s (%s)', url, e)
        return ''

    if not raw:
        return ''
    data = _shrink(raw)
    # 原子落盘: 先写临时文件再改名, 避免并发请求读到半截文件
    tmp = path + '.part'
    try:
        with open(tmp, 'wb') as f:
            f.write(data)
        os.replace(tmp, path)
    except OSError as e:
        logger.debug('写入缩略图缓存失败: %s', e)
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return ''
    return path


def stats() -> dict:
    """缓存统计: 张数 / 占用字节 / 最早时间 / 目录路径"""
    path = thumbs_dir()
    count = 0
    total = 0
    oldest = None
    try:
        with os.scandir(path) as it:
            for entry in it:
                if not entry.is_file() or not entry.name.endswith(_EXT):
                    continue
                count += 1
                try:
                    st = entry.stat()
                    total += st.st_size
                    if oldest is None or st.st_mtime < oldest:
                        oldest = st.st_mtime
                except OSError:
                    continue
    except OSError:
        pass
    return {
        'dir': path,
        'count': count,
        'bytes': total,
        'oldest': oldest,
        'oldest_days': int((time.time() - oldest) / 86400) if oldest else None,
    }


def clear_all() -> int:
    """清空缓存, 返回删除的文件数"""
    path = thumbs_dir()
    removed = 0
    try:
        with os.scandir(path) as it:
            for entry in it:
                if entry.is_file() and entry.name.endswith(_EXT):
                    try:
                        os.remove(entry.path)
                        removed += 1
                    except OSError:
                        continue
    except OSError as e:
        logger.warning('清空缓存失败: %s', e)
    return removed


def cleanup_expired(days: int) -> int:
    """清理超过保留天数的缩略图(按 mtime), 返回删除的文件数

    只删缩略图缓存目录内的 jpg, 绝不触碰已整理到媒体库的封面文件。
    """
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 7
    if days <= 0:
        return 0
    cutoff = time.time() - days * 86400
    path = thumbs_dir()
    removed = 0
    try:
        with os.scandir(path) as it:
            for entry in it:
                if not entry.is_file() or not entry.name.endswith(_EXT):
                    continue
                try:
                    if entry.stat().st_mtime < cutoff:
                        os.remove(entry.path)
                        removed += 1
                except OSError:
                    continue
    except OSError as e:
        logger.warning('清理过期缓存失败: %s', e)
    if removed:
        logger.info('已清理过期缩略图 %d 张(保留 %d 天)', removed, days)
    return removed
