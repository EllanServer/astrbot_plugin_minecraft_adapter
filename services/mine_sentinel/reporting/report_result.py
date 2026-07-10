"""Report rendering result models."""

from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path


@dataclass
class MineSentinelRenderedReport:
    text: str
    image: BytesIO | None = None
    report_file: Path | None = None
    images: list[BytesIO] = field(default_factory=list)

    def __post_init__(self):
        if self.images and self.image is None:
            self.image = self.images[0]
        elif self.image is not None and not self.images:
            self.images = [self.image]

    def __str__(self) -> str:
        return self.text

    def __contains__(self, value: str) -> bool:
        return value in self.text
