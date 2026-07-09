"""Disk-backed MineSentinel observation store."""

from __future__ import annotations

import gzip
import threading
import time
from contextlib import ExitStack
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..models import MineSentinelConfig, ObservationRecord
from .codec import ObservationRecordCodec
from .dedupe import DedupeTracker
from .models import RecentObservationWindow
from .offset_index import JsonlOffsetIndex
from .paths import (
    candidate_files,
    cleanup_old_files,
    export_path,
    record_path,
    safe_name,
)
from .window import RecentWindowBuilder


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
        # 全局写/读锁：DiskObservationStore 的 add_batch / recent_window /
        # export_recent / cleanup 会并发操作同一组磁盘文件与内存缓存，
        # io_workers>0 时通过 to_thread 并发调用可达竞态。用一把可重入锁
        # 串行化所有公共方法，保证缓存/索引/JSONL 写入的一致性。可重入
        # 允许 cleanup_if_due 在 add_batch 持锁时安全调用。
        self._lock = threading.RLock()
        # Short-lived cache for the most recent window read, so that an alert
        # triggered right after a periodic report (or vice versa) does not scan
        # disk twice for the same window. Key: (window_minutes, server_id).
        self._window_cache_key: tuple[int, str | None] | None = None
        self._window_cache_value: RecentObservationWindow | None = None
        self._window_cache_at: float = 0.0
        self._window_cache_ttl: float = 30.0

    def add_batch(self, server_id: str, payload: dict[str, Any]) -> int:
        if not self.config.enabled or not self.config.storage.enabled:
            return 0

        observations = payload.get("observations") or []
        if not isinstance(observations, list):
            return 0

        now = time.time()
        cutoff_ms = int((now - self.config.storage.retention_minutes * 60) * 1000)
        batch_server_id = str(payload.get("serverId") or server_id)
        batch_server_name = str(payload.get("serverName") or batch_server_id)

        with self._lock:
            written = 0
            skipped = 0
            handles: dict[Path, Any] = {}
            indexes: dict[Path, JsonlOffsetIndex] = {}
            with ExitStack() as stack:
                for item in observations:
                    if not isinstance(item, dict):
                        continue
                    # 单条容错：畸形 observation 不再中断整个 batch。
                    # from_dict 已对 context/raw/player 做类型防御，但
                    # normalize_record/codec 仍可能对极端输入抛异常。
                    try:
                        record = ObservationRecord.from_dict(
                            item,
                            batch_server_id,
                            batch_server_name,
                        )
                    except Exception:
                        skipped += 1
                        continue
                    if record.kind.upper() != "SERVER_LOG":
                        continue
                    if not record.server_id:
                        record.server_id = batch_server_id
                    if record.timestamp and record.timestamp < cutoff_ms:
                        continue
                    try:
                        self.codec.normalize_record(record)
                    except Exception:
                        skipped += 1
                        continue
                    path = self._record_path(record)
                    handle = handles.get(path)
                    if handle is None:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        # PR9 hotfix v3: 用二进制 append 模式打开。
                        # 文本模式的 tell() 返回 TextIO cookie，不应被当作
                        # 二进制 byte offset 用于 read_jsonl_window 的 seek()。
                        handle = stack.enter_context(path.open("ab"))
                        handles[path] = handle
                        idx = JsonlOffsetIndex.for_jsonl(
                            path,
                            trust_legacy_index=self.config.storage.trust_legacy_index,
                        )
                        idx.load()
                        indexes[path] = idx
                    idx = indexes[path]
                    line_offset = handle.tell()
                    idx.maybe_index(record.timestamp, line_offset)
                    handle.write(self.codec.json_line(record).encode("utf-8"))
                    handle.write(b"\n")
                    written += 1

                # 先 flush 数据句柄，再 flush 索引：避免索引先落盘指向尚未
                # 写入的数据行，导致并发读取方 seek 到 EOF 漏记录。
                for handle in handles.values():
                    try:
                        handle.flush()
                    except Exception:
                        pass
                # Flush all touched indexes so reads can use them immediately.
                for idx in indexes.values():
                    idx.flush()

            self.cleanup_if_due(now)
            # New observations invalidate the cached window read.
            self._window_cache_key = None
            self._window_cache_value = None
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

        with self._lock:
            cache_key = (window_minutes, server_id)
            now = time.time()
            if (
                self._window_cache_key == cache_key
                and self._window_cache_value is not None
                and now - self._window_cache_at < self._window_cache_ttl
                and max_records is None
            ):
                # 返回缓存的浅拷贝：records 是可变 list，调用方就地修改会
                # 污染缓存。RecentObservationWindow 是 frozen，但 list 字段
                # 本身可变，故复制 list。
                cached = self._window_cache_value
                return RecentObservationWindow(
                    records=list(cached.records),
                    total_count=cached.total_count,
                    unique_players=cached.unique_players,
                    truncated=cached.truncated,
                    max_records=cached.max_records,
                )

            cutoff_ms = int((now - window_minutes * 60) * 1000)
            end_ms = int(now * 1000) + 1
            limit = max(1, max_records or self.config.report.max_records_in_memory)
            builder = RecentWindowBuilder(limit)
            with self._dedupe_tracker() as seen:
                for path in self._candidate_files(server_id, cutoff_ms):
                    idx = JsonlOffsetIndex.for_jsonl(
                        path,
                        trust_legacy_index=self.config.storage.trust_legacy_index,
                    )
                    idx.load()
                    for row in self.codec.read_jsonl_window(
                        path, cutoff_ms, end_ms, index=idx
                    ):
                        try:
                            record = ObservationRecord.from_dict(row)
                        except Exception:
                            continue
                        if record.timestamp < cutoff_ms:
                            continue
                        if record.timestamp > end_ms:
                            continue
                        key = self.codec.dedupe_key(record)
                        if seen.seen_or_add(key):
                            continue
                        builder.add(record)
            result = builder.build()

            if max_records is None:
                self._window_cache_key = cache_key
                self._window_cache_value = result
                self._window_cache_at = now
            return result

    def _export_suffix(self) -> str:
        """Return the file suffix for exports based on config."""
        if self.config.report.export_format == "jsonl.gz":
            return ".jsonl.gz"
        return ".jsonl"

    def _open_export(self, path: Path, mode: str = "w"):
        """Open an export file, using gzip when the suffix is ``.jsonl.gz``.

        识别时先剥离原子写阶段追加的 ``.tmp`` 后缀，避免临时文件名
        ``foo.jsonl.gz.tmp`` 被误判为普通文本文件。
        """
        name = path.name
        if name.endswith(".tmp"):
            name = name[: -len(".tmp")]
        if name.endswith(".gz"):
            return gzip.open(path, mode + "t", encoding="utf-8")
        return path.open(mode, encoding="utf-8")

    def export_records(
        self,
        records: list[ObservationRecord],
        window_minutes: int,
        server_id: str | None = None,
        label: str = "",
        end_ms: int | None = None,
    ) -> Path | None:
        if not records:
            return None
        with self._lock:
            now = int(time.time() * 1000) if end_ms is None else end_ms
            suffix = self._export_suffix()
            path = export_path(
                self.export_dir, window_minutes, server_id, label, now, suffix=suffix
            )
            # 同窗口复用：如果文件已存在且复用开启，直接返回
            if self.config.report.export_reuse_existing and path.exists():
                return path
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            try:
                with self._open_export(tmp_path) as handle:
                    for record in records:
                        handle.write(self.codec.json_line(record))
                        handle.write("\n")
                # 原子 rename：避免中途异常留下截断文件被 reuse_existing 复用。
                tmp_path.replace(path)
            except Exception:
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass
                raise
            return path

    def export_recent(
        self,
        window_minutes: int,
        server_id: str | None = None,
        label: str = "",
        predicate: Callable[[ObservationRecord], bool] | None = None,
    ) -> Path | None:
        if not self.config.enabled or not self.config.storage.enabled:
            return None

        with self._lock:
            now_ts = int(time.time())
            cutoff_ms = int((now_ts - window_minutes * 60) * 1000)
            end_ms = int(now_ts * 1000) + 1
            suffix = self._export_suffix()
            path = export_path(
                self.export_dir, window_minutes, server_id, label, end_ms, suffix=suffix
            )

            # 同窗口复用：如果文件已存在且复用开启，直接返回
            if self.config.report.export_reuse_existing and path.exists():
                return path

            tmp_path = path.with_suffix(path.suffix + ".tmp")
            written = 0
            try:
                with self._dedupe_tracker() as seen:
                    with self._open_export(tmp_path) as handle:
                        for source_path in self._candidate_files(server_id, cutoff_ms):
                            idx = JsonlOffsetIndex.for_jsonl(
                                source_path,
                                trust_legacy_index=self.config.storage.trust_legacy_index,
                            )
                            idx.load()
                            for row in self.codec.read_jsonl_window(
                                source_path, cutoff_ms, end_ms, index=idx
                            ):
                                try:
                                    record = ObservationRecord.from_dict(row)
                                except Exception:
                                    continue
                                if record.timestamp < cutoff_ms:
                                    continue
                                if record.timestamp > end_ms:
                                    continue
                                key = self.codec.dedupe_key(record)
                                if seen.seen_or_add(key):
                                    continue
                                if predicate is not None and not predicate(record):
                                    continue
                                handle.write(self.codec.json_line(record))
                                handle.write("\n")
                                written += 1
                if not written:
                    tmp_path.unlink(missing_ok=True)
                    return None
                # 原子 rename：避免中途异常留下截断文件被 reuse_existing 复用。
                tmp_path.replace(path)
            except Exception:
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass
                raise
            return path

    def cleanup_if_due(self, now: float | None = None) -> bool:
        with self._lock:
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
        with self._lock:
            cleanup_old_files(
                self.observation_dir,
                self.export_dir,
                self.config.storage.retention_minutes,
            )

    def _record_path(self, record: ObservationRecord) -> Path:
        return record_path(self.observation_dir, record)

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
