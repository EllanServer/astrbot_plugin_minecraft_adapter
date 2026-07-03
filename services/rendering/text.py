"""Text fallback formatting for renderer outputs."""

from __future__ import annotations

from typing import Any

from .common import flatten_player_cards, get_effective_server_name, mode_cn


def format_multi_player_list_text(
    cards: list[tuple[str, list[Any], int, str]],
) -> str:
    flattened = flatten_player_cards(cards)
    total = sum((total if total > 0 else len(players)) for _, players, total, _ in flattened)
    lines = [f"👥 在线玩家总览 | {total}人", "────────────────────────────"]
    for sid, players, total_count, server_name in flattened:
        count = total_count if total_count > 0 else len(players)
        display_name = server_name or sid
        lines.append("")
        lines.append(f"📌 服务器: {display_name} ({count}人)")

        if not players:
            lines.append("  当前没有玩家在线")
            continue

        for player in players:
            lines.append(
                f"  - {player.name} | {mode_cn(player.game_mode)} | "
                f"世界:{player.world or '未知'} | 延迟:{player.ping}ms"
            )
    return "\n".join(lines)


def format_server_status_text(
    info: Any,
    status: Any,
    server_tag: str = "",
) -> str:
    online = info.online_count or status.online_players
    max_players = info.max_players or status.max_players
    uptime = info.uptime_formatted or status.uptime_formatted or "未知"

    title = f"🖥️ 服务器状态 | {info.name}"
    if server_tag:
        title += f" | ID: {server_tag}"

    lines = [
        title,
        "────────────────────────────",
        f"平台: {info.platform} {info.minecraft_version}",
        f"在线: {online}/{max_players}",
        f"运行: {uptime}",
        "",
        "📊 性能",
        f"内存: {status.memory_used}MB/{status.memory_max}MB ({status.memory_usage_percent:.1f}%)",
    ]

    if not status.is_proxy:
        lines.append(
            f"TPS: {status.tps_1m:.1f} / {status.tps_5m:.1f} / {status.tps_15m:.1f}"
        )

    if info.is_proxy and info.aggregate_online > 0:
        lines.append(f"总在线: {info.aggregate_online}/{info.aggregate_max}")

    if status.worlds:
        lines.extend(["", "🌍 世界列表"])
        for world in status.worlds:
            lines.append(
                f"- {world.get('name', 'world')}: 玩家 {world.get('players', 0)}, "
                f"实体 {world.get('entities', 0)}, 区块 {world.get('loadedChunks', 0)}"
            )

    return "\n".join(lines)


def format_player_detail_text(player: Any, server_tag: str = "") -> str:
    detail_server_name = get_effective_server_name(player, server_tag)
    lines = [
        f"👤 玩家详情 | {player.name}",
        "────────────────────────────",
        f"服务器: {detail_server_name or '未提供'}",
        f"UUID: {player.uuid}",
        "",
        "【基础信息】",
        f"世界: {player.world or '未知'}",
        f"模式: {mode_cn(player.game_mode)}",
        f"延迟: {player.ping}ms",
        "",
        "【状态面板】",
        f"生命值: {player.health:.1f}/{player.max_health:.1f}",
        f"饥饿值: {player.food_level}/20",
        f"等级: {player.level} ({player.exp * 100:.1f}%)",
    ]
    if player.location:
        lines.append(
            f"位置: X={player.location.get('x', 0):.1f}, "
            f"Y={player.location.get('y', 0):.1f}, "
            f"Z={player.location.get('z', 0):.1f}"
        )
    lines.append(f"在线时长: {player.online_time_formatted or '未知'}")
    if player.is_op:
        lines.insert(2, "权限: 管理员")
    return "\n".join(lines)
