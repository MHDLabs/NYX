"""
SQLite connection manager for the NYX client.

Design:
  - One connection per process for MVP (WAL mode for concurrent readers).
  - Application-level encryption for sensitive columns (see profile.py).
  - Extension point: swap connect() to SQLCipher when the library is present
    without changing callers.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Optional

from nyx_client.storage.schema import SCHEMA_SQL, SCHEMA_VERSION
from nyx_client.config.logging import get_logger

log = get_logger(__name__)


class Database:
    """Thin wrapper around sqlite3 with schema bootstrap."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(self.path),
            check_same_thread=False,
            isolation_level=None,  # autocommit; we use explicit transactions
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        self._conn = conn
        self._ensure_schema(conn)
        log.info("storage.connected", path=str(self.path))
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(SCHEMA_SQL)
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'version'"
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO schema_meta(key, value) VALUES ('version', ?)",
                (str(SCHEMA_VERSION),),
            )
            conn.execute(
                "INSERT INTO schema_meta(key, value) VALUES ('created_at', ?)",
                (str(int(time.time())),),
            )
            log.info("storage.schema_initialized", version=SCHEMA_VERSION)
        else:
            current = int(row["value"])
            if current != SCHEMA_VERSION:
                log.warning(
                    "storage.schema_version_mismatch",
                    current=current,
                    expected=SCHEMA_VERSION,
                )

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            log.info("storage.closed", path=str(self.path))

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self.connect().execute(sql, params)

    def executemany(self, sql: str, seq: list) -> sqlite3.Cursor:
        return self.connect().executemany(sql, seq)

    def commit(self) -> None:
        if self._conn:
            self._conn.commit()

    def transaction(self):
        """Context manager for an explicit transaction."""
        return _Transaction(self.connect())


class _Transaction:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def __enter__(self):
        self._conn.execute("BEGIN")
        return self._conn

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._conn.execute("COMMIT")
        else:
            self._conn.execute("ROLLBACK")
        return False
