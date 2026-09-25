"""
Voice notes and online meeting sessions.

Voice notes (MVP): audio files via AttachmentStore + [voice] message marker.
Meetings (MVP): local registry with join code; WebRTC/SFU is an extension point.
"""

from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import List, Optional

from nyx_client.storage.db import Database


@dataclass
class MeetingSession:
    meeting_id: str
    host_id: str
    title: str
    join_code: str
    room_id: str
    status: str  # scheduled | live | ended
    created_at: int
    started_at: int = 0
    ended_at: int = 0
    participants: List[str] = field(default_factory=list)


class MediaSessionStore:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._ensure()

    def _ensure(self) -> None:
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS meetings (
                meeting_id TEXT PRIMARY KEY,
                host_id TEXT NOT NULL,
                title TEXT NOT NULL,
                join_code TEXT NOT NULL UNIQUE,
                room_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                started_at INTEGER NOT NULL DEFAULT 0,
                ended_at INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS meeting_participants (
                meeting_id TEXT NOT NULL,
                identity_id TEXT NOT NULL,
                joined_at INTEGER NOT NULL,
                PRIMARY KEY (meeting_id, identity_id)
            )
            """
        )
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

    def create_meeting(self, host_id: str, title: str, room_id: str = "") -> MeetingSession:
        title = (title or "NYX Meeting").strip()[:120]
        mid = "mtg_" + uuid.uuid4().hex[:16]
        code = secrets.token_hex(3).upper()
        now = int(time.time())
        self._db.execute(
            """
            INSERT INTO meetings(
                meeting_id, host_id, title, join_code, room_id, status, created_at
            ) VALUES (?, ?, ?, ?, ?, 'scheduled', ?)
            """,
            (mid, host_id, title, code, room_id or "", now),
        )
        self._db.execute(
            "INSERT INTO meeting_participants(meeting_id, identity_id, joined_at) VALUES (?, ?, ?)",
            (mid, host_id, now),
        )
        self._db.commit()
        return self.get_meeting(mid)  # type: ignore

    def get_meeting(self, meeting_id: str) -> Optional[MeetingSession]:
        row = self._db.execute(
            "SELECT * FROM meetings WHERE meeting_id = ?", (meeting_id,)
        ).fetchone()
        if not row:
            return None
        parts = self._db.execute(
            "SELECT identity_id FROM meeting_participants WHERE meeting_id = ?",
            (meeting_id,),
        ).fetchall()
        return MeetingSession(
            meeting_id=row["meeting_id"],
            host_id=row["host_id"],
            title=row["title"],
            join_code=row["join_code"],
            room_id=row["room_id"] or "",
            status=row["status"],
            created_at=int(row["created_at"]),
            started_at=int(row["started_at"] or 0),
            ended_at=int(row["ended_at"] or 0),
            participants=[p["identity_id"] for p in parts],
        )

    def get_by_code(self, join_code: str) -> Optional[MeetingSession]:
        row = self._db.execute(
            "SELECT meeting_id FROM meetings WHERE join_code = ?",
            (join_code.upper(),),
        ).fetchone()
        return self.get_meeting(row["meeting_id"]) if row else None

    def start_meeting(self, meeting_id: str, host_id: str) -> MeetingSession:
        m = self.get_meeting(meeting_id)
        if m is None:
            raise ValueError("meeting not found")
        if m.host_id != host_id:
            raise PermissionError("only host can start the meeting")
        self._db.execute(
            "UPDATE meetings SET status = 'live', started_at = ? WHERE meeting_id = ?",
            (int(time.time()), meeting_id),
        )
        self._db.commit()
        return self.get_meeting(meeting_id)  # type: ignore

    def join_meeting(self, join_code: str, identity_id: str) -> MeetingSession:
        m = self.get_by_code(join_code)
        if m is None:
            raise ValueError("invalid join code")
        if m.status == "ended":
            raise ValueError("meeting has ended")
        self._db.execute(
            """
            INSERT OR IGNORE INTO meeting_participants(meeting_id, identity_id, joined_at)
            VALUES (?, ?, ?)
            """,
            (m.meeting_id, identity_id, int(time.time())),
        )
        self._db.commit()
        return self.get_meeting(m.meeting_id)  # type: ignore

    def end_meeting(self, meeting_id: str, host_id: str) -> MeetingSession:
        m = self.get_meeting(meeting_id)
        if m is None:
            raise ValueError("meeting not found")
        if m.host_id != host_id:
            raise PermissionError("only host can end the meeting")
        self._db.execute(
            "UPDATE meetings SET status = 'ended', ended_at = ? WHERE meeting_id = ?",
            (int(time.time()), meeting_id),
        )
        self._db.commit()
        return self.get_meeting(meeting_id)  # type: ignore

    def list_meetings(self, host_id: Optional[str] = None, limit: int = 20) -> List[MeetingSession]:
        if host_id:
            rows = self._db.execute(
                "SELECT meeting_id FROM meetings WHERE host_id = ? ORDER BY created_at DESC LIMIT ?",
                (host_id, limit),
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT meeting_id FROM meetings ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out = []
        for r in rows:
            m = self.get_meeting(r["meeting_id"])
            if m:
                out.append(m)
        return out



@dataclass
class SignalMessage:
    meeting_id: str
    from_id: str
    type: str
    payload: str
    created_at: int


class LocalSignalingBuffer:
    """In-process buffer used when offline; mirrors relay signaling schema."""

    def __init__(self) -> None:
        self._items: list[dict] = []

    def post(self, meeting_id: str, from_id: str, type_: str, payload: str) -> dict:
        import time
        row = {
            "meeting_id": meeting_id,
            "from_id": from_id,
            "type": type_,
            "payload": payload,
            "created_at": int(time.time() * 1000),
        }
        self._items.append(row)
        return row

    def pull(self, meeting_id: str, for_id: str, since: int = 0) -> list[dict]:
        out = []
        for s in self._items:
            if s["meeting_id"] != meeting_id:
                continue
            if s["created_at"] <= since:
                continue
            if s["from_id"] == for_id:
                continue
            out.append(s)
        return out
