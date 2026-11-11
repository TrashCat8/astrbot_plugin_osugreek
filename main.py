"""4K 希腊字母背景生成器（移植自 nonebot-plugin-osugreek）。

`/osugreek <字母> [色散强度] [故障强度]`（别名 `/希腊字母`）：
对附带或引用的图片做 RGB 色散 + 可选的故障特效，再把 4K 希腊字母居中贴上去，
生成 osu!mania 风格的练习图背景。

移植说明：原插件针对 NoneBot2 + OneBot v11 编写，图像处理逻辑保持不变，
仅重写入口层（指令注册、取图、配置、发图）。原项目 MIT 许可，见 LICENSE。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Reply
from astrbot.api.star import Context, Star
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .greek_ops import (
    CHROMATIC_RANGE,
    DEFAULT_CHROMATIC,
    DEFAULT_GLITCH,
    DEFAULT_LETTER_SCALE,
    DEFAULT_MAX_SIDE,
    GLITCH_RANGE,
    clamp,
    clamp_scale,
    is_animated,
    render,
    render_gif,
    save_gif,
)

PLUGIN_NAME = "astrbot_plugin_osugreek"

IMAGES_DIR = Path(__file__).resolve().parent / "images"
"""字母图片目录，随插件分发"""

CACHE_TTL_SECONDS = 3600
"""生成结果在缓存目录中的保留时间，超时在下次调用时清理"""

GIF_KEYWORDS = {"gif", "动图", "动画", "gif动图"}
"""第四个参数填这些词时，静态图会生成故障循环动图"""

DEFAULT_GIF_FRAMES = 12
"""静态图生成动图时的帧数"""

DEFAULT_GIF_DURATION = 100
"""动图每帧时长（毫秒）"""

DEFAULT_GIF_MAX_SIDE = 800
"""动图长边上限，动图比静态图更吃体积和上传时间"""

DEFAULT_GIF_MAX_FRAMES = 24
"""输入动图最多处理的帧数，超出时等间隔抽帧"""

HELP_TEXT = (
    "用法：/osugreek <希腊字母名称> [缩放] [色散强度] [故障强度] [gif]\n"
    "或：/希腊字母 <希腊字母名称> [缩放] [色散强度] [故障强度] [gif]\n"
    "需要附带一张图片，或先引用一张图片再发指令。\n"
    "参数说明：\n"
    "- 缩放：希腊字母大小系数，范围 [0.1,3.0]，1.0 为默认大小，\n"
    "  调大时字母会变大（透明边距被裁，字母本体不超过画布）\n"
    "- 色散强度：图片 RGB 通道分离程度，范围 [1,20]，不填用默认值\n"
    "- 故障强度：故障特效强度，范围 [0,5]，0 表示不加故障效果\n"
    "- gif：填 gif 或 动图 时，静态图会生成故障循环动图；\n"
    "  输入本身就是动图时会自动逐帧处理并输出动图，无需该参数\n"
    "例：/osugreek alpha 0.6 4 0、/osugreek alpha 2 6 3 gif"
)


class OsuGreekPlugin(Star):
    """在图片中央贴上 4K 希腊字母并添加色散/故障效果。

    指令：/osugreek <字母> [缩放] [色散强度] [故障强度]，别名 /希腊字母。
    图片先发后指令，或引用图片再发指令均可。
    """

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        self.cache_dir = Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self._letters: dict[str, Path] | None = None

    # ==================== 配置与素材 ====================

    def _cfg_int(self, key: str, default: int, low: int, high: int) -> int:
        """读取整数配置，脏值回落默认并夹到区间内。"""
        raw = self.config.get(key, default)
        try:
            return clamp(int(raw), low, high)
        except (TypeError, ValueError):
            return clamp(default, low, high)

    def _cfg_scale(self, key: str, default: float = DEFAULT_LETTER_SCALE) -> float:
        """读取缩放系数配置，脏值回落默认并夹到区间内。"""
        raw = self.config.get(key, default)
        try:
            return clamp_scale(float(raw))
        except (TypeError, ValueError):
            return clamp_scale(default)

    def _letter_map(self) -> dict[str, Path]:
        """字母名（小写）-> 图片路径，懒加载并缓存。"""
        if self._letters is None:
            self._letters = {p.stem.lower(): p for p in IMAGES_DIR.glob("*.png")}
        return self._letters

    def _letter_names(self) -> list[str]:
        return sorted(self._letter_map().values(), key=lambda p: p.stem)

    # ==================== 取图 ====================

    @staticmethod
    def _find_image(event: AstrMessageEvent) -> Image | None:
        """优先取本条消息里的图片，其次取被引用消息里的图片。

        aiocqhttp 适配器会把被引用的消息展开成 Reply(chain=[...])，
        所以引用取图不用自己调 get_msg。
        """
        messages = event.get_messages()
        for comp in messages:
            if isinstance(comp, Image):
                return comp
        for comp in messages:
            if isinstance(comp, Reply) and comp.chain:
                for sub in comp.chain:
                    if isinstance(sub, Image):
                        return sub
        return None

    # ==================== 缓存 ====================

    def _cleanup_cache(self) -> None:
        """清理过期的生成结果，避免缓存目录无限增长。"""
        now = time.time()
        for path in list(self.cache_dir.glob("*.png")) + list(self.cache_dir.glob("*.gif")):
            try:
                if now - path.stat().st_mtime > CACHE_TTL_SECONDS:
                    path.unlink()
            except OSError:
                pass

    def _cache_path(self, suffix: str = ".png") -> Path:
        return self.cache_dir / f"osugreek_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}{suffix}"

    # ==================== 指令 ====================

    @filter.command("osugreek", alias={"希腊字母"})
    async def osugreek(
        self,
        event: AstrMessageEvent,
        greek_name: str = "",
        size: str = "",
        chromatic: str = "",
        glitch: str = "",
        gif: str = "",
    ):
        """在图片中央贴上希腊字母：/osugreek <字母> [缩放] [色散强度] [故障强度] [gif]"""
        name = (greek_name or "").strip()
        if not name or name.lower() == "help":
            names = ", ".join(p.stem for p in self._letter_names())
            yield event.plain_result(f"{HELP_TEXT}\n\n可用的字母：{names}")
            return

        chromatic_value = self._parse_int(
            chromatic,
            self._cfg_int("chromatic_intensity", DEFAULT_CHROMATIC, *CHROMATIC_RANGE),
            *CHROMATIC_RANGE,
        )
        glitch_value = self._parse_int(
            glitch,
            self._cfg_int("glitch_intensity", DEFAULT_GLITCH, *GLITCH_RANGE),
            *GLITCH_RANGE,
        )

        letter_path = self._letter_map().get(name.lower())
        if letter_path is None:
            names = ", ".join(p.stem for p in self._letter_names())
            yield event.plain_result(f"未找到 {name}.png\n可用的字母：{names}")
            return

        image_comp = self._find_image(event)
        if image_comp is None:
            yield event.plain_result("请附带一张图片，或引用一张图片后再发指令")
            return

        try:
            source = await image_comp.convert_to_file_path()
        except Exception as exc:  # noqa: BLE001 —— 来源可能是损坏的 URL / base64
            logger.error(f"[{PLUGIN_NAME}] 获取原图失败: {exc}")
            yield event.plain_result("图片下载失败，请换一张图重试")
            return

        max_side = self._cfg_int("max_input_side", DEFAULT_MAX_SIDE, 0, 8192)
        # 参数顺序上缩放/强度都能省略，gif 关键词落在哪个位置都认
        gif_flag = any(
            (value or "").strip().lower() in GIF_KEYWORDS
            for value in (gif, size, chromatic, glitch)
        )
        scale_value = self._parse_scale(size, self._cfg_scale("letter_scale"))
        want_gif = is_animated(source) or gif_flag

        self._cleanup_cache()
        try:
            if want_gif:
                output_path = self._cache_path(".gif")
                frames, durations = await asyncio.to_thread(
                    render_gif,
                    source,
                    letter_path,
                    chromatic=chromatic_value,
                    glitch=glitch_value,
                    max_side=self._cfg_int("gif_max_side", DEFAULT_GIF_MAX_SIDE, 100, 2000),
                    static_frames=self._cfg_int("gif_frames", DEFAULT_GIF_FRAMES, 2, 30),
                    static_duration=self._cfg_int("gif_duration", DEFAULT_GIF_DURATION, 20, 500),
                    max_frames=self._cfg_int("gif_max_frames", DEFAULT_GIF_MAX_FRAMES, 2, 60),
                    scale=scale_value,
                )
                await asyncio.to_thread(save_gif, frames, output_path, durations)
                for frame in frames:
                    frame.close()
            else:
                output_path = self._cache_path(".png")
                result = await asyncio.to_thread(
                    render,
                    source,
                    letter_path,
                    chromatic_value,
                    glitch_value,
                    max_side,
                    scale_value,
                )
                result.save(output_path, format="PNG")
                result.close()
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[{PLUGIN_NAME}] 图片处理失败: {exc}", exc_info=True)
            yield event.plain_result(f"图片处理失败：{exc}")
            return

        yield event.image_result(str(output_path))

    # ==================== 工具 ====================

    @staticmethod
    def _parse_scale(raw: str, default: float) -> float:
        """解析字母名后面那个缩放系数；非数字（含省略）用配置默认值。"""
        text = (raw or "").strip()
        try:
            return clamp_scale(float(text))
        except ValueError:
            return clamp_scale(default)

    @staticmethod
    def _parse_int(raw: str, default: int, low: int, high: int) -> int:
        """解析强度参数；空值用默认，非数字忽略（沿用原插件行为）。"""
        text = (raw or "").strip()
        if not text:
            return clamp(default, low, high)
        try:
            return clamp(int(text), low, high)
        except ValueError:
            return clamp(default, low, high)
