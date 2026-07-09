"""Storage data models for MineSentinel."""

from __future__ import annotations

from dataclasses import dataclass

from ..models import ObservationRecord


@dataclass(frozen=True)
class RecentObservationWindow:
    # records 用 tuple 而非 list：frozen dataclass 仅冻结字段绑定，list 字段
    # 本身仍可变（调用方可 append/sort 污染缓存共享对象）。改为 tuple 在类型
    # 层阻止可变操作，保证窗口记录不可变约定。
    records: tuple[ObservationRecord, ...]
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
