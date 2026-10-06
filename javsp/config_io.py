"""config.yml 的「保注释」读写

背景
----
直接 `yaml.safe_dump(整份配置)` 重写 config.yml 会把文件里的**全部中文注释丢掉**
（实测：注释 100 行 → 0 行，总行数 200 → 136）。而 config.yml 有近一半内容是注释说明，
丢掉后只剩一堆裸字段，后续手工调整会非常痛苦。

这里的做法是**只替换发生变化的字段行**，其余行（含注释、空行、未改动字段）原样保留。
好处：注释不丢、排版不乱、git diff 最小（只有真正改动的那些行）。

实现要点
--------
- 按缩进栈解析出「字段路径 → 行号」映射，不依赖第三方库（ruamel.yaml 未装，也不想为
  此新增依赖）。
- 列表统一写成单行 flow 风格（`key: [a, b]`）；若原字段是多行 block 风格（`- a`），
  则整块替换为单行。
- 换行符：读写均使用 `newline=''`，保持文件原有的 LF/CRLF
  （Python 在 Windows 上默认写文件会把 LF 转成 CRLF，导致整文件 diff）。
"""
import os
import re

__all__ = ['diff_leaves', 'write_config_preserving_comments', 'render_changes']

# 匹配 "  key: value" 形式的行（值可为空）
_KEY_RE = re.compile(r'^(\s*)([A-Za-z_][\w\-]*):(.*)$')
_ITEM_RE = re.compile(r'^(\s*)-(\s*)(.*)$')


def _split_eol(line):
    """拆出行内容与行尾，便于写回时保持原换行符"""
    for eol in ('\r\n', '\n', '\r'):
        if line.endswith(eol):
            return line[:-len(eol)], eol
    return line, ''


def _strip_comment(text):
    """去掉行尾 YAML 注释，注意不要误伤引号内的 # （如 http://a#b）"""
    quote = None
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = None
        elif ch in ('"', "'"):
            quote = ch
        elif ch == '#' and (i == 0 or text[i - 1] in ' \t'):
            return text[:i]
    return text


def _needs_quote(s):
    """判断字符串是否必须加引号，避免写回后被解析成别的类型"""
    if s == '' or s.strip() != s:
        return True
    if re.match(r'^(null|true|false|~|yes|no|on|off)$', s, re.I):
        return True
    if re.match(r'^[-+]?\d', s):          # 看起来像数字
        return True
    if re.search(r'[:#\[\]{},&*!|>%@`]', s):
        return True
    return False


def _scalar(v):
    """把标量渲染成 YAML 字面量"""
    if v is None:
        return 'null'
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if _needs_quote(s):
        return "'" + s.replace("'", "''") + "'"
    return s


def _render(value):
    """渲染字段值：列表用 flow 风格，其余按标量"""
    if isinstance(value, (list, tuple)):
        return '[' + ', '.join(_scalar(v) for v in value) + ']'
    return _scalar(value)


def _build_index(lines):
    """解析出 {字段路径: (类型, 行号)}，类型取 'leaf' / 'container'"""
    index = {}
    stack = []                      # [(缩进, key)]
    for i, raw in enumerate(lines):
        content, _ = _split_eol(raw)
        stripped = content.strip()
        if not stripped or stripped.startswith('#'):
            continue
        m = _KEY_RE.match(content)
        if not m:
            continue                # 跳过 "- item" 等列表项行
        indent, key, rest = m.group(1), m.group(2), m.group(3)
        ind = len(indent)
        while stack and stack[-1][0] >= ind:
            stack.pop()
        path = tuple(k for _, k in stack) + (key,)
        if _strip_comment(rest).strip() == '':
            index[path] = ('container', i)
            stack.append((ind, key))
        else:
            index[path] = ('leaf', i)
    return index


def _block_end(lines, start, parent_indent):
    """从 start+1 起，吞掉属于该容器的列表项行，返回结束下标（不含）

    注意必须先判缩进再判注释：块尾常常紧跟着**下一个字段的注释行**（缩进与当前字段同级），
    若先跳过注释就会把它一并吞掉，导致注释丢失。
    """
    j = start + 1
    while j < len(lines):
        content, _ = _split_eol(lines[j])
        stripped = content.strip()
        if not stripped:                            # 空行：保留，不纳入删除
            break
        ind = len(content) - len(content.lstrip())
        if ind <= parent_indent:                    # 缩进回到同级（含注释行）：结束
            break
        j += 1
    return j


def render_changes(changes):
    """把变更渲染成便于展示的文本（调试/日志用）"""
    return ['%s: %s' % ('.'.join(p), _render(v)) for p, v in sorted(changes.items())]


def diff_leaves(current, merged, path=()):
    """对比两份配置，返回发生变化的叶子字段 {路径元组: 新值}"""
    out = {}
    for key in (merged or {}):
        new_v = merged[key]
        old_v = (current or {}).get(key) if isinstance(current, dict) else None
        p = path + (key,)
        if isinstance(new_v, dict) and isinstance(old_v, dict):
            out.update(diff_leaves(old_v, new_v, p))
        elif new_v != old_v:
            out[p] = new_v
    return out


def write_config_preserving_comments(cfg_path, changes):
    """只替换 changes 中指定字段，其余内容（含注释）原样保留

    返回 (写入的字段数, 未能在文件中定位到的字段列表)
    """
    if not changes:
        return 0, []
    with open(cfg_path, 'r', encoding='utf-8', newline='') as f:
        text = f.read()
    lines = text.splitlines(keepends=True)
    index = _build_index(lines)

    to_replace = {}                 # 行号 -> 新行内容
    to_delete = set()               # block 列表被替换成单行后需要删除的旧行
    missing = []

    for path, value in changes.items():
        entry = index.get(path)
        if entry is None:
            missing.append('.'.join(path))
            continue
        kind, i = entry
        content, eol = _split_eol(lines[i])
        indent = len(content) - len(content.lstrip())
        new_line = ' ' * indent + path[-1] + ': ' + _render(value)
        if kind == 'container' and isinstance(value, (list, tuple)):
            # 原为多行 block 列表，整块替换成单行 flow 风格
            end = _block_end(lines, i, indent)
            to_delete.update(range(i + 1, end))
        to_replace[i] = new_line + eol

    out_lines = []
    for i, line in enumerate(lines):
        if i in to_delete:
            continue
        out_lines.append(to_replace.get(i, line))

    _atomic_write(cfg_path, ''.join(out_lines))
    return len(to_replace), missing


def _atomic_write(cfg_path, text):
    """原子写文件：先写同目录临时文件，再 os.replace 整体替换

    配置热重载会在保存后立即重新读盘，若直接原地写，存在读到「写了一半」文件的
    风险（进而让服务拿到残缺配置）。同目录临时文件 + os.replace 在 POSIX/Windows
    上都是原子替换，读端只会看到旧文件或新文件。
    """
    import tempfile
    folder = os.path.dirname(os.path.abspath(cfg_path)) or '.'
    fd, tmp = tempfile.mkstemp(prefix='.cfg_', suffix='.tmp', dir=folder)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as f:
            f.write(text)
        os.replace(tmp, cfg_path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
