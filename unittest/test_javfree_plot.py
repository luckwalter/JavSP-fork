# -*- coding: utf-8 -*-
"""javfree 简介(plot)解析的回归测试

对应缺陷 B-021: javfree 源未提取 plot, 一旦把 plot 列入「至少命中字段」,
core.py 的 required_keys 校验必然失败(刮削测试报「未获取到必需字段」)。

用例取自 2026-11-25 实测 javfree.me 的真实页面片段, **完全离线, 不联网**。
"""
from lxml import html as LH

from javsp.web.javfree import _extract_plot, _split_paragraph_lines


# 真实片段: 元信息逐行 <br> 分隔, 末行是简介正文
BLOCKQUOTE_HTML = """<div class="entry-content">
<blockquote><p>発売日： 2017/08/13<br>
収録時間： 160分<br>
出演者：   妃月るい<br>
監督： 刃修羅<br>
シリーズ： —-<br>
メーカー： アイデアポケット<br>
レーベル： ティッシュ<br>
ジャンル： 女子校生 美少女 巨乳 単体作品 イラマチオ ハメ撮り デジモ サンプル動画<br>
品番： <strong>ipx001</strong><br>
あぁ臭いぃ！臭いよぉ！おじさんのションベン味の臭チンポたまらないよぉ！
</p></blockquote>
</div>"""


def test_split_paragraph_lines():
    doc = LH.fromstring('<p>甲<br>乙<br>丙</p>')
    p = doc.xpath('//p')[0]
    assert _split_paragraph_lines(p) == ['甲', '乙', '丙']


def test_extract_plot_takes_last_line():
    doc = LH.fromstring(BLOCKQUOTE_HTML)
    plot = _extract_plot(doc)
    assert plot is not None
    assert plot.startswith('あぁ臭いぃ！')


def test_extract_plot_excludes_meta():
    """元信息不得混入简介"""
    doc = LH.fromstring(BLOCKQUOTE_HTML)
    plot = _extract_plot(doc)
    assert 'ipx001' not in plot
    assert '発売日' not in plot
    assert 'ジャンル' not in plot


def test_extract_plot_without_blockquote():
    """站点结构不统一: 部分条目没有 blockquote -> 静默 None, 不抛异常"""
    doc = LH.fromstring('<div class="entry-content"><p>只有广告链接</p></div>')
    assert _extract_plot(doc) is None


def test_extract_plot_meta_only():
    """末行仍是元信息 -> 判定该条目没有简介"""
    html = """<div class="entry-content"><blockquote><p>発売日： 2020/01/01<br>
品番： <strong>ABC-123</strong><br>
</p></blockquote></div>"""
    doc = LH.fromstring(html)
    assert _extract_plot(doc) is None
