"""
Rooms: groups and channels (local MVP).

Whitepaper conversation types:
  private_group, private_channel, public_channel, technical_room

MVP stores room metadata locally. Federation / membership sync is an
extension point — the schema and IDs are protocol-compatible.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass
from typing import List, Optional

from nyx_client.storage.db import Database


@dataclass
class Room:
    room_id: str
    room_type: str  # private_group | private_channel | public_channel
    title: str
    description: str
    owner_id: str
    is_public: bool
    created_at: int
    updated_at: int


def _new_room_id(prefix: str) -> str:
    raw = uuid.uuid4().hex
    return f"{prefix}_{raw}"


class RoomStore:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS rooms (
                room_id       TEXT PRIMARY KEY,
                room_type     TEXT NOT NULL,
                title         TEXT NOT NULL,
                description   TEXT NOT NULL DEFAULT '',
                owner_id      TEXT NOT NULL,
                is_public     INTEGER NOT NULL DEFAULT 0,
                created_at    INTEGER NOT NULL,
                updated_at    INTEGER NOT NULL
            )
            """
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_rooms_title ON rooms(title)"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_rooms_type ON rooms(room_type)"
        )
        self._db.commit()

    def create(
        self,
        *,
        room_type: str,
        title: str,
        owner_id: str,
        description: str = "",
        is_public: bool = False,
    ) -> Room:
        title = title.strip()
        if not title:
            raise ValueError("title is required")
        if room_type not in (
            "private_group",
            "private_channel",
            "public_channel",
            "technical_room",
        ):
            raise ValueError("invalid room_type")
        prefix = "grp" if "group" in room_type else "chn"
        room_id = _new_room_id(prefix)
        now = int(time.time())
        room = Room(
            room_id=room_id,
            room_type=room_type,
            title=title[:80],
            description=(description or "")[:280],
            owner_id=owner_id,
            is_public=is_public or room_type == "public_channel",
            created_at=now,
            updated_at=now,
        )
        self._db.execute(
            """
            INSERT INTO rooms(
                room_id, room_type, title, description, owner_id, is_public, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                room.room_id,
                room.room_type,
                room.title,
                room.description,
                room.owner_id,
                1 if room.is_public else 0,
                room.created_at,
                room.updated_at,
            ),
        )
        # Mirror into conversations so Home list shows it
        self._db.execute(
            """
            INSERT OR IGNORE INTO conversations(
                conversation_id, type, peer_id, title, created_at, updated_at, last_sequence
            ) VALUES (?, ?, ?, ?, ?, ?, 0)
            """,
            (room.room_id, room.room_type, None, room.title, now, now),
        )
        self._db.commit()
        return room

    def get(self, room_id: str) -> Optional[Room]:
        row = self._db.execute(
            "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
        ).fetchone()
        return _row_to_room(row) if row else None

    def update_settings(
        self,
        room_id: str,
        *,
        title: Optional[str] = None,
        description: Optional[str] = None,
        is_public: Optional[bool] = None,
    ) -> Room:
        room = self.get(room_id)
        if room is None:
            raise ValueError("room not found")
        if title is not None:
            room.title = title.strip()[:80]
        if description is not None:
            room.description = description.strip()[:280]
        if is_public is not None:
            room.is_public = is_public
        room.updated_at = int(time.time())
        self._db.execute(
            """
            UPDATE rooms SET title=?, description=?, is_public=?, updated_at=?
            WHERE room_id=?
            """,
            (
                room.title,
                room.description,
                1 if room.is_public else 0,
                room.updated_at,
                room.room_id,
            ),
        )
        self._db.execute(
            "UPDATE conversations SET title=?, type=?, updated_at=? WHERE conversation_id=?",
            (room.title, room.room_type, room.updated_at, room.room_id),
        )
        self._db.commit()
        return room

    def list_all(self, room_type: Optional[str] = None) -> List[Room]:
        if room_type:
            rows = self._db.execute(
                "SELECT * FROM rooms WHERE room_type=? ORDER BY updated_at DESC",
                (room_type,),
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT * FROM rooms ORDER BY updated_at DESC"
            ).fetchall()
        return [_row_to_room(r) for r in rows]

    def search(self, query: str, limit: int = 50) -> List[Room]:
        q = f"%{(query or '').strip().lower()}%"
        if q == "%%":
            return self.list_all()[:limit]
        rows = self._db.execute(
            """
            SELECT * FROM rooms
            WHERE lower(title) LIKE ? OR lower(description) LIKE ? OR lower(room_id) LIKE ?
            ORDER BY updated_at DESC LIMIT ?
            """,
            (q, q, q, limit),
        ).fetchall()
        return [_row_to_room(r) for r in rows]


def _row_to_room(row) -> Room:
    return Room(
        room_id=row["room_id"],
        room_type=row["room_type"],
        title=row["title"],
        description=row["description"] or "",
        owner_id=row["owner_id"],
        is_public=bool(row["is_public"]),
        created_at=int(row["created_at"]),
        updated_at=int(row["updated_at"]),
    )
