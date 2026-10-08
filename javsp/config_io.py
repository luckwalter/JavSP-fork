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

__all__ = ['diff_leaves', 'write_config_preserving_comments', 'render_changes',
           'mask_secrets', 'unmask_secrets']

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
    # 必须包含换行/回车/制表: 否则值里的裸换行会被原样写入 YAML,
    # 解析时折叠成一行(值被静默篡改)甚至破坏结构。多行文本一律用双引号块标量。
    if re.search(r'[\n\r\t]', s):
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
    if re.search(r'[\n\r\t]', s):
        # 含换行/制表: 必须用双引号块标量(YAML 双引号支持 \n 转义, 单引号里换行会被折叠成空格)
        esc = (s.replace('\\', '\\\\').replace('"', '\\"')
                .replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t'))
        return '"' + esc + '"'
    if _needs_quote(s):
        return "'" + s.replace("'", "''") + "'"
    return s


def _render_key(k):
    """渲染映射的键（与标量同理，必要时加引号）"""
    s = str(k)
    return "'" + s.replace("'", "''") + "'" if _needs_quote(s) else s


def _render(value):
    """渲染字段值：列表→flow 序列、映射→flow 映射（均单行），其余按标量

    flow 映射形如 `{name: slimeface}`，YAML 能正常解析成嵌套对象。这样做的好处是
    「把一个标量字段改成嵌套对象」（如 `engine: null` → `engine: {name: slimeface}`）
    只需替换一行，不必往满是注释的文件里插入多行块，注释与排版都不会动。
    """
    if isinstance(value, (list, tuple)):
        if not value:
            return '[]'
        return '[' + ', '.join(_scalar(v) for v in value) + ']'
    if isinstance(value, dict):
        if not value:
            return '{}'
        items = ', '.join(f'{_render_key(k)}: {_render(v)}' for k, v in value.items())
        return '{' + items + '}'
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


def _flow_end(lines, i):
    """若第 i 行的值是**跨行**的 flow 集合([...] / {...})，返回其续行结束下标（不含）

    YAML 允许 flow 集合跨行书写，例如：

        normal: [airav, avsox, javbus,
                 javdbapi, javmenu]

    这在语法上完全合法，但 `_render()` 对任何取值都只产出**单行**。若只替换首行，
    第二行就会变成孤立的标量残留，整个文件随即解析失败 —— 实测报错正是
    `expected <block end>, but found '<scalar>'`。所以替换 leaf 时必须把续行一并吞掉。

    括号深度按字符统计，并跳过引号内的字符（避免把 `'a[b'` 这类字面量误算成括号）。
    """
    content, _ = _split_eol(lines[i])
    rest = content.split(':', 1)[1] if ':' in content else ''
    depth = _bracket_depth(rest)
    if depth <= 0:
        return i + 1                      # 值在首行就已闭合, 无续行
    j = i
    while depth > 0 and j + 1 < len(lines):
        j += 1
        c, _ = _split_eol(lines[j])
        depth += _bracket_depth(_strip_comment(c))
    return j + 1


def _bracket_depth(text):
    """统计 flow 括号的净深度（开 - 闭），忽略引号内的字符"""
    depth = 0
    quote = None
    for ch in text:
        if quote:
            if ch == quote:
                quote = None
        elif ch in ('"', "'"):
            quote = ch
        elif ch in '[{':
            depth += 1
        elif ch in ']}':
            depth -= 1
    return depth


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
        if kind == 'container':
            # 原字段是多行嵌套块，而 _render 对任何取值都只产出**单行**，
            # 故必须把原有子行整块删除，否则残留的缩进行会让 YAML 结构错乱
            # （如 `engine: null` 写成 `engine: {name: slimeface}`，或反过来收敛为 null）。
            # 注意：块内部更深缩进的注释会一并被吞掉，这是「替换整块」语义的固有代价。
            end = _block_end(lines, i, indent)
            to_delete.update(range(i + 1, end))
        else:
            # leaf 也未必只有一行: flow 集合允许跨行书写, 只换首行会留下孤立续行。
            end = _flow_end(lines, i)
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


# ---------------------------------------------------------------------------
# 敏感字段脱敏 / 还原
# ---------------------------------------------------------------------------
# 本服务无鉴权且默认曾监听全网卡, GET /api/config 若原样返回配置, 等于把翻译
# api_key / app_id 明文交给任何能访问该端口的客户端。这里做掩码, 但**必须同时
# 支持还原**——否则前端把配置原样回传时会用掩码覆盖真实密钥, 反而造成丢密钥。
_SENSITIVE_KEYS = frozenset({'api_key', 'app_id', 'secret', 'token', 'password'})
_MASK_PREFIX = '***MASKED***'


def _mask_value(val: str) -> str:
    """把敏感值替换为固定掩码串（保留可识别的形态，便于前端原样回传后还原）"""
    if not isinstance(val, str) or not val:
        return val
    return _MASK_PREFIX


def mask_secrets(obj):
    """递归地把 dict/list 中的敏感字段值替换为掩码串（返回新对象, 不改原对象）"""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in _SENSITIVE_KEYS:
                out[k] = _mask_value(v)
            else:
                out[k] = mask_secrets(v)
        return out
    if isinstance(obj, list):
        return [mask_secrets(i) for i in obj]
    return obj


def unmask_secrets(submitted, reference):
    """把提交数据里的掩码串还原为 reference 里的真实值（只处理掩码, 不动用户新填的值）

    这样前端拿到的是掩码, 回传时若仍是掩码, 我们就填回原值 → 既不泄露, 又不丢密钥。
    """
    if isinstance(submitted, dict):
        out = {}
        for k, v in submitted.items():
            ref_v = reference.get(k) if isinstance(reference, dict) else None
            if k in _SENSITIVE_KEYS and isinstance(v, str) and v == _MASK_PREFIX:
                out[k] = ref_v
            else:
                out[k] = unmask_secrets(v, ref_v)
        return out
    if isinstance(submitted, list):
        # 必须用 enumerate 拿「值」并按同位置对齐, 不能用 for i in range(len(...)) ——
        # 那样 reference[i] 会变成按整数下标取值, 且对非 dict 元素毫无意义。
        # 这里保持「逐项递归, 不做替换」即可(列表里的掩码串极少, 真有则按同位置还原)。
        out = []
        for idx, item in enumerate(submitted):
            ref_v = reference[idx] if isinstance(reference, list) and idx < len(reference) else None
            out.append(unmask_secrets(item, ref_v))
        return out
    return submitted
