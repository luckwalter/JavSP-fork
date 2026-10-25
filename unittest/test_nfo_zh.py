"""NFO 中文字体一致性测试

背景(v0.3.16): 翻译开启后制作的 NFO 里 genre 仍简繁混用 —— title/plot 走 LLM 翻译
但 genre 从未走翻译, 它来自 javbus/javdb 的 genre 映射表(translate 列上游即简繁混杂)
与站点原文标签, 多源合并后同一份 NFO 里出现 '出轨'(简) 与 '出軌'(繁) 并存。

修复三层: javsp/zh.py 内置零依赖繁简归一化 + write_nfo 按目标语种原地归一化
+ 4 个映射表 translate 列统一简体。本测试覆盖前两层(代码路径)。
"""
import os
import sys
import re
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from confz import FileSource
from javsp.config import Cfg
from javsp.datatype import MovieInfo
from javsp.nfo import write_nfo

_CONFIG = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'config.yml'))
Cfg.CONFIG_SOURCES = [FileSource(_CONFIG)]

# 繁体字残留检测用的样本字(覆盖实测出现过的几个)
_TRAD_CHARS = '獨畫條軌語體齣'


def _build_info():
    """构造 HOMA-170 实测数据: genre_norm(javbus 简体映射值) + genre(另一源繁体/日文原文)"""
    info = MovieInfo('HOMA-170')
    info.cid = 'homa00170'
    info.title = 'HOMA-170 目标是让对方彻底崩溃的乱伦，让已婚男性陷入疯狂的派遣女职员。'
    info.ori_title = '狙った男は絶対に沼らせる不倫中出しで既婚男性を狂わせる派遣OL'
    info.plot = '测试剧情简介。'
    info.score = '8.72'
    info.duration = '145'
    info.genre_norm = ['中出', '苗条', '出轨', '单体作品', '淫语', '白天出轨', '通奸']
    info.genre = ['中出', 'DMM獨家', '高畫質', '苗條', '出軌', '單體作品', '淫語',
                  '出轨', '淫语', '白天出轨', '苗條', '通姦', '单体作品', '白天出軌', '无码']
    info.actress = ['北野未奈']
    info.producer = 'h.m.p DORAMA'
    info.publish_date = '2026-10-24'
    info.preview_video = 'https://example.com/pv.mp4'
    return info


def _write_and_read(info):
    nfo_file = os.path.join(tempfile.mkdtemp(prefix='nfo_zh_test_'), 'movie.nfo')
    write_nfo(info, nfo_file)
    with open(nfo_file, encoding='utf-8') as f:
        return f.read()


def test_nfo_genre_simplified():
    """目标语种为简体中文时, genre 节点不得残留繁体字"""
    content = _write_and_read(_build_info())
    genres = re.findall(r'<genre>(.*?)</genre>', content)
    assert genres, '未生成 genre 节点'
    bad = [g for g in genres if any(c in g for c in _TRAD_CHARS)]
    assert not bad, f'仍有繁体残留: {bad}'


def test_nfo_genre_deduplicated():
    """归一化后简繁同义项应收敛为一条, 不再成对出现"""
    content = _write_and_read(_build_info())
    genres = re.findall(r'<genre>(.*?)</genre>', content)
    assert '苗条' in genres and '苗條' not in genres
    assert '出轨' in genres and '出軌' not in genres
    assert '单体作品' in genres and '單體作品' not in genres
    # 拼接串(自定义字段 {genre})也应已归一化
    assert '單體作品' not in content and 'DMM獨家' not in content


def test_nfo_traditional_target(tmp_path):
    """目标语种为繁体中文时, genre 应统一为繁体

    confz 的配置模型是 frozen 的(不可直接赋值), 且 Cfg() 会缓存实例
    (Cfg.confz_instance), 故用官方 SourceChangeManager 临时切换配置源。
    """
    import yaml
    from confz.change import SourceChangeManager
    with open(_CONFIG, encoding='utf-8-sig') as f:
        cfg = yaml.safe_load(f)
    cfg.setdefault('translator', {})['language'] = 'zh_TW'
    override = tmp_path / 'cfg_zh_tw.yml'
    override.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding='utf-8')

    with SourceChangeManager(Cfg, config_sources=[FileSource(str(override))]):
        content = _write_and_read(_build_info())
    genres = re.findall(r'<genre>(.*?)</genre>', content)
    assert '單體作品' in genres, f'繁体目标下未转繁体: {genres}'
    assert '单体作品' not in genres


def test_nfo_title_not_broken_by_normalize():
    """归一化只作用于 genre, 标题/原文标题不受影响"""
    content = _write_and_read(_build_info())
    assert '狙った男は絶対に沼らせる' in content
    assert '目标是让对方彻底崩溃' in content
