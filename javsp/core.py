"""核心业务服务层(与 CLI 解耦,供 Web 后端与命令行共用)

本模块从原 __main__.py 抽取出纯逻辑,去除 tqdm/print/os.chdir 等 CLI 副作用,
改用 progress_cb 回调上报进度,便于 Web 端通过 SSE 推送。爬虫层 javsp/web/* 完全复用,不改动。
"""
import os
import re
import sys
import json
import time
import logging
import threading
from typing import Dict, List, Callable, Optional

import requests
from tqdm import tqdm
from PIL import Image

from javsp.lib import resource_path
from javsp.nfo import write_nfo
from javsp.file import (
    scan_movies, replace_illegal_chars, get_remaining_path_len, get_fmt_size,
)
from javsp.func import split_by_punc, remove_trail_actor_in_title
from javsp.image import valid_pic, get_pic_size, add_label_to_poster, LabelPostion
from javsp.datatype import Movie, MovieInfo
from javsp.web.base import download
from javsp.web.exceptions import *
from javsp.web.translate import translate_movie_info
from javsp.config import Cfg, CrawlerID
from javsp.cropper import get_cropper
from javsp.avid import guess_av_type, get_id

logger = logging.getLogger('javsp.core')

# 女优别名表(Web/CLI 共用,启动时 load_alias_map 加载)
actressAliasMap = {}

SUBTITLE_MARK_FILE = Image.open(os.path.abspath(resource_path('image/sub_mark.png')))
UNCENSORED_MARK_FILE = Image.open(os.path.abspath(resource_path('image/unc_mark.png')))


def load_alias_map():
    """加载女优别名表(替代原 entry() 内的内联加载)"""
    global actressAliasMap
    if Cfg().crawler.normalize_actress_name:
        actressAliasMapPath = resource_path("data/actress_alias.json")
        with open(actressAliasMapPath, "r", encoding="utf-8") as f:
            actressAliasMap = json.load(f)
    else:
        actressAliasMap = {}


def resolve_alias(name):
    """将别名解析为固定的名字"""
    for fixedName, aliases in actressAliasMap.items():
        if name in aliases:
            return fixedName
    return name  # 如果找不到别名对应的固定名字，则返回原名


def import_crawlers():
    """按配置文件的抓取器顺序将该字段转换为抓取器的函数列表"""
    unknown_mods = []
    for _, mods in Cfg().crawler.selection.items():
        valid_mods = []
        for name in mods:
            try:
                import_name = 'javsp.web.' + name
                __import__(import_name)
                valid_mods.append(import_name)  # 抓取器有效: 使用完整模块路径，便于程序实际使用
            except ModuleNotFoundError:
                unknown_mods.append(name)       # 抓取器无效: 仅使用模块名，便于显示
    if unknown_mods:
        logger.warning('配置的抓取器无效: ' + ', '.join(unknown_mods))


# progress_cb: 可选回调, 签名为 (crawler_name: str, status: str) -> None
#   status 取值: start / success / not_found / duplicate / blocked / error
ProgressCb = Optional[Callable[[str, str], None]]


# 爬虫是IO密集型任务，可以通过多线程提升效率
def parallel_crawler(movie: Movie, tqdm_bar=None, progress_cb: ProgressCb = None):
    """使用多线程抓取不同网站的数据"""
    def wrapper(parser, info: MovieInfo, retry):
        """对抓取器函数进行包装，便于更新提示信息和自动重试"""
        crawler_name = threading.current_thread().name
        task_info = f'Crawler: {crawler_name}: {info.dvdid}'
        if callable(progress_cb):
            progress_cb(crawler_name, 'start')
        for cnt in range(retry):
            try:
                parser(info)
                movie_id = info.dvdid or info.cid
                logger.debug(f"{crawler_name}: 抓取成功: '{movie_id}': '{info.url}'")
                setattr(info, 'success', True)
                if isinstance(tqdm_bar, tqdm):
                    tqdm_bar.set_description(f'{crawler_name}: 抓取完成')
                if callable(progress_cb):
                    progress_cb(crawler_name, 'success')
                break
            except MovieNotFoundError as e:
                logger.debug(e)
                if callable(progress_cb):
                    progress_cb(crawler_name, 'not_found')
                break
            except MovieDuplicateError as e:
                logger.exception(e)
                if callable(progress_cb):
                    progress_cb(crawler_name, 'duplicate')
                break
            except (SiteBlocked, SitePermissionError, CredentialError) as e:
                logger.error(e)
                if callable(progress_cb):
                    progress_cb(crawler_name, 'blocked')
                break
            except requests.exceptions.RequestException as e:
                logger.debug(f'{crawler_name}: 网络错误，正在重试 ({cnt+1}/{retry}): \n{repr(e)}')
                if isinstance(tqdm_bar, tqdm):
                    tqdm_bar.set_description(f'{crawler_name}: 网络错误，正在重试')
            except Exception as e:
                logger.exception(e)
                if callable(progress_cb):
                    progress_cb(crawler_name, 'error')

    # 根据影片的数据源获取对应的抓取器
    crawler_mods: List[CrawlerID] = Cfg().crawler.selection[movie.data_src]

    all_info = {i.value: MovieInfo(movie) for i in crawler_mods}
    # 番号为cid但同时也有有效的dvdid时，也尝试使用普通模式进行抓取
    if movie.data_src == 'cid' and movie.dvdid:
        crawler_mods = crawler_mods + Cfg().crawler.selection.normal
        for i in all_info.values():
            i.dvdid = None
        for i in Cfg().crawler.selection.normal:
            all_info[i.value] = MovieInfo(movie.dvdid)
    thread_pool = []
    for mod_partial, info in all_info.items():
        mod = f"javsp.web.{mod_partial}"
        parser = getattr(sys.modules[mod], 'parse_data')
        # 将all_info中的info实例传递给parser，parser抓取完成后，info实例的值已经完成更新
        # TODO: 抓取器如果带有parse_data_raw，说明它已经自行进行了重试处理，此时将重试次数设置为1
        if hasattr(sys.modules[mod], 'parse_data_raw'):
            th = threading.Thread(target=wrapper, name=mod, args=(parser, info, 1))
        else:
            th = threading.Thread(target=wrapper, name=mod, args=(parser, info, Cfg().network.retry))
        th.start()
        thread_pool.append(th)
    # 等待所有线程结束
    timeout = Cfg().network.retry * Cfg().network.timeout.total_seconds()
    for th in thread_pool:
        th: threading.Thread
        th.join(timeout=timeout)
    # 根据抓取结果更新影片类型判定
    if movie.data_src == 'cid' and movie.dvdid:
        titles = [all_info[i].title for i in Cfg().crawler.selection[movie.data_src]]
        if any(titles):
            movie.dvdid = None
            all_info = {k: v for k, v in all_info.items() if k in Cfg().crawler.selection['cid']}
        else:
            logger.debug(f'自动更正影片数据源类型: {movie.dvdid} ({movie.cid}): normal')
            movie.data_src = 'normal'
            movie.cid = None
            all_info = {k: v for k, v in all_info.items() if k not in Cfg().crawler.selection['cid']}
    # 删除抓取失败的站点对应的数据
    all_info = {k: v for k, v in all_info.items() if hasattr(v, 'success')}
    for info in all_info.values():
        del info.success
    # 删除all_info中键名中的'web.'
    all_info = {k[4:]: v for k, v in all_info.items()}
    return all_info


def info_summary(movie: Movie, all_info: Dict[str, MovieInfo]):
    """汇总多个来源的在线数据生成最终数据"""
    final_info = MovieInfo(movie)
    ########## 部分字段配置了专门的选取逻辑，先处理这些字段 ##########
    # genre
    if 'javdb' in all_info and all_info['javdb'].genre:
        final_info.genre = all_info['javdb'].genre

    ########## 移除所有抓取器数据中，标题尾部的女优名 ##########
    if Cfg().summarizer.title.remove_trailing_actor_name:
        for name, data in all_info.items():
            data.title = remove_trail_actor_in_title(data.title, data.actress)
    ########## 然后检查所有字段，如果某个字段还是默认值，则按照优先级选取数据 ##########
    # parser直接更新了all_info中的项目，而初始all_info是按照优先级生成的，已经符合配置的优先级顺序了
    # 按照优先级取出各个爬虫获取到的信息
    attrs = [i for i in dir(final_info) if not i.startswith('_')]
    covers, big_covers = [], []
    for name, data in all_info.items():
        absorbed = []
        # 遍历所有属性，如果某一属性当前值为空而爬取的数据中含有该属性，则采用爬虫的属性
        for attr in attrs:
            incoming = getattr(data, attr)
            current = getattr(final_info, attr)
            if attr == 'cover':
                if incoming and (incoming not in covers):
                    covers.append(incoming)
                    absorbed.append(attr)
            elif attr == 'big_cover':
                if incoming and (incoming not in big_covers):
                    big_covers.append(incoming)
                    absorbed.append(attr)
            elif attr == 'uncensored':
                if (current is None) and (incoming is not None):
                    setattr(final_info, attr, incoming)
                    absorbed.append(attr)
            else:
                if (not current) and (incoming):
                    setattr(final_info, attr, incoming)
                    absorbed.append(attr)
        if absorbed:
            logger.debug(f"从'{name}'中获取了字段: " + ' '.join(absorbed))
    # 使用网站的番号作为番号
    if Cfg().crawler.respect_site_avid:
        id_weight = {}
        for name, data in all_info.items():
            if data.title:
                if movie.dvdid:
                    id_weight.setdefault(data.dvdid, []).append(name)
                else:
                    id_weight.setdefault(data.cid, []).append(name)
        # 根据权重选择最终番号
        if id_weight:
            id_weight = {k: v for k, v in sorted(id_weight.items(), key=lambda x: len(x[1]), reverse=True)}
            final_id = list(id_weight.keys())[0]
            if movie.dvdid:
                final_info.dvdid = final_id
            else:
                final_info.cid = final_id
    # javdb封面有水印，优先采用其他站点的封面
    javdb_cover = getattr(all_info.get('javdb'), 'cover', None)
    if javdb_cover is not None:
        match Cfg().crawler.use_javdb_cover:
            case UseJavDBCover.fallback:
                covers.remove(javdb_cover)
                covers.append(javdb_cover)
            case UseJavDBCover.no:
                covers.remove(javdb_cover)

    setattr(final_info, 'covers', covers)
    setattr(final_info, 'big_covers', big_covers)
    # 对cover和big_cover赋值，避免后续检查必须字段时出错
    if covers:
        final_info.cover = covers[0]
    if big_covers:
        final_info.big_cover = big_covers[0]
    ########## 部分字段放在最后进行检查 ##########
    # 特殊的 genre
    if final_info.genre is None:
        final_info.genre = []
    if movie.hard_sub:
        final_info.genre.append('内嵌字幕')
    if movie.uncensored:
        final_info.genre.append('无码流出/破解')

    # 女优别名固定
    if Cfg().crawler.normalize_actress_name and bool(final_info.actress_pics):
        final_info.actress = [resolve_alias(i) for i in final_info.actress]
        if final_info.actress_pics:
            final_info.actress_pics = {
                resolve_alias(key): value for key, value in final_info.actress_pics.items()
            }

    # 检查是否所有必需的字段都已经获得了值
    for attr in Cfg().crawler.required_keys:
        if not getattr(final_info, attr, None):
            logger.error(f"所有抓取器均未获取到字段: '{attr}'，抓取失败")
            return False
    # 必需字段均已获得了值：将最终的数据附加到movie
    movie.info = final_info
    return True


def generate_names(movie: Movie):
    """按照模板生成相关文件的文件名"""

    def legalize_path(path: str):
        """
            Windows下文件名中不能包含换行 #467
            所以这里对文件路径进行合法化
        """
        return ''.join(c for c in path if c not in {'\n'})

    info = movie.info
    # 准备用来填充命名模板的字典
    d = info.get_info_dic()

    if info.actress and len(info.actress) > Cfg().summarizer.path.max_actress_count:
        logging.debug('女优人数过多，按配置保留了其中的前n个: ' + ','.join(info.actress))
        actress = info.actress[:Cfg().summarizer.path.max_actress_count] + ['…']
    else:
        actress = info.actress
    d['actress'] = ','.join(actress) if actress else Cfg().summarizer.default.actress

    # 保存label供后面判断裁剪图片的方式使用
    setattr(info, 'label', d['label'].upper())
    # 处理字段：替换不能作为文件名的字符，移除首尾的空字符
    for k, v in d.items():
        d[k] = replace_illegal_chars(v.strip())

    # 生成nfo文件中的影片标题
    nfo_title = Cfg().summarizer.nfo.title_pattern.format(**d)
    setattr(info, 'nfo_title', nfo_title)

    # 使用字典填充模板，生成相关文件的路径（多分片影片要考虑CD-x部分）
    cdx = '' if len(movie.files) <= 1 else '-CD1'
    if hasattr(info, 'title_break'):
        title_break = info.title_break
    else:
        title_break = split_by_punc(d['title'])
    if hasattr(info, 'ori_title_break'):
        ori_title_break = info.ori_title_break
    else:
        ori_title_break = split_by_punc(d['rawtitle'])
    copyd = d.copy()

    def legalize_info():
        if movie.save_dir != None:
            movie.save_dir = legalize_path(movie.save_dir)
        if movie.nfo_file != None:
            movie.nfo_file = legalize_path(movie.nfo_file)
        if movie.fanart_file != None:
            movie.fanart_file = legalize_path(movie.fanart_file)
        if movie.poster_file != None:
            movie.poster_file = legalize_path(movie.poster_file)
        if d['title'] != copyd['title']:
            logger.info(f"自动截短标题为:\n{copyd['title']}")
        if d['rawtitle'] != copyd['rawtitle']:
            logger.info(f"自动截短原始标题为:\n{copyd['rawtitle']}")
        return

    copyd['num'] = copyd['num'] + movie.attr_str
    longest_ext = max((os.path.splitext(i)[1] for i in movie.files), key=len)
    for end in range(len(ori_title_break), 0, -1):
        copyd['rawtitle'] = replace_illegal_chars(''.join(ori_title_break[:end]).strip())
        for sub_end in range(len(title_break), 0, -1):
            copyd['title'] = replace_illegal_chars(''.join(title_break[:sub_end]).strip())
            if Cfg().summarizer.move_files:
                save_dir = os.path.normpath(Cfg().summarizer.path.output_folder_pattern.format(**copyd)).strip()
                basename = os.path.normpath(Cfg().summarizer.path.basename_pattern.format(**copyd)).strip()
            else:
                # 如果不整理文件，则保存抓取的数据到当前目录
                save_dir = os.path.dirname(movie.files[0])
                filebasename = os.path.basename(movie.files[0])
                ext = os.path.splitext(filebasename)[1]
                basename = filebasename.replace(ext, '')
            long_path = os.path.join(save_dir, basename + longest_ext)
            remaining = get_remaining_path_len(os.path.abspath(long_path))
            if remaining > 0:
                movie.save_dir = save_dir
                movie.basename = basename
                movie.nfo_file = os.path.join(save_dir, Cfg().summarizer.nfo.basename_pattern.format(**copyd) + '.nfo')
                movie.fanart_file = os.path.join(save_dir, Cfg().summarizer.fanart.basename_pattern.format(**copyd) + '.jpg')
                movie.poster_file = os.path.join(save_dir, Cfg().summarizer.cover.basename_pattern.format(**copyd) + '.jpg')
                return legalize_info()
    else:
        # 以防万一，当整理路径非常深或者标题起始很长一段没有标点符号时，硬性截短生成的名称
        copyd['title'] = copyd['title'][:remaining]
        copyd['rawtitle'] = copyd['rawtitle'][:remaining]
        # 如果不整理文件，则保存抓取的数据到当前目录
        if not Cfg().summarizer.move_files:
            save_dir = os.path.dirname(movie.files[0])
            filebasename = os.path.basename(movie.files[0])
            ext = os.path.splitext(filebasename)[1]
            basename = filebasename.replace(ext, '')
        else:
            save_dir = os.path.normpath(Cfg().summarizer.path.output_folder_pattern.format(**copyd)).strip()
            basename = os.path.normpath(Cfg().summarizer.path.basename_pattern.format(**copyd)).strip()
        movie.save_dir = save_dir
        movie.basename = basename

        movie.nfo_file = os.path.join(save_dir, Cfg().summarizer.nfo.basename_pattern.format(**copyd) + '.nfo')
        movie.fanart_file = os.path.join(save_dir, Cfg().summarizer.fanart.basename_pattern.format(**copyd) + '.jpg')
        movie.poster_file = os.path.join(save_dir, Cfg().summarizer.cover.basename_pattern.format(**copyd) + '.jpg')

        return legalize_info()


def process_poster(movie: Movie):
    def should_use_ai_crop_match(label):
        for r in Cfg().summarizer.cover.crop.on_id_pattern:
            if re.match(r, label):
                return True
        return False
    crop_engine = None
    if (movie.info.uncensored or
       movie.data_src == 'fc2' or
       should_use_ai_crop_match(movie.info.label.upper())):
        crop_engine = Cfg().summarizer.cover.crop.engine
    cropper = get_cropper(crop_engine)
    fanart_image = Image.open(movie.fanart_file)
    fanart_cropped = cropper.crop(fanart_image)

    if Cfg().summarizer.cover.add_label:
        if movie.hard_sub:
            fanart_cropped = add_label_to_poster(fanart_cropped, SUBTITLE_MARK_FILE, LabelPostion.BOTTOM_RIGHT)
        if movie.uncensored:
            fanart_cropped = add_label_to_poster(fanart_cropped, UNCENSORED_MARK_FILE, LabelPostion.BOTTOM_LEFT)
    fanart_cropped.save(movie.poster_file)


def download_cover(covers, fanart_path, big_covers=[]):
    """下载封面图片"""
    # 优先下载高清封面
    for url in big_covers:
        pic_path = get_pic_path(fanart_path, url)
        for _ in range(Cfg().network.retry):
            try:
                info = download(url, pic_path)
                if valid_pic(pic_path):
                    filesize = get_fmt_size(pic_path)
                    width, height = get_pic_size(pic_path)
                    elapsed = time.strftime("%M:%S", time.gmtime(info['elapsed']))
                    speed = get_fmt_size(info['rate']) + '/s'
                    logger.info(f"已下载高清封面: {width}x{height}, {filesize} [{elapsed}, {speed}]")
                    return (url, pic_path)
            except requests.exceptions.HTTPError:
                # HTTPError通常说明猜测的高清封面地址实际不可用，因此不再重试
                break
    # 如果没有高清封面或高清封面下载失败
    for url in covers:
        pic_path = get_pic_path(fanart_path, url)
        for _ in range(Cfg().network.retry):
            try:
                download(url, pic_path)
                if valid_pic(pic_path):
                    logger.debug(f"已下载封面: '{url}'")
                    return (url, pic_path)
                else:
                    logger.debug(f"图片无效或已损坏: '{url}'，尝试更换下载地址")
                    break
            except Exception as e:
                logger.debug(e, exc_info=True)
    logger.error(f"下载封面图片失败")
    logger.debug('big_covers:' + str(big_covers) + ', covers' + str(covers))
    return None


def get_pic_path(fanart_path, url):
    fanart_base = os.path.splitext(fanart_path)[0]
    pic_extend = url.split('.')[-1]
    # 判断 url 是否带？后面的参数
    if '?' in pic_extend:
        pic_extend = pic_extend.split('?')[0]

    pic_path = fanart_base + "." + pic_extend
    return pic_path


# ============================================================================
# 高层封装: 供 Web 后端直接调用(不依赖 tqdm / print / os.chdir)
# ============================================================================

def scrape_movie(avid: str, progress_cb: ProgressCb = None) -> Optional[Movie]:
    """单部影片刮削: 番号 -> 含元数据的 Movie(不落盘)。

    返回 Movie 对象(movie.info 为合并后的元数据); 若抓取失败返回 None。
    """
    avid = (avid or '').strip()
    if not avid:
        return None
    src = guess_av_type(avid)
    if src == 'cid':
        movie = Movie(cid=avid)
        movie.dvdid = get_id(avid)
    else:
        movie = Movie(avid)
    movie.data_src = src
    all_info = parallel_crawler(movie, progress_cb=progress_cb)
    if not all_info:
        return None
    ok = info_summary(movie, all_info)
    if not ok:
        return None
    return movie


def preview_metadata(avid: str, progress_cb: ProgressCb = None) -> Optional[MovieInfo]:
    """仅刮削并返回合并后的元数据(不落盘), 供前端预览"""
    movie = scrape_movie(avid, progress_cb=progress_cb)
    return movie.info if movie else None


def organize_movie(movie: Movie, progress_cb: ProgressCb = None,
                   translate: Optional[bool] = None,
                   move_files: Optional[bool] = None) -> dict:
    """整理(落盘): 生成 NFO + 封面 + 重命名。movie 需已含 files 与 info。

    返回结果字典: {status, steps, save_dir, nfo_file, ...}
    """
    result = {'status': 'ok', 'steps': [], 'errors': []}

    do_translate = Cfg().translator.engine if translate is None else translate
    if do_translate:
        try:
            translate_movie_info(movie.info)
            result['steps'].append('translate')
        except Exception as e:
            logger.warning(f"翻译失败(忽略继续): {e}")
            result['errors'].append(f'translate: {e}')

    generate_names(movie)
    if not movie.save_dir:
        raise RuntimeError('无法按命名规则生成目标文件夹')
    if not os.path.exists(movie.save_dir):
        os.makedirs(movie.save_dir)

    # 下载封面
    if Cfg().summarizer.cover.highres:
        cover_dl = download_cover(movie.info.covers, movie.fanart_file, movie.info.big_covers)
    else:
        cover_dl = download_cover(movie.info.covers, movie.fanart_file)
    if not cover_dl:
        result['status'] = 'cover_failed'
        result['errors'].append('下载封面图片失败')
    else:
        cover, pic_path = cover_dl
        # 确保实际下载的封面的url与即将写入到movie.info中的一致
        if cover != movie.info.cover:
            movie.info.cover = cover
        # 根据实际下载的封面的格式更新fanart/poster等图片的文件名
        if pic_path != movie.fanart_file:
            movie.fanart_file = pic_path
            actual_ext = os.path.splitext(pic_path)[1]
            movie.poster_file = os.path.splitext(movie.poster_file)[0] + actual_ext
    try:
        process_poster(movie)
        result['steps'].append('poster')
    except Exception as e:
        logger.warning(f"裁剪封面失败: {e}")
        result['errors'].append(f'poster: {e}')

    # 剧照(可选)
    if Cfg().summarizer.extra_fanarts.enabled:
        scrape_interval = Cfg().summarizer.extra_fanarts.scrap_interval.total_seconds()
        if movie.info.preview_pics:
            extrafanartdir = movie.save_dir + '/extrafanart'
            os.makedirs(extrafanartdir, exist_ok=True)
            for (pid, pic_url) in enumerate(movie.info.preview_pics):
                fanart_destination = f"{extrafanartdir}/{pid}.png"
                try:
                    download(pic_url, fanart_destination)
                    result['steps'].append(f'extrafanart:{pid}')
                except Exception as e:
                    logger.warning(f"下载剧照失败 {pic_url}: {e}")
                time.sleep(scrape_interval)

    try:
        write_nfo(movie.info, movie.nfo_file)
        result['steps'].append('nfo')
    except Exception as e:
        logger.error(f"写入 NFO 失败: {e}")
        result['status'] = 'nfo_failed'
        result['errors'].append(f'nfo: {e}')

    do_move = Cfg().summarizer.move_files if move_files is None else move_files
    if do_move:
        try:
            movie.rename_files(Cfg().summarizer.path.hard_link)
            result['steps'].append('move')
        except Exception as e:
            logger.error(f"移动影片文件失败: {e}")
            result['status'] = 'move_failed'
            result['errors'].append(f'move: {e}')

    result['save_dir'] = movie.save_dir
    result['nfo_file'] = movie.nfo_file
    result['poster_file'] = getattr(movie, 'poster_file', None)
    result['fanart_file'] = getattr(movie, 'fanart_file', None)
    return result


def scan_library(root: str) -> List[Movie]:
    """扫描目录返回影片列表(供 Web /api/scan 使用)"""
    return scan_movies(root)
