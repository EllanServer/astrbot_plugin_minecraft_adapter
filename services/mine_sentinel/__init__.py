"""MineSentinel read-only observation service."""

from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = ["MineSentinelService"]

if TYPE_CHECKING:
    from .service import MineSentinelService


def __getattr__(name: str):
    if name == "MineSentinelService":
        from .service import MineSentinelService

        return MineSentinelService
    raise AttributeError(name)
