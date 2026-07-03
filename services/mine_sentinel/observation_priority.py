"""Lightweight observation priority scoring used before full report analysis."""

from __future__ import annotations

from .models import ObservationRecord
from .reporting.dialogue_rules import DialogueRule, dialogue_rules_from_config
from .reporting.dialogue_terms import matched_terms, normalize_text
from .reporting.metrics_context import memory_usage_percent


def observation_priority_score(
    record: ObservationRecord,
    rules: tuple[DialogueRule, ...] | None = None,
) -> float:
    """Score records that should survive bounded-memory report sampling."""

    score = 0.0
    text = normalize_text(f"{record.content} {' '.join(record.tags)}")
    dialogue_rules = rules or dialogue_rules_from_config(None)

    if record.kind == "CHAT":
        score += 1.0
        for rule in dialogue_rules:
            terms = matched_terms(text, rule.keywords)
            if not terms:
                continue
            score += 4.0 + min(3, len(terms))
            if matched_terms(text, rule.urgent_terms):
                score += 2.0
            if rule.base_severity in ("high", "critical"):
                score += 1.0
    elif record.kind == "PLUGIN_ERROR":
        score += 5.0
    elif record.kind == "SERVER_SWITCH":
        score += 2.0
    elif record.kind == "SERVER_METRICS":
        score += _metrics_priority(record)

    return score


def _metrics_priority(record: ObservationRecord) -> float:
    try:
        tps = float(record.metrics.get("tps1m") or record.metrics.get("tps") or 20.0)
    except (TypeError, ValueError):
        tps = 20.0
    memory = memory_usage_percent(record.metrics) or 0.0

    score = 0.0
    if tps < 18.0:
        score += 3.0
    if tps < 15.0:
        score += 2.0
    if memory >= 90.0:
        score += 2.0
    return score
