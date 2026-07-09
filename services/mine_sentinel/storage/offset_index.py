"""Lightweight byte-offset index for JSONL observation files.

Each ``.jsonl`` file has an optional ``.idx`` sidecar that records
``(timestamp_ms, byte_offset)`` pairs periodically. ``read_jsonl_window``
uses this index to ``seek`` near the cutoff timestamp instead of scanning
from the file start — turning a window read from O(当天日志量) into
O(窗口日志量).

Index file format (text, one entry per line)::

    <timestamp_ms>\t<byte_offset>\n

Optional header lines starting with ``#`` carry metadata. Currently
recognized::

    #monotonic\t<0|1>
    #trust_legacy\t<0|1>

``#monotonic\t0`` marks the file as having out-of-order timestamps;
``read_jsonl_window`` will then disable seek + early-break optimization
for that file and fall back to a full scan with filter, so window reads
never miss records even when the JSONL is not strictly time-ordered
(e.g. backfill mixing multiple source files, log rotation races, or
clock skew).

``#trust_legacy\t0`` marks the file as a legacy index (no monotonic
guarantee for the rows between index entries). When set, the caller
treats the file as non-monotonic even if the recorded index entries
themselves are non-decreasing — because the absence of per-row tracking
in older versions means we can't prove the rows *between* index points
were ordered. New indexes written by this version always emit
``#trust_legacy\t1`` once the first row is observed, indicating the
per-row ``_last_seen_ts`` tracking is active.
"""

from __future__ import annotations

import bisect
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


class JsonlOffsetIndex:
    """Append-only ``(timestamp_ms, byte_offset)`` index for a JSONL file.

    A new entry is recorded when either ``line_interval`` lines have been
    written since the last entry, or ``time_interval_ms`` milliseconds
    have elapsed since the last entry's timestamp — whichever comes first.
    This keeps the index small (≈1 entry per 256 lines or per minute)
    while bounding the worst-case scan overshoot to at most one interval.

    PR9 hotfix v2: ``maybe_index`` tracks ``_last_seen_ts`` — the maximum
    timestamp seen across *all* rows, not just indexed ones. Every call
    compares the incoming timestamp against ``_last_seen_ts`` and flips
    ``_monotonic`` to False on any regression. This closes the gap where
    out-of-order rows between two index entries would escape detection
    (e.g. 1000 indexed → 1100/1200/1150 unindexed → 1150 > 1000 so the
    old check passed).

    Legacy ``.idx`` files written before this version have no
    ``#trust_legacy`` header. On load they are treated as
    non-monotonic-by-default unless ``trust_legacy_index=True`` is
    configured, because we cannot prove the rows between index entries
    were ordered. New indexes written by this version emit
    ``#trust_legacy\t1`` once ``_last_seen_ts`` tracking is active.
    """

    DEFAULT_LINE_INTERVAL = 256
    DEFAULT_TIME_INTERVAL_MS = 60_000  # 1 minute

    def __init__(
        self,
        index_path: Path,
        line_interval: int = DEFAULT_LINE_INTERVAL,
        time_interval_ms: int = DEFAULT_TIME_INTERVAL_MS,
        trust_legacy_index: bool = True,
    ):
        self.index_path = index_path
        self.line_interval = max(1, int(line_interval))
        self.time_interval_ms = max(1, int(time_interval_ms))
        # When False, a legacy .idx without #trust_legacy header is treated
        # as non-monotonic (conservative). Default True keeps backward
        # compatibility with existing deployments.
        self.trust_legacy_index = bool(trust_legacy_index)
        # Parallel lists for bisect; kept sorted by timestamp (append-only).
        self._timestamps: list[int] = []
        self._offsets: list[int] = []
        self._last_indexed_ts: int = 0
        # Max timestamp seen across ALL rows (indexed or not). Updated on
        # every maybe_index() call. Used for strict per-row monotonicity
        # detection that survives gaps between index entries.
        self._last_seen_ts: int = 0
        self._lines_since_last_index: int = 0
        # How many entries have been persisted to disk. Entries beyond this
        # count are new and need to be flushed.
        self._persisted_count: int = 0
        self._loaded: bool = False
        # Whether all recorded timestamps are non-decreasing. Once flipped
        # to False it stays False for the lifetime of this index object.
        self._monotonic: bool = True
        # Whether the on-disk monotonic flag has been written. Used to
        # decide whether flush() needs to rewrite the header.
        self._monotonic_persisted: bool = True
        # Whether the on-disk #trust_legacy header has been written.
        # Initialized to None (unknown); load() sets True when the header
        # is present (legacy or new persisted), and maybe_index() sets
        # False on first call for a new file so flush() will write it.
        self._trust_legacy_persisted: bool | None = None
        # Whether per-row _last_seen_ts tracking is in effect for this
        # index. True for any index created/written by this version;
        # False for legacy .idx files without the #trust_legacy header
        # (when trust_legacy_index=False, those are treated as non-monotonic).
        self._trust_legacy: bool = True

    @classmethod
    def for_jsonl(
        cls,
        jsonl_path: Path,
        line_interval: int = DEFAULT_LINE_INTERVAL,
        time_interval_ms: int = DEFAULT_TIME_INTERVAL_MS,
        trust_legacy_index: bool = True,
    ) -> "JsonlOffsetIndex":
        """Create an index path that sits next to ``jsonl_path``.

        ``20250705.jsonl`` → ``20250705.idx``
        """
        return cls(
            jsonl_path.with_suffix(".idx"),
            line_interval=line_interval,
            time_interval_ms=time_interval_ms,
            trust_legacy_index=trust_legacy_index,
        )

    # ------------------------------------------------------------------
    # Load / flush
    # ------------------------------------------------------------------
    def load(self) -> None:
        """Load existing index entries from disk (once).

        Recognizes optional headers:
        - ``#monotonic\t<0|1>``: explicit monotonic flag.
        - ``#trust_legacy\t<0|1>``: whether per-row ``_last_seen_ts``
          tracking was active when the file was written.

        Absent ``#monotonic`` header means monotonic=True (legacy).
        Absent ``#trust_legacy`` header means the file is a legacy index;
        when ``trust_legacy_index=False`` is configured, such files are
        treated as non-monotonic because we cannot prove the rows between
        index entries were ordered.
        """
        if self._loaded:
            return
        self._timestamps.clear()
        self._offsets.clear()
        # Start optimistic; only flip to False if we see #monotonic\t0,
        # detect out-of-order entries while loading, or encounter a legacy
        # file without #trust_legacy header (when configured conservative).
        self._monotonic = True
        self._monotonic_persisted = True
        self._trust_legacy = True
        # After load, mark as persisted only if the header was actually
        # present. None means "unknown / file not loaded / empty file" —
        # maybe_index() will set False on first call for a new file.
        self._trust_legacy_persisted: bool | None = None
        saw_trust_legacy_header = False
        saw_monotonic_header = False
        try:
            with self.index_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    raw = line.rstrip("\n")
                    if not raw:
                        continue
                    if raw.startswith("#"):
                        # Header line: parse metadata.
                        parts = raw.split("\t")
                        if len(parts) == 2 and parts[0] == "#monotonic":
                            saw_monotonic_header = True
                            if parts[1] == "0":
                                self._monotonic = False
                                self._monotonic_persisted = True
                            elif parts[1] == "1":
                                self._monotonic = True
                                self._monotonic_persisted = True
                        elif len(parts) == 2 and parts[0] == "#trust_legacy":
                            saw_trust_legacy_header = True
                            if parts[1] == "0":
                                self._trust_legacy = False
                                self._trust_legacy_persisted = True
                            elif parts[1] == "1":
                                self._trust_legacy = True
                                self._trust_legacy_persisted = True
                        continue
                    parts = raw.split("\t")
                    if len(parts) != 2:
                        continue
                    try:
                        ts = int(parts[0])
                        off = int(parts[1])
                    except ValueError:
                        continue
                    # Detect non-monotonic entries on load (defense in depth
                    # in case the header is missing but the file is broken).
                    if self._timestamps and ts < self._timestamps[-1]:
                        self._monotonic = False
                        self._monotonic_persisted = False
                    self._timestamps.append(ts)
                    self._offsets.append(off)
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            # UnicodeDecodeError 继承 ValueError，不被 OSError 捕获；
            # 索引文件损坏时按空索引继续（不抛异常），仅记 debug 日志便于排查。
            logger.debug("加载索引失败，按空索引继续: %s", self.index_path, exc_info=True)
        # Legacy file: no #trust_legacy header → we cannot prove per-row
        # ordering between index entries. When configured conservative,
        # treat as non-monotonic so reads fall back to full scan.
        if not saw_trust_legacy_header and not self._is_empty_after_load():
            self._trust_legacy = self.trust_legacy_index
            # Legacy file has no trust_legacy header on disk → mark as
            # not persisted so flush() will write it on next write.
            self._trust_legacy_persisted = False
            if not self.trust_legacy_index:
                # Conservative mode: legacy files are non-monotonic.
                # Don't clobber an explicit #monotonic\t1 header though.
                if not saw_monotonic_header:
                    self._monotonic = False
                    self._monotonic_persisted = False
        elif saw_trust_legacy_header:
            # Header was present on disk → already persisted.
            self._trust_legacy_persisted = True
        # If file is empty / new (no entries, no headers), leave
        # _trust_legacy_persisted as None so maybe_index() will mark it
        # False and trigger a header write on first flush.
        if self._timestamps:
            self._last_indexed_ts = self._timestamps[-1]
            # _last_seen_ts starts at the last indexed ts; subsequent
            # maybe_index() calls will track the true max across all rows.
            self._last_seen_ts = self._timestamps[-1]
        self._persisted_count = len(self._timestamps)
        self._loaded = True

    def _is_empty_after_load(self) -> bool:
        """Helper used during load() before _loaded is set."""
        return not self._timestamps

    def flush(self) -> None:
        """Persist new entries (and headers if changed) to disk.

        Headers written:
        - ``#monotonic\\t0`` when monotonicity flips to False.
        - ``#trust_legacy\\t1`` on first flush of a new file, indicating
          per-row ``_last_seen_ts`` tracking is active.

        Both headers require a full file rewrite (rare). Subsequent
        flushes append data rows only.
        """
        if not self._loaded:
            self.load()
        new_count = len(self._timestamps) - self._persisted_count
        needs_header_rewrite = (
            (not self._monotonic and not self._monotonic_persisted)
            or (self._trust_legacy_persisted is False)
        )
        if new_count <= 0 and not needs_header_rewrite:
            return
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        if needs_header_rewrite:
            # 原子重写：先写临时文件再 os.replace 替换，避免 "w" 直接覆写时
            # 写入中途崩溃截断索引、导致读路径 seek 到错误偏移或漏记录。
            # 临时文件 .idx.tmp，replace 后恢复为 .idx。
            tmp_path = self.index_path.with_suffix(".idx.tmp")
            try:
                with tmp_path.open("w", encoding="utf-8") as handle:
                    handle.write(f"#trust_legacy\t{1 if self._trust_legacy else 0}\n")
                    handle.write(f"#monotonic\t{1 if self._monotonic else 0}\n")
                    for ts, off in zip(self._timestamps, self._offsets):
                        handle.write(f"{ts}\t{off}\n")
                os.replace(tmp_path, self.index_path)
            finally:
                # 写失败或 replace 失败时清理残留临时文件（成功后已不存在）。
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            self._persisted_count = len(self._timestamps)
            self._monotonic_persisted = True
            self._trust_legacy_persisted = True
            return
        with self.index_path.open("a", encoding="utf-8") as handle:
            for i in range(self._persisted_count, len(self._timestamps)):
                handle.write(f"{self._timestamps[i]}\t{self._offsets[i]}\n")
        self._persisted_count = len(self._timestamps)

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------
    def maybe_index(self, timestamp_ms: int, byte_offset: int) -> bool:
        """Record an entry if interval thresholds are met.

        Call this after writing each JSONL line, passing the line's
        timestamp and its starting byte offset. Returns ``True`` if a
        new index entry was added.

        PR9 hotfix v2: every call updates ``_last_seen_ts`` (the max
        timestamp seen across ALL rows, not just indexed ones) and
        compares the incoming timestamp against it. This catches
        regressions that happen *between* index entries, which the
        previous check against ``_last_indexed_ts`` would miss:

            indexed ts=1000
            row ts=1100 (not indexed)
            row ts=1200 (not indexed)
            row ts=1150 (not indexed)  ← regression vs 1200, but > 1000

        With the old check (vs ``_last_indexed_ts``) this regression
        escaped detection. With ``_last_seen_ts`` it is caught and the
        file is marked non-monotonic.
        """
        # Ensure any existing on-disk entries are loaded before we append,
        # so we don't lose them when flush() writes only new entries.
        if not self._loaded:
            self.load()
        # Strict per-row monotonicity check: compare against the max
        # timestamp seen across ALL rows, not just the last indexed one.
        if self._last_seen_ts > 0 and timestamp_ms < self._last_seen_ts:
            if self._monotonic:
                self._monotonic = False
                self._monotonic_persisted = False
        # Update _last_seen_ts to the running max. We use max (not just
        # assignment) so that a regression doesn't lower the bar for
        # future rows — once we've seen ts=1200, a later ts=1100 also
        # counts as a regression even after we already flagged it.
        if timestamp_ms > self._last_seen_ts:
            self._last_seen_ts = timestamp_ms
        # Mark that this index has per-row tracking active (new file).
        # _trust_legacy_persisted is None (new/empty file) or False (legacy
        # file without header); either way, set it to False so flush()
        # will write the #trust_legacy\t1 header on next write.
        if self._trust_legacy_persisted is None or not self._trust_legacy_persisted:
            self._trust_legacy = True
            self._trust_legacy_persisted = False  # will be written on flush
        if not self._timestamps:
            self._timestamps.append(timestamp_ms)
            self._offsets.append(byte_offset)
            self._last_indexed_ts = timestamp_ms
            self._lines_since_last_index = 0
            return True
        self._lines_since_last_index += 1
        # Only compare time gap if we have a previous entry; otherwise
        # _last_indexed_ts == 0 would make time_gap == timestamp_ms (huge)
        # and trigger an immediate index entry on the very first line.
        if self._last_indexed_ts > 0:
            time_gap = timestamp_ms - self._last_indexed_ts
        else:
            time_gap = 0
        if (
            self._lines_since_last_index >= self.line_interval
            or time_gap >= self.time_interval_ms
        ):
            self._timestamps.append(timestamp_ms)
            self._offsets.append(byte_offset)
            self._last_indexed_ts = timestamp_ms
            self._lines_since_last_index = 0
            return True
        return False

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------
    def seek_offset(self, cutoff_ms: int) -> int:
        """Return the byte offset to start scanning for ``ts >= cutoff_ms``.

        Returns the offset of the last index entry whose timestamp is
        ``< cutoff_ms``. Scanning from this offset means we may re-read
        a few lines just before the cutoff (which ``read_jsonl_window``
        filters out via ``ts < cutoff_ms``), but we will never miss a
        record inside the window.

        Returns ``0`` if no suitable entry exists (scan from start), or
        if the file has been marked non-monotonic — in the latter case
        the caller must fall back to a full scan because timestamps may
        appear out of order anywhere in the file.
        """
        self.load()
        if not self._monotonic:
            return 0
        if not self._timestamps:
            return 0
        # bisect_left returns the insertion point: the index of the first
        # entry with timestamp >= cutoff_ms.
        idx = bisect.bisect_left(self._timestamps, cutoff_ms)
        if idx == 0:
            # All entries have ts >= cutoff_ms → scan from file start.
            return 0
        # Use the entry just before cutoff: its line has ts < cutoff_ms,
        # so we'll skip it, but subsequent lines may enter the window.
        return self._offsets[idx - 1]

    @property
    def is_monotonic(self) -> bool:
        """Whether the index believes the JSONL file is time-ordered.

        ``False`` means ``read_jsonl_window`` must not use early-break
        on ``ts >= end_ms`` and should ignore ``seek_offset``.
        """
        self.load()
        return self._monotonic

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------
    @property
    def entry_count(self) -> int:
        self.load()
        return len(self._timestamps)

    @property
    def is_empty(self) -> bool:
        self.load()
        return not self._timestamps
