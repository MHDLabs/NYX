"""
Cryptographic key primitives for NYX.

Whitepaper Section 05 (User Model & Identity) and Section 49 (Crypto Inventory):

  Identity Key   : Ed25519, long-term
  Device Key     : Ed25519, per-device, revocable
  Session Key    : X25519, per-session (placeholder for later ratchet)
  Message signing: Ed25519

All private key material is kept in memory only; never logged.
Uses the audited `cryptography` library (no invented algorithms).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature

from nyx_client.crypto.bech32 import encode as bech32_encode, decode as bech32_decode
from nyx_client.config.logging import get_logger

log = get_logger(__name__)

HRP = "nyx"  # Bech32 human-readable part -> nyx1...


@dataclass(frozen=True, slots=True)
class IdentityKeyPair:
    """Long-term identity key pair (Ed25519)."""

    private_key: Ed25519PrivateKey
    public_key: Ed25519PublicKey

    @classmethod
    def generate(cls) -> "IdentityKeyPair":
        priv = Ed25519PrivateKey.generate()
        return cls(private_key=priv, public_key=priv.public_key())

    @classmethod
    def from_private_bytes(cls, data: bytes) -> "IdentityKeyPair":
        if len(data) != 32:
            raise ValueError("Ed25519 private key must be 32 bytes")
        priv = Ed25519PrivateKey.from_private_bytes(data)
        return cls(private_key=priv, public_key=priv.public_key())

    def private_bytes(self) -> bytes:
        return self.private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )

    def public_bytes(self) -> bytes:
        return self.public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    def sign(self, message: bytes) -> bytes:
        return self.private_key.sign(message)

    def verify(self, signature: bytes, message: bytes) -> bool:
        try:
            self.public_key.verify(signature, message)
            return True
        except InvalidSignature:
            return False

    def identity_string(self) -> str:
        """Bech32-encoded identity: nyx1..."""
        return bech32_encode(HRP, 0, self.public_bytes())

    @staticmethod
    def public_key_from_identity(identity: str) -> Ed25519PublicKey:
        hrp, witver, prog = bech32_decode(identity)
        if hrp != HRP:
            raise ValueError(f"unexpected HRP: {hrp}")
        if witver != 0:
            raise ValueError(f"unexpected witness version: {witver}")
        if len(prog) != 32:
            raise ValueError("identity public key must be 32 bytes")
        return Ed25519PublicKey.from_public_bytes(prog)

    @staticmethod
    def verify_with_identity(
        identity: str, signature: bytes, message: bytes
    ) -> bool:
        try:
            pub = IdentityKeyPair.public_key_from_identity(identity)
            pub.verify(signature, message)
            return True
        except (ValueError, InvalidSignature):
            return False


@dataclass(frozen=True, slots=True)
class DeviceKeyPair:
    """Per-device Ed25519 key pair, signed by the identity key."""

    private_key: Ed25519PrivateKey
    public_key: Ed25519PublicKey
    device_id: str  # short fingerprint for display

    @classmethod
    def generate(cls) -> "DeviceKeyPair":
        priv = Ed25519PrivateKey.generate()
        pub = priv.public_key()
        pub_bytes = pub.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        # First 8 bytes of public key as hex device_id (whitepaper style)
        device_id = "dev_" + pub_bytes[:8].hex()
        return cls(private_key=priv, public_key=pub, device_id=device_id)

    def private_bytes(self) -> bytes:
        return self.private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )

    def public_bytes(self) -> bytes:
        return self.public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    def sign(self, message: bytes) -> bytes:
        return self.private_key.sign(message)

    def verify(self, signature: bytes, message: bytes) -> bool:
        try:
            self.public_key.verify(signature, message)
            return True
        except InvalidSignature:
            return False


@dataclass(frozen=True, slots=True)
class X25519KeyPair:
    """X25519 key pair for key agreement (sessions / prekeys)."""

    private_key: X25519PrivateKey
    public_key: X25519PublicKey

    @classmethod
    def generate(cls) -> "X25519KeyPair":
        priv = X25519PrivateKey.generate()
        return cls(private_key=priv, public_key=priv.public_key())

    @classmethod
    def from_private_bytes(cls, data: bytes) -> "X25519KeyPair":
        if len(data) != 32:
            raise ValueError("X25519 private key must be 32 bytes")
        priv = X25519PrivateKey.from_private_bytes(data)
        return cls(private_key=priv, public_key=priv.public_key())

    def private_bytes(self) -> bytes:
        return self.private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )

    def public_bytes(self) -> bytes:
        return self.public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    def exchange(self, peer_public_bytes: bytes) -> bytes:
        peer = X25519PublicKey.from_public_bytes(peer_public_bytes)
        return self.private_key.exchange(peer)


def generate_random_bytes(n: int = 32) -> bytes:
    """Cryptographically secure random bytes."""
    return os.urandom(n)
