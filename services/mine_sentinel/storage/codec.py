"""Observation JSONL serialization and normalization."""

from __future__ import annotations

import json
from hashlib import blake2s
from pathlib import Path
from typing import Any

from ..models import MineSentinelConfig, ObservationRecord
from ..reporting.dialogue_terms import message_fingerprint


JSON_SEPARATORS = (",", ":")


class ObservationRecordCodec:
    """Converts observation records to bounded JSONL-safe payloads."""

    def __init__(self, config: MineSentinelConfig):
        self.config = config

    def normalize_record(self, record: ObservationRecord):
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
        return json.dumps(
            self.record_to_json(record),
            ensure_ascii=False,
            separators=JSON_SEPARATORS,
        )

    def json_data_line(self, data: dict[str, Any]) -> str:
        return json.dumps(
            data,
            ensure_ascii=False,
            separators=JSON_SEPARATORS,
        )

    def read_jsonl(self, path: Path, cutoff_ms: int | None = None):
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line or line == "\n":
                        continue
                    if _line_timestamp_before_cutoff(line, cutoff_ms):
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(data, dict):
                        yield data
        except FileNotFoundError:
            return

    def read_jsonl_with_lines(self, path: Path, cutoff_ms: int | None = None):
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line or line == "\n":
                        continue
                    if _line_timestamp_before_cutoff(line, cutoff_ms):
                        continue
                    raw_line = line.rstrip("\r\n")
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(data, dict):
                        yield data, raw_line
        except FileNotFoundError:
            return

    def dedupe_key(self, record: ObservationRecord) -> str:
        return self.dedupe_keys(record)[0]

    def dedupe_keys(self, record: ObservationRecord) -> tuple[str, ...]:
        keys = []
        if record.event_id:
            keys.append(f"event|{record.event_id}")
        if record.kind == "CHAT":
            keys.extend(
                self._content_dedupe_keys(
                    record.kind,
                    record.server_id,
                    record.identity,
                    record.content,
                    record.timestamp,
                )
            )
        elif not record.event_id:
            keys.append(self._record_payload_dedupe_key(record))
        return tuple(keys)

    @staticmethod
    def _content_dedupe_text(record: ObservationRecord) -> str:
        if record.kind == "CHAT":
            return message_fingerprint(record.content)
        return " ".join(record.content.lower().split())

    def raw_dedupe_keys(
        self,
        data: dict[str, Any],
        batch_server_id: str = "",
    ) -> tuple[str, ...]:
        event_id = str(data.get("eventId") or "")
        kind = str(data.get("kind") or "")
        if event_id and kind != "CHAT":
            return (f"event|{event_id}",)
        timestamp = self._raw_int(data.get("timestamp"))
        server_id = str(data.get("serverId") or batch_server_id)
        player = data.get("player") or {}
        if not isinstance(player, dict):
            player = {}
        identity = str(player.get("uuidHash") or player.get("name") or "")
        content = str(data.get("content") or "")

        keys = []
        if event_id:
            keys.append(f"event|{event_id}")
        if kind == "CHAT":
            keys.extend(self._content_dedupe_keys(kind, server_id, identity, content, timestamp))
        elif not event_id:
            keys.append(self._raw_payload_dedupe_key(data, server_id, timestamp))
        return tuple(keys)

    def raw_chat_content_dedupe_key(
        self,
        data: dict[str, Any],
        batch_server_id: str = "",
    ) -> str:
        keys = self.raw_chat_content_dedupe_keys(data, batch_server_id)
        return keys[0] if keys else ""

    def raw_chat_content_dedupe_keys(
        self,
        data: dict[str, Any],
        batch_server_id: str = "",
    ) -> tuple[str, ...]:
        kind = str(data.get("kind") or "")
        if kind != "CHAT" or data.get("eventId"):
            return ()
        player = data.get("player") or {}
        if not isinstance(player, dict):
            player = {}
        return self._content_dedupe_keys(
            kind,
            str(data.get("serverId") or batch_server_id),
            str(player.get("uuidHash") or player.get("name") or ""),
            str(data.get("content") or ""),
            self._raw_int(data.get("timestamp")),
        )

    def _content_dedupe_keys(
        self,
        kind: str,
        server_id: str,
        identity: str,
        content: str,
        timestamp: int,
    ) -> tuple[str, ...]:
        bucket = timestamp // max(1, self.config.dedupe_window_seconds * 1000)
        content_key = self._content_dedupe_content_key(kind, content)
        return (
            self._content_dedupe_key(kind, server_id, identity, content_key, bucket),
            self._content_dedupe_key(kind, server_id, identity, content_key, bucket - 1),
        )

    @staticmethod
    def _content_dedupe_content_key(kind: str, content: str) -> str:
        if kind == "CHAT":
            return message_fingerprint(content)
        return " ".join(content.lower().split())

    @staticmethod
    def _content_dedupe_key(
        kind: str,
        server_id: str,
        identity: str,
        content_key: str,
        bucket: int,
    ) -> str:
        return f"content|{kind}|{server_id}|{identity}|{content_key}|{bucket}"

    def seen_or_add_record(self, tracker, record: ObservationRecord) -> bool:
        return tracker.seen_any_or_add_all(self.dedupe_keys(record))

    def seen_or_add_raw(self, tracker, data: dict[str, Any]) -> bool:
        return tracker.seen_any_or_add_all(self.raw_dedupe_keys(data))

    def seen_or_add_raw_batch(
        self,
        tracker,
        data: dict[str, Any],
        batch_server_id: str,
    ) -> bool:
        return tracker.seen_any_or_add_all(
            self.raw_dedupe_keys(data, batch_server_id)
        )

    def legacy_content_dedupe_key(self, record: ObservationRecord) -> str:
        bucket = record.timestamp // max(1, self.config.dedupe_window_seconds * 1000)
        return "|".join(
            [
                record.kind,
                record.server_id,
                record.identity,
                self._content_dedupe_text(record),
                str(bucket),
            ]
        )

    def _record_payload_dedupe_key(self, record: ObservationRecord) -> str:
        return self._payload_dedupe_key_from_values(
            record.kind,
            record.server_id,
            record.identity,
            record.timestamp,
            (
                record.backend_server,
                record.content,
                record.context,
                record.metrics,
                record.proxy_id,
                record.raw if self.config.storage.include_raw else {},
                record.tags,
            ),
        )

    def _raw_payload_dedupe_key(
        self,
        data: dict[str, Any],
        server_id: str,
        timestamp: int,
    ) -> str:
        player = data.get("player") or {}
        if not isinstance(player, dict):
            player = {}
        identity = str(player.get("uuidHash") or player.get("name") or "")
        return self._payload_dedupe_key_from_values(
            str(data.get("kind") or ""),
            server_id,
            identity,
            timestamp,
            (
                data.get("backendServer") or "",
                data.get("content") or "",
                data.get("context") or {},
                data.get("metrics") or {},
                data.get("proxyId") or "",
                data.get("raw") or {},
                data.get("tags") or [],
            ),
        )

    def _payload_dedupe_key_from_values(
        self,
        kind: str,
        server_id: str,
        identity: str,
        timestamp: int,
        values: tuple[Any, ...],
    ) -> str:
        try:
            payload_json = json.dumps(
                values,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
                separators=JSON_SEPARATORS,
            )
        except Exception:
            payload_json = str(values)
        payload_key = blake2s(payload_json.encode("utf-8"), digest_size=16).hexdigest()
        return f"payload|{kind}|{server_id}|{identity}|{timestamp}|{payload_key}"

    @staticmethod
    def _raw_int(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def compact_dict(self, data: dict[str, Any], max_fields: int) -> dict[str, Any]:
        compact: dict[str, Any] = {}
        for index, (key, value) in enumerate((data or {}).items()):
            if index >= max_fields:
                break
            compact[str(key)] = self.compact_value(value)
        return compact

    def compact_value(self, value: Any) -> Any:
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return self.truncate(value, self.config.storage.max_content_length)
        if isinstance(value, (dict, list, tuple, set)):
            return self._compact_collection_value(value)
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            text = str(value)
        return self.truncate(text, self.config.storage.max_content_length)

    def _compact_collection_value(self, value: Any) -> str:
        preview = self._bounded_json_preview(value)
        try:
            text = json.dumps(
                preview,
                ensure_ascii=False,
                default=str,
                separators=JSON_SEPARATORS,
            )
        except Exception:
            text = str(preview)
        return self.truncate(text, self.config.storage.max_content_length)

    def _bounded_json_preview(self, value: Any, depth: int = 0) -> Any:
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return self.truncate(value, self.config.storage.max_content_length)

        max_items = max(1, self.config.max_raw_fields)
        if isinstance(value, dict):
            preview: dict[str, Any] = {}
            for index, (key, item) in enumerate(value.items()):
                if index >= max_items:
                    preview["..."] = f"{len(value) - max_items} more"
                    break
                preview[str(key)] = self._bounded_nested_preview(item, depth)
            return preview

        if isinstance(value, (list, tuple, set)):
            preview_list: list[Any] = []
            for index, item in enumerate(value):
                if index >= max_items:
                    preview_list.append(f"... {len(value) - max_items} more")
                    break
                preview_list.append(self._bounded_nested_preview(item, depth))
            return preview_list

        return self.truncate(str(value), self.config.storage.max_content_length)

    def _bounded_nested_preview(self, value: Any, depth: int) -> Any:
        if depth >= 2 and isinstance(value, (dict, list, tuple, set)):
            return f"<{type(value).__name__}>"
        return self._bounded_json_preview(value, depth + 1)

    @staticmethod
    def truncate(value: str, max_length: int) -> str:
        if max_length <= 0:
            return ""
        if len(value) <= max_length:
            return value
        if max_length <= 3:
            return value[:max_length]
        return value[: max_length - 3] + "..."


def _line_timestamp_before_cutoff(line: str, cutoff_ms: int | None) -> bool:
    if cutoff_ms is None:
        return False
    timestamp = _line_timestamp(line)
    return timestamp is not None and timestamp < cutoff_ms


def _line_timestamp(line: str) -> int | None:
    key_index = line.find('"timestamp"')
    if key_index < 0:
        return None
    colon_index = line.find(":", key_index + 11)
    if colon_index < 0:
        return None

    index = colon_index + 1
    length = len(line)
    while index < length and line[index].isspace():
        index += 1
    if index >= length:
        return None

    quoted = line[index] == '"'
    if quoted:
        index += 1

    start = index
    if index < length and line[index] == "-":
        index += 1
    while index < length and line[index].isdigit():
        index += 1
    if index == start or (line[start] == "-" and index == start + 1):
        return None
    try:
        return int(line[start:index])
    except ValueError:
        return None
