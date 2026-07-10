"""Shared report analysis helpers."""

from __future__ import annotations

import re

from ..models import ObservationRecord


SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}
MAX_DISPLAY_PLAYERS = 16

_PLUGIN_PATTERNS = (
    re.compile(
        r"\[Craft Scheduler Thread[^\]]*? - (?P<name>[A-Za-z0-9_-]{2,48})/",
        re.IGNORECASE,
    ),
    re.compile(r"\]:\s*\[(?P<name>[A-Za-z0-9_-]{2,64})\]"),
    re.compile(r"\bplugins[/\\](?P<name>[^/\\\s]{2,64})[/\\]", re.IGNORECASE),
    re.compile(
        r"\b(?P<name>[A-Za-z][A-Za-z0-9_-]{2,48})-(?:HikariPool|Hikari|Pool)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\[(?P<name>[A-Za-z][A-Za-z0-9_-]{2,32})-(?:worker|thread)(?:-\d+)?/",
        re.IGNORECASE,
    ),
)
_PLUGIN_CONTEXT_FIELDS = (
    "plugin",
    "pluginName",
    "plugin_name",
    "sourcePlugin",
    "source_plugin",
)
_PERSON_CONTEXT_FIELDS = (
    "chatPlayer",
    "vulcanPlayer",
    "playerName",
    "targetPlayer",
    "actor",
    "sender",
)
_PERSON_CONTENT_PATTERNS = (
    re.compile(
        r"(?:^|:\s)(?P<name>[A-Za-z0-9_]{1,16})\s+"
        r"(?:moved wrongly|moved too quickly)!?",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bUUID of player (?P<name>[A-Za-z0-9_]{1,16}) is\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|:\s)(?P<name>[A-Za-z0-9_]{1,16})\s+issued server command:",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|:\s)(?P<name>[A-Za-z0-9_]{1,16})\s+lost connection:",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|:\s)(?P<name>[A-Za-z0-9_]{1,16})\[/[^\]]+\]\s+logged in\b",
        re.IGNORECASE,
    ),
)
_IGNORED_PLUGIN_NAMES = {
    "server",
    "minecraft",
    "paper",
    "spigot",
    "purpur",
    "folia",
    "velocity",
    "bungeecord",
    "hikari",
    "hikaripool",
    "poolbase",
    "sqlmanager",
    "warn",
    "warning",
    "error",
    "info",
}


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


def person_name_list(records: list[ObservationRecord]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for record in records:
        context = record.context or {}
        candidates = [record.player_name]
        candidates.extend(context.get(field) for field in _PERSON_CONTEXT_FIELDS)
        content = str(record.content or "")
        for pattern in _PERSON_CONTENT_PATTERNS:
            candidates.extend(match.group("name") for match in pattern.finditer(content))
        for candidate in candidates:
            name = str(candidate or "").strip()
            key = name.lower()
            if not name or key in seen or key in {"console", "server", "system"}:
                continue
            seen.add(key)
            names.append(name)
    return sorted(names)


def plugin_name_list(records: list[ObservationRecord], limit: int = 12) -> list[str]:
    """Extract concrete plugin/component names from structured fields and log prefixes."""

    names: list[str] = []
    seen: set[str] = set()
    for record in records:
        context = record.context or {}
        candidates = [context.get(field) for field in _PLUGIN_CONTEXT_FIELDS]
        content = str(record.content or "")
        for pattern in _PLUGIN_PATTERNS:
            candidates.extend(match.group("name") for match in pattern.finditer(content))
        for candidate in candidates:
            name = _normalize_plugin_name(candidate)
            key = re.sub(r"[^a-z0-9]", "", name.lower())
            if not name or key in seen:
                continue
            seen.add(key)
            names.append(name)
            if len(names) >= limit:
                return names
    return names


def log_file_list(records: list[ObservationRecord], limit: int = 8) -> list[str]:
    files: list[str] = []
    seen: set[str] = set()
    for record in records:
        value = str((record.context or {}).get("logFile") or "").strip()
        if not value:
            continue
        name = re.split(r"[/\\]", value)[-1] or value
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        files.append(name)
        if len(files) >= limit:
            break
    return files


def world_name_list(records: list[ObservationRecord], limit: int = 8) -> list[str]:
    worlds: list[str] = []
    seen: set[str] = set()
    for record in records:
        context = record.context or {}
        location = context.get("location")
        location = location if isinstance(location, dict) else {}
        value = str(
            context.get("world")
            or context.get("dimension")
            or location.get("world")
            or location.get("dimension")
            or ""
        ).strip()
        key = value.lower()
        if not value or key in seen:
            continue
        seen.add(key)
        worlds.append(value)
        if len(worlds) >= limit:
            break
    return worlds


def position_list(records: list[ObservationRecord], limit: int = 8) -> list[str]:
    positions: list[str] = []
    seen: set[str] = set()
    for record in records:
        context = record.context or {}
        location = context.get("location")
        location = location if isinstance(location, dict) else {}
        coordinates = []
        for key in ("x", "y", "z"):
            value = context.get(key, location.get(key))
            if value is None or value == "":
                coordinates = []
                break
            coordinates.append(str(value))
        if len(coordinates) != 3:
            continue
        world = str(
            context.get("world")
            or context.get("dimension")
            or location.get("world")
            or location.get("dimension")
            or ""
        ).strip()
        position = f"{world} ({', '.join(coordinates)})" if world else f"({', '.join(coordinates)})"
        if position in seen:
            continue
        seen.add(position)
        positions.append(position)
        if len(positions) >= limit:
            break
    return positions


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


def _normalize_plugin_name(value: object) -> str:
    name = str(value or "").strip().strip("[](){}:;,.")
    if not name or len(name) > 64 or "." in name or "/" in name or "\\" in name:
        return ""
    previous = ""
    while name != previous:
        previous = name
        name = re.sub(
            r"-(?:HikariPool|Hikari|Pool|Connection|Database)$",
            "",
            name,
            flags=re.IGNORECASE,
        )
    if not name or name.lower() in _IGNORED_PLUGIN_NAMES:
        return ""
    if re.fullmatch(r"pool-?\d*", name, re.IGNORECASE):
        return ""
    if name.lower().startswith(("java", "com_", "org_", "net_")):
        return ""
    return name
