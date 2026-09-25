"""
End-to-end encryption for NYX DMs — Double Ratchet.

Whitepaper Section 11: X3DH + Double Ratchet.
MVP establishes the initial shared secret via X25519 ECDH between
published device keys (stand-in for full X3DH prekey bundle), then
runs the Signal Double Ratchet for all subsequent messages.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from nyx_client.crypto.keys import X25519KeyPair
from nyx_client.crypto.ratchet import DoubleRatchetSession
from nyx_client.config.logging import get_logger

log = get_logger(__name__)

_INFO_X3DH = b"nyx-x3dh-sk-v1"


def _initial_shared_secret(
    local: X25519KeyPair,
    peer_public: bytes,
    conversation_id: str,
    id_a: str,
    id_b: str,
) -> bytes:
    """Derive SK equivalent from static ECDH (pre-X3DH-bundle MVP)."""
    dh = local.exchange(peer_public)
    a, b = sorted([id_a, id_b])
    transcript = conversation_id.encode() + b"|" + a.encode() + b"|" + b.encode()
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=transcript,
        info=_INFO_X3DH,
    ).derive(dh)


@dataclass
class DMSession:
    """Wrapper around DoubleRatchetSession with conversation metadata."""

    conversation_id: str
    local_identity: str
    peer_identity: str
    ratchet: DoubleRatchetSession
    is_initiator: bool

    def encrypt(self, plaintext: bytes, sequence: int = 0) -> bytes:
        # sequence is tracked inside the ratchet; param kept for API compat
        return self.ratchet.encrypt(plaintext)

    def decrypt(self, blob: bytes, sequence: int = 0) -> bytes:
        return self.ratchet.decrypt(blob)


def open_dm_session(
    conversation_id: str,
    local_identity: str,
    peer_identity: str,
    local_x25519: X25519KeyPair,
    peer_x25519_public: bytes,
    *,
    initiator: bool = True,
) -> DMSession:
    """
    Open a Double Ratchet session.

    initiator=True  -> Alice (sends first)
    initiator=False -> Bob (waits for first message, then replies)
    """
    sk = _initial_shared_secret(
        local_x25519, peer_x25519_public, conversation_id, local_identity, peer_identity
    )
    if initiator:
        ratchet = DoubleRatchetSession.initiate(sk, peer_x25519_public)
    else:
        ratchet = DoubleRatchetSession.respond(sk, local_x25519)
    log.debug(
        "e2ee.dr_session_opened",
        conversation_id=conversation_id,
        initiator=initiator,
    )
    return DMSession(
        conversation_id=conversation_id,
        local_identity=local_identity,
        peer_identity=peer_identity,
        ratchet=ratchet,
        is_initiator=initiator,
    )


def generate_dm_keypair() -> X25519KeyPair:
    return X25519KeyPair.generate()
