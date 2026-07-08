"""Bounded context snippets for dialogue evidence samples."""

from __future__ import annotations

from collections import defaultdict

from ..issue_formatting import format_millis
from ..models import ObservationRecord
from .common import record_location
from .dialogue_signals import DialogueSignalGroup


class DialogueEvidenceContextBuilder:
    """Build short same-location chat context around selected evidence records."""

    def __init__(
        self,
        window_seconds: int,
        messages_per_side: int,
        max_content_length: int,
        max_samples: int | None = None,
    ):
        self.window_ms = max(0, int(window_seconds)) * 1000
        self.messages_per_side = max(0, int(messages_per_side))
        self.max_content_length = max(1, int(max_content_length))
        self.max_samples = None if max_samples is None else max(0, int(max_samples))

    def attach(
        self,
        groups: list[DialogueSignalGroup],
        chat_records: list[ObservationRecord],
    ):
        if not self.window_ms or not self.messages_per_side:
            return
        buckets, indexes = self._index_by_location(chat_records)
        for group in groups:
            records = group.records
            if self.max_samples is not None:
                records = records[: self.max_samples]
            group.context_samples = [
                self._context_sample(record, buckets, indexes)
                for record in records
            ]

    def _context_sample(
        self,
        record: ObservationRecord,
        buckets: dict[str, list[ObservationRecord]],
        indexes: dict[int, tuple[str, int]],
    ) -> str:
        location, index = indexes.get(id(record), ("", -1))
        if not location or index < 0:
            return record.evidence_text()
        bucket = buckets[location]
        before = self._nearby_before(record, bucket, index)
        after = self._nearby_after(record, bucket, index)
        lines = [f"上下文 {location}:"]
        lines.extend(self._format_line(item, current=False) for item in before)
        lines.append(self._format_line(record, current=True))
        lines.extend(self._format_line(item, current=False) for item in after)
        return "\n".join(lines)

    def _nearby_before(
        self,
        record: ObservationRecord,
        bucket: list[ObservationRecord],
        index: int,
    ) -> list[ObservationRecord]:
        selected: list[ObservationRecord] = []
        for position in range(index - 1, -1, -1):
            item = bucket[position]
            if not self._within_window(record, item):
                break
            selected.append(item)
            if len(selected) >= self.messages_per_side:
                break
        return list(reversed(selected))

    def _nearby_after(
        self,
        record: ObservationRecord,
        bucket: list[ObservationRecord],
        index: int,
    ) -> list[ObservationRecord]:
        selected: list[ObservationRecord] = []
        for position in range(index + 1, len(bucket)):
            item = bucket[position]
            if not self._within_window(record, item):
                break
            selected.append(item)
            if len(selected) >= self.messages_per_side:
                break
        return selected

    def _within_window(
        self,
        current: ObservationRecord,
        nearby: ObservationRecord,
    ) -> bool:
        if not current.timestamp or not nearby.timestamp:
            return False
        return abs(current.timestamp - nearby.timestamp) <= self.window_ms

    def _format_line(self, record: ObservationRecord, current: bool) -> str:
        prefix = ">" if current else " "
        timestamp = format_millis(record.timestamp) if record.timestamp else "未知时间"
        player = record.player_name or record.identity or "未知玩家"
        content = truncate(record.content, self.max_content_length)
        return f"{prefix} {timestamp} {player}: {content}"

    @staticmethod
    def _index_by_location(
        records: list[ObservationRecord],
    ) -> tuple[dict[str, list[ObservationRecord]], dict[int, tuple[str, int]]]:
        buckets: dict[str, list[ObservationRecord]] = defaultdict(list)
        ordered = True
        last_timestamps: dict[str, int] = {}
        for record in records:
            location = record_location(record)
            if location:
                last_timestamp = last_timestamps.get(location)
                if last_timestamp is not None and record.timestamp < last_timestamp:
                    ordered = False
                last_timestamps[location] = record.timestamp
                buckets[location].append(record)

        indexes: dict[int, tuple[str, int]] = {}
        for location, bucket in buckets.items():
            if not ordered:
                bucket.sort(key=lambda item: item.timestamp)
            for index, record in enumerate(bucket):
                indexes[id(record)] = (location, index)
        return dict(buckets), indexes


def truncate(value: str, max_length: int) -> str:
    text = value or ""
    if len(text) <= max_length:
        return text
    if max_length <= 3:
        return text[:max_length]
    return text[: max_length - 3] + "..."
