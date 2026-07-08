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
        self._hot_keys: dict[str, None] = {}
        self._hot_key_limit = min(4096, self.max_memory_keys)
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
        if key in self._hot_keys:
            return True
        cursor = self._conn.execute("INSERT OR IGNORE INTO seen(key) VALUES (?)", (key,))
        seen = cursor.rowcount == 0
        self._remember_hot_key(key)
        return seen

    def seen_any_or_add_all(self, keys) -> bool:
        """Atomically add a record's dedupe keys only when none were seen."""
        unique_keys = _unique_nonempty_keys(keys)
        if not unique_keys:
            return False
        if len(unique_keys) == 1:
            return self.seen_or_add(unique_keys[0])

        if self._conn is None:
            if any(key in self._keys for key in unique_keys):
                return True
            if len(self._keys) + len(unique_keys) <= self.max_memory_keys:
                self._keys.update(unique_keys)
                return False
            self._spill_to_sqlite()

        assert self._conn is not None
        if any(key in self._hot_keys for key in unique_keys):
            return True
        placeholders = ",".join("?" for _ in unique_keys)
        cursor = self._conn.execute(
            f"SELECT key FROM seen WHERE key IN ({placeholders}) LIMIT 1",
            unique_keys,
        )
        row = cursor.fetchone()
        if row is not None:
            self._remember_hot_key(row[0])
            return True
        self._conn.executemany(
            "INSERT OR IGNORE INTO seen(key) VALUES (?)",
            ((key,) for key in unique_keys),
        )
        self._remember_hot_keys(unique_keys)
        return False

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        if self._path is not None:
            self._path.unlink(missing_ok=True)
            self._path = None
        self._keys.clear()
        self._hot_keys.clear()

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
        self._remember_hot_keys(self._keys)
        self._keys.clear()

    def _remember_hot_keys(self, keys):
        for key in keys:
            self._remember_hot_key(key)

    def _remember_hot_key(self, key: str):
        if not key or self._hot_key_limit <= 0:
            return
        if key in self._hot_keys:
            return
        self._hot_keys[key] = None
        while len(self._hot_keys) > self._hot_key_limit:
            self._hot_keys.pop(next(iter(self._hot_keys)))


def _unique_nonempty_keys(keys) -> tuple[str, ...]:
    if isinstance(keys, tuple):
        if not keys:
            return ()
        if len(keys) == 1:
            return (keys[0],) if keys[0] else ()
        if len(keys) == 2:
            first, second = keys
            if not first:
                return (second,) if second else ()
            if not second or second == first:
                return (first,)
            return keys
    return tuple(dict.fromkeys(key for key in keys if key))
