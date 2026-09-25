"""
Local user profile preferences (display name, bio, UI settings).

Stored in session_state so no schema migration is required for MVP.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from nyx_client.storage.db import Database


@dataclass
class UserProfile:
    display_name: str = ""
    bio: str = ""
    theme_id: str = "midnight"


class UserPrefs:
    """Read/write display name, bio, and small UI prefs."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def get_profile(self) -> UserProfile:
        tid = self._get_text("ui.theme") or "midnight"
        return UserProfile(
            display_name=self._get_text("profile.display_name"),
            bio=self._get_text("profile.bio"),
            theme_id=tid,
        )

    def get_theme_id(self) -> str:
        return self._get_text("ui.theme") or "midnight"

    def set_theme_id(self, theme_id: str) -> None:
        self._set_text("ui.theme", (theme_id or "midnight").strip()[:32])

    def set_display_name(self, name: str) -> None:
        self._set_text("profile.display_name", name.strip()[:64])

    def set_bio(self, bio: str) -> None:
        self._set_text("profile.bio", bio.strip()[:280])

    def _get_text(self, key: str) -> str:
        row = self._db.execute(
            "SELECT value FROM session_state WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return ""
        val = row["value"]
        if isinstance(val, memoryview):
            val = bytes(val)
        if isinstance(val, bytes):
            return val.decode("utf-8", errors="replace")
        return str(val)

    def _set_text(self, key: str, text: str) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO session_state(key, value, updated_at) VALUES (?, ?, ?)",
            (key, text.encode("utf-8"), int(time.time())),
        )
        self._db.commit()
