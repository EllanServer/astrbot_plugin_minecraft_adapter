"""Lightweight observation priority scoring used before full report analysis.

Delegates to the Rust implementation in ``mine_sentinel_rs`` when available
(runs up to 50k times per report window). Falls back to pure-Python when the
native extension is not installed.
"""

from __future__ import annotations

try:
    from mine_sentinel_rs import (  # type: ignore[import-not-found]
        observation_priority_score as _rs_observation_priority_score,
    )

    _HAS_RUST = True
except ImportError:  # pragma: no cover
    _HAS_RUST = False

from .models import ObservationRecord
from .reporting.dialogue_rules import DialogueRule, dialogue_rules_from_config
from .reporting.dialogue_terms import RuleTermMatcher, normalize_text
from .reporting.metrics_context import memory_usage_percent


# Matcher built once for the default rule set; observation_priority_score is
# called per record (up to 50k records per report), so recompiling the keyword
# regex each time would be wasteful. Custom rules fall back to a per-call build.
_DEFAULT_MATCHER: RuleTermMatcher | None = None


def _default_matcher() -> RuleTermMatcher:
    global _DEFAULT_MATCHER
    if _DEFAULT_MATCHER is None:
        rules = dialogue_rules_from_config(None)
        _DEFAULT_MATCHER = RuleTermMatcher(
            (rule, rule.keywords, rule.urgent_terms) for rule in rules
        )
    return _DEFAULT_MATCHER


def observation_priority_score(
    record: ObservationRecord,
    rules: tuple[DialogueRule, ...] | None = None,
) -> float:
    """Score records that should survive bounded-memory report sampling."""
    if _HAS_RUST:
        matcher = _default_matcher() if rules is None else RuleTermMatcher(
            (rule, rule.keywords, rule.urgent_terms) for rule in rules
        )
        # Rust impl handles normalize + scan + scoring in one call, only
        # needing the pre-built matcher (it caches the rule set internally).
        return _rs_observation_priority_score(record, matcher)
    return _observation_priority_score_py(record, rules)


def _observation_priority_score_py(
    record: ObservationRecord,
    rules: tuple[DialogueRule, ...] | None = None,
) -> float:
    """Pure-Python fallback (mirrors the Rust implementation exactly)."""
    score = 0.0
    text = normalize_text(f"{record.content} {' '.join(record.tags)}")

    if record.kind == "CHAT":
        score += 1.0
        matcher = _default_matcher() if rules is None else RuleTermMatcher(
            (rule, rule.keywords, rule.urgent_terms) for rule in rules
        )
        hits = matcher.scan(text)
        for rule, (keywords, urgent) in hits.items():
            if not keywords:
                continue
            score += 4.0 + min(3, len(keywords))
            if urgent:
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
