"""同语言跳过翻译测试(v0.3.16)

需求: 原文语种与所选输出语种相同时不做翻译。
实现: javsp/web/translate.py 的 _detect_source_lang(字符集判定, 零依赖) +
_SAME_LANG_SKIP(目标语种 -> 视为同语言的源语族)。中文/日文可可靠判定故启用;
英/法/德/西属拉丁语族, 零依赖无法细分故不启用(误跳的代价高于多打一次引擎调用)。
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import javsp.web.translate as tr
from javsp.web.translate import _detect_source_lang, _should_skip_translation, translate
from javsp.datatype import MovieInfo


def test_detect_source_lang():
    assert _detect_source_lang('狙った男は絶対に沼らせる') == 'ja'
    assert _detect_source_lang('HOMA-170 目标是让对方彻底崩溃的乱伦') == 'zh'
    assert _detect_source_lang('單體作品女優') == 'zh'
    assert _detect_source_lang('Sultry Stepsister') == 'latin'
    assert _detect_source_lang('HOMA-170') == 'latin'
    assert _detect_source_lang('') == ''
    assert _detect_source_lang(None) == ''


def test_should_skip_matrix():
    assert _should_skip_translation('中文标题', 'zh_CN') is True
    assert _should_skip_translation('繁體標題', 'zh_CN') is True
    assert _should_skip_translation('繁體標題', 'zh_TW') is True
    assert _should_skip_translation('狙った男は', 'ja') is True
    assert _should_skip_translation('狙った男は', 'zh_CN') is False
    assert _should_skip_translation('Sultry Stepsister', 'zh_CN') is False
    assert _should_skip_translation('中文标题', 'en') is False
    assert _should_skip_translation('中文标题', 'ja') is False
    assert _should_skip_translation('', 'zh_CN') is True


def test_translate_skips_engine_for_same_language():
    """同语言时 translate() 不应调用引擎"""
    calls = {'n': 0}

    class FakeEngine:
        name = 'openai'
        url = 'http://127.0.0.1:11434/v1/chat/completions'
        api_key = ''
        model = 'fake'
        use_proxy = False

    def fake_openai(texts, url, api_key, model, use_proxy=False, to='zh_CN'):
        calls['n'] += 1
        return '译文'

    orig = tr.openai_translate
    tr.openai_translate = fake_openai
    try:
        r = translate('中文标题测试', FakeEngine(), to='zh_CN')
        assert r['trans'] == '中文标题测试'
        assert calls['n'] == 0, '同语言不应调用引擎'
        r = translate('狙った男は絶対に', FakeEngine(), to='zh_CN')
        assert r['trans'] == '译文'
        assert calls['n'] == 1, '异语言应调用引擎'
    finally:
        tr.openai_translate = orig


def test_movie_info_same_lang_no_ori_title():
    """同语言时 title/plot 保持原文, 且不记 ori_title/ori_plot"""
    from confz import FileSource
    from javsp.config import Cfg
    _CONFIG = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'config.yml'))
    Cfg.CONFIG_SOURCES = [FileSource(_CONFIG)]

    info = MovieInfo('TEST-001')
    info.title = '这是一个中文标题'
    info.plot = '这是一个中文简介。'
    info.actress = []
    tr.translate_movie_info(info)
    assert info.title == '这是一个中文标题'
    assert info.ori_title is None, '同语言不应设置 ori_title'
    assert info.plot == '这是一个中文简介。'
    assert getattr(info, 'ori_plot', None) is None, '同语言不应设置 ori_plot'
