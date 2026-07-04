"""MineSentinel report delivery helpers."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain

from ..session_targets import resolve_astrbot_session


class MineSentinelDelivery:
    def __init__(self, context: Any):
        self.context = context
        self.last_error = ""

    async def send_message(
        self,
        umo: str,
        text: str,
        file_path: Path | None = None,
    ) -> bool:
        umo = self.resolve_session(umo)
        try:
            await self.context.send_message(umo, MessageChain([Plain(text=text)]))
            if file_path:
                await self.send_file(umo, file_path)
            return True
        except Exception as exc:
            self.last_error = f"发送报告到 {umo} 失败: {exc}"
            logger.error(f"[MineSentinel] {self.last_error}")
            return False

    async def send_report(
        self,
        umo: str,
        text: str,
        image: BytesIO | None = None,
        file_path: Path | None = None,
    ) -> bool:
        if image is not None:
            if await self.send_image(umo, image):
                if file_path:
                    await self.send_file(umo, file_path)
                return True
            logger.warning(f"[MineSentinel] 图片报告发送失败，回退文本: {umo}")
        return await self.send_message(umo, text, file_path)

    async def send_image(self, umo: str, image: BytesIO) -> bool:
        umo = self.resolve_session(umo)
        try:
            from astrbot.api.message_components import Image
        except Exception as exc:
            self.last_error = f"当前 AstrBot 不支持 Image 组件: {exc}"
            logger.debug(f"[MineSentinel] {self.last_error}")
            return False
        try:
            component = Image.fromBytes(image.getvalue())
            await self.context.send_message(umo, MessageChain([component]))
            return True
        except Exception as exc:
            self.last_error = f"发送图片报告到 {umo} 失败: {exc}"
            logger.error(f"[MineSentinel] {self.last_error}")
            return False

    async def send_file(self, umo: str, file_path: Path) -> bool:
        umo = self.resolve_session(umo)
        if not file_path.exists():
            logger.warning(f"[MineSentinel] 完整 observation 文件不存在: {file_path}")
            return False
        try:
            from astrbot.api.message_components import File
        except Exception as exc:
            logger.debug(f"[MineSentinel] 当前 AstrBot 不支持 File 组件: {exc}")
            return False
        try:
            component = File(file=str(file_path))
        except TypeError:
            component = File(str(file_path))
        try:
            await self.context.send_message(umo, MessageChain([component]))
            return True
        except Exception as exc:
            self.last_error = f"发送完整 observation 文件到 {umo} 失败: {exc}"
            logger.error(f"[MineSentinel] {self.last_error}")
            return False

    def resolve_session(self, umo: str) -> str:
        return resolve_astrbot_session(self.context, umo)
