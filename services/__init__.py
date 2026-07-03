"""Minecraft adapter services.

Keep this package initializer light so focused modules can be imported without
pulling AstrBot runtime dependencies into tests or tooling.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = ["BindingService", "MessageBridge", "InfoRenderer"]

if TYPE_CHECKING:
    from .binding import BindingService
    from .message_bridge import MessageBridge
    from .renderer import InfoRenderer


def __getattr__(name: str):
    if name == "BindingService":
        from .binding import BindingService

        return BindingService
    if name == "MessageBridge":
        from .message_bridge import MessageBridge

        return MessageBridge
    if name == "InfoRenderer":
        from .renderer import InfoRenderer

        return InfoRenderer
    raise AttributeError(name)
