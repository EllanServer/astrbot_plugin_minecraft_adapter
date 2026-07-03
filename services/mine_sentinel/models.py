"""MineSentinel models and configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


DEFAULT_REPORT_INTERVAL_HOURS = 8
DEFAULT_REPORT_INTERVAL_MINUTES = DEFAULT_REPORT_INTERVAL_HOURS * 60


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _positive_int(value: Any, default: int) -> int:
    return max(1, _as_int(value, default))


def _report_interval_minutes(report_data: dict[str, Any]) -> int:
    if "interval_hours" in report_data and report_data.get("interval_hours") not in (
        None,
        "",
    ):
        hours = _as_float(report_data.get("interval_hours"), DEFAULT_REPORT_INTERVAL_HOURS)
        return max(1, int(round(hours * 60)))
    return _positive_int(
        report_data.get("interval_minutes"),
        DEFAULT_REPORT_INTERVAL_MINUTES,
    )


@dataclass
class MineSentinelReportConfig:
    default_window_minutes: int = DEFAULT_REPORT_INTERVAL_MINUTES
    send_to_target_sessions: bool = True
    delivery_targets: list[Any] = field(default_factory=list)
    include_evidence_samples: bool = True
    max_evidence_samples: int = 5
    provider_id: str = ""
    enabled: bool = True
    interval_minutes: int = DEFAULT_REPORT_INTERVAL_MINUTES
    cooldown_seconds: int = 600
    max_ai_records: int = 120
    max_records_in_memory: int = 50000
    max_ai_prompt_chars: int = 100000
    max_ai_content_length: int = 240
    send_full_log_file: bool = True
    send_as_image: bool = True


@dataclass
class MineSentinelAlertConfig:
    enabled: bool = False
    min_severity: str = "high"
    cooldown_seconds: int = 600
    min_evidence_count: int = 3
    min_unique_players: int = 2
    window_minutes: int = 30
    analysis_interval_seconds: int = 60


@dataclass
class MineSentinelStorageConfig:
    enabled: bool = True
    retention_minutes: int = DEFAULT_REPORT_INTERVAL_MINUTES
    cleanup_interval_seconds: int = 300
    include_raw: bool = False
    max_content_length: int = 4000
    dedupe_memory_limit: int = 100000


@dataclass
class MineSentinelDialogueConfig:
    enabled: bool = True
    min_issue_score: float = 2.0
    min_evidence_count: int = 1
    max_findings: int = 10
    max_issue_records: int = 50
    incident_gap_seconds: int = 1800
    continuation_window_seconds: int = 90
    context_window_seconds: int = 120
    context_messages_per_side: int = 2
    custom_rules: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class MineSentinelConfig:
    enabled: bool = True
    retention_minutes: int = DEFAULT_REPORT_INTERVAL_MINUTES
    max_tags_per_record: int = 8
    max_metric_fields: int = 32
    max_raw_fields: int = 16
    dedupe_window_seconds: int = 120
    storage: MineSentinelStorageConfig = field(default_factory=MineSentinelStorageConfig)
    dialogue: MineSentinelDialogueConfig = field(default_factory=MineSentinelDialogueConfig)
    report: MineSentinelReportConfig = field(default_factory=MineSentinelReportConfig)
    alert: MineSentinelAlertConfig = field(default_factory=MineSentinelAlertConfig)

    @classmethod
    def from_dict(cls, data: dict | None) -> "MineSentinelConfig":
        data = data or {}
        storage_data = data.get("storage", {}) or {}
        dialogue_data = data.get("dialogue", {}) or {}
        report_data = data.get("report", {}) or {}
        alert_data = data.get("alert", {}) or {}
        interval_minutes = _report_interval_minutes(report_data)
        default_window_minutes = _positive_int(
            report_data.get("default_window_minutes"),
            interval_minutes,
        )
        retention_minutes = _positive_int(
            data.get("retention_minutes"),
            max(DEFAULT_REPORT_INTERVAL_MINUTES, default_window_minutes, interval_minutes),
        )
        return cls(
            enabled=data.get("enabled", True),
            retention_minutes=max(retention_minutes, default_window_minutes),
            max_tags_per_record=_positive_int(data.get("max_tags_per_record"), 8),
            max_metric_fields=_positive_int(data.get("max_metric_fields"), 32),
            max_raw_fields=_positive_int(data.get("max_raw_fields"), 16),
            dedupe_window_seconds=_positive_int(data.get("dedupe_window_seconds"), 120),
            storage=MineSentinelStorageConfig(
                enabled=bool(storage_data.get("enabled", True)),
                retention_minutes=max(
                    _positive_int(storage_data.get("retention_minutes"), retention_minutes),
                    default_window_minutes,
                ),
                cleanup_interval_seconds=max(
                    0,
                    _as_int(storage_data.get("cleanup_interval_seconds"), 300),
                ),
                include_raw=bool(storage_data.get("include_raw", False)),
                max_content_length=_positive_int(
                    storage_data.get("max_content_length"),
                    4000,
                ),
                dedupe_memory_limit=_positive_int(
                    storage_data.get("dedupe_memory_limit"),
                    100000,
                ),
            ),
            dialogue=MineSentinelDialogueConfig(
                enabled=bool(dialogue_data.get("enabled", True)),
                min_issue_score=max(
                    0.0,
                    _as_float(dialogue_data.get("min_issue_score"), 2.0),
                ),
                min_evidence_count=_positive_int(
                    dialogue_data.get("min_evidence_count"),
                    1,
                ),
                max_findings=_positive_int(dialogue_data.get("max_findings"), 10),
                max_issue_records=_positive_int(
                    dialogue_data.get("max_issue_records"),
                    50,
                ),
                incident_gap_seconds=max(
                    0,
                    _as_int(dialogue_data.get("incident_gap_seconds"), 1800),
                ),
                continuation_window_seconds=max(
                    0,
                    _as_int(dialogue_data.get("continuation_window_seconds"), 90),
                ),
                context_window_seconds=max(
                    0,
                    _as_int(dialogue_data.get("context_window_seconds"), 120),
                ),
                context_messages_per_side=max(
                    0,
                    _as_int(dialogue_data.get("context_messages_per_side"), 2),
                ),
                custom_rules=[
                    dict(item)
                    for item in (dialogue_data.get("custom_rules") or [])
                    if isinstance(item, dict)
                ],
            ),
            report=MineSentinelReportConfig(
                default_window_minutes=default_window_minutes,
                send_to_target_sessions=bool(report_data.get("send_to_target_sessions", True)),
                delivery_targets=[
                    item
                    for item in (report_data.get("delivery_targets") or [])
                    if item not in (None, "")
                ],
                include_evidence_samples=bool(report_data.get("include_evidence_samples", True)),
                max_evidence_samples=_positive_int(report_data.get("max_evidence_samples"), 5),
                provider_id=str(report_data.get("provider_id", "")),
                enabled=bool(report_data.get("enabled", True)),
                interval_minutes=interval_minutes,
                cooldown_seconds=max(0, _as_int(report_data.get("cooldown_seconds"), 600)),
                max_ai_records=_positive_int(report_data.get("max_ai_records"), 120),
                max_records_in_memory=_positive_int(
                    report_data.get("max_records_in_memory"),
                    50000,
                ),
                max_ai_prompt_chars=_positive_int(
                    report_data.get("max_ai_prompt_chars"),
                    100000,
                ),
                max_ai_content_length=_positive_int(
                    report_data.get("max_ai_content_length"),
                    240,
                ),
                send_full_log_file=bool(report_data.get("send_full_log_file", True)),
                send_as_image=bool(report_data.get("send_as_image", True)),
            ),
            alert=MineSentinelAlertConfig(
                enabled=bool(alert_data.get("enabled", False)),
                min_severity=str(alert_data.get("min_severity", "high")),
                cooldown_seconds=max(0, _as_int(alert_data.get("cooldown_seconds"), 600)),
                min_evidence_count=_positive_int(alert_data.get("min_evidence_count"), 3),
                min_unique_players=_positive_int(alert_data.get("min_unique_players"), 2),
                window_minutes=_positive_int(alert_data.get("window_minutes"), 30),
                analysis_interval_seconds=max(
                    0,
                    _as_int(alert_data.get("analysis_interval_seconds"), 60),
                ),
            ),
        )


@dataclass(slots=True)
class ObservationRecord:
    event_id: str = ""
    kind: str = ""
    timestamp: int = 0
    server_id: str = ""
    server_name: str = ""
    backend_server: str = ""
    proxy_id: str = ""
    player_name: str = ""
    player_uuid_hash: str = ""
    content: str = ""
    tags: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], batch_server_id: str = "", batch_server_name: str = ""
    ) -> "ObservationRecord":
        player = data.get("player") or {}
        return cls(
            event_id=str(data.get("eventId") or ""),
            kind=str(data.get("kind") or ""),
            timestamp=_as_int(data.get("timestamp"), 0),
            server_id=str(data.get("serverId") or batch_server_id),
            server_name=str(data.get("serverName") or batch_server_name),
            backend_server=str(data.get("backendServer") or ""),
            proxy_id=str(data.get("proxyId") or ""),
            player_name=str(player.get("name") or ""),
            player_uuid_hash=str(player.get("uuidHash") or ""),
            content=str(data.get("content") or ""),
            tags=[str(t) for t in data.get("tags", []) if t is not None],
            context=dict(data.get("context") or {}),
            metrics=dict(data.get("metrics") or {}),
            raw=dict(data.get("raw") or {}),
        )

    @property
    def identity(self) -> str:
        return self.player_uuid_hash or self.player_name

    def evidence_text(self) -> str:
        source = self.backend_server or self.server_id
        player = f"{self.player_name}: " if self.player_name else ""
        if self.kind == "SERVER_METRICS":
            return f"[{source}] metrics {self.metrics}"
        return f"[{source}] {player}{self.content}".strip()
