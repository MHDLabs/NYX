"""
Core protocol types for NYX.

Whitepaper Section 10 — Messaging Model / Abstract Message Structure.
Terminology is taken verbatim from the specification.
"""

from __future__ import annotations

import time
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class ConversationType(str, Enum):
    DM = "dm"
    PRIVATE_GROUP = "private_group"
    PRIVATE_CHANNEL = "private_channel"
    PUBLIC_CHANNEL = "public_channel"
    TECHNICAL_ROOM = "technical_room"


class MessageDirection(str, Enum):
    IN = "in"
    OUT = "out"


class MessageStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"


PROTOCOL_VERSION = 3


def generate_uuidv7() -> str:
    """
    Generate a UUIDv7 (time-sortable) as specified in the whitepaper.

    Layout (RFC 9562):
      48-bit unix timestamp ms | version(4) | 12-bit rand | variant | 62-bit rand
    """
    ts_ms = int(time.time() * 1000) & 0xFFFFFFFFFFFF  # 48 bits
    rand_a = int.from_bytes(os.urandom(2), "big") & 0x0FFF  # 12 bits
    rand_b = int.from_bytes(os.urandom(8), "big") & 0x3FFFFFFFFFFFFFFF  # 62 bits

    uuid_int = (ts_ms << 80) | (0x7 << 76) | (rand_a << 64) | (0x2 << 62) | rand_b
    hex_str = f"{uuid_int:032x}"
    return (
        f"{hex_str[0:8]}-{hex_str[8:12]}-{hex_str[12:16]}-"
        f"{hex_str[16:20]}-{hex_str[20:32]}"
    )


def message_id() -> str:
    """Whitepaper-style message ID: nyx_msg_<uuidv7>."""
    return f"nyx_msg_{generate_uuidv7().replace('-', '')}"


def conversation_id_for_dm(id_a: str, id_b: str) -> str:
    """
    Deterministic DM conversation ID from two identity strings.
    Order-independent so both peers derive the same ID.
    """
    a, b = sorted([id_a, id_b])
    return f"conv_dm_{_short_hash(a + b)}"


def _short_hash(data: str) -> str:
    import hashlib
    return hashlib.blake2b(data.encode(), digest_size=16).hexdigest()


@dataclass(frozen=True, slots=True)
class RelayMetadata:
    received_at: Optional[int] = None
    relay_id: Optional[str] = None
    hop_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.received_at is not None:
            d["received_at"] = self.received_at
        if self.relay_id is not None:
            d["relay_id"] = self.relay_id
        if self.hop_count:
            d["hop_count"] = self.hop_count
        return d


@dataclass
class MessageEnvelope:
    """
    Abstract message envelope (whitepaper Section 10).

    Fields mirror the specification exactly. `signature` covers all
    fields except `relay_metadata`.
    """

    message_id: str
    sender_id: str
    device_id: str
    conversation_id: str
    timestamp: int
    sequence: int
    ciphertext: bytes  # opaque; for public channels may hold plaintext
    signature: bytes = b""
    previous_hash: Optional[str] = None
    protocol_version: int = PROTOCOL_VERSION
    relay_metadata: Optional[RelayMetadata] = None

    # Local-only (not serialized to wire)
    direction: MessageDirection = MessageDirection.OUT
    status: MessageStatus = MessageStatus.PENDING

    def canonical_bytes(self) -> bytes:
        """
        Deterministic byte representation for signing and hashing.
        Excludes signature and relay_metadata (per whitepaper).
        """
        parts = [
            self.message_id,
            self.sender_id,
            self.device_id,
            self.conversation_id,
            str(self.timestamp),
            str(self.sequence),
            self.ciphertext.hex(),
            self.previous_hash or "",
            str(self.protocol_version),
        ]
        return "|".join(parts).encode("utf-8")

    def content_hash(self) -> str:
        """BLAKE2b hash of canonical content (BLAKE3 when package available)."""
        import hashlib
        return hashlib.blake2b(self.canonical_bytes(), digest_size=32).hexdigest()

    def to_wire_dict(self) -> dict[str, Any]:
        """Serialize for transport / storage (no local-only fields)."""
        d: dict[str, Any] = {
            "message_id": self.message_id,
            "sender_id": self.sender_id,
            "device_id": self.device_id,
            "conversation_id": self.conversation_id,
            "timestamp": self.timestamp,
            "sequence": self.sequence,
            "ciphertext": self.ciphertext.hex(),
            "signature": self.signature.hex() if self.signature else "",
            "previous_hash": self.previous_hash,
            "protocol_version": self.protocol_version,
        }
        if self.relay_metadata is not None:
            d["relay_metadata"] = self.relay_metadata.to_dict()
        return d

    @classmethod
    def from_wire_dict(cls, data: dict[str, Any]) -> "MessageEnvelope":
        rm = data.get("relay_metadata")
        relay = RelayMetadata(**rm) if isinstance(rm, dict) else None
        return cls(
            message_id=data["message_id"],
            sender_id=data["sender_id"],
            device_id=data["device_id"],
            conversation_id=data["conversation_id"],
            timestamp=int(data["timestamp"]),
            sequence=int(data["sequence"]),
            ciphertext=bytes.fromhex(data["ciphertext"]),
            signature=bytes.fromhex(data["signature"]) if data.get("signature") else b"",
            previous_hash=data.get("previous_hash"),
            protocol_version=int(data.get("protocol_version", PROTOCOL_VERSION)),
            relay_metadata=relay,
        )
