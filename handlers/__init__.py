"""MineSentinel command handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = ["MineSentinelCommandHandler"]

if TYPE_CHECKING:
    from .mine_sentinel_commands import MineSentinelCommandHandler


def __getattr__(name: str):
    if name == "MineSentinelCommandHandler":
        from .mine_sentinel_commands import MineSentinelCommandHandler

        return MineSentinelCommandHandler
    raise AttributeError(name)
