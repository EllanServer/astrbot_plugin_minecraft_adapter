"""Bounded-memory exact dedupe helpers for MineSentinel JSONL scans."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path


class DedupeTracker:
    """Exact seen-key tracker that spills to a temp SQLite file when needed."""

    def __init__(self, max_memory_keys: int = 100000, temp_dir: Path | None = None):
        self.max_memory_keys = max(1, int(max_memory_keys))
        self.temp_dir = temp_dir
        self._keys: set[str] = set()
        self._conn: sqlite3.Connection | None = None
        self._path: Path | None = None

    def __enter__(self) -> "DedupeTracker":
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    @property
    def spilled(self) -> bool:
        return self._conn is not None

    @property
    def path(self) -> Path | None:
        return self._path

    def seen_or_add(self, key: str) -> bool:
        if self._conn is None:
            if key in self._keys:
                return True
            if len(self._keys) < self.max_memory_keys:
                self._keys.add(key)
                return False
            self._spill_to_sqlite()

        assert self._conn is not None
        try:
            self._conn.execute("INSERT INTO seen(key) VALUES (?)", (key,))
            return False
        except sqlite3.IntegrityError:
            return True

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        if self._path is not None:
            self._path.unlink(missing_ok=True)
            self._path = None
        self._keys.clear()

    def _spill_to_sqlite(self):
        if self.temp_dir:
            self.temp_dir.mkdir(parents=True, exist_ok=True)
        fd, raw_path = tempfile.mkstemp(
            prefix="minesentinel_dedupe_",
            suffix=".sqlite3",
            dir=str(self.temp_dir) if self.temp_dir else None,
        )
        os.close(fd)
        self._path = Path(raw_path)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.execute("PRAGMA journal_mode=OFF")
        self._conn.execute("PRAGMA synchronous=OFF")
        self._conn.execute("CREATE TABLE seen(key TEXT PRIMARY KEY)")
        self._conn.executemany(
            "INSERT INTO seen(key) VALUES (?)",
            ((key,) for key in self._keys),
        )
        self._conn.commit()
        self._keys.clear()
