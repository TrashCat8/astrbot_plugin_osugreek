"""冒烟测试：用假事件把指令跑一遍，验证文案与出图。

需要 AstrBot 环境（本机或容器内）：

    ASTRBOT_ROOT=<临时目录> python tests/smoke_handlers.py

ASTRBOT_ROOT 必须指向临时目录，否则会把测试数据写进线上 plugin_data。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

# 必须在导入 astrbot 之前设置，避免污染线上数据
os.environ["ASTRBOT_ROOT"] = tempfile.mkdtemp(prefix="astrbot_osugreek_smoke_")

PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR.parent))

from PIL import Image as PILImage  # noqa: E402

from astrbot.api.message_components import Image, Reply  # noqa: E402
from astrbot_plugin_osugreek.main import OsuGreekPlugin  # noqa: E402

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


class FakeResult:
    def __init__(self, kind: str, value: str) -> None:
        self.kind = kind
        self.value = value


class FakeEvent:
    def __init__(self, components: list | None = None) -> None:
        self.components = components or []

    def get_messages(self) -> list:
        return self.components

    def plain_result(self, text: str) -> FakeResult:
        return FakeResult("text", text)

    def image_result(self, url_or_path: str) -> FakeResult:
        return FakeResult("image", url_or_path)


async def run(handler, event: FakeEvent, **kwargs) -> list[FakeResult]:
    return [item async for item in handler(event, **kwargs)]


async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="osugreek_smoke_assets_"))
    source = tmp / "src.png"
    PILImage.new("RGB", (640, 480), (40, 80, 120)).save(source)

    plugin = OsuGreekPlugin(context=None, config={"chromatic_intensity": 4, "glitch_intensity": 0})
    check("插件实例化成功", plugin.cache_dir.exists(), str(plugin.cache_dir))

    # 1) 无参数 -> 帮助
    out = await run(plugin.osugreek, FakeEvent())
    check("无参数返回帮助", len(out) == 1 and out[0].kind == "text" and "用法" in out[0].value)

    # 2) help -> 帮助
    out = await run(plugin.osugreek, FakeEvent(), greek_name="help")
    check("help 返回帮助", len(out) == 1 and out[0].kind == "text" and "可用的字母" in out[0].value)

    # 3) 未知字母 -> 提示
    out = await run(plugin.osugreek, FakeEvent(), greek_name="not_a_letter")
    check("未知字母给出提示", len(out) == 1 and out[0].kind == "text" and "未找到" in out[0].value)

    # 4) 缺图片 -> 提示
    out = await run(plugin.osugreek, FakeEvent(), greek_name="alpha")
    check("缺图片给出提示", len(out) == 1 and out[0].kind == "text" and "附带一张图片" in out[0].value)

    # 5) 同消息带图
    event = FakeEvent([Image(file=str(source))])
    out = await run(plugin.osugreek, event, greek_name="alpha")
    ok_image = len(out) == 1 and out[0].kind == "image"
    check("同消息带图能生成图片", ok_image, str(out[0].value) if out else "无输出")
    if ok_image:
        out_path = Path(out[0].value)
        check("生成的文件存在", out_path.exists(), str(out_path))
        with PILImage.open(out_path) as result:
            check("输出尺寸与原图一致", result.size == (640, 480), str(result.size))
        check("输出位于 plugin_data 缓存目录", "plugin_data" in str(out_path), str(out_path))

    # 5.5) 透明底图（回归：曾出现满屏彩色噪点）
    trans_src = tmp / "trans.png"
    PILImage.new("RGBA", (400, 300), (0, 0, 0, 0)).save(trans_src)
    event = FakeEvent([Image(file=str(trans_src))])
    out = await run(plugin.osugreek, event, greek_name="alpha", glitch="3")
    ok_image = len(out) == 1 and out[0].kind == "image"
    check("透明底图能生成图片", ok_image, str(out[0].value) if out else "无输出")
    if ok_image:
        with PILImage.open(out[0].value) as result:
            corners = [result.getpixel(p)[3] for p in [(0, 0), (399, 0), (0, 299), (399, 299)]]
            check("透明底输出四角仍透明", corners == [0, 0, 0, 0], str(corners))

    # 5.6) 小尺寸图（回归：曾因强制最小 200px 导致字母被裁）
    small_src = tmp / "small.png"
    PILImage.new("RGB", (120, 90), (30, 60, 90)).save(small_src)
    event = FakeEvent([Image(file=str(small_src))])
    out = await run(plugin.osugreek, event, greek_name="alpha")
    ok_image = len(out) == 1 and out[0].kind == "image"
    check("小尺寸图能生成图片", ok_image, str(out[0].value) if out else "无输出")
    if ok_image:
        with PILImage.open(out[0].value) as result:
            check("小尺寸图输出尺寸不变", result.size == (120, 90), str(result.size))

    # 6) 引用图片
    event = FakeEvent([Reply(id=123, chain=[Image(file=str(source))])])
    out = await run(plugin.osugreek, event, greek_name="beta")
    check("引用图片能生成图片", len(out) == 1 and out[0].kind == "image" and Path(out[0].value).exists())

    # 7) 大小写与强度参数
    event = FakeEvent([Image(file=str(source))])
    out = await run(plugin.osugreek, event, greek_name="ALPHA", chromatic="999", glitch="999")
    check("大写字母名与超界强度可处理", len(out) == 1 and out[0].kind == "image")

    # 8) 非数字强度回落到配置默认值
    event = FakeEvent([Image(file=str(source))])
    out = await run(plugin.osugreek, event, greek_name="alpha", chromatic="abc", glitch="x")
    check("非数字强度不报错", len(out) == 1 and out[0].kind == "image")

    # 动图测试用有纹理的底图：纯色图在低强度故障下各帧完全相同，Pillow 会合并成单帧
    textured = tmp / "textured.png"
    PILImage.radial_gradient("L").resize((640, 480)).convert("RGB").save(textured)

    # 8.5) 缩放参数：紧跟在字母名后面
    event = FakeEvent([Image(file=str(source))])
    out = await run(plugin.osugreek, event, greek_name="alpha", size="0.5", chromatic="4")
    check(
        "缩放参数紧跟字母名能生成图片",
        len(out) == 1 and out[0].kind == "image",
        str(out[0].value) if out else "无输出",
    )
    event = FakeEvent([Image(file=str(textured))])
    out = await run(plugin.osugreek, event, greek_name="alpha", size="0.5", gif="gif")
    check(
        "gif 与缩放参数同时生效",
        len(out) == 1 and str(out[0].value).endswith(".gif"),
        str(out[0].value) if out else "无输出",
    )
    event = FakeEvent([Image(file=str(source))])
    out = await run(plugin.osugreek, event, greek_name="alpha", size="9", gif="abc")
    check(
        "缩放超界与脏值不报错",
        len(out) == 1 and out[0].kind == "image",
        str(out[0].value) if out else "无输出",
    )
    event = FakeEvent([Image(file=str(source))])
    out = await run(plugin.osugreek, event, greek_name="alpha", size="abc")
    check(
        "缩放脏值回落默认",
        len(out) == 1 and out[0].kind == "image",
        str(out[0].value) if out else "无输出",
    )

    # 9) 静态图 + gif 参数 -> 动图
    event = FakeEvent([Image(file=str(textured))])
    out = await run(plugin.osugreek, event, greek_name="alpha", gif="gif")
    ok_gif = len(out) == 1 and out[0].kind == "image" and str(out[0].value).endswith(".gif")
    check("静态图 + gif 生成动图", ok_gif, str(out[0].value) if out else "无输出")
    if ok_gif:
        with PILImage.open(out[0].value) as gif:
            check("静态图动图帧数为默认 12", gif.n_frames == 12, f"实际 {gif.n_frames}")

    # 10) 中文关键词「动图」
    event = FakeEvent([Image(file=str(textured))])
    out = await run(plugin.osugreek, event, greek_name="alpha", gif="动图")
    check("中文关键词动图生效", len(out) == 1 and str(out[0].value).endswith(".gif"))

    # 10.5) 省略强度、直接写 gif 关键词也能识别
    event = FakeEvent([Image(file=str(textured))])
    out = await run(plugin.osugreek, event, greek_name="alpha", chromatic="gif")
    check("省略强度直接写 gif 也生效", len(out) == 1 and str(out[0].value).endswith(".gif"))

    # 11) 输入动图自动输出动图
    anim_src = tmp / "anim.gif"
    anim_frames = [PILImage.new("RGB", (240, 140), (i * 15, 60, 200 - i * 10)) for i in range(10)]
    anim_frames[0].save(anim_src, save_all=True, append_images=anim_frames[1:], duration=60, loop=0)
    event = FakeEvent([Image(file=str(anim_src))])
    out = await run(plugin.osugreek, event, greek_name="gamma")
    ok_gif = len(out) == 1 and str(out[0].value).endswith(".gif")
    check("输入动图无需参数即输出动图", ok_gif, str(out[0].value) if out else "无输出")
    if ok_gif:
        with PILImage.open(out[0].value) as gif:
            check("输入动图帧数保留", gif.n_frames == 10, f"实际 {gif.n_frames}")

    # 11.5) 透明底动图输出仍然透明（回归：曾压成黑底）
    trans_anim = tmp / "trans_anim.gif"
    trans_anim_frames = []
    for i in range(3):
        frame = PILImage.new("RGBA", (80, 60), (0, 0, 0, 0))
        frame.paste(PILImage.new("RGBA", (30, 30), (255, 200, 0, 255)), (10, 10 + i * 5))
        trans_anim_frames.append(frame)
    trans_anim_frames[0].save(
        trans_anim,
        save_all=True,
        append_images=trans_anim_frames[1:],
        duration=80,
        loop=0,
        disposal=2,
    )
    event = FakeEvent([Image(file=str(trans_anim))])
    out = await run(plugin.osugreek, event, greek_name="alpha")
    ok_gif = len(out) == 1 and str(out[0].value).endswith(".gif")
    check("透明底动图能生成动图", ok_gif, str(out[0].value) if out else "无输出")
    if ok_gif:
        with PILImage.open(out[0].value) as gif:
            corner = gif.convert("RGBA").getpixel((0, 0))
            check("透明底动图输出仍透明", corner[3] == 0, str(corner))

    # 12) 缓存过期清理
    stale = plugin.cache_dir / "stale.png"
    PILImage.new("RGB", (10, 10)).save(stale)
    old = time.time() - 7200
    os.utime(stale, (old, old))
    event = FakeEvent([Image(file=str(source))])
    await run(plugin.osugreek, event, greek_name="alpha")
    check("过期缓存被清理", not stale.exists())

    # 13) 损坏图片 -> 友好提示而非崩溃
    broken = tmp / "broken.png"
    broken.write_bytes(b"not an image")
    event = FakeEvent([Image(file=str(broken))])
    out = await run(plugin.osugreek, event, greek_name="alpha")
    check("坏图给出处理失败提示", len(out) == 1 and out[0].kind == "text" and "失败" in out[0].value)

    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
