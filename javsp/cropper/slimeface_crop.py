import warnings
from PIL import Image
from javsp.cropper.interface import Cropper, DefaultCropper
from javsp.cropper.utils import get_bound_box_by_face


class SlimefaceCropper(Cropper):
    """基于 slimeface 人脸检测的封面裁剪器

    依赖是**可选**的（库缺失不能让整个刮削崩掉），所以 import 仍放在方法内部。
    但每种失败都必须留下 reason：不论是依赖缺失、检测报错，还是单纯没检测到人脸——
    它们最终都表现为「回退到默认裁剪」，若不留痕，用户看到的就只是「开了没效果」。
    """

    def crop_specific(self, fanart: Image.Image, ratio: float) -> Image.Image:
        try:
            # defer the libary import so we don't break if missing dependencies
            from slimeface import detectRGB
        except ImportError:
            self._mark_fallback('未安装 slimeface 依赖，无法使用人脸检测裁剪')
            return DefaultCropper().crop_specific(fanart, ratio)

        try:
            # slimeface 遇到大图会发 UserWarning 建议降采样：批量整理时每张封面都刷一条
            # 很吵，且不影响结果（只是提示可能变慢），故仅在此次调用内抑制。
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', UserWarning)
                bbox_confs = detectRGB(fanart.width, fanart.height,
                                       fanart.convert('RGB').tobytes())
        except Exception as e:
            self._mark_fallback(f'人脸检测出错: {type(e).__name__}: {e}')
            return DefaultCropper().crop_specific(fanart, ratio)

        if not bbox_confs:
            # 封面本来就可能没有正脸（背面/远景/纯文字构图），这是预期内的常见情况
            self._mark_fallback('未从封面中检测到人脸')
            return DefaultCropper().crop_specific(fanart, ratio)

        try:
            bbox_confs.sort(key=lambda conf_bbox: -conf_bbox[4])  # last arg stores confidence
            face = bbox_confs[0][:-1]
            poster_box = get_bound_box_by_face(face, fanart.size, ratio)
            cropped = fanart.crop(poster_box)
        except Exception as e:
            self._mark_fallback(f'按人脸位置裁剪失败: {type(e).__name__}: {e}')
            return DefaultCropper().crop_specific(fanart, ratio)

        self._mark_ok()
        return cropped


if __name__ == '__main__':
    from argparse import ArgumentParser

    arg_parser = ArgumentParser(prog='slimeface crop')

    arg_parser.add_argument('-i', '--image', help='path to image to detect')

    args, _ = arg_parser.parse_known_args()

    if(args.image is None):
        print("USAGE: slimeface_crop.py -i/--image [path]")
        exit(1)

    cropper = SlimefaceCropper()
    input = Image.open(args.image)
    im = cropper.crop(input)
    # 命令行场景同样要能看出到底用没用上人脸检测
    if not cropper.last_status['applied']:
        print(f"未使用人脸检测裁剪，已回退为默认裁剪：{cropper.last_status['reason']}")
    im.save('output.png')
