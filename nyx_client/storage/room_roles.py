"""
Channel / group roles, posting policy, and moderation.

Roles (highest → lowest authority):
  owner   — full control: settings, roles, policy, unban, always can post
  admin   — mute / kick members (not owner/admins), cannot change room settings
  poster  — may post when policy allows
  member  — read + post only if policy is ``members``

Post policy (owner-only):
  owner_only | posters | members

Moderation (admin or owner):
  mute until timestamp (0 = permanent until unmute)
  kick removes membership

Whitepaper extension point: federated moderation events signed by actor key.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List, Optional

from nyx_client.storage.db import Database

ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_POSTER = "poster"
ROLE_MEMBER = "member"

POLICY_OWNER_ONLY = "owner_only"
POLICY_POSTERS = "posters"
POLICY_MEMBERS = "members"

_ROLE_RANK = {
    ROLE_OWNER: 100,
    ROLE_ADMIN: 50,
    ROLE_POSTER: 20,
    ROLE_MEMBER: 10,
}


@dataclass
class MemberRole:
    room_id: str
    identity_id: str
    role: str
    created_at: int


class RoomRoleStore:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._ensure()

    def _ensure(self) -> None:
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS room_members (
                room_id TEXT NOT NULL,
                identity_id TEXT NOT NULL,
                role TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (room_id, identity_id)
            )
            """
        )
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS room_policy (
                room_id TEXT PRIMARY KEY,
                post_policy TEXT NOT NULL DEFAULT 'members',
                updated_at INTEGER NOT NULL
            )
            """
        )
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS room_mutes (
                room_id TEXT NOT NULL,
                identity_id TEXT NOT NULL,
                muted_by TEXT NOT NULL,
                until_ts INTEGER NOT NULL DEFAULT 0,
                reason TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL,
                PRIMARY KEY (room_id, identity_id)
            )
            """
        )
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS room_mod_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                action TEXT NOT NULL,
                target_id TEXT NOT NULL DEFAULT '',
                detail TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL
            )
            """
        )
        self._db.commit()

    def set_owner(self, room_id: str, owner_id: str) -> None:
        self.upsert_member(room_id, owner_id, ROLE_OWNER)
        self.set_post_policy(room_id, POLICY_OWNER_ONLY)

    def upsert_member(self, room_id: str, identity_id: str, role: str) -> None:
        if role not in (ROLE_OWNER, ROLE_ADMIN, ROLE_POSTER, ROLE_MEMBER):
            raise ValueError("invalid role")
        now = int(time.time())
        self._db.execute(
            """
            INSERT INTO room_members(room_id, identity_id, role, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(room_id, identity_id) DO UPDATE SET role = excluded.role
            """,
            (room_id, identity_id, role, now),
        )
        self._db.commit()

    def get_role(self, room_id: str, identity_id: str) -> Optional[str]:
        row = self._db.execute(
            "SELECT role FROM room_members WHERE room_id = ? AND identity_id = ?",
            (room_id, identity_id),
        ).fetchone()
        return row["role"] if row else None

    def rank(self, room_id: str, identity_id: str) -> int:
        role = self.get_role(room_id, identity_id)
        return _ROLE_RANK.get(role or "", 0)

    def list_members(self, room_id: str) -> List[MemberRole]:
        rows = self._db.execute(
            "SELECT * FROM room_members WHERE room_id = ? ORDER BY role, identity_id",
            (room_id,),
        ).fetchall()
        return [
            MemberRole(r["room_id"], r["identity_id"], r["role"], int(r["created_at"]))
            for r in rows
        ]

    def remove_member(self, room_id: str, identity_id: str) -> None:
        self._db.execute(
            "DELETE FROM room_members WHERE room_id = ? AND identity_id = ?",
            (room_id, identity_id),
        )
        self._db.execute(
            "DELETE FROM room_mutes WHERE room_id = ? AND identity_id = ?",
            (room_id, identity_id),
        )
        self._db.commit()

    def set_post_policy(self, room_id: str, policy: str) -> None:
        if policy not in (POLICY_OWNER_ONLY, POLICY_POSTERS, POLICY_MEMBERS):
            raise ValueError("invalid post_policy")
        self._db.execute(
            """
            INSERT INTO room_policy(room_id, post_policy, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(room_id) DO UPDATE SET post_policy = excluded.post_policy,
                updated_at = excluded.updated_at
            """,
            (room_id, policy, int(time.time())),
        )
        self._db.commit()

    def get_post_policy(self, room_id: str) -> str:
        row = self._db.execute(
            "SELECT post_policy FROM room_policy WHERE room_id = ?", (room_id,)
        ).fetchone()
        return row["post_policy"] if row else POLICY_MEMBERS

    def require_owner(self, room_id: str, actor_id: str) -> None:
        if self.get_role(room_id, actor_id) != ROLE_OWNER:
            raise PermissionError("only the room owner can change settings")

    def can_manage_settings(self, room_id: str, actor_id: str) -> bool:
        return self.get_role(room_id, actor_id) == ROLE_OWNER

    def can_moderate(self, room_id: str, actor_id: str) -> bool:
        return self.rank(room_id, actor_id) >= _ROLE_RANK[ROLE_ADMIN]

    def can_post(self, room_id: str, identity_id: str) -> bool:
        if self.is_muted(room_id, identity_id):
            return False
        role = self.get_role(room_id, identity_id)
        policy = self.get_post_policy(room_id)
        if role == ROLE_OWNER:
            return True
        if role == ROLE_ADMIN:
            return policy != POLICY_OWNER_ONLY
        if policy == POLICY_OWNER_ONLY:
            return False
        if policy == POLICY_POSTERS:
            return role == ROLE_POSTER
        return role in (ROLE_POSTER, ROLE_MEMBER, ROLE_ADMIN)

    def is_muted(self, room_id: str, identity_id: str) -> bool:
        row = self._db.execute(
            "SELECT until_ts FROM room_mutes WHERE room_id = ? AND identity_id = ?",
            (room_id, identity_id),
        ).fetchone()
        if row is None:
            return False
        until_ts = int(row["until_ts"])
        if until_ts == 0:
            return True
        return int(time.time()) < until_ts

    def mute(
        self,
        room_id: str,
        actor_id: str,
        target_id: str,
        duration_sec: int = 0,
        reason: str = "",
    ) -> None:
        """Mute target. duration_sec=0 means until unmute. Cannot mute owner or equal/higher rank."""
        if not self.can_moderate(room_id, actor_id):
            raise PermissionError("admin or owner required to mute")
        if self.rank(room_id, target_id) >= self.rank(room_id, actor_id):
            raise PermissionError("cannot mute equal or higher role")
        until_ts = 0 if duration_sec <= 0 else int(time.time()) + int(duration_sec)
        now = int(time.time())
        self._db.execute(
            """
            INSERT INTO room_mutes(room_id, identity_id, muted_by, until_ts, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(room_id, identity_id) DO UPDATE SET
                muted_by = excluded.muted_by,
                until_ts = excluded.until_ts,
                reason = excluded.reason,
                created_at = excluded.created_at
            """,
            (room_id, target_id, actor_id, until_ts, reason[:200], now),
        )
        self._log(room_id, actor_id, "mute", target_id, reason)
        self._db.commit()

    def unmute(self, room_id: str, actor_id: str, target_id: str) -> None:
        if not self.can_moderate(room_id, actor_id):
            raise PermissionError("admin or owner required to unmute")
        self._db.execute(
            "DELETE FROM room_mutes WHERE room_id = ? AND identity_id = ?",
            (room_id, target_id),
        )
        self._log(room_id, actor_id, "unmute", target_id, "")
        self._db.commit()

    def kick(self, room_id: str, actor_id: str, target_id: str, reason: str = "") -> None:
        if not self.can_moderate(room_id, actor_id):
            raise PermissionError("admin or owner required to kick")
        if self.rank(room_id, target_id) >= self.rank(room_id, actor_id):
            raise PermissionError("cannot kick equal or higher role")
        self.remove_member(room_id, target_id)
        self._log(room_id, actor_id, "kick", target_id, reason)
        self._db.commit()

    def _log(self, room_id: str, actor: str, action: str, target: str, detail: str) -> None:
        self._db.execute(
            """
            INSERT INTO room_mod_log(room_id, actor_id, action, target_id, detail, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (room_id, actor, action, target, detail[:200], int(time.time())),
        )
