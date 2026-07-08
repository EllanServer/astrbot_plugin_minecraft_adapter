"""Pure rendering helpers for Minecraft status/player views."""

from __future__ import annotations

from typing import Any


PROXY_NAMES = {"vc", "velocity", "proxy", "bungeecord", "waterfall"}


def safe_percent(value: float | int, lo: int = 0, hi: int = 100) -> int:
    try:
        return max(lo, min(hi, int(round(float(value)))))
    except Exception:
        return lo


def mode_cn(mode: str) -> str:
    return {
        "SURVIVAL": "生存",
        "CREATIVE": "创造",
        "ADVENTURE": "冒险",
        "SPECTATOR": "旁观",
    }.get(mode, mode or "未知")


def norm(value: str) -> str:
    return (value or "").strip()


def is_proxy_like_name(name: str, proxy_names: set[str] | None = None) -> bool:
    proxy_names = proxy_names or PROXY_NAMES
    lowered = norm(name).lower()
    if not lowered:
        return False
    if lowered in proxy_names:
        return True
    return any(
        marker in lowered
        for marker in ("velocity", "proxy", "bungee", "waterfall", "vc")
    )


def get_effective_server_name(
    player: Any,
    fallback: str,
    proxy_names: set[str] | None = None,
) -> str:
    """Prefer backend server names and avoid showing proxy-layer names."""

    proxy_names = proxy_names or PROXY_NAMES
    fallback_norm = norm(fallback)
    server = norm(getattr(player, "server", ""))
    if (
        server
        and server.lower() not in proxy_names
        and server.lower() != fallback_norm.lower()
        and not is_proxy_like_name(server, proxy_names)
    ):
        return server
    if fallback_norm and not is_proxy_like_name(fallback_norm, proxy_names):
        return fallback_norm
    return ""


def effective_player_info_server_id(
    player: Any,
    fallback: str,
    proxy_names: set[str] | None = None,
) -> str:
    return get_effective_server_name(player, fallback, proxy_names) or "未标记子服"


def flatten_player_cards(
    cards: list[tuple[str, list[Any], int, str]],
    proxy_names: set[str] | None = None,
) -> list[tuple[str, list[Any], int, str]]:
    """Split proxy player cards by backend while keeping standalone cards intact."""

    flattened: list[tuple[str, list[Any], int, str]] = []
    for sid, players, total, server_name in cards:
        primary_name = norm(server_name) or sid
        if not players:
            flattened.append((sid, [], total, primary_name))
            continue

        grouped: dict[str, list[Any]] = {}
        for player in players:
            group_id = effective_player_info_server_id(player, sid, proxy_names)
            grouped.setdefault(group_id, []).append(player)

        if len(grouped) <= 1:
            only_key = next(iter(grouped.keys())) if grouped else sid
            flattened.append(
                (
                    only_key,
                    players,
                    total if total > 0 else len(players),
                    (primary_name if only_key == sid else only_key),
                )
            )
            continue

        for group_id, group_players in grouped.items():
            display_name = primary_name if group_id == sid else group_id
            flattened.append((group_id, group_players, len(group_players), display_name))

    return flattened
