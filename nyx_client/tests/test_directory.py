
from pathlib import Path
from nyx_client.crypto import Identity
from nyx_client.storage import Database, ContactStore, UserPrefs
from nyx_client.core.directory import Directory

def test_display_name_and_profile(tmp_path: Path) -> None:
    db = Database(tmp_path / "d.db")
    db.connect()
    contacts = ContactStore(db)
    prefs = UserPrefs(db)
    me = Identity.create()
    other = Identity.create()
    d = Directory(contacts, prefs, self_id=me.id)
    prefs.set_display_name("Myself")
    prefs.set_bio("Local bio")
    assert d.display_name(me.id) == "Myself"
    d.set_remote_profile(other.id, display_name="Alice", bio="Night builder")
    p = d.profile(other.id)
    assert p.display_name == "Alice"
    assert p.bio == "Night builder"
    assert not p.is_self
    self_p = d.profile(me.id)
    assert self_p.is_self and self_p.bio == "Local bio"
    db.close()
