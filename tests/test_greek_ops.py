"""离线单测：只依赖 Pillow，不导入 astrbot。

    python tests/test_greek_ops.py
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageDraw

PLUGIN_DIR = Path(__file__).resolve().parents[1]
IMAGES_DIR = PLUGIN_DIR / "images"
sys.path.insert(0, str(PLUGIN_DIR))

import greek_ops  # noqa: E402

passed = 0
failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"[OK]   {name}")
    else:
        failed += 1
        print(f"[FAIL] {name} {detail}")


def gradient(width: int = 640, height: int = 480) -> Image.Image:
    """生成一张有纹理的测试底图（纯色会让色散差异不明显）。"""
    img = Image.new("RGB", (width, height))
    pixels = img.load()
    for y in range(height):
        for x in range(width):
            pixels[x, y] = (x % 256, y % 256, (x + y) % 256)
    return img


def diff_pixels(a: Image.Image, b: Image.Image) -> int:
    """统计两张同尺寸 RGBA 图有多少像素不同。"""
    if a.size != b.size:
        return -1
    pa, pb = a.load(), b.load()
    count = 0
    for y in range(a.height):
        for x in range(a.width):
            if pa[x, y] != pb[x, y]:
                count += 1
    return count


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="osugreek_test_"))
    source = tmp / "src.png"
    gradient().save(source)

    # ---------- 参数夹取 ----------
    check("clamp 上界", greek_ops.clamp(999, 1, 20) == 20)
    check("clamp 下界", greek_ops.clamp(-5, 1, 20) == 1)
    check("clamp 区间内", greek_ops.clamp(7, 1, 20) == 7)

    # ---------- 色散 ----------
    base = greek_ops.open_rgba(source)
    for intensity in (1, 4, 20, 999):
        out = greek_ops.add_chromatic_aberration(base, intensity)
        check(f"色散强度 {intensity} 尺寸不变", out.size == base.size)
        check(f"色散强度 {intensity} 模式 RGBA", out.mode == "RGBA")
    check(
        "色散强度 999 与 20 结果一致（夹取生效）",
        list(greek_ops.add_chromatic_aberration(base, 999).getdata())
        == list(greek_ops.add_chromatic_aberration(base, 20).getdata()),
    )
    check("色散确实改变了像素", diff_pixels(base, greek_ops.add_chromatic_aberration(base, 4)) > 0)

    # ---------- 故障 ----------
    for intensity in range(0, 6):
        out = greek_ops.add_glitch_effect(base, intensity)
        check(f"故障强度 {intensity} 尺寸不变", out.size == base.size)
        check(f"故障强度 {intensity} 模式 RGBA", out.mode == "RGBA")
    check(
        "故障强度 0 等于原图",
        diff_pixels(base, greek_ops.add_glitch_effect(base, 0)) == 0,
    )
    check(
        "故障强度 5 确实改变了像素",
        diff_pixels(base, greek_ops.add_glitch_effect(base, 5)) > 0,
    )

    # ---------- 字母叠加位置 ----------
    letter = Image.new("RGBA", (1000, 1000), (0, 0, 0, 0))
    red = Image.new("RGBA", (200, 200), (255, 0, 0, 255))
    letter.paste(red, (400, 400))
    letter_path = tmp / "red.png"
    letter.save(letter_path)

    out = greek_ops.render(source, letter_path, chromatic=4, glitch=0)
    check("完整流程尺寸不变", out.size == base.size)
    check("字母叠加后的中心像素是红色", out.getpixel((out.width // 2, out.height // 2)) == (255, 0, 0, 255))
    check("字母叠加后的角落不是红色", out.getpixel((5, 5)) != (255, 0, 0, 255))
    check("叠加确实产生差异", diff_pixels(base, out) > 0)

    # ---------- 输入缩放上限 ----------
    big = tmp / "big.png"
    gradient(2000, 1000).save(big)
    limited = greek_ops.render(big, IMAGES_DIR / "alpha.png", chromatic=4, glitch=0, max_side=1000)
    check("超过长边上限时缩小", limited.size == (1000, 500), f"实际 {limited.size}")
    unlimited = greek_ops.render(big, IMAGES_DIR / "alpha.png", chromatic=4, glitch=0, max_side=0)
    check("max_side=0 不缩放", unlimited.size == (2000, 1000), f"实际 {unlimited.size}")

    # ---------- 透明背景（回归：曾出现满屏彩色噪点） ----------
    trans_src = tmp / "trans.png"
    trans = Image.new("RGBA", (400, 300), (0, 0, 0, 0))
    ImageDraw.Draw(trans).ellipse((150, 90, 250, 210), fill=(200, 80, 40, 255))
    trans.save(trans_src)
    trans_out = greek_ops.render(trans_src, IMAGES_DIR / "alpha.png", chromatic=4, glitch=3)
    corners = [(0, 0), (399, 0), (0, 299), (399, 299)]
    check(
        "透明底四角保持透明（故障不会在透明区生成彩色噪点）",
        all(trans_out.getpixel(p)[3] == 0 for p in corners),
        str([trans_out.getpixel(p)[3] for p in corners]),
    )
    check("透明底不透明区域仍不透明", trans_out.getpixel((200, 150))[3] == 255)
    effected = greek_ops._apply_effects(greek_ops.open_rgba(trans_src), 4, 3)
    row = [effected.getpixel((x, 150))[3] for x in range(152, 248)]
    check(
        "透明底位移不会在不透明区域挖出透明缺口",
        all(a == 255 for a in row),
        str(row[:10]),
    )

    blank_src = tmp / "blank.png"
    Image.new("RGBA", (64, 64), (0, 0, 0, 0)).save(blank_src)
    blank_out = greek_ops.render(blank_src, IMAGES_DIR / "alpha.png", chromatic=4, glitch=4)
    check("全透明图不崩溃且四角仍透明", all(blank_out.getpixel(p)[3] == 0 for p in [(0, 0), (63, 0), (0, 63), (63, 63)]))

    # ---------- 小尺寸图（回归：曾因强制最小 200px 导致字母被裁） ----------
    small_src = tmp / "small.png"
    Image.new("RGB", (120, 90), (30, 60, 90)).save(small_src)
    letter_small = greek_ops._load_letter(IMAGES_DIR / "alpha.png", 120, 90)
    check(
        "小图字母完整落在画布内",
        letter_small.width <= 120 and letter_small.height <= 90,
        str(letter_small.size),
    )
    small_out = greek_ops.render(small_src, IMAGES_DIR / "alpha.png", chromatic=4, glitch=0)
    check("小图输出尺寸不变", small_out.size == (120, 90), str(small_out.size))
    wide_letter = greek_ops._load_letter(IMAGES_DIR / "alpha.png", 900, 60)
    check(
        "超宽画布字母不超边界",
        wide_letter.width <= 900 and wide_letter.height <= 60,
        str(wide_letter.size),
    )

    # ---------- 字母缩放系数 ----------
    base_letter = greek_ops._load_letter(IMAGES_DIR / "alpha.png", 640, 480)
    half_letter = greek_ops._load_letter(IMAGES_DIR / "alpha.png", 640, 480, 0.5)
    check(
        "缩放 0.5 时字母宽度按 0.5 倍计算",
        abs(half_letter.width - int(480 * 1.8 * 0.5)) <= 1,
        f"{half_letter.size} vs {base_letter.size}",
    )
    big_letter = greek_ops._load_letter(IMAGES_DIR / "alpha.png", 640, 480, 2.0)
    check(
        "缩放 2.0 时字母图变大",
        big_letter.width > base_letter.width,
        f"{big_letter.size} vs {base_letter.size}",
    )
    content = big_letter.getchannel("A").getbbox()
    check(
        "放大后字母本体仍在画布内",
        content[2] - content[0] <= 640 and content[3] - content[1] <= 480,
        str(content),
    )
    check("缩放系数上限 3.0", greek_ops.clamp_scale(9.0) == 3.0)
    check("缩放系数下限 0.1", greek_ops.clamp_scale(0.001) == 0.1)
    scaled_out = greek_ops.render(small_src, IMAGES_DIR / "alpha.png", chromatic=4, glitch=0, scale=0.5)
    check("缩小后输出尺寸仍不变", scaled_out.size == (120, 90), str(scaled_out.size))
    huge_out = greek_ops.render(small_src, IMAGES_DIR / "alpha.png", chromatic=4, glitch=0, scale=3.0)
    check("放大后输出尺寸仍不变", huge_out.size == (120, 90), str(huge_out.size))

    # ---------- 静态图生成动图 ----------
    frames, durations = greek_ops.render_gif(
        source,
        IMAGES_DIR / "alpha.png",
        chromatic=4,
        glitch=0,
        max_side=640,
        static_frames=8,
        static_duration=80,
    )
    check("静态图生成指定帧数", len(frames) == 8, f"实际 {len(frames)}")
    check("静态图动图帧时长正确", durations == [80] * 8, str(durations[:3]))
    check("动图遵守长边上限", frames[0].size == (640, 480), str(frames[0].size))
    check(
        "静态图动图各帧不完全相同（故障强度 0 自动按 1 处理）",
        diff_pixels(frames[0], frames[-1]) > 0,
    )

    gif_path = tmp / "static.gif"
    greek_ops.save_gif(frames, gif_path, durations)
    with Image.open(gif_path) as saved:
        check("保存后的 GIF 是动图", bool(getattr(saved, "is_animated", False)))
        check("保存后的 GIF 帧数正确", saved.n_frames == 8, f"实际 {saved.n_frames}")

    # ---------- 输入动图逐帧处理 ----------
    anim_src = tmp / "anim_src.gif"
    anim_frames = []
    for i in range(12):
        frame = Image.new("RGB", (200, 100), (i * 20, 255 - i * 20, 128))
        anim_frames.append(frame)
    anim_frames[0].save(
        anim_src,
        save_all=True,
        append_images=anim_frames[1:],
        duration=50,
        loop=0,
    )
    check("is_animated 识别动图", greek_ops.is_animated(anim_src))
    check("is_animated 不误判静态图", not greek_ops.is_animated(source))

    frames, durations = greek_ops.render_gif(
        anim_src,
        IMAGES_DIR / "beta.png",
        chromatic=4,
        glitch=2,
        max_side=200,
        max_frames=6,
    )
    check("动图按上限抽帧", len(frames) == 6, f"实际 {len(frames)}")
    check("抽帧后帧时长按步长放大", durations == [100] * 6, str(durations))
    check("动图帧尺寸不变", frames[0].size == (200, 100), str(frames[0].size))

    # ---------- 透明动图（回归：曾把透明帧压成黑底） ----------
    trans_anim = tmp / "trans_anim.gif"
    trans_anim_frames = []
    for i in range(3):
        frame = Image.new("RGBA", (80, 60), (0, 0, 0, 0))
        ImageDraw.Draw(frame).ellipse((10, 10 + i * 5, 40, 40 + i * 5), fill=(255, 200, 0, 255))
        trans_anim_frames.append(frame)
    trans_anim_frames[0].save(
        trans_anim, save_all=True, append_images=trans_anim_frames[1:], duration=80, loop=0, disposal=2
    )
    anim_out, anim_durations = greek_ops.render_gif(
        trans_anim, IMAGES_DIR / "alpha.png", chromatic=4, glitch=1, max_side=200
    )
    check(
        "透明动图帧保留透明像素",
        anim_out[0].getpixel((0, 0))[3] == 0,
        str(anim_out[0].getpixel((0, 0))),
    )
    anim_gif = tmp / "trans_anim_out.gif"
    greek_ops.save_gif(anim_out, anim_gif, anim_durations)
    with Image.open(anim_gif) as saved:
        check("透明动图保存后仍透明", saved.convert("RGBA").getpixel((0, 0))[3] == 0)

    static_gif = tmp / "trans_static_out.gif"
    static_trans_frames, static_trans_durations = greek_ops.render_gif(
        trans_src, IMAGES_DIR / "alpha.png", chromatic=4, glitch=1, static_frames=4
    )
    greek_ops.save_gif(static_trans_frames, static_gif, static_trans_durations)
    with Image.open(static_gif) as saved:
        check("透明静态图生成的动图仍透明", saved.convert("RGBA").getpixel((0, 0))[3] == 0)

    # ---------- 全部素材可用 ----------
    letters = sorted(IMAGES_DIR.glob("*.png"))
    check("字母图数量为 76", len(letters) == 76, f"实际 {len(letters)}")
    bad: list[str] = []
    for path in letters:
        try:
            with Image.open(path) as img:
                img.convert("RGBA").resize((200, 200))
        except Exception as exc:  # noqa: BLE001
            bad.append(f"{path.name}: {exc}")
    check("76 张字母图都能正常打开", not bad, "; ".join(bad[:3]))

    # ---------- 性能 ----------
    large = tmp / "large.png"
    gradient(1920, 1080).save(large)
    started = time.perf_counter()
    greek_ops.render(large, IMAGES_DIR / "alpha.png", chromatic=8, glitch=3)
    elapsed = time.perf_counter() - started
    print(f"      1920x1080 色散8+故障3 耗时 {elapsed:.2f}s")
    check("1080p 处理耗时在 10 秒内", elapsed < 10, f"{elapsed:.2f}s")

    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
