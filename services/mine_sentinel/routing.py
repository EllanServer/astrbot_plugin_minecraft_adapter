"""MineSentinel target-session routing helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from .models import ObservationRecord
from ..session_targets import normalize_session_targets


def normalize_delivery_targets(targets: Iterable[Any]) -> list[str]:
    return normalize_session_targets(targets)


class MineSentinelTargetRouter:
    """Resolve observation records into AstrBot target sessions."""

    def __init__(
        self,
        get_server_config: Callable[[str], Any | None],
        report_targets: Iterable[Any] | Callable[[], Iterable[Any]] | None = None,
    ):
        self.get_server_config = get_server_config
        self.report_targets = report_targets

    def records_by_session(
        self,
        records: list[ObservationRecord],
        include_server_targets: bool = True,
        include_report_targets: bool = True,
    ) -> dict[str, list[ObservationRecord]]:
        targets: dict[str, list[ObservationRecord]] = {}
        if include_server_targets:
            for record in records:
                for session in self._sessions_for_record(record):
                    targets.setdefault(session, []).append(record)
        if include_report_targets:
            self._add_records_to_sessions(targets, self._report_sessions(), records)
        return targets

    @staticmethod
    def _add_records_to_sessions(
        targets: dict[str, list[ObservationRecord]],
        sessions: list[str],
        records: list[ObservationRecord],
    ):
        for session in sessions:
            current = targets.setdefault(session, [])
            current_ids = {id(record) for record in current}
            for record in records:
                if id(record) not in current_ids:
                    current.append(record)

    def sessions_for_records(
        self,
        records: list[ObservationRecord],
        exclude_session: str = "",
        include_server_targets: bool = True,
        include_report_targets: bool = True,
    ) -> list[str]:
        sessions = set(
            self.records_by_session(
                records,
                include_server_targets=include_server_targets,
                include_report_targets=include_report_targets,
            )
        )
        if exclude_session:
            sessions.discard(exclude_session)
        return sorted(sessions)

    def _sessions_for_record(self, record: ObservationRecord) -> list[str]:
        if not record.server_id:
            return []
        config = self.get_server_config(record.server_id)
        raw_sessions = getattr(config, "target_sessions", []) or []
        return sorted(normalize_session_targets(raw_sessions))

    def _report_sessions(self) -> list[str]:
        if not self.report_targets:
            return []
        raw_targets = (
            self.report_targets()
            if callable(self.report_targets)
            else self.report_targets
        )
        return normalize_delivery_targets(raw_targets or [])
