"""批量刮削 + 整理 端到端验证（合成片源，屏蔽真实网络）

沙箱没有真实片源，这里用「合成大文件 + mock 爬虫/下载」把 /api/batch 全链路跑通，
真机联调时只剩「真实联网抓取」这一环待验。

注：合成文件各 233MiB（需 >= scanner.minimum_size 232MiB 才会被扫描识别），
跑完自动清理临时目录，不会在仓库留下垃圾。

覆盖:
  T1 /api/scan 识别番号并分配 guid
  T2 save_dir 锚定到扫描根目录（而非服务进程 CWD）—— 回归守卫
  T3 /api/batch 的 SSE 事件序列完整（movie_start / movie_done / all_done）
  T4 成功 / 失败分别计数，单部失败不中断整批
  T5 整理产物落盘：NFO 位于 <扫描根>/#整理完成/...
  T6 服务进程 CWD 未被污染出 #整理完成
"""
import os
import sys
import json
import shutil
import tempfile

PROJ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJ)
os.chdir(PROJ)          # Cfg 需能找到 config.yml；此目录同时充当「服务进程 CWD」的模拟对象

import javsp.core as core
import javsp.server as server
from javsp.config import Cfg
from javsp.datatype import Movie, MovieInfo

PASS, FAIL = [], []


def check(name, cond, extra=''):
    (PASS if cond else FAIL).append(name)
    print(('PASS ' if cond else 'FAIL ') + name + (('  | ' + extra) if extra else ''))


SIZE = 233 * 1024 * 1024          # 需 >= scanner.minimum_size(232MiB)

base = tempfile.mkdtemp(prefix='javsp_e2e_')
src = os.path.join(base, '影片源')
os.makedirs(src)
for n in ('ABP-123.mp4', 'SSIS-456.mp4'):
    with open(os.path.join(src, n), 'wb') as f:
        f.write(b'\0' * SIZE)
print(f'合成片源就绪: {src}（2 部，各 233MiB）')

# ---------------- mock: 爬虫（只让 ABP-123 成功，SSIS-456 模拟站点未收录） ----------------
core.import_crawlers()
patched = {}
for cid in Cfg().crawler.selection.normal:
    mod = sys.modules.get(f'javsp.web.{cid.value}')
    if not mod or not hasattr(mod, 'parse_data'):
        continue

    def make():
        def p(info):
            if getattr(info, 'dvdid', None) != 'ABP-123':
                return                       # 模拟未收录 -> 该部应计为失败
            info.title = '测试标题'
            info.actress = ['测试女优']
            info.genre = ['剧情']
            info.cover = 'http://example.com/cover.jpg'
            info.covers = ['http://example.com/cover.jpg']
            info.big_covers = []
            info.preview_pics = []
        return p

    patched[cid.value] = mod.parse_data
    mod.parse_data = make()
print(f'已 mock {len(patched)} 个爬虫的 parse_data')

# ---------------- mock: 整理阶段的联网 / 重活 ----------------
def fake_download_cover(covers, fanart_path, big_covers=[]):
    os.makedirs(os.path.dirname(fanart_path), exist_ok=True)
    with open(fanart_path, 'wb') as f:
        f.write(b'\xff\xd8\xff\xe0' + b'\0' * 512)      # 占位 JPEG，本用例不校验内容
    return ('http://example.com/cover.jpg', fanart_path)


core.download_cover = fake_download_cover
core.process_poster = lambda movie: None
core.translate_movie_info = lambda info: None

try:
    from fastapi.testclient import TestClient
    client = TestClient(server.app)

    # ---------------- T1: 扫描 ----------------
    scan = client.post('/api/scan', json={'path': src}).json()
    check('T1 /api/scan 识别出 2 部合成片源', scan.get('count') == 2, f"count={scan.get('count')}")
    guid_map = {m['dvdid']: m['guid'] for m in scan.get('movies', [])}
    check('T1 番号识别正确', set(guid_map) == {'ABP-123', 'SSIS-456'}, str(sorted(guid_map)))

    # ---------------- T2: save_dir 锚定到扫描根目录 ----------------
    probe = Movie('ABP-123')
    probe.files = [os.path.join(src, 'ABP-123.mp4')]
    probe.scan_root = os.path.abspath(src)
    pinfo = MovieInfo('ABP-123')
    pinfo.title = '测试标题'
    pinfo.actress = ['测试女优']
    probe.info = pinfo
    core.generate_names(probe)
    abs_save = os.path.abspath(probe.save_dir)
    check('T2 save_dir 锚定到扫描根目录（非进程 CWD）',
          abs_save.startswith(os.path.abspath(src)), abs_save)

    # ---------------- T3/T4: 批量端点 SSE ----------------
    guids = [guid_map['ABP-123'], guid_map['SSIS-456']]
    resp = client.post('/api/batch', json={'guids': guids, 'organize': True})
    events = []
    for block in resp.text.split('\n\n'):
        line = block.strip()
        if line.startswith('data: '):
            try:
                events.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                pass
    types = [e.get('type') for e in events]
    check('T3 SSE 含 movie_start ×2', types.count('movie_start') == 2, str(types))
    check('T3 SSE 含 movie_done ×2', types.count('movie_done') == 2)
    check('T3 SSE 以 all_done 收尾', types.count('all_done') == 1)

    done = next((e for e in events if e.get('type') == 'all_done'), {})
    check('T4 成功 1 / 失败 1（失败不中断整批）',
          done.get('success') == 1 and done.get('fail') == 1, str(done))
    ok_ev = [e for e in events if e.get('type') == 'movie_done' and e.get('ok')]
    check('T4 成功部已整理(organized=True)',
          len(ok_ev) == 1 and ok_ev[0].get('organized') is True, str(ok_ev))

    # ---------------- T5: 整理产物落盘位置 ----------------
    out_root = os.path.join(src, '#整理完成')
    nfo = []
    for dp, _dn, fn in os.walk(out_root):
        nfo += [os.path.join(dp, f) for f in fn if f.endswith('.nfo')]
    check('T5 NFO 落在 <扫描根>/#整理完成/', len(nfo) >= 1, nfo[0] if nfo else '未找到')
    with open(nfo[0], 'r', encoding='utf-8') as f:
        nfo_txt = f.read()
    check('T5 NFO 含标题', '测试标题' in nfo_txt)

    # ---------------- T6: 服务进程 CWD 未被污染 ----------------
    stray = os.path.join(PROJ, '#整理完成')
    polluted = os.path.exists(stray)
    check('T6 服务进程 CWD 未被污染出 #整理完成', not polluted)
    if polluted:                       # 万一回归，立刻清掉，不留垃圾在仓库
        shutil.rmtree(stray, ignore_errors=True)

finally:
    for cid, orig in patched.items():
        sys.modules[f'javsp.web.{cid}'].parse_data = orig
    shutil.rmtree(base, ignore_errors=True)
    print('临时片源已清理')

print('---')
print(f'PASS {len(PASS)}  FAIL {len(FAIL)}')
if FAIL:
    print('FAILED:', FAIL)
    sys.exit(1)
print('ALL GREEN')
