
from pathlib import Path
from nyx_client.crypto import Identity
from nyx_client.storage import Database, ContactStore, MessageStore
from nyx_client.storage.rooms import RoomStore
from nyx_client.core.search import SearchService

def test_create_group_and_channel(tmp_path: Path) -> None:
    db = Database(tmp_path / "r.db")
    db.connect()
    rooms = RoomStore(db)
    ident = Identity.create()
    g = rooms.create(room_type="private_group", title="Night Ops", owner_id=ident.id, description="team")
    assert g.room_id.startswith("grp_")
    c = rooms.create(room_type="public_channel", title="Announcements", owner_id=ident.id, is_public=True)
    assert c.is_public
    rooms.update_settings(g.room_id, title="Night Ops 2", description="updated")
    g2 = rooms.get(g.room_id)
    assert g2 and g2.title == "Night Ops 2"
    found = rooms.search("Night")
    assert any(x.room_id == g.room_id for x in found)
    db.close()

def test_search_users_and_rooms(tmp_path: Path) -> None:
    db = Database(tmp_path / "s.db")
    db.connect()
    contacts = ContactStore(db)
    rooms = RoomStore(db)
    messages = MessageStore(db)
    contacts.upsert("nyx1abc", display_name="Bob Builder")
    rooms.create(room_type="private_group", title="Builders", owner_id="nyx1x")
    svc = SearchService(contacts, rooms, messages)
    hits = svc.search("bob")
    assert any(h.kind == "user" and "Bob" in h.title for h in hits)
    hits2 = svc.search("build")
    assert len(hits2) >= 1
    db.close()
