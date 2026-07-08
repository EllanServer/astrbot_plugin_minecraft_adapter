"""Lightweight observation priority scoring used before full report analysis."""

from __future__ import annotations

from typing import Any

from .models import ObservationRecord
from .reporting.dialogue_rules import DialogueRule, dialogue_rules_from_config
from .reporting.dialogue_terms import DialogueRuleMatcher, normalize_text
from .reporting.metrics_context import memory_usage_percent


def observation_priority_score(
    record: ObservationRecord,
    rules: tuple[DialogueRule, ...] | None = None,
    matcher: DialogueRuleMatcher | None = None,
) -> float:
    """Score records that should survive bounded-memory report sampling."""

    if record.kind == "CHAT":
        return _chat_priority(record.content, record.tags, rules, matcher)
    elif record.kind == "PLUGIN_ERROR":
        return 5.0
    elif record.kind == "SERVER_SWITCH":
        return 2.0
    elif record.kind == "SERVER_METRICS":
        return _metrics_priority(record.metrics or {})
    return 0.0


def raw_observation_priority_score(
    data: dict[str, Any],
    rules: tuple[DialogueRule, ...] | None = None,
    matcher: DialogueRuleMatcher | None = None,
) -> float:
    kind = str(data.get("kind") or "")
    if kind == "CHAT":
        tags = data.get("tags") or []
        return _chat_priority(str(data.get("content") or ""), tags, rules, matcher)
    if kind == "PLUGIN_ERROR":
        return 5.0
    if kind == "SERVER_SWITCH":
        return 2.0
    if kind == "SERVER_METRICS":
        metrics = data.get("metrics") or {}
        return _metrics_priority(metrics if isinstance(metrics, dict) else {})
    return 0.0


def _chat_priority(
    content: str,
    tags,
    rules: tuple[DialogueRule, ...] | None = None,
    matcher: DialogueRuleMatcher | None = None,
) -> float:
    score = 1.0
    if isinstance(tags, (list, tuple)):
        tags_text = " ".join(str(tag) for tag in tags if tag)
    else:
        tags_text = str(tags or "")
    text = normalize_text(f"{content} {tags_text}" if tags_text else content)
    dialogue_rules = rules or dialogue_rules_from_config(None)
    dialogue_matcher = matcher or DialogueRuleMatcher(dialogue_rules)
    for rule, terms in dialogue_matcher.direct_matches(text):
        score += 4.0 + min(3, len(terms))
        if dialogue_matcher.urgent_terms(text, rule):
            score += 2.0
        if rule.base_severity in ("high", "critical"):
            score += 1.0
    return score


def _metrics_priority(metrics: dict) -> float:
    try:
        tps = float(metrics.get("tps1m") or metrics.get("tps") or 20.0)
    except (TypeError, ValueError):
        tps = 20.0
    memory = _fast_memory_usage_percent(metrics)

    score = 0.0
    if tps < 18.0:
        score += 3.0
    if tps < 15.0:
        score += 2.0
    if memory >= 90.0:
        score += 2.0
    return score


def _fast_memory_usage_percent(metrics: dict) -> float:
    for key in (
        "memoryUsagePercent",
        "memory_usage_percent",
        "heapUsagePercent",
        "heap_usage_percent",
    ):
        value = _to_float(metrics.get(key))
        if value is not None:
            return value * 100 if 0 <= value <= 1 else value

    for used_key, max_key in (
        ("memoryUsedMb", "memoryMaxMb"),
        ("memoryUsed", "memoryMax"),
        ("heapUsedMb", "heapMaxMb"),
        ("usedMemoryMb", "maxMemoryMb"),
    ):
        used = _to_float(metrics.get(used_key))
        maximum = _to_float(metrics.get(max_key))
        if used is not None and maximum is not None and maximum > 0:
            return max(0.0, min(100.0, used / maximum * 100))

    return memory_usage_percent(metrics) or 0.0


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
