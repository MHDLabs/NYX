"""NYX local SQLite storage — identities, contacts, messages, profile, groups."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional


class NYXDatabase:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS identities (
                id TEXT PRIMARY KEY,
                private_key BLOB NOT NULL,
                public_key BLOB NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS contacts (
                identity_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                public_key TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS conversations (
                conversation_id TEXT PRIMARY KEY,
                participants TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                from_id TEXT NOT NULL,
                to_id TEXT NOT NULL,
                content TEXT NOT NULL,
                message_id TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id)
            );

            CREATE TABLE IF NOT EXISTS profile (
                id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL DEFAULT '',
                avatar TEXT NOT NULL DEFAULT '👤',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS groups (
                room_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                creator_id TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS group_members (
                room_id TEXT NOT NULL,
                identity_id TEXT NOT NULL,
                display_name TEXT NOT NULL DEFAULT '',
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (room_id, identity_id),
                FOREIGN KEY (room_id) REFERENCES groups(room_id)
            );

            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_messages_conversation
                ON messages(conversation_id);
            CREATE INDEX IF NOT EXISTS idx_messages_timestamp
                ON messages(timestamp);
            CREATE INDEX IF NOT EXISTS idx_messages_message_id
                ON messages(message_id);
            CREATE INDEX IF NOT EXISTS idx_group_members_room
                ON group_members(room_id);
            """
        )
        self.conn.commit()

    # ── low-level ──────────────────────────────────────────────────────────

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def commit(self) -> None:
        if self.conn:
            self.conn.commit()

    def close(self) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None

    # ── meta / session ─────────────────────────────────────────────────────

    def get_meta(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            (key, value),
        )
        self.conn.commit()

    def clear_meta(self, key: str) -> None:
        self.conn.execute("DELETE FROM meta WHERE key = ?", (key,))
        self.conn.commit()

    # ── identity ───────────────────────────────────────────────────────────

    def load_identity(self) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT id, private_key, public_key FROM identities LIMIT 1"
        ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "private_key": row["private_key"],
            "public_key": row["public_key"],
        }

    def save_identity(
        self, identity_id: str, private_key: bytes, public_key: bytes
    ) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO identities (id, private_key, public_key) "
            "VALUES (?, ?, ?)",
            (identity_id, private_key, public_key),
        )
        self.conn.commit()

    def delete_all_data_preserve_identity(self) -> None:
        """Factory-reset style wipe while keeping the local identity row."""
        for table in (
            "messages",
            "conversations",
            "contacts",
            "groups",
            "group_members",
            "profile",
            "meta",
        ):
            try:
                self.conn.execute(f"DELETE FROM {table}")
            except sqlite3.Error:
                pass
        self.conn.commit()

    # ── profile ────────────────────────────────────────────────────────────

    def get_profile(self, identity_id: str) -> Dict[str, str]:
        row = self.conn.execute(
            "SELECT display_name, avatar FROM profile WHERE id = ?",
            (identity_id,),
        ).fetchone()
        if row:
            return {
                "display_name": row["display_name"] or identity_id[:16],
                "avatar": row["avatar"] or "👤",
            }
        return {"display_name": identity_id[:16] if identity_id else "Unknown", "avatar": "👤"}

    def save_profile(
        self, identity_id: str, display_name: str, avatar: str = "👤"
    ) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO profile (id, display_name, avatar, updated_at) "
            "VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            (identity_id, display_name, avatar or "👤"),
        )
        self.conn.commit()

    # ── contacts ───────────────────────────────────────────────────────────

    def list_contacts(self) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT identity_id, name, public_key, added_at FROM contacts ORDER BY name"
        ).fetchall()
        return [dict(r) for r in rows]

    def save_contact(
        self, name: str, identity_id: str, public_key: str = ""
    ) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO contacts (identity_id, name, public_key) "
            "VALUES (?, ?, ?)",
            (identity_id, name, public_key),
        )
        self.conn.commit()

    def get_contact(self, identity_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT identity_id, name, public_key FROM contacts WHERE identity_id = ?",
            (identity_id,),
        ).fetchone()
        return dict(row) if row else None

    def delete_contact(self, identity_id: str) -> None:
        self.conn.execute(
            "DELETE FROM contacts WHERE identity_id = ?", (identity_id,)
        )
        self.conn.commit()

    # ── conversations / messages ───────────────────────────────────────────

    def list_conversations(self) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT
                conversation_id,
                participants,
                created_at,
                (SELECT COUNT(*) FROM messages m
                 WHERE m.conversation_id = conversations.conversation_id) AS message_count
            FROM conversations
            ORDER BY created_at DESC
            """
        ).fetchall()
        result = []
        for row in rows:
            participants = json.loads(row["participants"])
            result.append(
                {
                    "conversation_id": row["conversation_id"],
                    "participants": participants,
                    "participant_count": len(participants),
                    "message_count": row["message_count"],
                    "created_at": row["created_at"],
                    "type": "dm",
                }
            )
        return result

    def ensure_conversation(self, participants: List[str]) -> str:
        sorted_participants = sorted(participants)
        conv_hash = hashlib.sha256(
            json.dumps(sorted_participants).encode()
        ).hexdigest()[:16]
        conversation_id = f"conv_{conv_hash}"

        existing = self.conn.execute(
            "SELECT conversation_id FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        if existing:
            return conversation_id

        self.conn.execute(
            "INSERT INTO conversations (conversation_id, participants) VALUES (?, ?)",
            (conversation_id, json.dumps(sorted_participants)),
        )
        self.conn.commit()
        return conversation_id

    def get_messages(
        self, conversation_id: str, limit: int = 100
    ) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT id, conversation_id, from_id, to_id, content, message_id, timestamp
            FROM messages
            WHERE conversation_id = ?
            ORDER BY timestamp ASC, id ASC
            LIMIT ?
            """,
            (conversation_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def save_message(
        self,
        conversation_id: str,
        from_id: str,
        to_id: str,
        content: str,
        message_id: Optional[str] = None,
    ) -> None:
        if message_id:
            existing = self.conn.execute(
                "SELECT id FROM messages WHERE message_id = ?", (message_id,)
            ).fetchone()
            if existing:
                return  # dedupe synced messages
        self.conn.execute(
            """
            INSERT INTO messages (conversation_id, from_id, to_id, content, message_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (conversation_id, from_id, to_id, content, message_id),
        )
        self.conn.commit()

    def delete_messages_for_conversation(self, conversation_id: str) -> None:
        self.conn.execute(
            "DELETE FROM messages WHERE conversation_id = ?", (conversation_id,)
        )
        self.conn.execute(
            "DELETE FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        )
        self.conn.commit()

    def get_unread_count(self, identity_id: str) -> int:
        return 0

    # ── groups ─────────────────────────────────────────────────────────────

    def create_group(
        self,
        title: str,
        creator_id: str,
        description: str = "",
        room_id: Optional[str] = None,
    ) -> str:
        if not room_id:
            room_id = "grp_" + hashlib.sha256(
                f"{creator_id}:{title}:{json.dumps(description)}".encode()
            ).hexdigest()[:16]

        self.conn.execute(
            """
            INSERT OR REPLACE INTO groups (room_id, title, description, creator_id)
            VALUES (?, ?, ?, ?)
            """,
            (room_id, title, description or "", creator_id),
        )
        self.conn.execute(
            """
            INSERT OR IGNORE INTO group_members (room_id, identity_id, display_name)
            VALUES (?, ?, ?)
            """,
            (room_id, creator_id, ""),
        )
        # Ensure a conversation row so messages can be stored against room_id
        self.conn.execute(
            """
            INSERT OR IGNORE INTO conversations (conversation_id, participants)
            VALUES (?, ?)
            """,
            (room_id, json.dumps([creator_id])),
        )
        self.conn.commit()
        return room_id

    def join_group(
        self,
        room_id: str,
        identity_id: str,
        title: str = "Group",
        display_name: str = "",
    ) -> None:
        # Create stub group if unknown
        existing = self.conn.execute(
            "SELECT room_id FROM groups WHERE room_id = ?", (room_id,)
        ).fetchone()
        if not existing:
            self.conn.execute(
                """
                INSERT INTO groups (room_id, title, description, creator_id)
                VALUES (?, ?, '', ?)
                """,
                (room_id, title, identity_id),
            )
        self.conn.execute(
            """
            INSERT OR IGNORE INTO group_members (room_id, identity_id, display_name)
            VALUES (?, ?, ?)
            """,
            (room_id, identity_id, display_name),
        )
        self.conn.execute(
            """
            INSERT OR IGNORE INTO conversations (conversation_id, participants)
            VALUES (?, ?)
            """,
            (room_id, json.dumps([identity_id])),
        )
        self.conn.commit()

    def list_groups(self) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT room_id, title, description, creator_id, created_at
            FROM groups
            ORDER BY created_at DESC
            """
        ).fetchall()
        result = []
        for r in rows:
            members = self.get_group_members(r["room_id"])
            result.append(
                {
                    "room_id": r["room_id"],
                    "title": r["title"],
                    "description": r["description"],
                    "creator_id": r["creator_id"],
                    "created_at": r["created_at"],
                    "member_count": len(members),
                    "type": "group",
                }
            )
        return result

    def get_group(self, room_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT room_id, title, description, creator_id, created_at "
            "FROM groups WHERE room_id = ?",
            (room_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_group_members(self, room_id: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT identity_id, display_name, joined_at
            FROM group_members
            WHERE room_id = ?
            ORDER BY joined_at ASC
            """,
            (room_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_group(self, room_id: str) -> None:
        self.conn.execute(
            "DELETE FROM group_members WHERE room_id = ?", (room_id,)
        )
        self.conn.execute("DELETE FROM groups WHERE room_id = ?", (room_id,))
        self.conn.execute(
            "DELETE FROM messages WHERE conversation_id = ?", (room_id,)
        )
        self.conn.execute(
            "DELETE FROM conversations WHERE conversation_id = ?", (room_id,)
        )
        self.conn.commit()

    def leave_group(self, room_id: str, identity_id: str) -> None:
        self.conn.execute(
            "DELETE FROM group_members WHERE room_id = ? AND identity_id = ?",
            (room_id, identity_id),
        )
        remaining = self.conn.execute(
            "SELECT COUNT(*) AS c FROM group_members WHERE room_id = ?",
            (room_id,),
        ).fetchone()
        if remaining and remaining["c"] == 0:
            self.delete_group(room_id)
        else:
            self.conn.commit()
