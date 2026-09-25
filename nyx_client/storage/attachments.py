"""
Local attachment store for images and files linked to messages.
"""

from __future__ import annotations

import hashlib
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from nyx_client.storage.db import Database


@dataclass
class Attachment:
    attachment_id: str
    conversation_id: str
    message_id: str
    filename: str
    mime: str
    size: int
    sha256: str
    local_path: str
    created_at: int


class AttachmentStore:
    def __init__(self, db: Database, media_dir: Path) -> None:
        self._db = db
        self._media = media_dir
        self._media.mkdir(parents=True, exist_ok=True)
        self._ensure()

    def _ensure(self) -> None:
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS attachments (
                attachment_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                mime TEXT NOT NULL,
                size INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                local_path TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        self._db.commit()

    def store_file(
        self,
        src: Path,
        conversation_id: str,
        message_id: str,
        mime: Optional[str] = None,
    ) -> Attachment:
        src = Path(src)
        if not src.is_file():
            raise FileNotFoundError(str(src))
        data = src.read_bytes()
        if len(data) > 50 * 1024 * 1024:
            raise ValueError("file too large (max 50MB local MVP)")
        digest = hashlib.sha256(data).hexdigest()
        aid = "att_" + uuid.uuid4().hex[:16]
        ext = src.suffix[:16]
        dest = self._media / f"{aid}{ext}"
        dest.write_bytes(data)
        now = int(time.time())
        mime = mime or _guess_mime(src.name)
        self._db.execute(
            """
            INSERT INTO attachments(
                attachment_id, conversation_id, message_id, filename,
                mime, size, sha256, local_path, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (aid, conversation_id, message_id, src.name[:200], mime, len(data), digest, str(dest), now),
        )
        self._db.commit()
        return Attachment(aid, conversation_id, message_id, src.name[:200], mime, len(data), digest, str(dest), now)

    def list_for_conversation(self, conversation_id: str) -> List[Attachment]:
        rows = self._db.execute(
            "SELECT * FROM attachments WHERE conversation_id = ? ORDER BY created_at DESC",
            (conversation_id,),
        ).fetchall()
        return [_arow(r) for r in rows]

    def get(self, attachment_id: str) -> Optional[Attachment]:
        row = self._db.execute(
            "SELECT * FROM attachments WHERE attachment_id = ?", (attachment_id,)
        ).fetchone()
        return _arow(row) if row else None


def _arow(r) -> Attachment:
    return Attachment(
        r["attachment_id"], r["conversation_id"], r["message_id"], r["filename"],
        r["mime"], int(r["size"]), r["sha256"], r["local_path"], int(r["created_at"]),
    )


def _guess_mime(name: str) -> str:
    n = name.lower()
    if n.endswith((".png",)):
        return "image/png"
    if n.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if n.endswith((".gif",)):
        return "image/gif"
    if n.endswith((".webp",)):
        return "image/webp"
    if n.endswith((".pdf",)):
        return "application/pdf"
    if n.endswith((".zip",)):
        return "application/zip"
    if n.endswith((".txt", ".md")):
        return "text/plain"
    if n.endswith((".py", ".rs", ".js", ".ts")):
        return "text/plain"
    return "application/octet-stream"
