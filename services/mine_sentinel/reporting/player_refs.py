"""Player identity helpers for MineSentinel reports."""

from __future__ import annotations

import re

from ..models import ObservationRecord


MENTION_SKIP_WORDS = {
    "ban",
    "bug",
    "cheat",
    "dupe",
    "lag",
    "lobby",
    "rollback",
    "server",
    "tps",
}
MENTION_RE = re.compile(r"(?<![A-Za-z0-9_])@?([A-Za-z0-9_]{3,16})(?![A-Za-z0-9_])")


def record_player(record: ObservationRecord) -> str:
    return (record.player_name or record.identity or "").strip()


def mentioned_players(text: str, speaker: str = "") -> list[str]:
    speaker_lower = speaker.lower()
    found = []
    seen = set()
    for match in MENTION_RE.finditer(text or ""):
        name = match.group(1)
        lowered = name.lower()
        if lowered == speaker_lower or lowered in MENTION_SKIP_WORDS:
            continue
        if lowered in seen:
            continue
        seen.add(lowered)
        found.append(name)
    return found
