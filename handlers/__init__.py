"""Minecraft adapter command handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = ["CommandHandler"]

if TYPE_CHECKING:
    from .commands import CommandHandler


def __getattr__(name: str):
    if name == "CommandHandler":
        from .commands import CommandHandler

        return CommandHandler
    raise AttributeError(name)
