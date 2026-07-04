"""Observation JSONL serialization and normalization.

Hot per-record methods (``normalize_record`` / ``record_to_json`` /
``json_line`` / ``dedupe_key``) delegate to the Rust implementation in
``mine_sentinel_rs`` when available; the streaming JSONL readers stay on
Python because they are I/O-bound and already cheap relative to the parsing
they avoid. Falls back to pure-Python if the native extension is missing.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

try:
    from mine_sentinel_rs import (  # type: ignore[import-not-found]
        ObservationRecordCodec as _RsObservationRecordCodec,
    )

    _HAS_RUST = True
except ImportError:  # pragma: no cover
    _HAS_RUST = False

from ..models import MineSentinelConfig, ObservationRecord


class ObservationRecordCodec:
    """Converts observation records to bounded JSONL-safe payloads."""

    def __init__(self, config: MineSentinelConfig):
        self.config = config
        if _HAS_RUST:
            self._rs = _RsObservationRecordCodec(
                config.storage.max_content_length,
                config.max_tags_per_record,
                config.max_metric_fields,
                config.max_raw_fields,
                config.storage.include_raw,
                config.dedupe_window_seconds,
            )
        else:
            self._rs = None

    def normalize_record(self, record: ObservationRecord):
        # Stays in Python: this method mutates the dataclass in place by
        # reassigning its fields with rebuilt dicts. Every value's type check
        # (`compact_value`) crosses the PyO3 boundary in Rust, while Python's
        # `isinstance` is a single C-level opcode — measured ~1.4x slower in
        # Rust. The Rust `normalize_record` is still exposed for callers that
        # want a single boundary crossing, but the default path stays Python
        # for raw throughput. The other codec methods return a value, so they
        # benefit from Rust's serde_json path; this one doesn't.
        record.content = self.truncate(
            record.content,
            self.config.storage.max_content_length,
        )
        record.tags = [
            self.truncate(str(tag), self.config.storage.max_content_length)
            for tag in record.tags[: self.config.max_tags_per_record]
        ]
        record.metrics = self.compact_dict(
            record.metrics,
            self.config.max_metric_fields,
        )
        record.context = self.compact_dict(record.context, self.config.max_raw_fields)
        if self.config.storage.include_raw:
            record.raw = self.compact_dict(record.raw, self.config.max_raw_fields)
        else:
            record.raw = {}

    def record_to_json(self, record: ObservationRecord) -> dict[str, Any]:
        if self._rs is not None:
            return self._rs.record_to_json(record)
        return {
            "eventId": record.event_id,
            "kind": record.kind,
            "timestamp": record.timestamp,
            "serverId": record.server_id,
            "serverName": record.server_name,
            "backendServer": record.backend_server,
            "proxyId": record.proxy_id,
            "player": {
                "name": record.player_name,
                "uuidHash": record.player_uuid_hash,
            },
            "content": record.content,
            "tags": record.tags,
            "context": record.context,
            "metrics": record.metrics,
            "raw": record.raw if self.config.storage.include_raw else {},
        }

    def json_line(self, record: ObservationRecord) -> str:
        if self._rs is not None:
            return self._rs.json_line(record)
        return json.dumps(
            self.record_to_json(record),
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def read_jsonl(self, path: Path):
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(data, dict):
                        yield data
        except FileNotFoundError:
            return

    def read_jsonl_window(
        self,
        path: Path,
        cutoff_ms: int,
        end_ms: int | None = None,
    ):
        """Yield JSONL rows whose timestamp falls in [cutoff_ms, end_ms).

        JSONL files are append-only and written in timestamp order, so once a
        record's timestamp exceeds ``end_ms`` we can stop scanning the rest of
        the file. Records older than ``cutoff_ms`` are skipped but scanning
        continues because earlier files may still contain in-window rows.
        """
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(data, dict):
                        continue
                    ts = data.get("timestamp")
                    if not isinstance(ts, (int, float)):
                        yield data
                        continue
                    if ts < cutoff_ms:
                        continue
                    if end_ms is not None and ts > end_ms:
                        break
                    yield data
        except FileNotFoundError:
            return

    def dedupe_key(self, record: ObservationRecord) -> str:
        if self._rs is not None:
            return self._rs.dedupe_key(record)
        if record.event_id:
            return record.event_id
        bucket = record.timestamp // max(1, self.config.dedupe_window_seconds * 1000)
        content = " ".join(record.content.lower().split())
        raw = "|".join(
            [
                record.kind,
                record.server_id,
                record.identity,
                content,
                str(bucket),
            ]
        )
        # blake2b digest keeps keys fixed-length (32 hex chars) regardless of
        # content length. With 100k+ keys in DedupeTracker's set or SQLite,
        # shorter keys mean less memory and faster comparisons. 128-bit digest
        # makes collisions practically impossible for this scale.
        return "h:" + hashlib.blake2b(raw.encode("utf-8"), digest_size=16).hexdigest()

    def compact_dict(self, data: dict[str, Any], max_fields: int) -> dict[str, Any]:
        compact: dict[str, Any] = {}
        for key, value in list((data or {}).items())[:max_fields]:
            compact[str(key)] = self.compact_value(value)
        return compact

    def compact_value(self, value: Any) -> Any:
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return self.truncate(value, self.config.storage.max_content_length)
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            text = str(value)
        return self.truncate(text, self.config.storage.max_content_length)

    @staticmethod
    def truncate(value: str, max_length: int) -> str:
        if max_length <= 0:
            return ""
        if len(value) <= max_length:
            return value
        if max_length <= 3:
            return value[:max_length]
        return value[: max_length - 3] + "..."
