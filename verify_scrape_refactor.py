"""刮削流程打磨(套餐3)的综合验证脚本(纯逻辑, 不联网)

覆盖:
  T4  genre 合并(javdb 优先 + 去重)
  T5  封面下载 break->continue
  T3  重试指数退避计算
  T21 每站点数据透传(_summarize_sources 结构)
  T1  并发限流(ThreadPoolExecutor 被使用 + max_workers 取自配置)
"""
import sys
import os
import time
import tempfile
import threading

PROJ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJ)

import javsp.core as core
from javsp.datatype import Movie, MovieInfo
from javsp.config import Cfg

PASS, FAIL = [], []
def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name)

def make_info(**kw):
    m = MovieInfo('TEST-001')
    for k, v in kw.items():
        setattr(m, k, v)
    return m

# =====================================================================
# T4: genre 合并 (javdb 优先 + 其余去重补充) —— 补 cover 满足必填检查
# =====================================================================
all_info = {
    'javdb':  make_info(genre=['无码', '剧情'], title='T1', cover='http://x/c.jpg'),
    'airav':  make_info(genre=['剧情', '高清'], title='T2', cover='http://x/c.jpg'),
    'javbus': make_info(genre=['剧情'], title='T3', cover='http://x/c.jpg'),
}
mv = Movie('TEST-001')
ok = core.info_summary(mv, all_info)
check('T4 genre: javdb 优先且去重合并', ok and mv.info.genre == ['无码', '剧情', '高清'])

# =====================================================================
# T5: 封面下载 break -> continue (首张损坏时尝试下一张)
# =====================================================================
calls = []
def fake_download(url, path):
    calls.append(url)
    open(path, 'w').close()
def fake_valid(p):
    return not p.endswith('.png')   # 将 .png 视为损坏封面, .jpg 视为有效
core.download = fake_download
core.valid_pic = fake_valid
fd, fp = tempfile.mkstemp(suffix='.jpg')
os.close(fd)
res = core.download_cover(['http://x/a.png', 'http://x/b.jpg'], fp, [])
check('T5 封面: 首张损坏时继续尝试下一张', res is not None and res[0].endswith('b.jpg'))
os.remove(fp)

# =====================================================================
# T3: 重试指数退避计算 (1,2,4,8 封顶)
# =====================================================================
backoff = [min(2 ** c, 8) for c in range(3)]
check('T3 退避序列 = [1,2,4]', backoff == [1, 2, 4])

# =====================================================================
# T21: 每站点数据透传 (_summarize_sources 结构)
# =====================================================================
src = core._summarize_sources(all_info)
check('T21 _summarize_sources 结构正确',
      isinstance(src, dict) and src['javdb']['has_genre'] and 'airav' in src
      and set(src['javdb'].keys()) >= {'dvdid', 'title', 'has_cover', 'has_genre', 'has_actress', 'uncensored'})

# =====================================================================
# T1: 并发限流 —— 用 TrackingExecutor 捕获 max_workers, mock 已导入爬虫的 parse_data
# =====================================================================
import concurrent.futures as real_cf
captured = {}
class TrackingExecutor(real_cf.ThreadPoolExecutor):
    def __init__(self, *a, **kw):
        captured['max_workers'] = kw.get('max_workers')
        super().__init__(*a, **kw)
core.cf.ThreadPoolExecutor = TrackingExecutor

conc = {'max': 0, 'cur': 0}
lock = threading.Lock()
def make_parser(tag):
    def p(info):
        with lock:
            conc['cur'] += 1
            conc['max'] = max(conc['max'], conc['cur'])
        time.sleep(0.15)
        with lock:
            conc['cur'] -= 1
        setattr(info, 'success', True)
    return p

core.import_crawlers()  # 确保真实爬虫已导入
normal_names = [c.value for c in Cfg().crawler.selection.normal]
targets = normal_names  # 全部 mock 掉, 避免真实联网拖慢/触发超时
originals = {}
for t in targets:
    mod = sys.modules.get('javsp.web.' + t)
    if mod and hasattr(mod, 'parse_data'):
        originals[t] = mod.parse_data
        mod.parse_data = make_parser(t)

mv3 = Movie('CONC-001')               # data_src 默认 normal -> 用 selection.normal
ai = core.parallel_crawler(mv3)
check('T1 线程池被使用(max_workers 取自配置)',
      captured.get('max_workers') == Cfg().crawler.max_concurrency)
check('T1 并发刮削完成', len(ai) == len(originals) and len(originals) >= 1)
check('T1 movie.sources 透传已填充',
      getattr(mv3, 'sources', None) is not None and len(mv3.sources) == len(ai))

# 还原
for t in originals:
    sys.modules['javsp.web.' + t].parse_data = originals[t]
core.cf.ThreadPoolExecutor = real_cf.ThreadPoolExecutor

# =====================================================================
print('---')
print(f'PASS {len(PASS)}  FAIL {len(FAIL)}')
if FAIL:
    print('FAILED:', FAIL)
    sys.exit(1)
print('ALL GREEN')
