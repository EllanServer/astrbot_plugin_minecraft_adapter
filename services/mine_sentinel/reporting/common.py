"""Shared report analysis helpers."""

from __future__ import annotations

from ..models import ObservationRecord


SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}
MAX_DISPLAY_PLAYERS = 16


def format_players(players: list[str]) -> str:
    if not players:
        return "未知"
    shown = players[:MAX_DISPLAY_PLAYERS]
    text = "、".join(shown)
    if len(players) > MAX_DISPLAY_PLAYERS:
        text += f" 等 {len(players)} 人"
    return text


def player_name_list(records: list[ObservationRecord]) -> list[str]:
    names = []
    seen = set()
    for record in records:
        name = (record.player_name or record.identity or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return sorted(names)


def record_location(record: ObservationRecord) -> str:
    server = (record.server_id or "").strip()
    backend = (record.backend_server or "").strip()
    context = record.context or {}
    world = str(context.get("world") or "").strip()
    if server and backend and backend != server:
        base = f"{server}/{backend}"
    else:
        base = backend or server
    if world:
        return f"{base}@{world}" if base else world
    return base


def location_list(records: list[ObservationRecord]) -> list[str]:
    locations = []
    seen = set()
    for record in records:
        location = record_location(record)
        if not location or location in seen:
            continue
        seen.add(location)
        locations.append(location)
    return sorted(locations)


def format_locations(locations: list[str]) -> str:
    if not locations:
        return "未知"
    shown = locations[:MAX_DISPLAY_PLAYERS]
    text = "、".join(shown)
    if len(locations) > MAX_DISPLAY_PLAYERS:
        text += f" 等 {len(locations)} 处"
    return text
