"""Report rendering result models."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path


@dataclass
class MineSentinelRenderedReport:
    text: str
    image: BytesIO | None = None
    report_file: Path | None = None

    def __str__(self) -> str:
        return self.text

    def __contains__(self, value: str) -> bool:
        return value in self.text
