"""Public renderer facade for Minecraft server/player info."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from astrbot.api import logger

from .rendering import (
    RenderResult,
    effective_player_info_server_id,
    flatten_player_cards,
    format_multi_player_list_text,
    format_player_detail_text,
    format_server_status_text,
    get_effective_server_name,
    is_proxy_like_name,
    mode_cn,
    norm,
    safe_percent,
)
from .rendering.cards import ImageCardRenderer
from .rendering.image import status_color

if TYPE_CHECKING:
    from ..core.models import PlayerDetail, PlayerInfo, ServerInfo, ServerStatus


class InfoRenderer:
    """Chooses text or image rendering and keeps image failures non-fatal."""

    _PROXY_NAMES = {"vc", "velocity", "proxy", "bungeecord", "waterfall"}

    def __init__(self, text2image_enabled: bool = True, cache_dir: Path | None = None):
        self.text2image_enabled = text2image_enabled
        self._cache_dir = cache_dir or (Path(__file__).parent.parent / ".cache")
        self._image_cards = ImageCardRenderer(self._cache_dir)

    async def render_multi_server_status(
        self,
        cards: list[tuple[str, "ServerInfo", "ServerStatus"]],
        as_image: bool = True,
    ) -> RenderResult:
        if not cards:
            return RenderResult(" 没有可渲染的服务器状态", is_image=False)

        if not as_image or not self.text2image_enabled:
            return RenderResult(self._multi_server_status_text(cards), is_image=False)

        try:
            out = await self._image_cards.render_multi_server_status(cards)
            return RenderResult(out, is_image=True)
        except Exception as exc:
            logger.warning(f"[Renderer] 多服务器状态合图失败，回退文本: {exc}")
            return RenderResult(self._multi_server_status_text(cards), is_image=False)

    async def render_multi_player_list(
        self,
        cards: list[tuple[str, list["PlayerInfo"], int, str]],
        as_image: bool = True,
    ) -> RenderResult:
        if not cards:
            return RenderResult(" 没有可渲染的玩家列表", is_image=False)

        if not as_image or not self.text2image_enabled:
            return RenderResult(self._format_multi_player_list_text(cards), is_image=False)

        try:
            out = await self._image_cards.render_multi_player_list(cards)
            return RenderResult(out, is_image=True)
        except Exception as exc:
            logger.warning(f"[Renderer] 多服务器玩家列表合图失败，回退文本: {exc}")
            return RenderResult(self._format_multi_player_list_text(cards), is_image=False)

    async def render_multi_player_detail(
        self,
        cards: list[tuple[str, "PlayerDetail"]],
        as_image: bool = True,
    ) -> RenderResult:
        if not cards:
            return RenderResult(" 没有可渲染的玩家详情", is_image=False)

        if not as_image or not self.text2image_enabled:
            return RenderResult(self._multi_player_detail_text(cards), is_image=False)

        try:
            out = await self._image_cards.render_multi_player_detail(cards)
            return RenderResult(out, is_image=True)
        except Exception as exc:
            logger.warning(f"[Renderer] 多服务器玩家详情合图失败，回退文本: {exc}")
            return RenderResult(self._multi_player_detail_text(cards), is_image=False)

    async def render_server_status(
        self,
        server_info: "ServerInfo",
        server_status: "ServerStatus",
        as_image: bool = True,
    ) -> RenderResult:
        return await self.render_multi_server_status(
            [("", server_info, server_status)],
            as_image=as_image,
        )

    async def render_player_list(
        self,
        players: list["PlayerInfo"],
        total: int,
        server_name: str = "",
        as_image: bool = True,
    ) -> RenderResult:
        return await self.render_multi_player_list(
            [("", players, total, server_name)],
            as_image=as_image,
        )

    async def render_player_detail(
        self,
        player: "PlayerDetail",
        server_tag: str = "",
        as_image: bool = True,
    ) -> RenderResult:
        if not as_image or not self.text2image_enabled:
            return RenderResult(
                self._format_player_detail_text(player, server_tag=server_tag),
                is_image=False,
            )

        try:
            out = await self._image_cards.render_player_detail(
                player,
                server_tag=server_tag,
            )
            return RenderResult(out, is_image=True)
        except Exception as exc:
            logger.warning(f"[Renderer] 玩家详情图片渲染失败，回退文本: {exc}")
            return RenderResult(
                self._format_player_detail_text(player, server_tag=server_tag),
                is_image=False,
            )

    def _multi_server_status_text(
        self,
        cards: list[tuple[str, "ServerInfo", "ServerStatus"]],
    ) -> str:
        return "\n\n".join(
            self._format_server_status_text(info, status, server_tag=tag)
            for tag, info, status in cards
        )

    def _multi_player_detail_text(
        self,
        cards: list[tuple[str, "PlayerDetail"]],
    ) -> str:
        return "\n\n".join(
            self._format_player_detail_text(player, server_tag=tag)
            for tag, player in cards
        )

    def _format_multi_player_list_text(
        self,
        cards: list[tuple[str, list["PlayerInfo"], int, str]],
    ) -> str:
        return format_multi_player_list_text(cards)

    def _format_server_status_text(
        self,
        info: "ServerInfo",
        status: "ServerStatus",
        server_tag: str = "",
    ) -> str:
        return format_server_status_text(info, status, server_tag=server_tag)

    def _format_player_detail_text(
        self,
        player: "PlayerDetail",
        server_tag: str = "",
    ) -> str:
        return format_player_detail_text(player, server_tag=server_tag)

    @staticmethod
    def _safe_percent(value: float | int, lo: int = 0, hi: int = 100) -> int:
        return safe_percent(value, lo, hi)

    @staticmethod
    def _mode_cn(mode: str) -> str:
        return mode_cn(mode)

    @staticmethod
    def _norm(value: str) -> str:
        return norm(value)

    def _get_status_color(self, value: float, type: str = "tps") -> str:
        return status_color(value, type)

    def _is_proxy_like_name(self, name: str) -> bool:
        return is_proxy_like_name(name, self._PROXY_NAMES)

    def _get_effective_server_name(
        self,
        player: "PlayerInfo | PlayerDetail",
        fallback: str,
    ) -> str:
        return get_effective_server_name(player, fallback, self._PROXY_NAMES)

    def _effective_player_server_id(
        self,
        player: "PlayerDetail",
        fallback: str,
    ) -> str:
        return self._get_effective_server_name(player, fallback)

    def _effective_player_info_server_id(
        self,
        player: "PlayerInfo",
        fallback: str,
    ) -> str:
        return effective_player_info_server_id(player, fallback, self._PROXY_NAMES)

    def _flatten_player_cards(
        self,
        cards: list[tuple[str, list["PlayerInfo"], int, str]],
    ) -> list[tuple[str, list["PlayerInfo"], int, str]]:
        return flatten_player_cards(cards, self._PROXY_NAMES)
