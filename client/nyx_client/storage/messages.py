"""
Local message history store.

Stores opaque payloads (E2EE ciphertext for DMs). The storage layer
never attempts to decrypt message content.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List, Optional

from nyx_client.storage.db import Database
from nyx_client.config.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class StoredMessage:
    message_id: str
    conversation_id: str
    sender_id: str
    device_id: Optional[str]
    payload: bytes
    signature: Optional[bytes]
    previous_hash: Optional[str]
    sequence: int
    timestamp: int
    direction: str
    status: str
    protocol_version: int


class MessageStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    def ensure_conversation(
        self,
        conversation_id: str,
        peer_id: Optional[str] = None,
        conv_type: str = "dm",
        title: Optional[str] = None,
    ) -> None:
        now = int(time.time())
        existing = self._db.execute(
            "SELECT 1 FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        if existing:
            return
        self._db.execute(
            """
            INSERT INTO conversations(
                conversation_id, type, peer_id, title, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (conversation_id, conv_type, peer_id, title, now, now),
        )
        self._db.commit()

    def insert(self, msg: StoredMessage) -> None:
        now = int(time.time())
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO messages(
                    message_id, conversation_id, sender_id, device_id,
                    payload, signature, previous_hash, sequence, timestamp,
                    direction, status, protocol_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    msg.message_id,
                    msg.conversation_id,
                    msg.sender_id,
                    msg.device_id,
                    msg.payload,
                    msg.signature,
                    msg.previous_hash,
                    msg.sequence,
                    msg.timestamp,
                    msg.direction,
                    msg.status,
                    msg.protocol_version,
                    now,
                ),
            )
            conn.execute(
                """
                UPDATE conversations
                SET last_sequence = MAX(last_sequence, ?),
                    updated_at = ?
                WHERE conversation_id = ?
                """,
                (msg.sequence, now, msg.conversation_id),
            )

    def history(
        self,
        conversation_id: str,
        limit: int = 100,
        before_sequence: Optional[int] = None,
    ) -> List[StoredMessage]:
        if before_sequence is not None:
            rows = self._db.execute(
                """
                SELECT * FROM messages
                WHERE conversation_id = ? AND sequence < ?
                ORDER BY sequence DESC LIMIT ?
                """,
                (conversation_id, before_sequence, limit),
            ).fetchall()
        else:
            rows = self._db.execute(
                """
                SELECT * FROM messages
                WHERE conversation_id = ?
                ORDER BY sequence DESC LIMIT ?
                """,
                (conversation_id, limit),
            ).fetchall()
        messages = [_row_to_msg(r) for r in rows]
        messages.reverse()  # chronological order
        return messages

    def get(self, message_id: str) -> Optional[StoredMessage]:
        row = self._db.execute(
            "SELECT * FROM messages WHERE message_id = ?", (message_id,)
        ).fetchone()
        return _row_to_msg(row) if row else None


    def list_conversations(
        self,
        conv_type: Optional[str] = None,
        limit: int = 200,
    ) -> List[dict]:
        """
        Return conversations ordered by last activity (updated_at DESC).

        Each item: conversation_id, type, peer_id, title, updated_at, last_sequence.
        """
        if conv_type:
            rows = self._db.execute(
                """
                SELECT conversation_id, type, peer_id, title, updated_at, last_sequence
                FROM conversations
                WHERE type = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (conv_type, limit),
            ).fetchall()
        else:
            rows = self._db.execute(
                """
                SELECT conversation_id, type, peer_id, title, updated_at, last_sequence
                FROM conversations
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "conversation_id": r["conversation_id"],
                "type": r["type"],
                "peer_id": r["peer_id"],
                "title": r["title"],
                "updated_at": int(r["updated_at"]),
                "last_sequence": int(r["last_sequence"]),
            }
            for r in rows
        ]

    def last_sequence(self, conversation_id: str) -> int:
        row = self._db.execute(
            "SELECT last_sequence FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        return int(row["last_sequence"]) if row else 0


def _row_to_msg(row) -> StoredMessage:
    return StoredMessage(
        message_id=row["message_id"],
        conversation_id=row["conversation_id"],
        sender_id=row["sender_id"],
        device_id=row["device_id"],
        payload=bytes(row["payload"]),
        signature=bytes(row["signature"]) if row["signature"] else None,
        previous_hash=row["previous_hash"],
        sequence=row["sequence"],
        timestamp=row["timestamp"],
        direction=row["direction"],
        status=row["status"],
        protocol_version=row["protocol_version"],
    )
