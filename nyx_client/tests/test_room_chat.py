
"""Room chat + unread badges."""
from __future__ import annotations
from pathlib import Path
from nyx_client.crypto import Identity
from nyx_client.storage import Database, MessageStore, ContactStore
from nyx_client.storage.rooms import RoomStore
from nyx_client.core.messaging import MessagingService


def test_room_send_and_history(tmp_path: Path) -> None:
    db = Database(tmp_path / "r.db")
    db.connect()
    ident = Identity.create()
    rooms = RoomStore(db)
    g = rooms.create(room_type="private_group", title="Ops", owner_id=ident.id)
    svc = MessagingService(ident, MessageStore(db), ContactStore(db))
    env = svc.send_room_message(g.room_id, b"hello group")
    assert env.sequence == 1
    hist = svc.history_conversation(g.room_id)
    assert len(hist) == 1
    assert hist[0].plaintext == b"hello group"
    db.close()


def test_unread_badge(tmp_path: Path) -> None:
    db = Database(tmp_path / "u.db")
    db.connect()
    ident = Identity.create()
    store = MessageStore(db)
    rooms = RoomStore(db)
    g = rooms.create(room_type="public_channel", title="News", owner_id=ident.id, is_public=True)
    svc = MessagingService(ident, store, ContactStore(db))
    svc.send_room_message(g.room_id, b"one")
    svc.send_room_message(g.room_id, b"two")
    assert store.unread_count(g.room_id) == 2
    store.mark_read(g.room_id)
    assert store.unread_count(g.room_id) == 0
    listed = store.list_conversations()
    row = next(x for x in listed if x["conversation_id"] == g.room_id)
    assert row["unread"] == 0
    svc.send_room_message(g.room_id, b"three")
    assert store.unread_count(g.room_id) == 1
    db.close()
