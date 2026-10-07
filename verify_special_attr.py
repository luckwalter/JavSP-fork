"""验证 detect_special_attr 的正则构造顺序正确（v0.1.24 修复）

## 背景：v0.1.20 引入的真 bug

为修「番号正则注入」，把原来的

    re.sub(r'[_-]', '[_-]*', avid)

改成了

    re_escape(avid).replace('-', '[_-]*').replace('_', '[_-]*')

并在注释里写下了一个**错误推断**：「re_escape 的转义表不含 - 与 _, 故这一步不会破坏
已转义的部分」。实际上 re_escape **会转义 [ ] ***，所以第二次 replace 会命中第一次
replace 刚生成的 '[_-]*' 里的 '_' 与 '['，产出嵌套字符类 '[[_-]*-]*'：

    /app/javsp/lib.py:71: FutureWarning: Possible nested set at position 4
      match = re.search(pattern_str, base, flags=re.I)

实测后果：
- Python 未来版本会把它当 SyntaxWarning/Error；
- `-/_` 放宽语义被破坏（形如 '[[_-]*-]*' 的字符类不再等价于 '[_-]*'）；
- 模式可能匹配到本不该匹配的内容。

正确做法：**先按分隔符切分 -> 逐段 re_escape -> 用未转义的分隔符合并**。

本脚本同时验证两个方向：
1. 正向：语义与「修复前」完全一致（不能为了消警告而改变行为）；
2. 反向：FutureWarning 当错误跑（任何嵌套字符类都会立刻失败）；
3. 安全：恶意 avid 仍无法注入。
"""
import os
import re
import sys
import warnings

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from javsp.lib import re_escape, detect_special_attr

PASS = 0
FAIL = 0
FAILURES = []


def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  [PASS] {name}')
    else:
        FAIL += 1
        FAILURES.append(f'{name} {detail}')
        print(f'  [FAIL] {name} {detail}')


def section(t):
    print(f'\n=== {t} ===')


def build_pattern(avid: str) -> str:
    """从 javsp/lib.py 源码里抽出实际的模式构造逻辑, 避免测试与实现漂移

    直接复制一份实现会随实现变更而失同步(项目已两次踩坑), 故从源码正则提取。
    """
    import inspect
    from javsp import lib
    src = inspect.getsource(lib.detect_special_attr)
    m = re.search(r"pattern_str = (.+?)\n\s*pattern_str \+= ", src, re.S)
    assert m, '未能从 lib.py 提取 pattern_str 构造代码, 实现结构可能变了'
    expr = m.group(1)
    scope = {'re': re, 're_escape': re_escape, 'avid': avid}
    return eval(expr, scope) + r'(UC|U|C)\b'  # noqa: S307 - 提取自本项目源码


def old_build(avid: str) -> str:
    """v0.1.20 的错误实现, 用作行为基线对照"""
    return re_escape(avid).replace('-', '[_-]*').replace('_', '[_-]*') + r'(UC|U|C)\b'


def main():
    section('A 构造出的模式无嵌套字符类（核心回归点）')
    for avid in ['ABC-123', 'ABC_123', 'MFCS-082', 'A-B-C', 'X', 'A_1-B_2']:
        pat = build_pattern(avid)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter('always')
            try:
                re.compile(pat)
                err = None
            except Exception as e:      # noqa: BLE001
                err = str(e)
        nested = [str(x.message) for x in w if 'nested set' in str(x.message)]
        check(f' avid={avid!r} 编译无错', err is None, err or '')
        check(f' avid={avid!r} 无 nested set 警告', not nested, str(nested))
        check(f' avid={avid!r} 无 [[ 双重开括号', '[[' not in pat, repr(pat))
        # 只有「含分隔符的 avid」才该出现 [_-]* 分隔符; 单段 avid 不该被强加
        if '-' in avid or '_' in avid:
            check(f' avid={avid!r} 模式含未转义的 [_-]* 分隔符',
                  '[_-]*' in pat, repr(pat))
        else:
            check(f' avid={avid!r} 单段: 不含 [_-]*', '[_-]*' not in pat, repr(pat))

    section('B 语义与修复前完全一致（不能为消警告而改行为）')
    cases = [
        ('ABC-123-C.mp4', 'ABC-123'), ('ABC123-C.mp4', 'ABC-123'),
        ('ABC-123-UC.mp4', 'ABC-123'), ('ABC-123-U.mp4', 'ABC-123'),
        ('ABC_123_C.mp4', 'ABC-123'), ('ABC-123.mp4', 'ABC-123'),
        ('ABC123.mp4', 'ABC-123'), ('ABC_123.mp4', 'ABC-123'),
        ('MFCS-082-C.mp4', 'MFCS-082'), ('IPX-177-C.mp4', 'IPX-177'),
    ]
    for fname, avid in cases:
        old_pat = old_build(avid)
        new_pat = build_pattern(avid)
        o = bool(re.search(old_pat, fname.split('.')[0], re.I))
        n = bool(re.search(new_pat, fname.split('.')[0], re.I))
        check(f' {fname:16} 匹配行为与修复前一致', o == n,
              f'修复前={o} 修复后={n} pattern={new_pat!r}')

    section('C detect_special_attr 端到端（真实调用路径）')
    expect = [
        ('ABC-123-C.mp4', 'ABC-123', 'C'),
        ('ABC-123-UC.mp4', 'ABC-123', 'UC'),
        ('ABC-123-U.mp4', 'ABC-123', 'U'),
        ('ABC-123.mp4', 'ABC-123', ''),
        ('ABC123-C.mp4', 'ABC-123', 'C'),
    ]
    with warnings.catch_warnings():
        warnings.simplefilter('error', FutureWarning)   # 任何 FutureWarning 直接失败
        for fname, avid, exp in expect:
            try:
                got = detect_special_attr(fname, avid)
                check(f' {fname:16} -> {exp!r}', got == exp, f'got {got!r}')
            except Exception as e:                     # noqa: BLE001
                check(f' {fname:16} 无警告且结果正确', False, f'{type(e).__name__}: {e}')

    section('D 恶意 avid 仍无法注入')
    evil = ['A)(.*)', 'X[', 'A{2,}', r'\d+', 'A-B.*', '(a+)+', '[a-z]]+', 'A|B']
    with warnings.catch_warnings():
        warnings.simplefilter('error', FutureWarning)
        for e in evil:
            pat = build_pattern(e)
            # 模式里的每个元字符都应已被转义: 除我们自己加的 [_-]* 与末尾后缀外无裸元字符
            stripped = pat.replace('[_-]*', '').replace(r'(UC|U|C)\b', '')
            bare = re.search(r'(?<!\\)[.*+?()\[\]{}|^$]', stripped)
            check(f' avid={e!r} 无未转义元字符', bare is None,
                  f'pattern={pat!r} 残留={bare.group() if bare else None!r}')

    section('E 顺序不变式（源码级守卫）')
    import inspect
    from javsp import lib
    src = inspect.getsource(lib.detect_special_attr)
    check('实现用「切分->逐段转义->合并」而非「先转义再 replace」',
          "re_escape(seg) for seg in re.split" in src, '实现结构变了')
    check('实现不再使用 re_escape(avid).replace(...) 顺序',
          're_escape(avid).replace(' not in src, '旧的错误顺序回来了')

    print('\n' + '=' * 60)
    print(f'RESULT: PASS={PASS}  FAIL={FAIL}')
    if FAILURES:
        print('失败明细:')
        for f in FAILURES:
            print('  -', f)
    print('=' * 60)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
