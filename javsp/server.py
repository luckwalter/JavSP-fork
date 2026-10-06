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
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from javsp.config import Cfg
from javsp.core import (
    import_crawlers, load_alias_map,
    parallel_crawler, info_summary,
    scrape_movie, organize_movie, scan_library,
)
from javsp.datatype import Movie

logger = logging.getLogger('javsp.server')

# 运行内存中的任务缓存: guid -> Movie
TASKS: Dict[str, Movie] = {}

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
    """递归合并 updates 到 base"""
    for k, v in (updates or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k] = _deep_update(base[k], v)
        else:
            base[k] = v
    return base


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


# ----------------------------- 路由 -----------------------------
@app.get('/api/health')
def api_health():
    return {'status': 'ok', 'version': __version__}


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
    """返回当前运行时配置(只读展示)"""
    try:
        return Cfg().model_dump(mode='json')
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'读取配置失败: {e}')


@app.put('/api/config')
def api_config_put(updates: dict):
    """更新并写回 config.yml(重启后生效)"""
    try:
        current = Cfg().model_dump(mode='json')
        merged = _deep_update(current, updates)
        Cfg.model_validate(merged)  # 仅做校验, 不替换运行时单例
    except Exception as e:
        raise HTTPException(status_code=400, detail=f'配置校验失败: {e}')
    cfg_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.yml')
    try:
        import yaml
        with open(cfg_path, 'w', encoding='utf-8') as f:
            yaml.safe_dump(merged, f, allow_unicode=True, sort_keys=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'写入 config.yml 失败: {e}')
    return {'status': 'written', 'path': cfg_path, 'note': '已写入 config.yml, 重启服务后生效'}


# ----------------------------- 前端静态托管(生产构建后) -----------------------------
_dist_dir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'frontend', 'dist')
if os.path.isdir(_dist_dir):
    app.mount('/', StaticFiles(directory=_dist_dir, html=True), name='static')


def entry():
    """console script 入口: 启动 FastAPI 服务"""
    import uvicorn
    host = os.getenv('JAVSP_HOST', '0.0.0.0')
    port = int(os.getenv('JAVSP_PORT', '8000'))
    logger.info(f'启动 JavSP WebUI: http://{host}:{port}')
    uvicorn.run(app, host=host, port=port)


if __name__ == '__main__':
    entry()
