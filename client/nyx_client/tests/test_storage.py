"""Unit tests for the storage layer."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from nyx_client.crypto import Identity, create_recoverable_identity, aead_generate_key
from nyx_client.crypto.aead import generate_key
from nyx_client.storage import (
    Database,
    ProfileStore,
    MessageStore,
    StoredMessage,
    ContactStore,
)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "test.db")
    database.connect()
    yield database
    database.close()


@pytest.fixture
def profile_key() -> bytes:
    return generate_key()


def test_schema_creates_tables(db: Database) -> None:
    tables = {
        r["name"]
        for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "identity_profile" in tables
    assert "devices" in tables
    assert "contacts" in tables
    assert "conversations" in tables
    assert "messages" in tables


def test_profile_save_and_load(db: Database, profile_key: bytes) -> None:
    store = ProfileStore(db, profile_key)
    assert not store.has_profile()

    bundle = create_recoverable_identity()
    store.save_identity(bundle.identity, recovery_seed=bundle.seed)
    assert store.has_profile()

    loaded = store.load_identity()
    assert loaded is not None
    assert loaded.id == bundle.identity.id
    assert loaded.public_key_bytes == bundle.identity.public_key_bytes
    assert len(loaded.devices) == len(bundle.identity.devices)

    # Sign with loaded key must work
    msg = b"persist-test"
    sig = loaded.sign(msg)
    assert loaded.verify(sig, msg)

    seed = store.load_recovery_seed()
    assert seed == bundle.seed


def test_profile_wrong_key_fails(db: Database, profile_key: bytes) -> None:
    from cryptography.exceptions import InvalidTag

    store = ProfileStore(db, profile_key)
    ident = Identity.create()
    store.save_identity(ident)

    bad_store = ProfileStore(db, generate_key())
    with pytest.raises(InvalidTag):
        bad_store.load_identity()


def test_contacts_crud(db: Database) -> None:
    store = ContactStore(db)
    c = store.upsert(
        "nyx1testcontact000000000000000000000000000000000000000",
        display_name="Alice",
        trusted=True,
    )
    assert c.display_name == "Alice"
    assert c.trusted is True

    got = store.get(c.identity_id)
    assert got is not None
    assert got.display_name == "Alice"

    store.upsert(c.identity_id, display_name="Alice Updated")
    assert store.get(c.identity_id).display_name == "Alice Updated"  # type: ignore

    all_c = store.list_all()
    assert len(all_c) == 1

    assert store.delete(c.identity_id) is True
    assert store.get(c.identity_id) is None


def test_messages_history(db: Database) -> None:
    store = MessageStore(db)
    conv = "conv_test_1"
    store.ensure_conversation(conv, peer_id="nyx1peer")

    for i in range(1, 6):
        store.insert(
            StoredMessage(
                message_id=f"msg_{i}",
                conversation_id=conv,
                sender_id="nyx1me",
                device_id="dev_1",
                payload=f"cipher-{i}".encode(),
                signature=b"sig",
                previous_hash=None if i == 1 else f"hash_{i-1}",
                sequence=i,
                timestamp=int(time.time()) + i,
                direction="out",
                status="sent",
                protocol_version=3,
            )
        )

    hist = store.history(conv, limit=10)
    assert len(hist) == 5
    assert [m.sequence for m in hist] == [1, 2, 3, 4, 5]
    assert store.last_sequence(conv) == 5

    partial = store.history(conv, limit=2, before_sequence=4)
    assert [m.sequence for m in partial] == [2, 3]
