"""Integration tests for DM messaging (crypto + protocol + storage)."""

from __future__ import annotations

from pathlib import Path

import pytest

from nyx_client.crypto import Identity
from nyx_client.core.messaging import MessagingService
from nyx_client.core.e2ee import open_dm_session, generate_dm_keypair as gen_kp
from nyx_client.storage import Database, MessageStore, ContactStore
from nyx_client.protocol import conversation_id_for_dm, verify_envelope


@pytest.fixture
def alice_bob(tmp_path: Path):
    """Two identities with separate databases and registered peer keys."""
    alice = Identity.create()
    bob = Identity.create()

    db_a = Database(tmp_path / "alice.db")
    db_a.connect()
    db_b = Database(tmp_path / "bob.db")
    db_b.connect()

    # Each side has its own DM X25519 key; exchange public keys
    alice_svc = MessagingService(alice, MessageStore(db_a), ContactStore(db_a))
    bob_svc = MessagingService(bob, MessageStore(db_b), ContactStore(db_b))

    alice_svc.register_peer_key(bob.id, bob_svc.dm_public_key)
    bob_svc.register_peer_key(alice.id, alice_svc.dm_public_key)

    alice_svc.ensure_contact(bob.id, display_name="Bob")
    bob_svc.ensure_contact(alice.id, display_name="Alice")

    yield alice, bob, alice_svc, bob_svc

    db_a.close()
    db_b.close()


def test_e2ee_session_roundtrip() -> None:
    a = Identity.create()
    b = Identity.create()
    ka = gen_kp()
    kb = gen_kp()
    conv = conversation_id_for_dm(a.id, b.id)

    sess_a = open_dm_session(conv, a.id, b.id, ka, kb.public_bytes(), initiator=True)
    sess_b = open_dm_session(conv, b.id, a.id, kb, ka.public_bytes(), initiator=False)

    blob = sess_a.encrypt(b"hello bob")
    assert sess_b.decrypt(blob) == b"hello bob"
    blob2 = sess_b.encrypt(b"hello alice")
    assert sess_a.decrypt(blob2) == b"hello alice"


def test_send_dm_persists_and_signs(alice_bob) -> None:
    alice, bob, alice_svc, bob_svc = alice_bob
    env = alice_svc.send_dm(bob.id, b"Hello, Bob!")
    assert verify_envelope(env)
    assert env.sequence == 1
    assert env.sender_id == alice.id

    hist = alice_svc.history(bob.id)
    assert len(hist) == 1
    assert hist[0].plaintext == b"Hello, Bob!"
    assert hist[0].verified is True


def test_send_receive_cross_party(alice_bob) -> None:
    alice, bob, alice_svc, bob_svc = alice_bob

    # Alice sends
    env = alice_svc.send_dm(bob.id, b"secret message")

    # Bob ingests the same envelope (simulating network delivery)
    # Bob needs Alice's DM public key (already registered in fixture)
    decrypted = bob_svc.ingest_envelope(env)
    assert decrypted.plaintext == b"secret message"
    assert decrypted.verified is True
    assert decrypted.sender_id == alice.id

    # Bob's history shows the message
    hist = bob_svc.history(alice.id)
    assert len(hist) == 1
    assert hist[0].plaintext == b"secret message"


def test_hash_chain_across_messages(alice_bob) -> None:
    alice, bob, alice_svc, _ = alice_bob
    e1 = alice_svc.send_dm(bob.id, b"one")
    e2 = alice_svc.send_dm(bob.id, b"two")
    e3 = alice_svc.send_dm(bob.id, b"three")
    assert e1.previous_hash is None
    assert e2.previous_hash == e1.content_hash()
    assert e3.previous_hash == e2.content_hash()
    assert e1.sequence == 1
    assert e3.sequence == 3


def test_send_without_peer_key_fails(tmp_path: Path) -> None:
    alice = Identity.create()
    db = Database(tmp_path / "x.db")
    db.connect()
    svc = MessagingService(alice, MessageStore(db), ContactStore(db))
    with pytest.raises(RuntimeError, match="no X25519 key"):
        svc.send_dm("nyx1unknownpeer", b"hi")
    db.close()


def test_empty_plaintext_rejected(alice_bob) -> None:
    _, bob, alice_svc, _ = alice_bob
    with pytest.raises(ValueError):
        alice_svc.send_dm(bob.id, b"")


def test_contact_created_on_ensure(alice_bob) -> None:
    _, bob, alice_svc, _ = alice_bob
    c = alice_svc.ensure_contact(bob.id, display_name="Bobby")
    assert c.display_name == "Bobby"
    assert c.identity_id == bob.id
