"""JavSP WebUI 后端 (FastAPI)

预留入口: pyproject.toml 中 `server = "javsp.server:entry"`
复用 javsp.core 的核心逻辑, 不改动爬虫层与 CLI。
刮削/整理进度通过 SSE(Server-Sent Events) 推送, 任务以 Movie.guid 为 ID 在内存缓存。
"""
import os
import sys
import json
import queue
import threading
import hashlib
import logging
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from javsp.config import Cfg
from javsp.config_io import diff_leaves, mask_secrets, unmask_secrets
from javsp.config_reload import apply_config_changes, describe_runtime, config_transaction
from javsp.core import (
    import_crawlers, load_alias_map,
    parallel_crawler, info_summary,
    scrape_movie, organize_movie, scan_library,
)
from javsp.datatype import Movie
from javsp.task_store import TaskStore

logger = logging.getLogger('javsp.server')

# 运行内存中的任务缓存: guid -> Movie
# 用 TaskStore 封装以获得 TTL 过期 + 容量上限 + 活跃任务保护(避免长跑内存无界增长);
# 它是 dict 兼容的, 下方所有 TASKS[...] / .get() / in / .values() 用法均无需改动。
TASKS = TaskStore()

# 版本号: 单一版本源 = pyproject.toml(实读; 不再用已安装元数据优先, 避免 editable install 快照滞后导致版本漂移)
from javsp.version import get_version
__version__ = get_version()


def assign_guid(movie: Movie) -> str:
    """为 Movie 生成稳定 guid(基于番号 + 文件路径)"""
    key = (movie.dvdid or movie.cid or '') + '|' + '|'.join(movie.files)
    movie.guid = hashlib.md5(key.encode('utf-8')).hexdigest()[:12]
    return movie.guid


def movie_info_dict(info) -> Optional[dict]:
    """将 MovieInfo 转为可 JSON 序列化的 dict"""
    if info is None:
        return None
    return {k: v for k, v in vars(info).items()}


def sse_pack(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _scrape_into(movie: Movie, progress_cb=None) -> bool:
    """对已有 Movie 对象执行刮削(复用其 files/识别结果)"""
    all_info = parallel_crawler(movie, progress_cb=progress_cb)
    if not all_info:
        return False
    return info_summary(movie, all_info)


def _deep_update(base: dict, updates: dict) -> dict:
    """递归合并 updates 到 base，返回**新的** dict（不修改传入的 base）

    早期实现是原地修改 base，导致调用方 `merged = _deep_update(current, updates)`
    后 current 自身也被改成了 merged，后续 `diff_leaves(current, merged)` 恒为空、
    配置保存静默失效。故这里改成纯函数式。
    """
    out = dict(base or {})
    for k, v in (updates or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_update(out[k], v)
        else:
            out[k] = v
    return out


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时初始化: 加载配置、导入爬虫、加载别名表
    Cfg()
    import_crawlers()
    load_alias_map()
    logger.info('JavSP WebUI 服务已初始化')
    yield
    # 关闭时无特殊处理


app = FastAPI(title='JavSP WebUI', version=__version__, lifespan=lifespan)

# 校验 Host 头: 阻断 DNS Rebinding(攻击者把恶意域名解析到本机/内网 IP 后,
# 浏览器会认为是同源从而直接调用本接口)。本服务无鉴权, 故此项是必要的纵深防御。
# 需要通过别的域名/主机名访问时, 用环境变量 JAVSP_ALLOWED_HOSTS 显式放开(逗号分隔,
# 填'*' 表示不校验)。
_allowed_hosts = [h.strip() for h in os.getenv('JAVSP_ALLOWED_HOSTS', '').split(',') if h.strip()]
if _allowed_hosts != ['*']:
    # 默认只允许回环地址 + 局域网常见主机名; IP 需显式加入(如 NAS 的局域网 IP)
    _allowed_hosts += ['127.0.0.1', 'localhost', '[::1]', 'testserver']
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=_allowed_hosts)


# ----------------------------- 请求模型 -----------------------------
class ScanRequest(BaseModel):
    path: str


class ScrapeRequest(BaseModel):
    guid: Optional[str] = None
    avid: Optional[str] = None


class OrganizeRequest(BaseModel):
    guid: str


class BatchRequest(BaseModel):
    guids: List[str]
    organize: bool = False


# 目录浏览的根边界: 目录列举只允许在该路径及其子目录内进行。
# 为什么要限制: 本服务无鉴权, 若允许任意路径浏览, 等于把整个文件系统的目录结构
# 暴露给任何能访问该端口的客户端(审查时已标记「/api/scan 无白名单」是中风险面,
# 浏览功能会把它从「能读媒体目录」扩大到「能枚举任意目录」)。
# 默认值: 取环境变量 JAVSP_BROWSE_ROOT; 未设置时用 '/' (即不限制, 与原先手输路径
# 的能力一致——本项目定位是单用户自用工具, 详见 README 安全说明)。
def _browse_root() -> str:
    return os.getenv('JAVSP_BROWSE_ROOT', '/')


def _within_browse_root(target: str) -> bool:
    """判断 target 是否位于允许浏览的根内(realpath 消解 .. 与符号链接后再比)"""
    root = os.path.realpath(_browse_root())
    cand = os.path.realpath(target)
    return cand == root or cand.startswith(root + os.sep)


# ----------------------------- 路由 -----------------------------
@app.get('/api/health')
def api_health():
    return {'status': 'ok', 'version': __version__}


@app.get('/api/browse')
def api_browse(path: Optional[str] = None):
    """列举指定目录下的**子目录**(不列文件), 供前端目录选择器逐级导航

    只返回目录名与拼接后的完整路径, 不返回文件列表 —— 避免把文件名等无关信息
    也一并暴露出去。路径必须在 JAVSP_BROWSE_ROOT 之内; 省略 path 时从该根本身开始。
    """
    # 缺省/空路径时用「允许浏览的根」而不是硬编码 '/':
    # 否则 JAVSP_BROWSE_ROOT 设成别处(如容器里的 /data)时, '/' 会落在根之外 → 403。
    if not path:
        path = _browse_root()
    path = os.path.abspath(path)
    if not _within_browse_root(path):
        raise HTTPException(status_code=403, detail='该路径不在允许浏览的范围内')
    if not os.path.isdir(path):
        raise HTTPException(status_code=400, detail='目录不存在或无法访问')

    # os.scandir + follow_symlinks=False: 避免跟随符号链接导致越界; 单层列举不递归,
    # 因此不存在符号链接环路问题
    dirs = []
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        dirs.append({'name': entry.name,
                                     'path': os.path.join(path, entry.name)})
                except OSError:
                    # 单个条目不可读(权限等)不应让整个列举失败
                    continue
    except PermissionError:
        raise HTTPException(status_code=403, detail='无权访问该目录')
    except OSError as e:
        raise HTTPException(status_code=400, detail=f'读取目录失败: {e}')

    # 排序: 目录名自然序; 并给出上级路径便于「返回上一层」
    dirs.sort(key=lambda d: d['name'].lower())
    parent = os.path.dirname(path.rstrip(os.sep)) or path
    # 上级若已越出允许根, 置为 None(前端隐藏「上一层」按钮)
    parent_ok = (os.path.realpath(parent) != os.path.realpath(path)) and _within_browse_root(parent)
    return {
        'current': path,
        'parent': parent if parent_ok else None,
        'dirs': dirs,
        'root': _browse_root(),
    }


@app.post('/api/scan')
def api_scan(req: ScanRequest):
    root = req.path
    if not os.path.isdir(root):
        raise HTTPException(status_code=400, detail='目录不存在或无法访问')
    movies = scan_library(root)
    out = []
    for m in movies:
        assign_guid(m)
        # 记录扫描根目录: 整理时用它锚定相对的输出目录模板(否则会相对服务进程 CWD 解析)
        m.scan_root = os.path.abspath(root)
        TASKS[m.guid] = m
        out.append({
            'guid': m.guid,
            'dvdid': m.dvdid,
            'cid': m.cid,
            'data_src': m.data_src,
            'files': m.files,
            'scraped': m.info is not None,
        })
    return {'count': len(out), 'movies': out}


@app.get('/api/movies')
def api_movies():
    return [{
        'guid': m.guid,
        'dvdid': m.dvdid,
        'cid': m.cid,
        'data_src': m.data_src,
        'files': m.files,
        'scraped': m.info is not None,
    } for m in TASKS.values()]


@app.post('/api/scrape')
def api_scrape(req: ScrapeRequest):
    """刮削单部影片, 通过 SSE 推送各爬虫进度与最终结果"""
    movie = TASKS.get(req.guid) if req.guid else None
    avid = req.avid or (movie.dvdid if movie else None) or (movie.cid if movie else None)
    if not avid and not movie:
        raise HTTPException(status_code=400, detail='需提供 guid 或 avid')

    def event_gen():
        q: queue.Queue = queue.Queue()

        def progress_cb(name: str, status: str):
            q.put({'type': 'progress', 'crawler': name, 'status': status})

        def run():
            try:
                if movie is not None:
                    ok = _scrape_into(movie, progress_cb)
                    m = movie
                else:
                    m = scrape_movie(avid, progress_cb=progress_cb)
                    ok = m is not None
                if not ok or m is None:
                    q.put({'type': 'error', 'msg': '刮削失败: 未获取到必需字段'})
                else:
                    if movie is None:
                        assign_guid(m)
                        TASKS[m.guid] = m
                    q.put({'type': 'result', 'guid': m.guid, 'info': movie_info_dict(m.info), 'sources': getattr(m, 'sources', None)})
            except Exception as e:
                logger.exception(e)
                q.put({'type': 'error', 'msg': str(e)})
            finally:
                q.put(None)

        threading.Thread(target=run, daemon=True).start()
        while True:
            item = q.get()
            if item is None:
                break
            yield sse_pack(item)

    return StreamingResponse(event_gen(), media_type='text/event-stream')


@app.post('/api/organize')
def api_organize(req: OrganizeRequest):
    """整理(落盘): 生成 NFO + 封面 + 重命名。要求该影片已刮削(scrape)"""
    movie = TASKS.get(req.guid)
    if movie is None:
        raise HTTPException(status_code=404, detail='任务不存在')
    if movie.info is None:
        raise HTTPException(status_code=400, detail='请先对该影片执行刮削')

    def event_gen():
        q: queue.Queue = queue.Queue()

        def progress_cb(name: str, status: str):
            q.put({'type': 'progress', 'crawler': name, 'status': status})

        def run():
            try:
                q.put({'type': 'stage', 'stage': 'organizing'})
                # 标记活跃:整理期间持有 Movie,不能被 TTL/容量回收(否则落盘到一半源对象被删)
                with TASKS.mark_active(req.guid):
                    result = organize_movie(movie, progress_cb=progress_cb)
                q.put({'type': 'result', 'result': result})
            except Exception as e:
                logger.exception(e)
                q.put({'type': 'error', 'msg': str(e)})
            finally:
                q.put(None)

        threading.Thread(target=run, daemon=True).start()
        while True:
            item = q.get()
            if item is None:
                break
            yield sse_pack(item)

    return StreamingResponse(event_gen(), media_type='text/event-stream')


@app.post('/api/batch')
def api_batch(req: BatchRequest):
    """批量刮削(可附带整理), 通过 SSE 推送每部进度与整体进度"""
    movies = [TASKS[g] for g in req.guids if g in TASKS]
    total = len(movies)
    if total == 0:
        raise HTTPException(status_code=400, detail='没有可处理的任务')

    def event_gen():
        q: queue.Queue = queue.Queue()

        def run():
            success = fail = 0
            # 整批处理期间所有影片都标记活跃:批量可能跑很久,期间不能被回收
            with TASKS.mark_active(*[m.guid for m in movies]):
                for idx, m in enumerate(movies, 1):
                    avid = m.dvdid or m.cid
                    q.put({'type': 'movie_start', 'index': idx, 'total': total,
                           'guid': m.guid, 'avid': avid})

                    def progress_cb(name, status, _m=m):
                        q.put({'type': 'progress', 'index': idx, 'guid': _m.guid,
                               'crawler': name, 'status': status})

                    try:
                        ok = _scrape_into(m, progress_cb)
                    except Exception as e:
                        logger.exception(e)
                        ok = False
                    if ok and req.organize:
                        try:
                            organize_movie(m, progress_cb=progress_cb)
                            organized = True
                        except Exception as e:
                            logger.exception(e)
                            organized = False
                    else:
                        organized = False
                    if ok:
                        success += 1
                        q.put({'type': 'movie_done', 'index': idx, 'guid': m.guid,
                               'ok': True, 'organized': organized,
                               'title': (m.info.title if m.info else None),
                               'sources': getattr(m, 'sources', None)})
                    else:
                        fail += 1
                        q.put({'type': 'movie_done', 'index': idx, 'guid': m.guid,
                               'ok': False, 'organized': False,
                               'sources': getattr(m, 'sources', None)})
                q.put({'type': 'all_done', 'success': success, 'fail': fail, 'total': total})
            q.put(None)

        threading.Thread(target=run, daemon=True).start()
        while True:
            item = q.get()
            if item is None:
                break
            yield sse_pack(item)

    return StreamingResponse(event_gen(), media_type='text/event-stream')


@app.get('/api/config')
def api_config_get():
    """返回当前运行时配置(只读展示)

    敏感字段(翻译 api_key / app_id 等)以掩码返回: 本服务无鉴权, 原样返回等于
    把凭据交给任何能访问该端口的客户端。掩码串可被前端原样回传, PUT 时会还原为
    真实值(见 unmask_secrets), 因此不会造成丢密钥。
    """
    try:
        return mask_secrets(Cfg().model_dump(mode='json'))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'读取配置失败: {e}')


@app.put('/api/config')
def api_config_put(updates: dict):
    """更新并写回 config.yml，并让运行时立即生效（无需重启）

    写回采用「只替换变更字段」的方式(config_io.write_config_preserving_comments),
    以保留 config.yml 里的中文注释与原有排版——早期版本用 yaml.safe_dump 整体重写,
    会把全部注释丢掉(实测 100 行注释 → 0 行)。

    写回后调用 config_reload.apply_config_changes 做两件事：
    1) 重载 Cfg 单例（置空 confz_instance 触发重新读盘）；
    2) 刷新各爬虫模块级 Request 的代理/超时——它们在 import 时就被固化了,
       只重载 Cfg 的话爬虫仍会用旧代理。
    任一环节失败都会自动回滚文件与运行时配置。
    """
    try:
        # 整个「读快照→合并→校验→写入」放进同一把锁, 避免并发 PUT 互相覆盖
        # (原先快照与合并在锁外, 后写者会用旧快照算出的 changes 覆盖先写者的改动)
        with config_transaction():
            try:
                current = Cfg().model_dump(mode='json')
                # 前端回传的敏感字段是掩码串, 先还原为真实值, 避免把 '***MASKED***' 写进 config.yml
                updates = unmask_secrets(updates, current)
                merged = _deep_update(current, updates)
                Cfg.model_validate(merged)  # 仅做校验, 不替换运行时单例
            except Exception as e:
                raise HTTPException(status_code=400, detail=f'配置校验失败: {e}')
            cfg_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.yml')
            try:
                changes = diff_leaves(current, merged)
                res = apply_config_changes(cfg_path, changes)
            except Exception as e:
                raise HTTPException(status_code=500, detail=f'写入 config.yml 失败: {e}')
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'写入 config.yml 失败: {e}')

    n, missing = res['written'], res['missing']
    if res.get('rolled_back'):
        raise HTTPException(
            status_code=500,
            detail=f'新配置未能加载，已自动回滚 config.yml 与运行时配置。原因：{res["error"]}')

    if n == 0:
        note = '没有检测到字段变化，未写入文件'
        return {'status': 'unchanged', 'path': cfg_path, 'changed': 0,
                'missing': missing, 'reloaded': False, 'note': note}

    if res['reloaded']:
        note = (f'已写入 config.yml（改动 {n} 个字段，注释与排版保留），'
                f'并已立即生效（刷新 {len(res["refreshed"])} 个爬虫出口），无需重启')
    else:
        note = (f'已写入 config.yml（改动 {n} 个字段），但热重载未成功，'
                f'需重启服务后生效。原因：{res["error"]}')
    if missing:
        note += f'；以下字段未能在文件中定位到，未写入：{", ".join(missing)}'
    return {'status': 'applied' if res['reloaded'] else 'written', 'path': cfg_path,
            'changed': n, 'missing': missing, 'reloaded': res['reloaded'],
            'refreshed': res['refreshed'], 'note': note}


@app.get('/api/config/runtime')
def api_config_runtime():
    """返回当前**运行时**实际生效的关键配置（含各爬虫出口的代理/超时）

    用于确认界面保存后是否真的即时生效——config.yml 是磁盘值，这里读到的是内存值。
    """
    try:
        return {'status': 'ok', 'runtime': describe_runtime()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'读取运行时配置失败: {e}')


# ----------------------------- 前端静态托管(生产构建后) -----------------------------
_dist_dir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'frontend', 'dist')
if os.path.isdir(_dist_dir):
    app.mount('/', StaticFiles(directory=_dist_dir, html=True), name='static')


def entry():
    """console script 入口: 启动 FastAPI 服务"""
    import uvicorn
    # 默认只监听本机: 本服务无鉴权且具备任意目录读写/配置改写能力,
    # 监听 0.0.0.0 等于把 NAS 共享目录的写权限暴露给整个局域网。
    # 需要局域网/容器外访问时, 由部署方显式设 JAVSP_HOST(如 Docker 镜像里已设 0.0.0.0)。
    host = os.getenv('JAVSP_HOST', '127.0.0.1')
    port = int(os.getenv('JAVSP_PORT', '8000'))
    logger.info(f'启动 JavSP WebUI: http://{host}:{port}')
    uvicorn.run(app, host=host, port=port)


if __name__ == '__main__':
    entry()
