"""Font discovery and disk cache for rendered cards."""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import ImageFont

try:
    from astrbot.api import logger
except ModuleNotFoundError:  # pragma: no cover - used by standalone unit tests.
    logger = logging.getLogger(__name__)

try:
    import aiohttp
except Exception:  # pragma: no cover - minimal test environments may omit aiohttp.
    aiohttp = None


DEFAULT_FONT_FILENAME = "LXGWWenKaiGB-Regular.ttf"
DEFAULT_FONT_URLS = [
    "https://ghproxy.net/https://raw.githubusercontent.com/lxgw/LxgwWenkaiGB/main/fonts/TTF/LXGWWenKaiGB-Regular.ttf",
    "https://jsd.cdn.zzko.cn/gh/lxgw/LxgwWenkaiGB@main/fonts/TTF/LXGWWenKaiGB-Regular.ttf",
    "https://raw.githubusercontent.com/lxgw/LxgwWenkaiGB/main/fonts/TTF/LXGWWenKaiGB-Regular.ttf",
    "https://cdn.jsdelivr.net/gh/lxgw/LxgwWenkaiGB@main/fonts/TTF/LXGWWenKaiGB-Regular.ttf",
]


class FontProvider:
    def __init__(
        self,
        font_dir: Path,
        filename: str = DEFAULT_FONT_FILENAME,
        urls: list[str] | None = None,
    ):
        self.font_dir = font_dir
        self.font_path = font_dir / filename
        self.urls = urls or DEFAULT_FONT_URLS

    async def ensure_cached(self):
        self.font_dir.mkdir(parents=True, exist_ok=True)
        if self.font_path.exists() and self.font_path.stat().st_size > 0:
            return
        if aiohttp is None:
            logger.warning("[Renderer] aiohttp 不可用，跳过字体下载")
            return

        timeout = aiohttp.ClientTimeout(total=60)
        for url in self.urls:
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url) as resp:
                        if resp.status != 200:
                            continue
                        data = await resp.read()
                if len(data) < 100 * 1024:
                    continue
                self.font_path.write_bytes(data)
                logger.info(
                    f"[Renderer] 已缓存中文字体: {self.font_path.name} ({len(data) // 1024}KB)"
                )
                return
            except Exception as exc:
                logger.debug(f"[Renderer] 字体下载失败: {url} -> {exc}")
        logger.warning("[Renderer] 字体下载失败，将回退到系统字体")

    def font(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        try:
            if self.font_path.exists():
                return ImageFont.truetype(str(self.font_path), size=size)
        except Exception as exc:
            logger.debug(f"[Renderer] 加载已缓存字体失败: {exc}")

        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path

            custom_font = Path(get_astrbot_data_path()) / "font.ttf"
            if custom_font.exists():
                return ImageFont.truetype(str(custom_font), size=size)
        except Exception:
            pass

        for font_name in (
            "msyh.ttc",
            "simsun.ttc",
            "NotoSansCJK-Regular.ttc",
            "wqy-microhei.ttc",
            "PingFang.ttc",
            "DroidSansFallback.ttf",
        ):
            try:
                return ImageFont.truetype(font_name, size=size)
            except Exception:
                continue

        if not self.font_path.exists():
            logger.warning(
                f"[Renderer] 无法加载中文字体，渲染结果可能乱码。请确保网络畅通以自动下载字体，或手动放置字体文件到: {self.font_path}"
            )
        return ImageFont.load_default()
