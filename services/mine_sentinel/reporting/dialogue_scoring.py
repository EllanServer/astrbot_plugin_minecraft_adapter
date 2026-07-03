"""Scoring and alert gates for dialogue issue signals."""

from __future__ import annotations

from ..models import MineSentinelConfig
from .common import SEVERITY_RANK
from .dialogue_rules import DialogueRule


def dialogue_score(
    signal_count: int,
    unique_player_count: int,
    urgent_signal_count: int,
    affected_location_count: int = 0,
    affected_server_count: int = 0,
) -> float:
    player_floor = max(1, unique_player_count)
    evidence_weight = min(signal_count, player_floor * 3 + 2)
    location_weight = min(affected_location_count, 4) * 1.25
    server_weight = min(affected_server_count, 3) * 1.5
    return (
        evidence_weight
        + unique_player_count * 2.0
        + urgent_signal_count * 2.5
        + location_weight
        + server_weight
    )


def dialogue_severity(
    rule: DialogueRule,
    signal_count: int,
    unique_player_count: int,
    urgent_signal_count: int,
    affected_location_count: int = 0,
    affected_server_count: int = 0,
) -> str:
    base = SEVERITY_RANK.get(rule.base_severity, 2)
    wide_scope = affected_server_count >= 2 or affected_location_count >= 2
    if (
        unique_player_count >= 4
        or signal_count >= 8
        or urgent_signal_count >= 3
        or (wide_scope and unique_player_count >= 2)
    ):
        base += 2
    elif (
        unique_player_count >= 2
        or signal_count >= 3
        or urgent_signal_count >= 1
        or wide_scope
    ):
        base += 1
    if rule.category == "suggestion":
        base = min(base, SEVERITY_RANK["medium"])
    if rule.category in ("bug", "economy", "moderation") and urgent_signal_count:
        base += 1
    if base >= SEVERITY_RANK["critical"]:
        return "critical"
    if base >= SEVERITY_RANK["high"]:
        return "high"
    if base >= SEVERITY_RANK["medium"]:
        return "medium"
    return "low"


def should_alert(
    config: MineSentinelConfig,
    severity: str,
    signal_count: int,
    unique_players: int,
) -> bool:
    alert = config.alert
    return (
        alert.enabled
        and SEVERITY_RANK.get(severity, 0)
        >= SEVERITY_RANK.get(alert.min_severity, 3)
        and signal_count >= alert.min_evidence_count
        and unique_players >= alert.min_unique_players
    )
