import os
import re
import sys
import json
import time
import logging
from PIL import Image
from pydantic import ValidationError
from pydantic_extra_types.pendulum_dt import Duration
import requests
import threading
from typing import Dict, List

sys.stdout.reconfigure(encoding='utf-8')

import colorama
import pretty_errors
from colorama import Fore, Style
from tqdm import tqdm


pretty_errors.configure(display_link=True)


from javsp.print import TqdmOut
from javsp.cropper import Cropper, get_cropper

# 将StreamHandler的stream修改为TqdmOut，以与Tqdm协同工作
root_logger = logging.getLogger()
for handler in root_logger.handlers:
    if type(handler) == logging.StreamHandler:
        handler.stream = TqdmOut

logger = logging.getLogger('main')


from javsp.lib import resource_path
from javsp.nfo import write_nfo
from javsp.file import *
from javsp.func import *
from javsp.image import *
from javsp.datatype import Movie, MovieInfo
from javsp.web.base import download
from javsp.web.exceptions import *
from javsp.web.translate import translate_movie_info

from javsp.config import Cfg, CrawlerID
from javsp.prompt import prompt

# 核心业务逻辑(与 CLI 解耦, Web 后端与命令行共用)
from javsp.core import (
    import_crawlers, parallel_crawler, info_summary,
    generate_names, process_poster, download_cover, load_alias_map,
    output_enabled,
)


def reviewMovieID(all_movies, root):
    """人工检查每一部影片的番号"""
    count = len(all_movies)
    logger.info('进入手动模式检查番号: ')
    for i, movie in enumerate(all_movies, start=1):
        id = repr(movie)[7:-2]
        print(f'[{i}/{count}]\t{Fore.LIGHTMAGENTA_EX}{id}{Style.RESET_ALL}, 对应文件:')
        relpaths = [os.path.relpath(i, root) for i in movie.files]
        print('\n'.join(['  '+i for i in relpaths]))
        s = prompt("回车确认当前番号，或直接输入更正后的番号（如'ABC-123'或'cid:sqte00300'）", "更正后的番号")
        if not s:
            logger.info(f"已确认影片番号: {','.join(relpaths)}: {id}")
        else:
            s = s.strip()
            s_lc = s.lower()
            if s_lc.startswith(('cid:', 'cid=')):
                new_movie = Movie(cid=s_lc[4:])
                new_movie.data_src = 'cid'
                new_movie.files = movie.files
            elif s_lc.startswith('fc2'):
                new_movie = Movie(s)
                new_movie.data_src = 'fc2'
                new_movie.files = movie.files
            else:
                new_movie = Movie(s)
                new_movie.data_src = 'normal'
                new_movie.files = movie.files
            all_movies[i-1] = new_movie
            new_id = repr(new_movie)[7:-2]
            logger.info(f"已更正影片番号: {','.join(relpaths)}: {id} -> {new_id}")
        print()


def RunNormalMode(all_movies):
    """普通整理模式"""
    def check_step(result, msg='步骤错误'):
        """检查一个整理步骤的结果，并负责更新tqdm的进度"""
        if result:
            inner_bar.update()
        else:
            raise Exception(msg + '\n')

    outer_bar = tqdm(all_movies, desc='整理影片', ascii=True, leave=False)
    # 步骤数必须与下面实际执行的 check_step 次数一致，否则进度条走不满或溢出
    total_step = 3                      # 抓取 / 汇总 / 生成目标文件夹
    if Cfg().translator.engine:
        total_step += 1
    _need_cover = output_enabled('poster') or output_enabled('fanart')
    if _need_cover:
        total_step += 1                 # 下载封面
    if output_enabled('poster'):
        total_step += 1                 # 由封面裁剪出 poster
    if output_enabled('extrafanart'):
        total_step += 1
    if output_enabled('nfo'):
        total_step += 1

    return_movies = []
    for movie in outer_bar:
        try:
            # 初始化本次循环要整理影片任务
            filenames = [os.path.split(i)[1] for i in movie.files]
            logger.info('正在整理: ' + ', '.join(filenames))
            inner_bar = tqdm(total=total_step, desc='步骤', ascii=True, leave=False)
            # 依次执行各个步骤
            inner_bar.set_description(f'启动并发任务')
            all_info = parallel_crawler(movie, inner_bar)
            msg = f'为其配置的{len(Cfg().crawler.selection[movie.data_src])}个抓取器均未获取到影片信息'
            check_step(all_info, msg)

            inner_bar.set_description('汇总数据')
            has_required_keys = info_summary(movie, all_info)
            check_step(has_required_keys)

            if Cfg().translator.engine:
                inner_bar.set_description('翻译影片信息')
                success = translate_movie_info(movie.info)
                check_step(success)

            generate_names(movie)
            check_step(movie.save_dir, '无法按命名规则生成目标文件夹')
            if not os.path.exists(movie.save_dir):
                os.makedirs(movie.save_dir)

            cover_dl = None
            if _need_cover:
                inner_bar.set_description('下载封面图片')
                if Cfg().summarizer.cover.highres:
                    cover_dl = download_cover(movie.info.covers, movie.fanart_file, movie.info.big_covers)
                else:
                    cover_dl = download_cover(movie.info.covers, movie.fanart_file)
                check_step(cover_dl, '下载封面图片失败')
                cover, pic_path = cover_dl
                # 确保实际下载的封面的url与即将写入到movie.info中的一致
                if cover != movie.info.cover:
                    movie.info.cover = cover
                # 根据实际下载的封面的格式更新fanart/poster等图片的文件名
                if pic_path != movie.fanart_file:
                    movie.fanart_file = pic_path
                    actual_ext = os.path.splitext(pic_path)[1]
                    movie.poster_file = os.path.splitext(movie.poster_file)[0] + actual_ext

                if output_enabled('poster'):
                    process_poster(movie)
                    check_step(True)

                # 不需要保留横版封面原图时，poster 生成完毕即可删除
                if not output_enabled('fanart'):
                    if movie.fanart_file and os.path.exists(movie.fanart_file):
                        os.remove(movie.fanart_file)
                    movie.fanart_file = None

            if output_enabled('extrafanart'):
                scrape_interval = Cfg().summarizer.extra_fanarts.scrap_interval.total_seconds()
                inner_bar.set_description('下载剧照')
                if movie.info.preview_pics:
                    extrafanartdir = movie.save_dir + '/extrafanart'
                    os.mkdir(extrafanartdir)
                    for (id, pic_url) in enumerate(movie.info.preview_pics):
                        inner_bar.set_description(f"Downloading extrafanart {id} from url: {pic_url}")
                        fanart_destination = f"{extrafanartdir}/{id}.png"
                        try:
                            info = download(pic_url, fanart_destination)
                            if valid_pic(fanart_destination):
                                # 注意: 这里要显示的是剧照自身的大小, 不能沿用封面图的 pic_path
                                filesize = get_fmt_size(fanart_destination)
                                width, height = get_pic_size(fanart_destination)
                                elapsed = time.strftime("%M:%S", time.gmtime(info['elapsed']))
                                speed = get_fmt_size(info['rate']) + '/s'
                                logger.info(f"已下载剧照{pic_url} {id}.png: {width}x{height}, {filesize} [{elapsed}, {speed}]")
                            else:
                                check_step(False, f"下载剧照{id}: {pic_url}失败")
                        except:
                            check_step(False, f"下载剧照{id}: {pic_url}失败")
                        time.sleep(scrape_interval)
                check_step(True)

            if output_enabled('nfo'):
                inner_bar.set_description('写入NFO')
                write_nfo(movie.info, movie.nfo_file)
                check_step(True)
            if Cfg().summarizer.move_files:
                inner_bar.set_description('移动影片文件')
                movie.rename_files(Cfg().summarizer.path.hard_link)
                check_step(True)
                logger.info(f'整理完成，相关文件已保存到: {movie.save_dir}\n')
            else:
                saved_to = movie.nfo_file if output_enabled('nfo') else movie.save_dir
                logger.info(f'刮削完成，相关文件已保存到: {saved_to}\n')

            if movie != all_movies[-1] and Cfg().crawler.sleep_after_scraping > Duration(0):
                time.sleep(Cfg().crawler.sleep_after_scraping.total_seconds())
            return_movies.append(movie)
        # except Exception as e:
        #     logger.debug(e, exc_info=True)
        #     logger.error(f'整理失败: {e}')
        finally:
            inner_bar.close()
    return return_movies


def error_exit(success, err_info):
    """检查业务逻辑是否成功完成，如果失败则报错退出程序"""
    if not success:
        logger.error(err_info)
        sys.exit(1)


def entry():
    try:
        Cfg()
    except ValidationError as e:
        print(e.errors())
        exit(1)

    # 加载女优别名表(替代原内联加载)
    load_alias_map()

    colorama.init(autoreset=True)

    # 检查更新
    version_info = 'JavSP ' + getattr(sys, 'javsp_version', '未知版本/从代码运行')
    logger.debug(version_info.center(60, '='))
    check_update(Cfg().other.check_update, Cfg().other.auto_update)
    root = get_scan_dir(Cfg().scanner.input_directory)
    error_exit(root, '未选择要扫描的文件夹')
    # 导入抓取器，必须在chdir之前
    import_crawlers()
    os.chdir(root)

    print(f'扫描影片文件...')
    recognized = scan_movies(root)
    movie_count = len(recognized)
    recognize_fail = []
    error_exit(movie_count, '未找到影片文件')
    logger.info(f'扫描影片文件：共找到 {movie_count} 部影片')
    if Cfg().scanner.manual:
        reviewMovieID(recognized, root)
    RunNormalMode(recognized + recognize_fail)

    sys.exit(0)


if __name__ == "__main__":
    entry()
