# -*- coding: utf-8 -*-
"""配置写入: 新增顶层段(web)的落盘与兼容性

对应缺陷 B-022: 主题要入库统一管理, 但老用户的 config.yml 里根本没有 web 段,
而 config_io 原先只支持「在已存在的祖先容器内新建单级字段」——
新增顶层段会被判 missing, 表现为「界面提示保存成功、配置却没写进文件」。

本测试全部离线, 不触碰真实 config.yml。
"""
import yaml

from javsp.config_io import diff_leaves, write_config_preserving_comments


OLD_CONFIG = """# 顶部注释应保留
scanner:
  # 扫描目录
  paths: ['D:/movies']
other:
  interactive: true
"""


def _read(p):
    return yaml.safe_load(p.read_text(encoding='utf-8'))


def test_new_top_level_section_is_written(tmp_path):
    """老配置没有 web 段 -> 保存后应新增该段, 且不破坏原有内容与注释"""
    p = tmp_path / 'config.yml'
    p.write_text(OLD_CONFIG, encoding='utf-8')
    current = _read(p)
    merged = dict(current)
    merged['web'] = {'theme_mode': 'dark', 'theme_schedule_start': '19:00'}
    changes = diff_leaves(current, merged)
    n, missing = write_config_preserving_comments(str(p), changes, merged)

    assert missing == [], f'不应有字段写不进去: {missing}'
    assert n >= 1
    data = _read(p)
    assert data['web']['theme_mode'] == 'dark'
    assert data['web']['theme_schedule_start'] == '19:00'
    # 原有内容与中文注释必须原样保留
    assert data['scanner']['paths'] == ['D:/movies']
    assert data['other']['interactive'] is True
    text = p.read_text(encoding='utf-8')
    assert '# 顶部注释应保留' in text
    assert '# 扫描目录' in text


def test_existing_section_is_replaced_in_place(tmp_path):
    """已有 web 段 -> 原地改值, 不新增段也不重复追加"""
    p = tmp_path / 'config.yml'
    p.write_text(OLD_CONFIG + "\nweb:\n  theme_mode: light\n", encoding='utf-8')
    current = _read(p)
    merged = dict(current)
    merged['web'] = dict(current['web'])
    merged['web']['theme_mode'] = 'dark'
    changes = diff_leaves(current, merged)
    n, missing = write_config_preserving_comments(str(p), changes, merged)

    assert missing == []
    data = _read(p)
    assert data['web']['theme_mode'] == 'dark'
    text = p.read_text(encoding='utf-8')
    assert text.count('web:') == 1
    assert 'theme_mode: light' not in text


def test_missing_child_in_existing_section(tmp_path):
    """已有 web 段但缺 theme_mode 子键 -> 在段内补一行, 不重复建段"""
    p = tmp_path / 'config.yml'
    p.write_text(OLD_CONFIG + "\nweb:\n  theme_mode: light\n", encoding='utf-8')
    current = _read(p)
    merged = dict(current)
    merged['web'] = dict(current['web'])
    merged['web']['theme_schedule_end'] = '07:00'
    changes = diff_leaves(current, merged)
    n, missing = write_config_preserving_comments(str(p), changes, merged)

    assert missing == []
    data = _read(p)
    assert data['web']['theme_mode'] == 'light'
    assert data['web']['theme_schedule_end'] == '07:00'
    text = p.read_text(encoding='utf-8')
    assert text.count('web:') == 1
