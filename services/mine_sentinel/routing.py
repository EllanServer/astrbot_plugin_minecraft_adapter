"""MineSentinel target-session routing helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from .models import ObservationRecord


QQ_TARGET_TYPES = {
    "group": "GroupMessage",
    "qq_group": "GroupMessage",
    "qqgroup": "GroupMessage",
    "friend": "FriendMessage",
    "private": "FriendMessage",
    "qq": "FriendMessage",
    "user": "FriendMessage",
    "qq_user": "FriendMessage",
}


def normalize_delivery_target(target: Any) -> str:
    """Convert report delivery shorthand into an AstrBot/NapCat UMO."""
    if isinstance(target, dict):
        raw_id = target.get("id") or target.get("target") or target.get("qq")
        target_type = str(
            target.get("type") or target.get("message_type") or target.get("kind") or ""
        ).strip()
        platform = str(target.get("platform") or "aiocqhttp").strip() or "aiocqhttp"
        if not raw_id:
            return ""
        if target_type in {"GroupMessage", "FriendMessage"}:
            return f"{platform}:{target_type}:{str(raw_id).strip()}"
        mapped = QQ_TARGET_TYPES.get(target_type.lower())
        if mapped:
            return f"{platform}:{mapped}:{str(raw_id).strip()}"
        return str(raw_id).strip()

    text = str(target or "").strip()
    if not text:
        return ""
    if text.count(":") >= 2:
        return text
    if ":" in text:
        prefix, raw_id = text.split(":", 1)
        mapped = QQ_TARGET_TYPES.get(prefix.strip().lower())
        raw_id = raw_id.strip()
        if mapped and raw_id:
            return f"aiocqhttp:{mapped}:{raw_id}"
        return text
    if text.isdigit():
        return f"aiocqhttp:GroupMessage:{text}"
    return text


def normalize_delivery_targets(targets: Iterable[Any]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for target in targets:
        umo = normalize_delivery_target(target)
        if umo and umo not in seen:
            seen.add(umo)
            normalized.append(umo)
    return normalized


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
        return sorted({str(session) for session in raw_sessions if session})

    def _report_sessions(self) -> list[str]:
        if not self.report_targets:
            return []
        raw_targets = (
            self.report_targets()
            if callable(self.report_targets)
            else self.report_targets
        )
        return normalize_delivery_targets(raw_targets or [])
