"""
Local search across users (contacts), groups, and channels.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from nyx_client.storage.contacts import ContactStore
from nyx_client.storage.rooms import RoomStore
from nyx_client.storage.messages import MessageStore


@dataclass
class SearchHit:
    kind: str  # user | group | channel | conversation
    id: str
    title: str
    subtitle: str = ""


class SearchService:
    def __init__(
        self,
        contacts: ContactStore,
        rooms: RoomStore,
        messages: Optional[MessageStore] = None,
    ) -> None:
        self._contacts = contacts
        self._rooms = rooms
        self._messages = messages

    def search(self, query: str, limit: int = 40) -> List[SearchHit]:
        q = (query or "").strip().lower()
        hits: List[SearchHit] = []

        for c in self._contacts.list_all():
            name = (c.display_name or "").lower()
            iid = c.identity_id.lower()
            if not q or q in name or q in iid:
                hits.append(
                    SearchHit(
                        kind="user",
                        id=c.identity_id,
                        title=c.display_name or c.identity_id[:28],
                        subtitle=c.identity_id[:36],
                    )
                )

        for r in self._rooms.list_all():
            blob = (r.title + " " + r.description + " " + r.room_id).lower()
            if not q or q in blob:
                kind = "group" if "group" in r.room_type else "channel"
                hits.append(
                    SearchHit(
                        kind=kind,
                        id=r.room_id,
                        title=r.title,
                        subtitle=r.room_type + (" · public" if r.is_public else " · private"),
                    )
                )

        if self._messages is not None:
            for conv in self._messages.list_conversations(limit=100):
                title = (conv.get("title") or conv.get("peer_id") or conv["conversation_id"]).lower()
                if conv.get("type") in ("private_group", "private_channel", "public_channel"):
                    continue  # already covered by rooms
                if not q or q in title or q in (conv.get("peer_id") or "").lower():
                    hits.append(
                        SearchHit(
                            kind="conversation",
                            id=conv.get("peer_id") or conv["conversation_id"],
                            title=conv.get("title") or conv.get("peer_id") or conv["conversation_id"][:24],
                            subtitle="dm",
                        )
                    )

        # de-dup by kind+id
        seen = set()
        out: List[SearchHit] = []
        for h in hits:
            key = h.kind + ":" + h.id
            if key in seen:
                continue
            seen.add(key)
            out.append(h)
            if len(out) >= limit:
                break
        return out
