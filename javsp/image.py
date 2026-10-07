"""处理本地图片的相关功能"""
from enum import Enum
import os
import logging
from PIL import Image, ImageOps


__all__ = ['valid_pic', 'get_pic_size', 'add_label_to_poster', 'LabelPostion']

logger = logging.getLogger(__name__)


def valid_pic(pic_path):
    """检查图片文件是否完整

    注意: 只需让 Pillow 真正解码一次即可验证可用性; 早前用
    ImageOps.exif_transpose(...) 会额外 copy() 整张图(峰值内存翻倍),
    高分辨率封面(4000x6000)单次约 137MB, 批量整理时纯属浪费。
    """
    try:
        with Image.open(pic_path) as img:
            img.verify()          # 只校验结构, 不解码像素
        # verify() 后需重新打开才能确认可解码(verify 会把文件对象置空)
        with Image.open(pic_path) as img:
            img.load()
        return True
    except Exception as e:
        logger.debug(e, exc_info=True)
        return False


# 位置枚举
class LabelPostion(Enum):
    """水印位置枚举"""
    TOP_LEFT = 1
    TOP_RIGHT = 2
    BOTTOM_LEFT = 3
    BOTTOM_RIGHT = 4

def add_label_to_poster(poster: Image.Image, mark_pic_file: Image.Image, pos: LabelPostion) -> Image.Image:
    """向poster中添加标签(水印)"""
    mark_img = mark_pic_file.convert('RGBA')
    r,g,b,a = mark_img.split()
    # 计算水印位置
    if pos == LabelPostion.TOP_LEFT:
        box = (0, 0)
    elif pos == LabelPostion.TOP_RIGHT:
        box = (poster.size[0] - mark_img.size[0], 0)
    elif pos == LabelPostion.BOTTOM_LEFT:
        box = (0, poster.size[1] - mark_img.size[1])
    elif pos == LabelPostion.BOTTOM_RIGHT:
        box = (poster.size[0] - mark_img.size[0], poster.size[1] - mark_img.size[1])
    poster.paste(mark_img, box=box, mask=a)
    return poster


def get_pic_size(pic_path):
    """获取图片文件的分辨率

    仅读文件头(不 decode + 不 copy)。早前用 exif_transpose 会为了拿两个整数
    把整张图解码并复制一遍(4000x6000 峰值约 137MB), 每部影片每张封面都调一次,
    批量整理时是纯浪费。这里改为: 只读尺寸 + 仅当 EXIF 标明需旋转时才交换宽高,
    保持与 exif_transpose 一致的返回语义。
    """
    with Image.open(pic_path) as pic:
        width, height = pic.size
        try:
            exif = pic.getexif()
            orientation = exif.get(0x0112) if exif else None
        except Exception:
            orientation = None
    # EXIF Orientation 5/6/7/8 表示需顺时针/逆时针旋转 90°, 旋转后宽高互换
    if orientation in (5, 6, 7, 8):
        return height, width
    return width, height
