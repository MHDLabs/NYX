"""Contact management store."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List, Optional

from nyx_client.storage.db import Database
from nyx_client.config.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Contact:
    identity_id: str
    public_key: Optional[bytes]
    display_name: Optional[str]
    notes: Optional[str]
    trusted: bool
    created_at: int
    updated_at: int


class ContactStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert(
        self,
        identity_id: str,
        public_key: Optional[bytes] = None,
        display_name: Optional[str] = None,
        notes: Optional[str] = None,
        trusted: bool = False,
    ) -> Contact:
        now = int(time.time())
        existing = self._db.execute(
            "SELECT * FROM contacts WHERE identity_id = ?", (identity_id,)
        ).fetchone()
        if existing:
            self._db.execute(
                """
                UPDATE contacts SET
                    public_key = COALESCE(?, public_key),
                    display_name = COALESCE(?, display_name),
                    notes = COALESCE(?, notes),
                    trusted = ?,
                    updated_at = ?
                WHERE identity_id = ?
                """,
                (public_key, display_name, notes, int(trusted), now, identity_id),
            )
        else:
            self._db.execute(
                """
                INSERT INTO contacts(
                    identity_id, public_key, display_name, notes,
                    trusted, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (identity_id, public_key, display_name, notes, int(trusted), now, now),
            )
        self._db.commit()
        log.info("contact.upserted", identity=identity_id)
        return self.get(identity_id)  # type: ignore[return-value]

    def get(self, identity_id: str) -> Optional[Contact]:
        row = self._db.execute(
            "SELECT * FROM contacts WHERE identity_id = ?", (identity_id,)
        ).fetchone()
        return _row_to_contact(row) if row else None

    def list_all(self) -> List[Contact]:
        rows = self._db.execute(
            "SELECT * FROM contacts ORDER BY display_name, identity_id"
        ).fetchall()
        return [_row_to_contact(r) for r in rows]

    def delete(self, identity_id: str) -> bool:
        cur = self._db.execute(
            "DELETE FROM contacts WHERE identity_id = ?", (identity_id,)
        )
        self._db.commit()
        return cur.rowcount > 0


def _row_to_contact(row) -> Contact:
    return Contact(
        identity_id=row["identity_id"],
        public_key=bytes(row["public_key"]) if row["public_key"] else None,
        display_name=row["display_name"],
        notes=row["notes"],
        trusted=bool(row["trusted"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
