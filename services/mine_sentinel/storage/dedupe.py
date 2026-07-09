"""Bounded-memory exact dedupe helpers for MineSentinel JSONL scans."""

from __future__ import annotations

import logging
import os
import sqlite3
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


class DedupeTracker:
    """Exact seen-key tracker that spills to a temp SQLite file when needed."""

    # Number of inserts to buffer before committing to SQLite. Each implicit
    # commit forces a disk sync; batching amortizes that across many keys.
    _BATCH_SIZE = 2000

    def __init__(self, max_memory_keys: int = 100000, temp_dir: Path | None = None):
        self.max_memory_keys = max(1, int(max_memory_keys))
        self.temp_dir = temp_dir
        self._keys: set[str] = set()
        self._conn: sqlite3.Connection | None = None
        self._path: Path | None = None
        self._pending = 0
        # spill 到 sqlite 失败后置 True：此后不再重试 spill，保留内存 set
        # 继续工作（牺牲内存上界换可用性），避免每条新键都重复失败。
        self._spill_failed: bool = False

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
            if len(self._keys) < self.max_memory_keys or self._spill_failed:
                # spill 已失败时不再重试，允许内存 set 继续增长以保持可用性。
                self._keys.add(key)
                return False
            self._spill_to_sqlite()
            if self._conn is None:
                # spill 失败：_spill_to_sqlite 已回滚并保留内存 set。
                # 此处将当前键加入内存 set 继续工作（牺牲内存上界换可用性）。
                self._spill_failed = True
                self._keys.add(key)
                return False

        assert self._conn is not None
        # INSERT OR IGNORE is faster than try/except IntegrityError and lets us
        # use rowcount to detect duplicates without exception overhead.
        cursor = self._conn.execute("INSERT OR IGNORE INTO seen(key) VALUES (?)", (key,))
        if cursor.rowcount == 0:
            return True  # key already existed
        self._pending += 1
        if self._pending >= self._BATCH_SIZE:
            self._conn.commit()
            self._pending = 0
        return False

    def close(self):
        if self._conn is not None:
            # commit 失败也必须保证连接被关闭，否则 sqlite 句柄泄漏。
            # 用 try/finally 确保 close() 始终执行；close 本身再兜底 try/except。
            try:
                if self._pending:
                    self._conn.commit()
                    self._pending = 0
            finally:
                conn = self._conn
                self._conn = None
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        logger.warning("dedupe sqlite 连接关闭失败", exc_info=True)
        if self._path is not None:
            try:
                self._path.unlink(missing_ok=True)
            except OSError:
                logger.warning("dedupe 临时文件清理失败: %s", self._path, exc_info=True)
            self._path = None
        self._keys.clear()

    def _spill_to_sqlite(self):
        try:
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
        except Exception:
            # spill 失败（CREATE TABLE/executemany/commit 等任一环节抛异常）：
            # 回滚 _conn，避免半成品连接让后续 seen_or_add 走 sqlite 分支
            # 因表不存在而抛异常。保留内存 set 继续工作（牺牲精度换可用性）。
            logger.warning("dedupe spill 到 sqlite 失败，回退内存模式", exc_info=True)
            conn = self._conn
            self._conn = None
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            if self._path is not None:
                try:
                    self._path.unlink(missing_ok=True)
                except OSError:
                    pass
                self._path = None
            # 不清空 _keys：保留内存 set 继续工作。
