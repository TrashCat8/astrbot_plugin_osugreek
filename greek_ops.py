"""纯 Pillow 图像处理，不依赖 AstrBot，可脱离框架单独测试。

移植自 nonebot-plugin-osugreek（Copyright (c) 2026 ChewYakuwo, MIT）。
原实现见 https://github.com/YakumoZn/nonebot-plugin-osugreek

效果分三步：
1. 色散：RGB 三个通道分别向左上/不动/向右下偏移，模拟色差；
2. 故障：按强度叠加横向错位、噪点、扫描线、模糊；
3. 叠加：把 4K 希腊字母 PNG 等比放大到原图短边的 1.8 倍后居中贴上。
"""

from __future__ import annotations

import math
import random
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat

DEFAULT_CHROMATIC = 4
"""默认色散强度"""

DEFAULT_GLITCH = 0
"""默认故障强度，0 表示不启用"""

CHROMATIC_RANGE = (1, 20)
"""色散强度取值区间（含端点）"""

GLITCH_RANGE = (0, 5)
"""故障强度取值区间（含端点）"""

DEFAULT_MAX_SIDE = 4096
"""输入图片长边上限，超过则先等比缩小，避免 4K 图吃满内存"""

DEFAULT_LETTER_SCALE = 1.0
"""字母缩放系数，1.0 为默认大小；大于 1 会放大，直到字母本体铺满画布"""

LETTER_SCALE_RANGE = (0.1, 3.0)
"""字母缩放系数取值区间（含端点）"""


def clamp(value: int, low: int, high: int) -> int:
    """把整数夹到 [low, high] 区间内。"""
    return max(low, min(high, int(value)))


def clamp_scale(value: float) -> float:
    """把缩放系数夹到合法区间内。"""
    return max(LETTER_SCALE_RANGE[0], min(LETTER_SCALE_RANGE[1], float(value)))


def add_chromatic_aberration(image: Image.Image, intensity: int = DEFAULT_CHROMATIC) -> Image.Image:
    """色散效果：红蓝通道反向偏移，绿色保持不动。"""
    intensity = clamp(intensity, *CHROMATIC_RANGE)

    bands = image.split()
    r, g, b = bands[:3]
    r_offset = ImageChops.offset(r, -intensity, -intensity)
    b_offset = ImageChops.offset(b, intensity, intensity)

    if len(bands) == 4:
        return Image.merge("RGBA", (r_offset, g, b_offset, bands[3]))
    return Image.merge("RGB", (r_offset, g, b_offset))


def add_glitch_effect(image: Image.Image, intensity: int = DEFAULT_GLITCH) -> Image.Image:
    """故障效果：强度越高，错位/噪点/扫描线/模糊越明显。"""
    intensity = clamp(intensity, *GLITCH_RANGE)
    if intensity == 0:
        return image.copy()

    width, height = image.size
    glitched = image.copy()
    if width < 2 or height < 2:
        return glitched

    # 1) 横向错位
    num_shifts = min(3, max(1, intensity))
    for _ in range(num_shifts):
        max_shift = max(5, int(width * 0.1 * intensity / 5))
        shift_amount = random.randint(2, max_shift) * random.choice([-1, 1])

        min_shift_height = max(1, height // 20)
        max_shift_height = height // 6 + (height // 12) * (intensity - 1)
        max_shift_height = max(min_shift_height, min(max_shift_height, height - 1))
        shift_height = random.randint(min_shift_height, max_shift_height)
        shift_y = random.randint(0, height - shift_height)

        region = glitched.crop((0, shift_y, width, shift_y + shift_height))
        if glitched.mode != "RGBA":
            glitched.paste(region, (shift_amount, shift_y))
            continue
        # 用 alpha_composite 叠加而不是直接 paste：透明底的图位移后，原本透明
        # 的像素会盖到不透明区域上，留下一个个透明缺口。合成时透明像素不生效，
        # 缺口处仍保留下层内容。
        band = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        band.paste(region, (shift_amount, shift_y))
        glitched = Image.alpha_composite(glitched, band)

    # 2) 噪点（强度 >= 2）
    if intensity >= 2:
        for _ in range(50 * intensity**2):
            x = random.randint(0, width - 1)
            y = random.randint(0, height - 1)
            color = (
                random.randint(0, 255),
                random.randint(0, 255),
                random.randint(0, 255),
                255,
            )
            glitched.putpixel((x, y), color)

        # 噪点块（强度 >= 3）
        if intensity >= 3:
            for _ in range(random.randint(1, intensity - 1)):
                block_width = random.randint(5, 20)
                block_height = random.randint(5, 20)
                block_x = random.randint(0, width - block_width)
                block_y = random.randint(0, height - block_height)
                for bx in range(block_width):
                    for by in range(block_height):
                        if random.random() < 0.7:
                            px = min(block_x + bx, width - 1)
                            py = min(block_y + by, height - 1)
                            color = (
                                random.randint(0, 255),
                                random.randint(0, 255),
                                random.randint(0, 255),
                                255,
                            )
                            glitched.putpixel((px, py), color)

    # 3) 扫描线（强度 >= 3）
    if intensity >= 3:
        scanline_spacing = random.randint(8 - intensity, 15 - intensity)
        scanline_probability = 0.15 + (intensity - 3) * 0.05
        brightness = 150 + (intensity - 3) * 25
        for y in range(0, height, max(1, scanline_spacing)):
            if random.random() < scanline_probability:
                line_height = random.randint(1, 2)
                line_region = glitched.crop((0, y, width, y + line_height))
                line_mask = Image.new("RGBA", (width, line_height), (brightness, brightness, brightness, 255))
                line_region = ImageChops.multiply(line_region, line_mask)
                glitched.paste(line_region, (0, y))

    # 4) 模糊（强度 >= 4）
    if intensity >= 4:
        blur_radius = 0.5 + (intensity - 4) * 0.5
        glitched = glitched.filter(ImageFilter.GaussianBlur(radius=blur_radius))

        # 5) 通道错位（强度 >= 5）
        if intensity >= 5 and len(glitched.split()) >= 3:
            bands = glitched.split()
            r, g, b = bands[:3]
            offset_x = random.randint(-3, 3)
            offset_y = random.randint(-3, 3)
            r_offset = ImageChops.offset(r, offset_x, offset_y)
            b_offset = ImageChops.offset(b, -offset_x, -offset_y)
            if len(bands) == 4:
                glitched = Image.merge("RGBA", (r_offset, g, b_offset, bands[3]))
            else:
                glitched = Image.merge("RGB", (r_offset, g, b_offset))

    return glitched


def resize_greek_image(
    greek_img: Image.Image,
    original_width: int,
    original_height: int,
    scale: float = DEFAULT_LETTER_SCALE,
) -> Image.Image:
    """把字母图等比缩放；字母本体（含阴影）始终完整落在画布内。

    基准是原图短边的 1.8 倍乘 scale。字母素材本身带大片透明边距，所以
    scale > 1 时字母图会超出画布、只裁掉透明边距，字母本体不会被切；
    放大到字母本体也装不下时会自动收回，保证可见部分完整。
    """
    greek_w, greek_h = greek_img.size
    if greek_w <= 0 or greek_h <= 0:
        return greek_img

    scale = clamp_scale(scale)
    target_size = int(min(original_width, original_height) * 1.8 * scale)
    if scale <= 1.0:
        target_size = min(target_size, max(original_width, original_height))
    target_size = max(1, target_size)

    scale_ratio = target_size / max(greek_w, greek_h)
    new_width = max(1, int(greek_w * scale_ratio))
    new_height = max(1, int(greek_h * scale_ratio))

    # 字母实际内容的边界（去掉透明边距），缩放后同样按比例放大
    content_box = greek_img.getchannel("A").getbbox()
    if content_box is None:
        content_width, content_height = new_width, new_height
    else:
        content_width = (content_box[2] - content_box[0]) * new_width / greek_w
        content_height = (content_box[3] - content_box[1]) * new_height / greek_h
    if content_width <= 0 or content_height <= 0:
        return greek_img.resize((new_width, new_height), Image.Resampling.LANCZOS)

    # 缩小或放大都以「内容不超出画布」为底线
    fit = min(1.0, original_width / content_width, original_height / content_height)
    if fit < 1.0:
        new_width = max(1, int(new_width * fit))
        new_height = max(1, int(new_height * fit))

    return greek_img.resize((new_width, new_height), Image.Resampling.LANCZOS)


def _fit(image: Image.Image, max_side: int) -> Image.Image:
    """长边超过 max_side（>0 时）等比缩小到上限。"""
    if not max_side or max(image.size) <= max_side:
        return image
    ratio = max_side / max(image.size)
    new_size = (max(1, int(image.width * ratio)), max(1, int(image.height * ratio)))
    return image.resize(new_size, Image.Resampling.LANCZOS)


def open_rgba(path: str | Path, max_side: int = DEFAULT_MAX_SIDE) -> Image.Image:
    """读图并转成 RGBA；长边超过 max_side（>0 时）先等比缩小。"""
    with Image.open(path) as im:
        image = ImageOps.exif_transpose(im).convert("RGBA")
    return _fit(image, max_side)


def _frame_to_paletted(frame: Image.Image) -> Image.Image:
    """把一帧转成 P 模式；带透明像素时保留透明索引。

    GIF 只支持 1 位透明：调色板留出最后一个索引（255）作为透明色，
    alpha < 128 的像素全部映射到它。这样透明底图生成的动图不会变成黑底。
    """
    rgba = frame if frame.mode == "RGBA" else frame.convert("RGBA")
    alpha = rgba.getchannel("A")
    if alpha.getextrema() == (255, 255):
        return rgba.convert("RGB").convert("P", palette=Image.ADAPTIVE, colors=256)

    paletted = rgba.convert("RGB").convert("P", palette=Image.ADAPTIVE, colors=255)
    palette = list(paletted.getpalette() or [])
    if len(palette) < 768:
        palette += [0] * (768 - len(palette))
    paletted.putpalette(palette[: 255 * 3] + [0, 0, 0])

    transparent = alpha.point(lambda value: 255 if value < 128 else 0)
    paletted.paste(255, (0, 0, paletted.width, paletted.height), transparent)
    paletted.info["transparency"] = 255
    return paletted


def _load_letter(
    letter_path: str | Path,
    width: int,
    height: int,
    scale: float = DEFAULT_LETTER_SCALE,
) -> Image.Image:
    with Image.open(letter_path) as letter_file:
        letter = letter_file.convert("RGBA")
    return resize_greek_image(letter, width, height, scale)


def _opaque_mean_color(image: Image.Image) -> tuple[int, int, int] | None:
    """求不透明区域的平均颜色；整张图不透明或全透明时返回 None。"""
    alpha = image.getchannel("A")
    low, high = alpha.getextrema()
    if low == high:
        return None
    try:
        mean = ImageStat.Stat(image.convert("RGB"), mask=alpha).mean
    except Exception:  # noqa: BLE001 —— 极端尺寸等情况下退回黑色
        return None
    return tuple(max(0, min(255, int(value))) for value in mean[:3])  # type: ignore[return-value]


def _bleed_transparent(image: Image.Image) -> Image.Image:
    """把透明像素的 RGB 换成不透明区域的平均色（alpha 保持不变）。

    透明 PNG 的透明像素 RGB 通常是 0，色散偏移会把颜色通道从这些纯黑像素
    采样进来，边缘就会出现黑边/青边。先做一次颜色扩散，边缘过渡会自然很多。
    """
    mean = _opaque_mean_color(image)
    if mean is None:
        return image

    alpha = image.getchannel("A")
    filled = Image.composite(image.convert("RGB"), Image.new("RGB", image.size, mean), alpha)
    result = filled.convert("RGBA")
    result.putalpha(alpha)
    return result


def _apply_effects(image: Image.Image, chromatic: int, glitch: int) -> Image.Image:
    """色散 + 故障；带透明通道的图会保留原始透明度轮廓。

    故障效果（噪点、噪点块）会往随机位置写入 alpha=255 的彩色像素，
    如果原图是透明背景，这些像素会变成满屏的彩色噪点，所以最后用原始
    alpha 做一次蒙版，透明区域始终透明。
    """
    if image.mode != "RGBA":
        image = image.convert("RGBA")

    alpha = image.getchannel("A")
    prepared = _bleed_transparent(image)

    result = add_chromatic_aberration(prepared, chromatic)
    if glitch > 0:
        result = add_glitch_effect(result, glitch)

    # 用原始 alpha 蒙版收口，透明区域不会出现凭空生成的彩色噪点
    result.putalpha(ImageChops.multiply(result.getchannel("A"), alpha))
    return result


def _paste_letter(image: Image.Image, letter: Image.Image) -> Image.Image:
    canvas = image.copy()
    x = (canvas.width - letter.width) // 2
    y = (canvas.height - letter.height) // 2
    canvas.paste(letter, (x, y), letter)
    return canvas


def render(
    source: str | Path,
    letter_path: str | Path,
    chromatic: int = DEFAULT_CHROMATIC,
    glitch: int = DEFAULT_GLITCH,
    max_side: int = DEFAULT_MAX_SIDE,
    scale: float = DEFAULT_LETTER_SCALE,
) -> Image.Image:
    """静态图完整流程：读图 -> 色散 -> 故障 -> 居中叠加字母。返回 RGBA 图像。"""
    original = open_rgba(source, max_side)
    letter = _load_letter(letter_path, original.width, original.height, scale)
    return _paste_letter(_apply_effects(original, chromatic, glitch), letter)


def is_animated(path: str | Path) -> bool:
    """判断是否为多帧动图；打不开的图片交由 render 统一报错。"""
    try:
        with Image.open(path) as im:
            return bool(getattr(im, "is_animated", False)) and getattr(im, "n_frames", 1) > 1
    except Exception:  # noqa: BLE001
        return False


def render_gif(
    source: str | Path,
    letter_path: str | Path,
    *,
    chromatic: int = DEFAULT_CHROMATIC,
    glitch: int = DEFAULT_GLITCH,
    max_side: int = 800,
    static_frames: int = 12,
    static_duration: int = 100,
    max_frames: int = 24,
    scale: float = DEFAULT_LETTER_SCALE,
) -> tuple[list[Image.Image], list[int]]:
    """生成动图帧。

    - 输入本身是动图：逐帧处理，超过 max_frames 时等间隔抽帧并等比拉长帧时长；
    - 输入是静态图：重复生成 static_frames 帧，每帧重新随机一次故障效果，
      所以故障强度为 0 时会按 1 处理，否则整段动画完全没有变化。
    """
    static_frames = max(2, int(static_frames))
    max_frames = max(2, int(max_frames))
    static_duration = max(20, int(static_duration))

    with Image.open(source) as im:
        animated = bool(getattr(im, "is_animated", False)) and getattr(im, "n_frames", 1) > 1

        if animated:
            total = im.n_frames
            step = max(1, math.ceil(total / max_frames))
            frames: list[Image.Image] = []
            durations: list[int] = []
            for index in range(0, total, step):
                im.seek(index)
                # 不压平透明通道：GIF 保存时会保留透明区域（动态表情多为透明底）
                frame = _fit(im.convert("RGBA"), max_side)
                frames.append(frame)
                frame_duration = int(im.info.get("duration", 100) or 100)
                # 抽帧后按步长拉长，保持总时长不变
                durations.append(max(20, frame_duration * step))
                if len(frames) >= max_frames:
                    break

            width, height = frames[0].size
            letter = _load_letter(letter_path, width, height, scale)
            out = [_paste_letter(_apply_effects(f, chromatic, glitch), letter) for f in frames]
            return out, durations

        base = _fit(ImageOps.exif_transpose(im).convert("RGBA"), max_side)

    width, height = base.size
    letter = _load_letter(letter_path, width, height, scale)
    effective_glitch = glitch if glitch > 0 else 1
    out = [
        _paste_letter(_apply_effects(base, chromatic, effective_glitch), letter)
        for _ in range(static_frames)
    ]
    return out, [static_duration] * static_frames


def save_gif(
    frames: list[Image.Image],
    path: str | Path,
    durations: int | list[int],
    loop: int = 0,
) -> None:
    """把 RGBA 帧量化成 256 色后写成循环 GIF；透明像素保留为透明。"""
    if not frames:
        raise ValueError("没有可写入的帧")

    if isinstance(durations, int):
        durations = [durations] * len(frames)
    if len(durations) != len(frames):
        durations = [durations[0] if durations else 100] * len(frames)

    paletted = [_frame_to_paletted(frame) for frame in frames]
    paletted[0].save(
        path,
        save_all=True,
        append_images=paletted[1:],
        duration=[max(20, int(d)) for d in durations],
        loop=loop,
        optimize=True,
        disposal=2,
    )
