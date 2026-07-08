"""Disk-backed MineSentinel observation store."""

from __future__ import annotations

import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from ..models import MineSentinelConfig, ObservationRecord
from .codec import ObservationRecordCodec
from .dedupe import DedupeTracker
from .models import RecentObservationWindow
from .paths import (
    candidate_files,
    cleanup_old_files,
    export_path,
    record_path,
    safe_name,
)
from .window import RecentWindowBuilder
from ..reporting.dialogue_rules import dialogue_rules_from_config


WRITE_BUFFER_LINES = 1024


class DiskObservationStore:
    """Append-only JSONL store used as the complete report source."""

    def __init__(self, config: MineSentinelConfig, root_dir: Path):
        self.config = config
        self.root_dir = root_dir
        self.observation_dir = root_dir / "observations"
        self.export_dir = root_dir / "exports"
        self.codec = ObservationRecordCodec(config)
        self.observation_dir.mkdir(parents=True, exist_ok=True)
        self.export_dir.mkdir(parents=True, exist_ok=True)
        self._last_cleanup_at: float | None = None
        self._recent_ingest_chat: dict[str, int] = {}
        self._last_ingest_prune_ms = 0

    def add_batch(self, server_id: str, payload: dict[str, Any]) -> int:
        if not self.config.enabled or not self.config.storage.enabled:
            return 0

        observations = payload.get("observations") or []
        if not isinstance(observations, list):
            return 0

        now = time.time()
        now_ms = int(now * 1000)
        cutoff_ms = int((now - self.config.storage.retention_minutes * 60) * 1000)
        batch_server_id = str(payload.get("serverId") or server_id)
        batch_server_name = str(payload.get("serverName") or batch_server_id)

        written = 0
        buffers: dict[Path, list[str]] = {}
        handles = {}
        path_cache: dict[tuple[str, str], Path] = {}
        with ExitStack() as stack:
            with self._dedupe_tracker() as batch_seen:
                for item in observations:
                    if not isinstance(item, dict):
                        continue
                    timestamp = _raw_timestamp(item)
                    if timestamp and timestamp < cutoff_ms:
                        continue
                    dedupe_keys = self.codec.raw_dedupe_keys(item, batch_server_id)
                    if batch_seen.seen_any_or_add_all(dedupe_keys):
                        continue
                    if self._seen_recent_ingest_chat(
                        item,
                        batch_server_id,
                        now_ms,
                        dedupe_keys,
                    ):
                        continue
                    record = ObservationRecord.from_dict(
                        item,
                        batch_server_id,
                        batch_server_name,
                    )
                    if not record.server_id:
                        record.server_id = batch_server_id
                    if record.timestamp and record.timestamp < cutoff_ms:
                        continue
                    self.codec.normalize_record(record)
                    path = self._record_path_cached(record, path_cache)
                    buffer = buffers.setdefault(path, [])
                    buffer.append(self.codec.json_line(record))
                    written += 1
                    if len(buffer) >= WRITE_BUFFER_LINES:
                        handle = self._append_handle(path, handles, stack)
                        _flush_lines(handle, buffer)

            for path, buffer in buffers.items():
                if buffer:
                    handle = self._append_handle(path, handles, stack)
                    _flush_lines(handle, buffer)

        self.cleanup_if_due(now)
        return written

    def recent(
        self,
        window_minutes: int,
        server_id: str | None = None,
    ) -> list[ObservationRecord]:
        return self.recent_window(window_minutes, server_id).records

    def recent_window(
        self,
        window_minutes: int,
        server_id: str | None = None,
        max_records: int | None = None,
    ) -> RecentObservationWindow:
        if not self.config.enabled or not self.config.storage.enabled:
            return RecentObservationWindow([], 0, 0, False, 0)

        cutoff_ms = int((time.time() - window_minutes * 60) * 1000)
        limit = max(1, max_records or self.config.report.max_records_in_memory)
        builder = RecentWindowBuilder(
            limit,
            dialogue_rules_from_config(self.config.dialogue.custom_rules),
        )
        with self._dedupe_tracker() as seen:
            for row in self._iter_recent_rows(server_id, cutoff_ms, seen):
                builder.add_raw(row, lambda row=row: ObservationRecord.from_dict(row))
        return builder.build()

    def export_records(
        self,
        records: list[ObservationRecord],
        window_minutes: int,
        server_id: str | None = None,
        label: str = "",
    ) -> Path | None:
        if not records:
            return None
        now = int(time.time())
        path = export_path(self.export_dir, window_minutes, server_id, label, now)
        with path.open("w", encoding="utf-8") as handle:
            buffer: list[str] = []
            for record in records:
                buffer.append(self.codec.json_line(record))
                if len(buffer) >= WRITE_BUFFER_LINES:
                    _flush_lines(handle, buffer)
            if buffer:
                _flush_lines(handle, buffer)
        return path

    def export_recent(
        self,
        window_minutes: int,
        server_id: str | None = None,
        label: str = "",
    ) -> Path | None:
        if not self.config.enabled or not self.config.storage.enabled:
            return None

        now = int(time.time())
        cutoff_ms = int((now - window_minutes * 60) * 1000)
        path = export_path(self.export_dir, window_minutes, server_id, label, now)

        written = 0
        buffer: list[str] = []
        with self._dedupe_tracker() as seen:
            with path.open("w", encoding="utf-8") as handle:
                for _row, line in self._iter_recent_row_lines(server_id, cutoff_ms, seen):
                    buffer.append(line)
                    written += 1
                    if len(buffer) >= WRITE_BUFFER_LINES:
                        _flush_lines(handle, buffer)
                if buffer:
                    _flush_lines(handle, buffer)
        if not written:
            path.unlink(missing_ok=True)
            return None
        return path

    def cleanup_if_due(self, now: float | None = None) -> bool:
        current = time.time() if now is None else now
        interval = max(0, self.config.storage.cleanup_interval_seconds)
        if (
            interval > 0
            and self._last_cleanup_at is not None
            and current - self._last_cleanup_at < interval
        ):
            return False
        self.cleanup()
        self._last_cleanup_at = current
        return True

    def cleanup(self):
        cleanup_old_files(
            self.observation_dir,
            self.export_dir,
            self.config.storage.retention_minutes,
        )

    def _record_path(self, record: ObservationRecord) -> Path:
        return record_path(self.observation_dir, record)

    def _record_path_cached(
        self,
        record: ObservationRecord,
        cache: dict[tuple[str, str], Path],
    ) -> Path:
        server_id = record.server_id or "unknown"
        day = time.strftime("%Y%m%d", time.localtime(max(0, record.timestamp) / 1000))
        key = (server_id, day)
        path = cache.get(key)
        if path is None:
            path = self.observation_dir / safe_name(server_id) / f"{day}.jsonl"
            cache[key] = path
        return path

    def _candidate_files(
        self,
        server_id: str | None,
        cutoff_ms: int | None = None,
    ) -> list[Path]:
        return candidate_files(self.observation_dir, server_id, cutoff_ms)

    def _read_jsonl(self, path: Path):
        yield from self.codec.read_jsonl(path)

    def _normalize_record(self, record: ObservationRecord):
        self.codec.normalize_record(record)

    def _record_to_json(self, record: ObservationRecord) -> dict[str, Any]:
        return self.codec.record_to_json(record)

    def _compact_dict(self, data: dict[str, Any], max_fields: int) -> dict[str, Any]:
        return self.codec.compact_dict(data, max_fields)

    def _compact_value(self, value: Any) -> Any:
        return self.codec.compact_value(value)

    def _dedupe_key(self, record: ObservationRecord) -> str:
        return self.codec.dedupe_key(record)

    def _seen_recent_ingest_chat(
        self,
        data: dict[str, Any],
        batch_server_id: str,
        now_ms: int,
        dedupe_keys: tuple[str, ...] | None = None,
    ) -> bool:
        keys = self._raw_chat_content_keys(data, batch_server_id, dedupe_keys)
        if not keys:
            return False
        self._prune_recent_ingest_chat(now_ms)
        if any(key in self._recent_ingest_chat for key in keys):
            return True
        timestamp = _raw_timestamp(data) or now_ms
        for key in keys:
            self._recent_ingest_chat[key] = timestamp
        return False

    def _raw_chat_content_keys(
        self,
        data: dict[str, Any],
        batch_server_id: str,
        dedupe_keys: tuple[str, ...] | None = None,
    ) -> tuple[str, ...]:
        if str(data.get("kind") or "") != "CHAT" or data.get("eventId"):
            return ()
        if dedupe_keys:
            return dedupe_keys
        return self.codec.raw_chat_content_dedupe_keys(data, batch_server_id)

    def _prune_recent_ingest_chat(self, now_ms: int):
        window_ms = max(1, self.config.dedupe_window_seconds) * 1000
        if now_ms - self._last_ingest_prune_ms < window_ms:
            return
        cutoff_ms = now_ms - window_ms * 2
        self._recent_ingest_chat = {
            key: timestamp
            for key, timestamp in self._recent_ingest_chat.items()
            if timestamp >= cutoff_ms
        }
        self._last_ingest_prune_ms = now_ms

    def _iter_recent_rows(
        self,
        server_id: str | None,
        cutoff_ms: int,
        seen: DedupeTracker,
    ):
        for path in self._candidate_files(server_id, cutoff_ms):
            for row in self.codec.read_jsonl(path, cutoff_ms):
                timestamp = _raw_timestamp(row)
                if timestamp < cutoff_ms:
                    continue
                if self.codec.seen_or_add_raw(seen, row):
                    continue
                yield row

    def _iter_recent_row_lines(
        self,
        server_id: str | None,
        cutoff_ms: int,
        seen: DedupeTracker,
    ):
        for path in self._candidate_files(server_id, cutoff_ms):
            for row, line in self.codec.read_jsonl_with_lines(path, cutoff_ms):
                timestamp = _raw_timestamp(row)
                if timestamp < cutoff_ms:
                    continue
                if self.codec.seen_or_add_raw(seen, row):
                    continue
                yield row, line

    @staticmethod
    def _append_handle(path: Path, handles: dict, stack: ExitStack):
        handle = handles.get(path)
        if handle is None:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = stack.enter_context(path.open("a", encoding="utf-8"))
            handles[path] = handle
        return handle

    def _dedupe_tracker(self) -> DedupeTracker:
        return DedupeTracker(
            max_memory_keys=self.config.storage.dedupe_memory_limit,
            temp_dir=self.root_dir / "tmp",
        )

    @staticmethod
    def _safe_name(value: str) -> str:
        return safe_name(value)

    @staticmethod
    def _truncate(value: str, max_length: int) -> str:
        return ObservationRecordCodec.truncate(value, max_length)


def _raw_timestamp(data: dict[str, Any]) -> int:
    try:
        return int(data.get("timestamp") or 0)
    except (TypeError, ValueError):
        return 0


def _flush_lines(handle, lines: list[str]):
    handle.write("\n".join(lines))
    handle.write("\n")
    lines.clear()
