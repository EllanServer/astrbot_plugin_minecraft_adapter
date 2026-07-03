"""Server metric context for MineSentinel report issues."""

from __future__ import annotations

from typing import Any

from ..models import ObservationRecord
from .common import format_locations, record_location


LOW_TPS_THRESHOLD = 18.0
HIGH_MEMORY_PERCENT_THRESHOLD = 85.0

TPS_KEYS = (
    "tps1m",
    "tps",
    "tps_1m",
    "oneMinuteTps",
    "one_minute_tps",
)
MEMORY_PERCENT_KEYS = (
    "memoryUsagePercent",
    "memory_usage_percent",
    "memoryPercent",
    "memory_percent",
    "heapUsagePercent",
    "heap_usage_percent",
    "usedMemoryPercent",
    "used_memory_percent",
    "ramUsagePercent",
    "ram_usage_percent",
)
MEMORY_PAIR_KEYS = (
    ("memoryUsed", "memoryMax"),
    ("memoryUsedMb", "memoryMaxMb"),
    ("memoryUsedMB", "memoryMaxMB"),
    ("memory_used_mb", "memory_max_mb"),
    ("memory_used", "memory_max"),
    ("heapUsed", "heapMax"),
    ("heapUsedMb", "heapMaxMb"),
    ("heap_used_mb", "heap_max_mb"),
    ("heap_used", "heap_max"),
    ("usedMemory", "maxMemory"),
    ("usedMemoryMb", "maxMemoryMb"),
    ("used_memory_mb", "max_memory_mb"),
    ("used_memory", "max_memory"),
)
PERFORMANCE_ISSUE_TAGS = {"performance_lag", "disconnect_or_rollback"}
PERFORMANCE_HINTS = ("卡", "延迟", "lag", "tps", "掉线", "回档", "rollback")


def build_metric_context(records: list[ObservationRecord]) -> dict[str, dict[str, Any]]:
    """Aggregate server metrics by server/backend location."""

    aggregates: dict[str, dict[str, Any]] = {}
    for record in records:
        if record.kind != "SERVER_METRICS":
            continue
        location = record_location(record)
        if not location:
            continue
        metrics = record.metrics or {}
        item = aggregates.setdefault(location, _new_metric_context(location))
        item["samples"] += 1

        tps = _first_float(metrics, TPS_KEYS)
        if tps is not None:
            item["tps_samples"] += 1
            item["min_tps"] = tps if item["min_tps"] is None else min(item["min_tps"], tps)
            if tps < LOW_TPS_THRESHOLD:
                item["low_tps_count"] += 1

        memory_percent = memory_usage_percent(metrics)
        if memory_percent is not None:
            item["memory_samples"] += 1
            item["max_memory_percent"] = (
                memory_percent
                if item["max_memory_percent"] is None
                else max(item["max_memory_percent"], memory_percent)
            )
            if memory_percent >= HIGH_MEMORY_PERCENT_THRESHOLD:
                item["high_memory_count"] += 1

    return {
        location: _finalize_metric_context(item)
        for location, item in sorted(aggregates.items())
    }


def enrich_issues_with_metrics(
    issues: list[dict[str, Any]],
    metrics_by_location: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach same-location metric context to player performance issues."""

    if not metrics_by_location:
        return issues

    for issue in issues:
        if not _wants_metric_context(issue):
            continue
        context = issue_metric_context(issue, metrics_by_location)
        if not context:
            continue
        issue["metric_context"] = context
        issue["metric_context_text"] = context["text"]
    return issues


def issue_metric_context(
    issue: dict[str, Any],
    metrics_by_location: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    target_locations = _target_locations(issue, metrics_by_location)
    if not target_locations:
        return {}

    locations = [metrics_by_location[location] for location in target_locations]
    text = _format_metric_context(locations)
    if not text:
        return {}

    return {
        "locations": locations,
        "locations_text": format_locations(target_locations),
        "has_low_tps": any(item.get("low_tps_count", 0) > 0 for item in locations),
        "has_high_memory": any(
            item.get("high_memory_count", 0) > 0 for item in locations
        ),
        "text": text,
    }


def metric_ops_notes(
    metrics_by_location: dict[str, dict[str, Any]],
) -> list[str]:
    if not metrics_by_location:
        return []

    low_tps_count = sum(
        int(item.get("low_tps_count") or 0) for item in metrics_by_location.values()
    )
    high_memory_count = sum(
        int(item.get("high_memory_count") or 0) for item in metrics_by_location.values()
    )
    notes = []
    if low_tps_count:
        locations = [
            location
            for location, item in metrics_by_location.items()
            if int(item.get("low_tps_count") or 0) > 0
        ]
        notes.append(
            f"检测到 {low_tps_count} 条 TPS 低于 {LOW_TPS_THRESHOLD:g} 的指标观察，"
            f"位置：{format_locations(locations)}。"
        )
    if high_memory_count:
        locations = [
            location
            for location, item in metrics_by_location.items()
            if int(item.get("high_memory_count") or 0) > 0
        ]
        notes.append(
            f"检测到 {high_memory_count} 条内存高于 "
            f"{HIGH_MEMORY_PERCENT_THRESHOLD:g}% 的指标观察，"
            f"位置：{format_locations(locations)}。"
        )
    return notes


def _new_metric_context(location: str) -> dict[str, Any]:
    return {
        "location": location,
        "samples": 0,
        "tps_samples": 0,
        "memory_samples": 0,
        "min_tps": None,
        "max_memory_percent": None,
        "low_tps_count": 0,
        "high_memory_count": 0,
    }


def _finalize_metric_context(item: dict[str, Any]) -> dict[str, Any]:
    result = dict(item)
    if result["min_tps"] is not None:
        result["min_tps"] = round(float(result["min_tps"]), 2)
    if result["max_memory_percent"] is not None:
        result["max_memory_percent"] = round(float(result["max_memory_percent"]), 2)
    result["text"] = _format_metric_context([result])
    return result


def _target_locations(
    issue: dict[str, Any],
    metrics_by_location: dict[str, dict[str, Any]],
) -> list[str]:
    affected_locations = [
        str(location)
        for location in (issue.get("affected_locations") or [])
        if location
    ]
    metric_locations = set(metrics_by_location)
    targets: list[str] = []
    seen = set()

    for location in affected_locations:
        if location in metric_locations and location not in seen:
            targets.append(location)
            seen.add(location)

    if not targets:
        for location in affected_locations:
            for candidate in _location_fallbacks(location):
                if candidate in metric_locations and candidate not in seen:
                    targets.append(candidate)
                    seen.add(candidate)
                    break

    if not targets and not affected_locations and len(metrics_by_location) == 1:
        targets.append(next(iter(metrics_by_location)))

    return targets


def _location_fallbacks(location: str) -> list[str]:
    candidates: list[str] = []
    base = location.split("@", 1)[0]
    if base and base != location:
        candidates.append(base)
    server = base.split("/", 1)[0]
    if server:
        candidates.append(server)
    return candidates


def _wants_metric_context(issue: dict[str, Any]) -> bool:
    tag = str(issue.get("tag") or "")
    if tag in PERFORMANCE_ISSUE_TAGS:
        return True
    if issue.get("category") != "complaint":
        return False
    haystack = " ".join(
        str(value)
        for value in (
            tag,
            issue.get("suggested_action", ""),
            " ".join(issue.get("dialogue_terms") or []),
        )
    ).lower()
    return any(hint.lower() in haystack for hint in PERFORMANCE_HINTS)


def _format_metric_context(locations: list[dict[str, Any]]) -> str:
    parts = []
    for item in locations[:4]:
        detail = []
        min_tps = item.get("min_tps")
        if min_tps is not None:
            detail.append(f"TPS最低 {_format_number(float(min_tps))}")
        memory = item.get("max_memory_percent")
        if memory is not None:
            detail.append(f"内存最高 {_format_number(float(memory))}%")
        if not detail:
            continue
        parts.append(f"{item.get('location')} {'，'.join(detail)}")
    if len(locations) > 4:
        parts.append(f"另有 {len(locations) - 4} 处")
    return "；".join(parts)


def memory_usage_percent(metrics: dict[str, Any]) -> float | None:
    """Return memory pressure percent from common percent or used/max fields."""

    percent = _first_float(metrics, MEMORY_PERCENT_KEYS)
    if percent is not None:
        return percent * 100 if 0 <= percent <= 1 else percent

    for used_key, max_key in MEMORY_PAIR_KEYS:
        used = _to_float(metrics.get(used_key))
        maximum = _to_float(metrics.get(max_key))
        if used is None or maximum is None or maximum <= 0:
            continue
        return max(0.0, min(100.0, used / maximum * 100))
    return None


def _first_float(metrics: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _to_float(metrics.get(key))
        if value is not None:
            return value
    return None


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_number(value: float) -> str:
    return f"{value:.1f}"
