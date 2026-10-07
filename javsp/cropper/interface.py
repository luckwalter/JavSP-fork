from PIL.Image import Image
from abc import ABC, abstractmethod


class Cropper(ABC):
    """封面裁剪器基类

    为什么要有 last_status
    ----------------------
    AI 引擎（人脸检测）失败时会**回退到默认裁剪**。早期这里是一个裸 `except`，
    任何异常——包括「没检测到人脸」这种正常情况——都被悄悄吞掉：封面照旧生成，
    日志一个字没有。结果就是用户在配置里开了 AI 裁剪，实际走的还是居中裁剪，
    却完全无法察觉。因此这里要求子类记录本次裁剪的真实去向。
    """

    def __init__(self):
        # applied=True 表示本引擎的算法真正产出了裁剪结果；
        # applied=False 时 reason 说明为何回退（默认裁剪器恒为 True）
        self.last_status = {'applied': False, 'reason': '尚未执行裁剪'}

    @abstractmethod
    def crop_specific(self, fanart: Image, ratio: float) -> Image:
        pass

    def crop(self, fanart: Image, ratio: float | None = None) -> Image:
        if ratio is None:
            ratio = 1.42
        self.last_status = {'applied': False, 'reason': '尚未执行裁剪'}
        return self.crop_specific(fanart, ratio)

    def _mark_ok(self):
        self.last_status = {'applied': True, 'reason': None}

    def _mark_fallback(self, reason):
        self.last_status = {'applied': False, 'reason': reason}


class DefaultCropper(Cropper):
    def crop_specific(self, fanart: Image, ratio: float) -> Image:
        """将给定的fanart图片文件裁剪为适合poster尺寸的图片"""
        (fanart_w, fanart_h) = fanart.size
        (poster_w, poster_h) = \
            (int(fanart_h / ratio), fanart_h) \
            if fanart_h / fanart_w < ratio \
            else (fanart_w, int(fanart_w * ratio)) # 图片太“瘦”时以宽度来定裁剪高度

        dh = int((fanart_h - poster_h) / 2)
        box = (fanart_w - poster_w, dh, fanart_w, poster_h + dh)
        cropped = fanart.crop(box)
        self._mark_ok()
        return cropped
