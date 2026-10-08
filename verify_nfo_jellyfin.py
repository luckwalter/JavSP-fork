# -*- coding: utf-8 -*-
"""验证本项目生成的 NFO 与 Jellyfin 的 NFO 读取是否一致

为什么要单独写这个脚本
----------------------
JavSP 生成的 NFO 最终要交给 Jellyfin（或 Kodi）读取。两者不一致时症状很隐蔽：
文件照样生成、Jellyfin 照样能刮削成功，但某些字段悄悄丢失或数值失真，用户
很难反查到是 NFO 这一环出的问题。

本脚本的判据**不是**来自网络上的 NFO 教程，而是直接对 Jellyfin 程序集与其源码取证：

1. 本机 `C:\\Program Files\\Jellyfin\\Server\\jellyfin.dll` → ProductVersion = 10.10.7
2. 同目录 `MediaBrowser.XbmcMetadata.dll` 的 UTF-16 字符串表 → NFO 标签常量清单
3. GitHub 源码 v10.10.7 / v12.2 的 `MediaBrowser.XbmcMetadata/Parsers/BaseNfoParser.cs`
   （`FetchDataFromXmlNode` 的 switch-case）→ 权威标签清单与字段语义

两份证据互相印证，覆盖 Hopefully 后面不再因版本升级而漂移。

踩过的坑 / 已修复的真实缺陷
---------------------------
* **评分量纲**：Jellyfin 解析 `<rating>` 时只做一次 `float.TryParse`，**没有范围校验**
  （12.2 才给新标签 `<communityrating>` 加了 0~10 校验）。而 `javsp/web/fanza.py`
  有一条分支把星星图片文件名（00/05/.../50，5 分制的十倍值）**原样赋给了 score**，
  实测存档数据里真的出现了 score = 45（int）。在 Jellyfin 界面上就是「45 分」。
  已修：源头换算到 10 分制，并在 NFO 写入层加边界兜底（越界钳制 + warning）。
* **别凭印象判断站点的评分量纲**：一度以为 javlib 也是 5 分制要换算，核对存档数据后
  发现它抓下来就是 8.20/8.70 这类 10 分制数值，改了反而会错。凡是「要不要换算」，
  一律回到真实数据求证。
"""
import os
import re
import sys
import logging
import tempfile
from xml.etree import ElementTree

PROJ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJ)
os.chdir(PROJ)          # Cfg 需要能找到 config.yml

# Jellyfin 安装的 NFO 解析器所在目录（存在则顺带核对版本，缺失不影响其余断言）
JFDIR = r"C:\Program Files\Jellyfin\Server"

from javsp.config import Cfg
from javsp.nfo import write_nfo, _normalize_rating
from javsp.datatype import MovieInfo

PASS, FAIL = [], []


def check(name, cond, extra=''):
    if cond:
        PASS.append(name)
        print(f'  OK   {name}')
    else:
        FAIL.append(name)
        print(f'  FAIL {name}' + (f'  -> {extra}' if extra else ''))


# ---------------------------------------------------------------------------
# 取证所得：Jellyfin 读取 NFO 时支持的标签
# ---------------------------------------------------------------------------
# 来源：jellyfin v12.2 MediaBrowser.XbmcMetadata/Parsers/BaseNfoParser.cs
#       中 FetchDataFromXmlNode 的 switch-case 全部 case 值
#       （叠加 v10.10.7 的同名列表；12.2 仅多出 communityrating）
JELLYFIN_BASE_TAGS = {
    'dateadded', 'originaltitle', 'name', 'title', 'localtitle', 'sortname',
    'criticrating', 'sorttitle', 'biography', 'plot', 'review', 'language',
    'watched', 'playcount', 'lastplayed', 'countrycode', 'lockedfields',
    'tagline', 'country', 'mpaa', 'customrating', 'runtime', 'aspectratio',
    'lockdata', 'studio', 'director', 'credits', 'writer', 'actor', 'trailer',
    'displayorder', 'year', 'rating', 'ratings', 'communityrating',
    'aired', 'formed', 'premiered', 'releasedate', 'enddate', 'genre', 'style',
    'tag', 'fileinfo', 'uniqueid', 'thumb', 'fanart', 'streamdetails', 'video',
    'subtitle', 'format3d', 'aspect', 'width', 'height', 'durationinseconds',
    'value',
}
# MovieNfoParser('id'/'set'/'artist'/'album') 与 SeriesNfoParser 等补充的标签
JELLYFIN_MOVIE_TAGS = {'id', 'set', 'artist', 'album'}
JELLYFIN_TAGS = JELLYFIN_BASE_TAGS | JELLYFIN_MOVIE_TAGS

print('--- T1: 生成的 NFO 里每个标签都必须被 Jellyfin 识别 ---')
info = MovieInfo(from_file='unittest/data/IPX-177 (javdb).json')
info.nfo_title = None
tmpdir = tempfile.mkdtemp()
nfopath = os.path.join(tmpdir, 'movie.nfo')
write_nfo(info, nfopath)
tree = ElementTree.parse(nfopath)
root = tree.getroot()

check('T1.1 根元素为 movie', root.tag == 'movie', root.tag)
used_tags = {el.tag for el in root.iter() if el.tag != root.tag}
unknown = sorted(used_tags - JELLYFIN_TAGS)
check('T1.2 所有标签都在 Jellyfin 支持清单内', not unknown, f'未识别: {unknown}')
check('T1.3 实际写入了若干已识别标签', len(used_tags) > 5, str(sorted(used_tags)))

print('--- T2: 结构需与 Jellyfin 自己的 MovieNfoSaver 写法一致 ---')
# MovieNfoSaver.WriteCustomElements: <set><name>CollectionName</name></set>
set_node = root.find('set')
check('T2.1 set 节点存在', set_node is not None)
if set_node is not None:
    check('T2.2 set 内含 name 子节点', set_node.find('name') is not None,
          ElementTree.tostring(set_node, encoding='unicode'))
# XmlReaderExtensions.GetPersonFromXmlNode: <name>/<role>/<type>/<order>/<thumb>
actors = root.findall('actor')
check('T2.3 至少写入一个 actor', len(actors) > 0, str(len(actors)))
if actors:
    check('T2.4 actor 含 name 子节点', all(a.find('name') is not None for a in actors))
# Kodi/Jellyfin 的 uniqueid 靠 type 属性区分来源；范围内的 type 都收，default 属性会被忽略
uids = root.findall('uniqueid')
check('T2.5 uniqueid 带 type 属性', len(uids) > 0 and all('type' in u.attrib for u in uids),
      str([u.attrib for u in uids]))

print('--- T3: 日期与时长必须能被 Jellyfin 精确解析 ---')
# aired/formed/premiered/releasedate 共用 nfoConfiguration.ReleaseDateFormat 做 Exact 解析
prem = root.find('premiered')
if prem is not None and prem.text:
    check('T3.1 premiered 为 yyyy-MM-dd',
          bool(re.fullmatch(r'\d{4}-\d{2}-\d{2}', prem.text.strip())), prem.text)
# runtime: int.TryParse(runtimeText.LeftPart(' ')) 后按分钟换算
rt = root.find('runtime')
if rt is not None and rt.text:
    left = rt.text.split(' ')[0]
    check('T3.2 runtime 空格前的部分是整数(分钟)', left.isdigit(), rt.text)

print('--- T4: 评分必须落在 Jellyfin 的 0~10 区间 ---')
cases = [
    ('T4.1 合法评分原样保留', _normalize_rating('9.38') == '9.38'),
    ('T4.2 整数 10 分制值正常', _normalize_rating('10') == '10.00'),
    ('T4.3 零分不丢失', _normalize_rating('0') == '0.00'),
    ('T4.4 int 类型的 10 分制值被规范化', _normalize_rating(4) == '4.00'),
    ('T4.5 越界高值被钳制到 10', _normalize_rating(45) == '10.00'),
    ('T4.6 负值被钳制到 0', _normalize_rating(-1) == '0.00'),
    ('T4.7 非数字返回 None(不写入该字段)', _normalize_rating('abc') is None),
    ('T4.8 返回类型统一为字符串', isinstance(_normalize_rating(7.5), str)),
]
for name, cond in cases:
    check(name, cond)

print('--- T5: 历史存档数据经规范化后不得越界 ---')
bad, count = [], 0
for f in sorted(os.listdir('unittest/data')):
    if not f.endswith('.json'):
        continue
    try:
        mi = MovieInfo(from_file=os.path.join('unittest/data', f))
    except Exception:
        continue
    if mi.score is None:
        continue
    count += 1
    v = _normalize_rating(mi.score)
    if v is None or not (0.0 <= float(v) <= 10.0):
        bad.append((f, mi.score, v))
check('T5.1 存档样本全部落在 0~10 区间', not bad, str(bad))
check('T5.2 样本数量足够有代表性', count >= 10, str(count))

print('--- T6: 越界评分端到端写 NFO 时会被兜底 ---')
info45 = MovieInfo(from_file='unittest/data/1stars931r (fanza).json')
info45.nfo_title = None
p45 = os.path.join(tmpdir, 'bad.nfo')
logs = []
_handler = logging.Handler()
_handler.emit = lambda rec: logs.append(rec.getMessage())
logging.getLogger('javsp.nfo').addHandler(_handler)
logging.getLogger('javsp.nfo').setLevel(logging.WARNING)
write_nfo(info45, p45)
logging.getLogger('javsp.nfo').removeHandler(_handler)
raw_score = info45.score
r45 = ElementTree.parse(p45).getroot().find('rating')
check('T6.1 该样本原评分确实是越界值', isinstance(raw_score, (int, float)) and float(raw_score) > 10,
      str(raw_score))
check('T6.2 写入 NFO 的评分已被钳制',
      r45 is not None and 0.0 <= float(r45.text) <= 10.0, r45.text if r45 is not None else '缺失')
check('T6.3 越界时留有日志线索', any('评分' in m for m in logs), str(logs))

print('--- T7: 源码层面守住「含‘评分’的站点爬虫必须做量纲换算」 ---')
# fanza 曾有一条分支打出 int 原始值。这里用静态扫描防止同类写法复活：
# 任何 .score 的赋值都不能是裸 int(...)（即未换算、且违反「以字符串表示」的约定）
src = open('javsp/web/fanza.py', encoding='utf-8').read()
raw_int_assign = re.findall(r'movie\.score\s*=\s*int\(', src)
check('T7.1 fanza 不再直接赋 int 原始值', not raw_int_assign, str(raw_int_assign))
# 星星图分支(00/05/.../50)必须存在除以 5 的换算
check('T7.2 fanza 星星图分支有 /5 换算', '/ 5' in src or '/5' in src)
# 打分区分支必须存在 ×2 的换算
check('T7.3 fanza 打分区分支有 *2 换算', '* 2' in src or '*2' in src)
# 浮点提取不能再用 \d+ 丢掉小数
check('T7.4 fanza 评分正则不再截断小数', r"r'[\d.]+'" in src, re.findall(r"re\.search\(r'[^']+'", src)[:3])

print('--- T8: NFO 文件名需落在 Jellyfin 的查找规则内 ---')
# MovieNfoSaver.GetMovieSavePaths: 非混合文件夹且 ItemType==Movie 时找 <目录>/movie.nfo
basename_pattern = Cfg().summarizer.nfo.basename_pattern
check('T8.1 默认 NFO 名为 movie.nfo', basename_pattern == 'movie', basename_pattern)
# 每部片独立目录时才安全：output_folder_pattern 必须包含 {num} 以区分不同影片
folder_pattern = Cfg().summarizer.path.output_folder_pattern
check('T8.2 输出目录模板含 {num}(避免 movie.nfo 相互覆盖)',
      '{num}' in folder_pattern, folder_pattern)

print('--- T9: 本机 Jellyfin 版本（存在则核对，缺失不视为失败）---')
dll = os.path.join(JFDIR, 'jellyfin.dll')
if os.path.exists(dll):
    raw = open(dll, 'rb').read().decode('utf-16-le', errors='ignore')
    m = re.search(r'ProductVersion\x00(\d+\.\d+\.\d+)', raw)
    ver = m.group(1) if m else '?'
    print(f'   INFO 本机 Jellyfin 版本: {ver}')
    check('T9.1 能读出本机 Jellyfin 版本', ver != '?')
    xbmc = os.path.join(JFDIR, 'MediaBrowser.XbmcMetadata.dll')
    if os.path.exists(xbmc):
        xraw = open(xbmc, 'rb').read()
        hit = 'uniqueid'.encode('utf-16-le') in xraw
        check('T9.2 二进制里确实存在 uniqueid 标签(证明该项被支持)', hit)
else:
    print('   INFO 未检测到本机 Jellyfin 安装，跳过版本核对')

print('---')
print(f'PASS {len(PASS)}  FAIL {len(FAIL)}')
if FAIL:
    print('FAILED:')
    for n in FAIL:
        print('  - ' + n)
else:
    print('ALL GREEN')
