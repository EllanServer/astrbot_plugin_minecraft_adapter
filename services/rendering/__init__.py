"""Rendering helpers and shared models."""

from .common import (
    effective_player_info_server_id,
    flatten_player_cards,
    get_effective_server_name,
    is_proxy_like_name,
    mode_cn,
    norm,
    safe_percent,
)
from .models import RenderResult
from .text import (
    format_multi_player_list_text,
    format_player_detail_text,
    format_server_status_text,
)

__all__ = [
    "RenderResult",
    "effective_player_info_server_id",
    "flatten_player_cards",
    "format_multi_player_list_text",
    "format_player_detail_text",
    "format_server_status_text",
    "get_effective_server_name",
    "is_proxy_like_name",
    "mode_cn",
    "norm",
    "safe_percent",
]
