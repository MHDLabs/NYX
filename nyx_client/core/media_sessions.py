"""
Voice notes as file attachments (no live calls).
"""
from __future__ import annotations

import time
from typing import Optional

from nyx_client.storage.db import Database


class MediaSessionStore:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._ensure()

    def _ensure(self) -> None:
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS voice_notes (
                message_id TEXT PRIMARY KEY,
                attachment_id TEXT NOT NULL,
                duration_sec REAL NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL
            )
            """
        )
        self._db.commit()

    def register_voice_note(self, message_id: str, attachment_id: str, duration_sec: float = 0.0) -> None:
        self._db.execute(
            """
            INSERT OR REPLACE INTO voice_notes(message_id, attachment_id, duration_sec, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (message_id, attachment_id, float(duration_sec), int(time.time())),
        )
        self._db.commit()

    def get_voice_note(self, message_id: str) -> Optional[dict]:
        row = self._db.query_one(
            "SELECT * FROM voice_notes WHERE message_id = ?",
            (message_id,),
        )
        return dict(row) if row else None
