"""Tests for conversation list and user profile prefs."""

from __future__ import annotations
from pathlib import Path
import pytest
from nyx_client.storage import Database, MessageStore, StoredMessage, UserPrefs


def test_list_conversations_ordered(tmp_path: Path) -> None:
    db = Database(tmp_path / "c.db")
    db.connect()
    store = MessageStore(db)
    store.ensure_conversation("c1", peer_id="p1", conv_type="dm", title="first")
    store.ensure_conversation("c2", peer_id="p2", conv_type="dm", title="second")
    # bump c1 activity by inserting a message
    import time
    store.insert(StoredMessage(
        message_id="m1", conversation_id="c1", sender_id="me",
        device_id="d1", payload=b"x", signature=b"", previous_hash=None,
        sequence=1, timestamp=int(time.time()*1000),
        direction="out", status="sent", protocol_version=3,
    ))
    listed = store.list_conversations()
    assert len(listed) >= 2
    assert listed[0]["conversation_id"] == "c1"  # most recently updated
    dms = store.list_conversations(conv_type="dm")
    assert all(x["type"] == "dm" for x in dms)
    db.close()


def test_user_prefs_name_bio(tmp_path: Path) -> None:
    db = Database(tmp_path / "p.db")
    db.connect()
    prefs = UserPrefs(db)
    assert prefs.get_profile().display_name == ""
    prefs.set_display_name("Alice")
    prefs.set_bio("Building in the night.")
    p = prefs.get_profile()
    assert p.display_name == "Alice"
    assert "night" in p.bio
    db.close()
