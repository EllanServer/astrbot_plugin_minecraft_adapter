"""MineSentinel runtime-log audit plugin for AstrBot."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .main import MineSentinelPlugin

__all__ = ["MineSentinelPlugin"]


def __getattr__(name: str):
    if name == "MineSentinelPlugin":
        from .main import MineSentinelPlugin

        return MineSentinelPlugin
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
