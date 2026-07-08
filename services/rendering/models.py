"""Shared rendering models."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO


@dataclass
class RenderResult:
    content: str | BytesIO
    is_image: bool

    @property
    def text(self) -> str:
        if self.is_image:
            raise ValueError("无法从图片内容中获取文本")
        return str(self.content)

    @property
    def image(self) -> BytesIO:
        if not self.is_image:
            raise ValueError("无法从文本内容中获取图片")
        return self.content  # type: ignore[return-value]
