"""Record sampling strategies for bounded AI report prompts."""

from __future__ import annotations

import heapq
from typing import Any

from ..models import ObservationRecord


def sample_records_for_ai(
    records: list[ObservationRecord],
    max_records: int,
    fallback: dict[str, Any] | None = None,
) -> list[ObservationRecord]:
    """Keep the AI prompt small while preserving issue-relevant chat evidence."""

    if len(records) <= max_records:
        return list(records)
    if max_records <= 0:
        return []
    if max_records == 1:
        priority = _priority_records(records, fallback, 1)
        return [priority[0][1] if priority else records[-1]]

    priority_quota = max(1, min(max_records, max_records * 2 // 3))
    selected: list[ObservationRecord] = []
    selected_ids: set[int] = set()
    selected_order: dict[int, int] = {}

    for _, index, record in _priority_records(records, fallback, priority_quota):
        _add_record(selected, selected_ids, selected_order, index, record)

    remaining = max_records - len(selected)
    if remaining > 0:
        for index in even_sample_indexes(len(records), remaining + 2):
            record = records[index]
            if (
                _add_record(selected, selected_ids, selected_order, index, record)
                and len(selected) >= max_records
            ):
                break

    if len(selected) < max_records:
        for index, record in enumerate(records):
            if (
                _add_record(selected, selected_ids, selected_order, index, record)
                and len(selected) >= max_records
            ):
                break

    selected.sort(key=lambda record: (record.timestamp, selected_order.get(id(record), 0)))
    return selected[:max_records]


def even_sample(items: list[Any], max_items: int) -> list[Any]:
    if len(items) <= max_items:
        return list(items)
    if max_items <= 0:
        return []
    if max_items == 1:
        return [items[-1]]
    step = (len(items) - 1) / (max_items - 1)
    return [items[round(index * step)] for index in range(max_items)]


def even_sample_indexes(length: int, max_items: int) -> list[int]:
    if length <= 0 or max_items <= 0:
        return []
    if length <= max_items:
        return list(range(length))
    if max_items == 1:
        return [length - 1]
    step = (length - 1) / (max_items - 1)
    return [round(index * step) for index in range(max_items)]


def _priority_records(
    records: list[ObservationRecord],
    fallback: dict[str, Any] | None,
    limit: int | None = None,
) -> list[tuple[float, int, ObservationRecord]]:
    focus = _focus_from_fallback(fallback or {})
    if not any(focus.values()):
        return []

    key = lambda item: (-item[0], item[2].timestamp, item[1])
    scored = _scored_records(records, focus)
    if limit is not None:
        ranked = heapq.nsmallest(limit, scored, key=key)
    else:
        ranked = list(scored)
        ranked.sort(key=key)
    return ranked


def _scored_records(
    records: list[ObservationRecord],
    focus: dict[str, Any],
):
    for index, record in enumerate(records):
        score = _record_score(record, focus)
        if score > 0:
            yield (score, index, record)


def _focus_from_fallback(fallback: dict[str, Any]) -> dict[str, Any]:
    players: set[str] = set()
    terms: set[str] = set()
    evidence: set[str] = set()
    for issue in fallback.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        for field in ("players", "mentioned_players"):
            values = issue.get(field)
            if not isinstance(values, list):
                continue
            for player in values:
                value = _norm(player)
                if value:
                    players.add(value)
        for term in issue.get("dialogue_terms") or []:
            value = _norm(term)
            if value:
                terms.add(value)
        tag = _norm(issue.get("tag", ""))
        if tag:
            terms.update(part for part in tag.replace("_", " ").split() if part)
        for sample in issue.get("evidence_samples") or []:
            value = _norm(sample)
            if value:
                evidence.add(value)

    return {
        "players": players,
        "terms": terms,
        "evidence": evidence,
        "evidence_blob": "\0".join(evidence),
    }


def _record_score(record: ObservationRecord, focus: dict[str, Any]) -> float:
    players = focus["players"]
    terms = focus["terms"]
    evidence = focus["evidence"]
    evidence_blob = focus["evidence_blob"]
    player = _norm(record.player_name or record.identity) if players else ""

    score = 0.0
    if record.kind == "CHAT":
        score += 1.0
    if player and player in players:
        score += 5.0
    text = ""
    if terms or players:
        text = _norm(f"{record.content} {' '.join(record.tags)}")
        if any(term and term in text for term in terms):
            score += 4.0
        if any(player and player in text for player in players):
            score += 2.0
    if evidence:
        content = _norm(record.content)
        if content and content in evidence_blob:
            score += 8.0
        elif not content:
            evidence_text = _norm(record.evidence_text())
            if evidence_text and evidence_text in evidence:
                score += 8.0
    if record.kind != "CHAT" and score > 0:
        score *= 0.5
    return score


def _add_record(
    selected: list[ObservationRecord],
    selected_ids: set[int],
    selected_order: dict[int, int],
    index: int,
    record: ObservationRecord,
) -> bool:
    key = id(record)
    if key in selected_ids:
        return False
    selected.append(record)
    selected_ids.add(key)
    selected_order[key] = index
    return True


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().split())
