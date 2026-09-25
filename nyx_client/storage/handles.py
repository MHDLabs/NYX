"""
Unique public handles (usernames) for users, groups, and channels.

Uniqueness + race rule
----------------------
* One handle → exactly one target (user / group / channel).
* Concurrent claims: the registration with the **older** ``claimed_at``
  timestamp wins. The newer claimant receives a conflict error and must
  choose another handle (client shows "not registered — try again").
* Relay must apply the same rule when two nodes race on ``POST /handles/claim``.

Format: 3–32 chars, ``[a-z][a-z0-9_]*``.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import List, Optional

from nyx_client.storage.db import Database

HANDLE_RE = re.compile(r"^[a-z][a-z0-9_]{2,31}$")

KIND_USER = "user"
KIND_GROUP = "group"
KIND_CHANNEL = "channel"


class HandleConflictError(ValueError):
    """Raised when a handle is already taken by an older claim."""

    def __init__(self, handle: str, winner_target: str, winner_at: int) -> None:
        self.handle = handle
        self.winner_target = winner_target
        self.winner_at = winner_at
        super().__init__(
            f"handle @{handle} already taken (registered at {winner_at}); "
            "choose another id and try again"
        )


@dataclass
class HandleRecord:
    handle: str
    kind: str
    target_id: str
    owner_id: str
    created_at: int  # unix seconds (or ms normalized)
    updated_at: int
    claimed_at_ms: int = 0  # original claim time for race resolution


def normalize_handle(name: str) -> str:
    return (name or "").strip().lower()


def validate_handle_format(name: str) -> Optional[str]:
    h = normalize_handle(name)
    if not h:
        return "empty handle"
    if not HANDLE_RE.match(h):
        return "use 3-32 chars: start with a letter, then a-z 0-9 _"
    reserved = {
        "admin", "nyx", "system", "null", "undefined", "me", "owner", "support",
    }
    if h in reserved:
        return "reserved handle"
    return None


class HandleStore:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._ensure()

    def _ensure(self) -> None:
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS handles (
                handle TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                target_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                claimed_at_ms INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        # migrate older DBs missing claimed_at_ms
        cols = {
            r[1] for r in self._db.execute("PRAGMA table_info(handles)").fetchall()
        }
        # sqlite Row may be tuple-like
        try:
            info = self._db.execute("PRAGMA table_info(handles)").fetchall()
            names = set()
            for row in info:
                try:
                    names.add(row["name"])
                except Exception:
                    names.add(row[1])
            if "claimed_at_ms" not in names:
                self._db.execute(
                    "ALTER TABLE handles ADD COLUMN claimed_at_ms INTEGER NOT NULL DEFAULT 0"
                )
        except Exception:
            pass
        self._db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_handles_target ON handles(target_id)"
        )
        self._db.commit()

    def get(self, handle: str) -> Optional[HandleRecord]:
        h = normalize_handle(handle)
        row = self._db.execute(
            "SELECT * FROM handles WHERE handle = ?", (h,)
        ).fetchone()
        return _row(row) if row else None

    def get_by_target(self, target_id: str) -> Optional[HandleRecord]:
        row = self._db.execute(
            "SELECT * FROM handles WHERE target_id = ?", (target_id,)
        ).fetchone()
        return _row(row) if row else None

    def resolve(self, handle_or_id: str) -> Optional[str]:
        h = normalize_handle(handle_or_id)
        rec = self.get(h)
        if rec:
            return rec.target_id
        return handle_or_id

    def claim(
        self,
        handle: str,
        kind: str,
        target_id: str,
        owner_id: str,
        claimed_at_ms: Optional[int] = None,
    ) -> HandleRecord:
        """
        Register handle. Race rule: older ``claimed_at_ms`` wins permanently.
        """
        err = validate_handle_format(handle)
        if err:
            raise ValueError(err)
        if kind not in (KIND_USER, KIND_GROUP, KIND_CHANNEL):
            raise ValueError("invalid kind")
        h = normalize_handle(handle)
        now_ms = int(claimed_at_ms if claimed_at_ms is not None else time.time() * 1000)
        now_s = int(now_ms // 1000)

        existing = self.get(h)
        if existing is not None:
            win_ms = existing.claimed_at_ms or (existing.created_at * 1000)
            if existing.target_id == target_id:
                # same owner refreshing — keep original claimed_at
                self._db.execute(
                    """
                    UPDATE handles SET kind = ?, owner_id = ?, updated_at = ?
                    WHERE handle = ?
                    """,
                    (kind, owner_id, now_s, h),
                )
                self._db.commit()
                return self.get(h)  # type: ignore
            if win_ms <= now_ms:
                # existing is older or equal → existing wins
                raise HandleConflictError(h, existing.target_id, win_ms)
            # rare: our claim is strictly older than stored → we win, replace
        # free target previous handle if retargeting
        by_t = self.get_by_target(target_id)
        if by_t and by_t.handle != h:
            self._db.execute("DELETE FROM handles WHERE target_id = ?", (target_id,))

        self._db.execute(
            """
            INSERT INTO handles(
                handle, kind, target_id, owner_id, created_at, updated_at, claimed_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(handle) DO UPDATE SET
                kind = excluded.kind,
                target_id = excluded.target_id,
                owner_id = excluded.owner_id,
                updated_at = excluded.updated_at,
                claimed_at_ms = excluded.claimed_at_ms
            WHERE excluded.claimed_at_ms < handles.claimed_at_ms
               OR handles.claimed_at_ms = 0
            """,
            (h, kind, target_id, owner_id, now_s, now_s, now_ms),
        )
        self._db.commit()
        rec = self.get(h)
        if rec is None or rec.target_id != target_id:
            # lost race to concurrent writer with older or equal time
            winner = self.get(h)
            raise HandleConflictError(
                h,
                winner.target_id if winner else "",
                (winner.claimed_at_ms if winner else 0),
            )
        return rec

    def release(self, handle: str, owner_id: str) -> None:
        rec = self.get(handle)
        if rec is None:
            return
        if rec.owner_id != owner_id:
            raise PermissionError("not the handle owner")
        self._db.execute(
            "DELETE FROM handles WHERE handle = ?", (normalize_handle(handle),)
        )
        self._db.commit()

    def list_all(self, kind: Optional[str] = None) -> List[HandleRecord]:
        if kind:
            rows = self._db.execute(
                "SELECT * FROM handles WHERE kind = ? ORDER BY handle", (kind,)
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT * FROM handles ORDER BY handle"
            ).fetchall()
        return [_row(r) for r in rows]


def _row(r) -> HandleRecord:
    try:
        cam = int(r["claimed_at_ms"] or 0)
    except Exception:
        cam = 0
    return HandleRecord(
        handle=r["handle"],
        kind=r["kind"],
        target_id=r["target_id"],
        owner_id=r["owner_id"],
        created_at=int(r["created_at"]),
        updated_at=int(r["updated_at"]),
        claimed_at_ms=cam,
    )
