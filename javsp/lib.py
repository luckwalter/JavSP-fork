"""用来组织不需要依赖任何自定义类型的功能函数"""
import os
import re
import sys
from pathlib import Path


__all__ = ['re_escape', 'resource_path', 'strftime_to_minutes', 'detect_special_attr']


_special_chars_map = {i: '\\' + chr(i) for i in b'()[]{}?*+|^$\\.'}
def re_escape(s: str) -> str:
    """用来对字符串进行转义，以将转义后的字符串用于构造正则表达式"""
    pattern = s.translate(_special_chars_map)
    return pattern


def resource_path(path: str) -> str:
    """获取一个随代码打包的文件在解压后的路径"""
    if getattr(sys, "frozen", False):
        return path
    else:
        path_joined = Path(__file__).parent.parent / path
        return str(path_joined)


def strftime_to_minutes(s: str) -> int:
    """将HH:MM:SS或MM:SS的时长转换为分钟数返回

    Args:
        s (str): HH:MM:SS or MM:SS

    Returns:
        [int]: 取整后的分钟数
    """
    items = list(map(int, s.split(':')))
    if len(items) == 2:
        minutes = items[0] + round(items[1]/60)
    elif len(items) == 3:
        minutes = items[0] * 60 + items[1] + round(items[2]/60)
    else:
        raise ValueError(f"无法将字符串'{s}'转换为分钟")
    return minutes


_PATTERN = re.compile(r'(uncen(sor(ed)?)?([- _\s]*leak(ed)?)?|[无無][码碼](流出|破解))', flags=re.I)
def detect_special_attr(filepath: str, avid: str = None) -> str:
    """通过文件名检测影片是否有特殊属性（内嵌字幕、无码流出/破解）

    Returns:
        [str]: '', 'U', 'C', 'UC'
    """
    result = ''
    base = os.path.splitext(os.path.basename(filepath))[0].upper()
    # 尝试使用正则匹配
    match = _PATTERN.search(base)
    if match:
        result += 'U'
    # 尝试匹配-C/-U/-UC后缀的影片
    postfix = base.split('-')[-1]
    if postfix in ('U', 'C', 'UC'):
        result += postfix
    elif avid:
        # avid 来自文件名或 /api/scrape 的入参, 属不可信输入: 必须转义正则元字符,
        # 否则其中的 * + ( ) . 等会成为活跃语法(既能操纵下面的判定, 也可构造嵌套量词触发 ReDoS)。
        #
        # 顺序很关键 —— 必须**先切分、再逐段转义、最后用未转义的分隔符合并**:
        # - 先 replace 再 re_escape: re_escape 会把刚插入的 [ ] * 一并转义, 放宽语义全失效;
        # - 先 re_escape 再 replace: 第一次 replace 生成的 '[_-]*' 里的 _ 和 [ 会被第二次
        #   replace 当作待处理字符, 产出 '[[_-]*-]*' 这种嵌套字符类(Python 发 FutureWarning:
        #   Possible nested set), 既让放宽语义失效又能匹配到本不该匹配的内容。
        # 切分->逐段转义->拼接则不受两种顺序问题影响: 分隔符是我们自己加的、不参与转义。
        pattern_str = '[_-]*'.join(re_escape(seg) for seg in re.split(r'[-_]', avid))
        pattern_str += r'(UC|U|C)\b'
        match = re.search(pattern_str, base, flags=re.I)
        if match:
            result += match.group(1)
    # 最终格式化
    result = ''.join(sorted(set(result), reverse=True))
    return result


if __name__ == "__main__":
    print(detect_special_attr('ipx-177cd1.mp4', 'IPX-177'))
