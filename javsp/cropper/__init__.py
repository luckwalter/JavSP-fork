import logging

from javsp.config import SlimefaceEngine
from javsp.cropper.interface import Cropper, DefaultCropper
from javsp.cropper.slimeface_crop import SlimefaceCropper

logger = logging.getLogger('javsp.cropper')


def get_cropper(engine: SlimefaceEngine | None) -> Cropper:
    """按配置取裁剪器；引擎缺失或无法识别时退回默认裁剪（而不是返回 None）

    engine 的类型约束是 `SlimefaceEngine | None`，理论上只会是 slimeface，
    但一旦将来新增引擎却漏了这里的分支，隐式返回 None 会让调用点得到一个
    `AttributeError: 'NoneType' object has no attribute 'crop'` ——
    排查时很难联想到是裁剪器分支漏了，故显式兜底并留一行日志。
    """
    if engine is None:
        return DefaultCropper()
    if engine.name == 'slimeface':
        return SlimefaceCropper()

    logger.warning(f'未知的封面裁剪引擎: {getattr(engine, "name", engine)}，回退为默认裁剪')
    return DefaultCropper()
