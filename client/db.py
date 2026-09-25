"""
db.py — Local SQLite storage for the NYX client.

Stores:
  - identity metadata (device_id, registered flag)
  - contacts (device_id → public_key)
  - messages (plaintext + metadata)
  - sync state (last_sync_time)

Database location: config.db_path (default: ~/.nyx/nyx_local.db)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import List, Optional, Tuple


# ---------------------------------------------------------------------------
# Database class
# ---------------------------------------------------------------------------

class NYXDatabase:
    """Local SQLite database for NYX client persistence."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None

    # -- connection management ------------------------------------------------

    def connect(self) -> sqlite3.Connection:
        """Open (or return existing) database connection and ensure schema."""
        if self._conn is not None:
            return self._conn

        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()
        return self._conn

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _init_schema(self) -> None:
        """Create tables if they do not exist."""
        conn = self._conn
        assert conn is not None

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS contacts (
                device_id   TEXT PRIMARY KEY,
                public_key  TEXT NOT NULL,
                cached_at   TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS messages (
                message_id   TEXT PRIMARY KEY,
                sender_id    TEXT NOT NULL,
                recipient_id TEXT NOT NULL,
                ciphertext   TEXT NOT NULL DEFAULT '',
                nonce        TEXT NOT NULL DEFAULT '',
                plaintext    TEXT NOT NULL DEFAULT '',
                direction    TEXT NOT NULL DEFAULT 'received'
                    CHECK (direction IN ('sent', 'received')),
                created_at   TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)
        conn.commit()

    # -- identity helpers -----------------------------------------------------

    def is_registered(self) -> bool:
        """Return True if the device has been registered with the relay."""
        conn = self.connect()
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'registered'"
        ).fetchone()
        return row is not None and row['value'] == '1'

    def save_identity(self, device_id: str, public_key_b64: str) -> None:
        """Record that this device registered successfully."""
        conn = self.connect()
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('device_id', ?)",
            (device_id,)
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('public_key', ?)",
            (public_key_b64,)
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('registered', '1')"
        )
        conn.commit()

    def get_device_id(self) -> Optional[str]:
        """Return the stored device_id, or None."""
        conn = self.connect()
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'device_id'"
        ).fetchone()
        return row['value'] if row else None

    def get_public_key_b64(self) -> Optional[str]:
        """Return the stored public key, or None."""
        conn = self.connect()
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'public_key'"
        ).fetchone()
        return row['value'] if row else None

    # -- sync state -----------------------------------------------------------

    def get_last_sync_time(self) -> Optional[str]:
        """Return the ISO timestamp of the last sync, or None."""
        conn = self.connect()
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'last_sync_time'"
        ).fetchone()
        return row['value'] if row else None

    def set_last_sync_time(self, timestamp: str) -> None:
        """Store the ISO timestamp of the last successful sync."""
        conn = self.connect()
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('last_sync_time', ?)",
            (timestamp,)
        )
        conn.commit()

    # -- contacts -------------------------------------------------------------

    def save_contact(self, device_id: str, public_key: str) -> None:
        """Cache a contact's public key locally."""
        conn = self.connect()
        conn.execute(
            """
            INSERT INTO contacts (device_id, public_key, cached_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(device_id) DO UPDATE SET
                public_key = excluded.public_key,
                cached_at  = excluded.cached_at
            """,
            (device_id, public_key),
        )
        conn.commit()

    def get_contact(self, device_id: str) -> Optional[str]:
        """Return the cached public key for a device_id, or None."""
        conn = self.connect()
        row = conn.execute(
            "SELECT public_key FROM contacts WHERE device_id = ?",
            (device_id,)
        ).fetchone()
        return row['public_key'] if row else None

    def get_contacts(self) -> List[Tuple[str, str]]:
        """Return all known contacts as (device_id, public_key) tuples."""
        conn = self.connect()
        rows = conn.execute(
            "SELECT device_id, public_key FROM contacts ORDER BY device_id"
        ).fetchall()
        return [(r['device_id'], r['public_key']) for r in rows]

    def resolve_contact(self, name_or_id: str) -> Optional[str]:
        """
        Try to resolve a name/prefix to a full device_id.
        First checks exact match, then prefix match.
        """
        conn = self.connect()
        # Exact match
        row = conn.execute(
            "SELECT device_id FROM contacts WHERE device_id = ?",
            (name_or_id,)
        ).fetchone()
        if row:
            return row['device_id']

        # Prefix match
        rows = conn.execute(
            "SELECT device_id FROM contacts WHERE device_id LIKE ?",
            (name_or_id + '%',)
        ).fetchall()
        if len(rows) == 1:
            return rows[0]['device_id']
        elif len(rows) > 1:
            # Ambiguous — return None
            return None

        return None

    # -- messages -------------------------------------------------------------

    def save_message(
        self,
        message_id: str,
        sender_id: str,
        ciphertext: str,
        nonce: str,
        plaintext: str,
        created_at: str,
        is_sent: bool = False,
        recipient_id: Optional[str] = None,
    ) -> None:
        """
        Store a message in the local history.

        For received messages: direction='received', sender_id is the creator.
        For sent messages:     direction='sent',     sender_id is us,
                               recipient_id is the target.
        """
        if is_sent:
            direction = 'sent'
            actual_recipient = recipient_id or ''
        else:
            direction = 'received'
            actual_recipient = recipient_id or sender_id

        conn = self.connect()

        if created_at:
            conn.execute(
                """
                INSERT OR IGNORE INTO messages
                    (message_id, sender_id, recipient_id, ciphertext, nonce,
                     plaintext, direction, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (message_id, sender_id, actual_recipient, ciphertext, nonce,
                 plaintext, direction, created_at),
            )
        else:
            conn.execute(
                """
                INSERT OR IGNORE INTO messages
                    (message_id, sender_id, recipient_id, ciphertext, nonce,
                     plaintext, direction)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (message_id, sender_id, actual_recipient, ciphertext, nonce,
                 plaintext, direction),
            )
        conn.commit()

    def get_messages(self, limit: int = 50) -> List[sqlite3.Row]:
        """Return recent messages ordered by creation time (newest last)."""
        conn = self.connect()
        rows = conn.execute(
            """
            SELECT message_id, sender_id, recipient_id, plaintext, direction,
                   created_at
            FROM messages
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return list(reversed(rows))

    def message_exists(self, message_id: str) -> bool:
        """Check if a message_id is already in local history."""
        conn = self.connect()
        row = conn.execute(
            "SELECT 1 FROM messages WHERE message_id = ?", (message_id,)
        ).fetchone()
        return row is not None