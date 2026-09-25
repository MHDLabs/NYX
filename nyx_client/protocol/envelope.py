"""
Message envelope construction, signing, and verification.

Whitepaper Section 10 + 12:
  - Ed25519 sender signature over all fields except relay_metadata
  - Hash chain via previous_hash (tamper evidence)
  - Sequence numbers for ordering / gap detection
"""

from __future__ import annotations

import time
from typing import Optional

from nyx_client.crypto.identity import Identity
from nyx_client.crypto.keys import IdentityKeyPair
from nyx_client.protocol.types import (
    MessageEnvelope,
    MessageDirection,
    MessageStatus,
    message_id,
    PROTOCOL_VERSION,
)
from nyx_client.config.logging import get_logger

log = get_logger(__name__)


def build_envelope(
    identity: Identity,
    conversation_id: str,
    ciphertext: bytes,
    sequence: int,
    previous_hash: Optional[str] = None,
    timestamp: Optional[int] = None,
) -> MessageEnvelope:
    """
    Build and sign a new outbound message envelope.

    The identity key signs the canonical content. Device ID is taken
    from the primary device.
    """
    device = identity.primary_device()
    if device is None:
        raise RuntimeError("identity has no active device")

    env = MessageEnvelope(
        message_id=message_id(),
        sender_id=identity.id,
        device_id=device.device_id,
        conversation_id=conversation_id,
        timestamp=timestamp if timestamp is not None else int(time.time() * 1000),
        sequence=sequence,
        ciphertext=ciphertext,
        previous_hash=previous_hash,
        protocol_version=PROTOCOL_VERSION,
        direction=MessageDirection.OUT,
        status=MessageStatus.PENDING,
    )
    env.signature = identity.sign(env.canonical_bytes())
    log.debug(
        "envelope.built",
        message_id=env.message_id,
        conversation_id=conversation_id,
        sequence=sequence,
    )
    return env


def verify_envelope(envelope: MessageEnvelope) -> bool:
    """
    Verify the Ed25519 signature using the sender identity string.

    Returns True if the signature is valid for the claimed sender.
    Does not check hash-chain continuity (caller responsibility).
    """
    if not envelope.signature:
        return False
    return IdentityKeyPair.verify_with_identity(
        envelope.sender_id,
        envelope.signature,
        envelope.canonical_bytes(),
    )


def verify_hash_chain(
    current: MessageEnvelope,
    previous: Optional[MessageEnvelope],
) -> bool:
    """
    Verify hash-chain continuity.

    If previous is None, current.previous_hash must also be None (first message).
    Otherwise current.previous_hash must equal previous.content_hash().
    """
    if previous is None:
        return current.previous_hash is None
    if current.previous_hash is None:
        return False
    return current.previous_hash == previous.content_hash()
