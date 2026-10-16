# -*- coding: utf-8 -*-
"""上游 Yuukiy/JavSP open PR 稳健性修复的回归测试（零依赖 unittest）

背景: 上游项目已长期停更, 本文件收录经评估值得移植的修复及其回归防线,
     按"复现问题-修复问题-验证修复"的红绿流程固化, 防止未来重构踩回.

覆盖:
1. javsp.lib.detect_special_attr: 后缀分隔符兼容 '-' 与 '_'
   (上游 PR #520 方向): 'ABC-123_C.mp4' 应识别为 'C', 旧实现只认 '-'.
2. javsp.web.fc2ppvdb.parse_data: 页面缺"収録時間"字段时 duration 留空,
   不再抛 AttributeError 导致整个源解析报废.

运行: 仓库根执行 python -m unittest discover -s tests -v
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from javsp.lib import detect_special_attr
from javsp.datatype import MovieInfo
from lxml import html as lxml_html

import javsp.web.fc2ppvdb as fc2


class TestDetectSpecialAttrUnderscore(unittest.TestCase):
    """lib.detect_special_attr: '_' 分隔的 UC/U/C 后缀识别（修复目标）"""

    def test_underscore_suffix(self):
        self.assertEqual(detect_special_attr('ABC-123_C.mp4'), 'C')
        self.assertEqual(detect_special_attr('ABC-123_U.mp4'), 'U')
        self.assertEqual(detect_special_attr('ABC-123_UC.mp4'), 'UC')
        self.assertEqual(detect_special_attr('ABC_123_C.mp4'), 'C')
        self.assertEqual(detect_special_attr('abc-123_c.mp4'), 'C')  # 内部先 upper
        self.assertEqual(detect_special_attr('SSIS-001_U.mp4'), 'U')
        self.assertEqual(detect_special_attr('MIDE-137_UC.mp4'), 'UC')

    def test_underscore_suffix_with_avid(self):
        self.assertEqual(detect_special_attr('ABC-123_C.mp4', 'ABC-123'), 'C')

    def test_hyphen_suffix_regression(self):
        """既有行为: '-' 分隔不能坏"""
        self.assertEqual(detect_special_attr('ABC-123-C.mp4'), 'C')
        self.assertEqual(detect_special_attr('ABC-123-UC.mp4'), 'UC')
        self.assertEqual(detect_special_attr('SSIS-001-U.mkv'), 'U')

    def test_no_suffix_regression(self):
        """既有行为: 无后缀/无关后缀不能坏"""
        self.assertEqual(detect_special_attr('ABC-123.mp4'), '')
        self.assertEqual(detect_special_attr('ABC_123.mp4'), '')
        self.assertEqual(detect_special_attr('AB_C_D.mp4'), '')
        self.assertEqual(detect_special_attr('XXX-888-C-C.mp4'), 'C')  # 取最后一段
        self.assertEqual(detect_special_attr('ipx-177cd1.mp4', 'IPX-177'), '')


# fc2ppvdb 测试页面: 除"収録時間"外字段齐全（模拟站点改版/数据缺失）
_HTML_NO_DURATION = '''
<html><body>
<div class="container lg:px-5 px-2 py-12 mx-auto"><div>
  <h2><a href="/articles/4497837">测试影片标题 FC2-4497837</a></h2>
  <img alt="4497837" src="https://thumb.example/1.jpg"/>
  <div>女優：<span><a href="/actress/1">测试女优</a></span></div>
  <div>タグ：<span><a href="/tag/1">题材A</a></span></div>
  <div>販売日：<span>2024-01-15</span></div>
  <div>販売者：<span><a href="/seller/1">测试卖家</a></span></div>
  <div>モザイク：<span>無</span></div>
  <a href="/sample.mp4">サンプル動画</a>
</div></div>
</body></html>
'''

# 对照组: 正常页面, 有"収録時間"字段 (01:23:45 -> 60+23+round(45/60)=84)
_HTML_WITH_DURATION = _HTML_NO_DURATION.replace(
    '<div>女優：', '<div>収録時間：<span>01:23:45</span></div>\n  <div>女優：')


class TestFc2ppvdbMissingDuration(unittest.TestCase):
    """fc2ppvdb.parse_data: 缺"収録時間"字段时的容错（修复目标+回归对照）"""

    def _parse(self, html_text):
        movie = MovieInfo('FC2-4497837')
        with mock.patch.object(fc2, 'get_html', return_value=lxml_html.fromstring(html_text)):
            fc2.parse_data(movie)
        return movie

    def test_missing_duration_keeps_other_fields(self):
        """缺时长字段: 不崩溃, duration 留空, 其他字段保留"""
        movie = self._parse(_HTML_NO_DURATION)
        self.assertIsNone(movie.duration)
        self.assertEqual(movie.title, '测试影片标题 FC2-4497837')
        self.assertEqual(movie.actress, ['测试女优'])
        self.assertEqual(movie.publish_date, '2024-01-15')

    def test_normal_duration_regression(self):
        """既有行为: 正常页面时长解析不能坏"""
        movie = self._parse(_HTML_WITH_DURATION)
        self.assertEqual(movie.duration, '84')


if __name__ == '__main__':
    unittest.main()
