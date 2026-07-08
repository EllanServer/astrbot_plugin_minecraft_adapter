"""Storage data models for MineSentinel."""

from __future__ import annotations

from dataclasses import dataclass

from ..models import ObservationRecord


@dataclass(frozen=True)
class RecentObservationWindow:
    records: list[ObservationRecord]
    total_count: int
    unique_players: int
    truncated: bool
    max_records: int

    @property
    def retained_count(self) -> int:
        return len(self.records)

    @property
    def truncated_count(self) -> int:
        return max(0, self.total_count - self.retained_count)
