"""Unit tests for protocol types and message envelope."""

from __future__ import annotations

import time

import pytest

from nyx_client.crypto import Identity
from nyx_client.protocol import (
    build_envelope,
    verify_envelope,
    verify_hash_chain,
    message_id,
    generate_uuidv7,
    conversation_id_for_dm,
    MessageEnvelope,
    PROTOCOL_VERSION,
)


def test_uuidv7_format_and_sortable() -> None:
    u1 = generate_uuidv7()
    time.sleep(0.002)
    u2 = generate_uuidv7()
    assert len(u1) == 36
    assert u1[14] == "7"  # version nibble
    # Time-sortable: later UUID should be lexicographically greater
    assert u2 > u1


def test_message_id_prefix() -> None:
    mid = message_id()
    assert mid.startswith("nyx_msg_")


def test_conversation_id_deterministic() -> None:
    a, b = "nyx1aaa", "nyx1bbb"
    assert conversation_id_for_dm(a, b) == conversation_id_for_dm(b, a)


def test_build_and_verify_envelope() -> None:
    ident = Identity.create()
    env = build_envelope(
        identity=ident,
        conversation_id="conv_test",
        ciphertext=b"encrypted-payload",
        sequence=1,
    )
    assert env.sender_id == ident.id
    assert env.protocol_version == PROTOCOL_VERSION
    assert env.signature
    assert verify_envelope(env)


def test_tampered_envelope_fails_verify() -> None:
    ident = Identity.create()
    env = build_envelope(ident, "conv_x", b"data", sequence=1)
    # Tamper with ciphertext after signing
    tampered = MessageEnvelope(
        message_id=env.message_id,
        sender_id=env.sender_id,
        device_id=env.device_id,
        conversation_id=env.conversation_id,
        timestamp=env.timestamp,
        sequence=env.sequence,
        ciphertext=b"TAMPERED",
        signature=env.signature,
        previous_hash=env.previous_hash,
        protocol_version=env.protocol_version,
    )
    assert not verify_envelope(tampered)


def test_hash_chain() -> None:
    ident = Identity.create()
    e1 = build_envelope(ident, "conv_h", b"msg1", sequence=1)
    e2 = build_envelope(
        ident, "conv_h", b"msg2", sequence=2, previous_hash=e1.content_hash()
    )
    assert verify_hash_chain(e1, None)
    assert verify_hash_chain(e2, e1)
    # Broken chain
    e3 = build_envelope(ident, "conv_h", b"msg3", sequence=3, previous_hash="deadbeef")
    assert not verify_hash_chain(e3, e2)


def test_wire_roundtrip() -> None:
    ident = Identity.create()
    env = build_envelope(ident, "conv_w", b"\x00\x01\xff", sequence=7)
    wire = env.to_wire_dict()
    restored = MessageEnvelope.from_wire_dict(wire)
    assert restored.message_id == env.message_id
    assert restored.ciphertext == env.ciphertext
    assert restored.signature == env.signature
    assert verify_envelope(restored)
