"""Bounded observation window sampling."""

from __future__ import annotations

import heapq
from typing import Any, Callable

from ..models import ObservationRecord
from ..observation_priority import observation_priority_score, raw_observation_priority_score
from ..reporting.dialogue_rules import DialogueRule, dialogue_rules_from_config
from ..reporting.dialogue_terms import DialogueRuleMatcher
from .models import RecentObservationWindow


RecordMaterializer = Callable[[], ObservationRecord]


class RecentWindowBuilder:
    """Keeps bounded analysis records while counting the complete window."""

    def __init__(
        self,
        max_records: int,
        dialogue_rules: tuple[DialogueRule, ...] | None = None,
    ):
        self.max_records = max(1, max_records)
        self.dialogue_rules = dialogue_rules or dialogue_rules_from_config(None)
        self.dialogue_matcher = DialogueRuleMatcher(self.dialogue_rules)
        self.priority_limit = max(1, min(self.max_records, (self.max_records * 2 + 2) // 3))
        self.reservoir_limit = max(0, self.max_records - self.priority_limit)
        self.priority_records: list[tuple[float, int, RecordMaterializer]] = []
        self.reservoir_records: list[RecordMaterializer] = []
        self.identities: set[str] = set()
        self.total_count = 0

    def add(self, record: ObservationRecord):
        score = observation_priority_score(
            record,
            self.dialogue_rules,
            self.dialogue_matcher,
        )
        self.add_scored(record.identity, score, _cached_materializer(lambda: record))

    def add_raw(self, data: dict[str, Any], materialize: RecordMaterializer):
        score = raw_observation_priority_score(
            data,
            self.dialogue_rules,
            self.dialogue_matcher,
        )
        self.add_scored(_raw_identity(data), score, _cached_materializer(materialize))

    def add_scored(
        self,
        identity: str,
        score: float,
        materialize: RecordMaterializer,
    ):
        self.total_count += 1
        if identity:
            self.identities.add(identity)
        self._add_priority_candidate(score, materialize)
        self._add_reservoir_candidate(materialize)

    def build(self) -> RecentObservationWindow:
        records = self._merge_bounded_records()
        records.sort(key=lambda item: item.timestamp)
        return RecentObservationWindow(
            records=records,
            total_count=self.total_count,
            unique_players=len(self.identities),
            truncated=self.total_count > len(records),
            max_records=self.max_records,
        )

    def _add_priority_candidate(self, score: float, materialize: RecordMaterializer):
        if self.priority_limit <= 0:
            return
        if score <= 0:
            return
        item = (score, self.total_count, materialize)
        if len(self.priority_records) < self.priority_limit:
            heapq.heappush(self.priority_records, item)
            return
        if item[:2] > self.priority_records[0][:2]:
            heapq.heapreplace(self.priority_records, item)

    def _add_reservoir_candidate(self, materialize: RecordMaterializer):
        if self.reservoir_limit <= 0:
            return
        if len(self.reservoir_records) < self.reservoir_limit:
            self.reservoir_records.append(materialize)
            return

        # Deterministic reservoir-style replacement: bounded memory while still
        # sampling across the whole time window instead of keeping only the head.
        index = (self.total_count * 1103515245 + 12345) % self.total_count
        if index < self.reservoir_limit:
            self.reservoir_records[index] = materialize

    def _merge_bounded_records(self) -> list[ObservationRecord]:
        merged: list[ObservationRecord] = []
        seen_ids: set[int] = set()
        for _score, _idx, materialize in sorted(
            self.priority_records,
            key=lambda item: (-item[0], item[1]),
        ):
            record = materialize()
            if id(record) in seen_ids:
                continue
            merged.append(record)
            seen_ids.add(id(record))
            if len(merged) >= self.max_records:
                return merged

        for materialize in self.reservoir_records:
            record = materialize()
            if id(record) in seen_ids:
                continue
            merged.append(record)
            seen_ids.add(id(record))
            if len(merged) >= self.max_records:
                break
        return merged


def _cached_materializer(materialize: RecordMaterializer) -> RecordMaterializer:
    record: ObservationRecord | None = None

    def cached() -> ObservationRecord:
        nonlocal record
        if record is None:
            record = materialize()
        return record

    return cached


def _raw_identity(data: dict[str, Any]) -> str:
    player = data.get("player") or {}
    if not isinstance(player, dict):
        return ""
    return str(player.get("uuidHash") or player.get("name") or "")
