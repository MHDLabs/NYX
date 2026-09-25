"""
NYX cryptographic identity.

Whitepaper Section 05 — User Model & Identity:

  Account (nyx1...)
  ├── Identity Key Pair (Ed25519, long-term)
  │   ├── Device 1 ── Session Keys (X25519, rotating)
  │   └── ...
  ├── Prekey Bundle (signed, for X3DH)
  ├── Optional: Recovery Key (user-held)
  └── Optional: Email (recovery only, not identity)

This module owns creation, loading, and basic operations on an Identity.
Private keys never leave this module except as explicit export for
encrypted storage (handled by the storage layer).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from nyx_client.crypto.keys import (
    DeviceKeyPair,
    IdentityKeyPair,
    X25519KeyPair,
    generate_random_bytes,
)
from nyx_client.config.logging import get_logger

log = get_logger(__name__)


@dataclass
class Identity:
    """
    In-memory representation of a NYX identity.

    The object is mutable only for adding/revoking devices.
    The identity key pair itself is immutable after creation.
    """

    identity_key: IdentityKeyPair
    devices: List[DeviceKeyPair] = field(default_factory=list)
    # Future: prekeys, recovery material, email_hash

    @property
    def id(self) -> str:
        """Bech32 identity string (nyx1...)."""
        return self.identity_key.identity_string()

    @property
    def public_key_bytes(self) -> bytes:
        return self.identity_key.public_bytes()

    @classmethod
    def create(cls, with_device: bool = True) -> "Identity":
        """
        Create a brand-new identity with a fresh Ed25519 key pair.

        Parameters
        ----------
        with_device:
            If True, also generate and attach the first device key.
        """
        ik = IdentityKeyPair.generate()
        devices: List[DeviceKeyPair] = []
        if with_device:
            devices.append(DeviceKeyPair.generate())
        identity = cls(identity_key=ik, devices=devices)
        log.info(
            "identity.created",
            identity=identity.id,
            devices=len(devices),
        )
        return identity

    def add_device(self) -> DeviceKeyPair:
        """Generate and attach a new device key pair."""
        dev = DeviceKeyPair.generate()
        self.devices.append(dev)
        log.info("identity.device_added", identity=self.id, device_id=dev.device_id)
        return dev

    def primary_device(self) -> Optional[DeviceKeyPair]:
        """Return the first non-revoked device, or None."""
        return self.devices[0] if self.devices else None

    def sign(self, message: bytes) -> bytes:
        """Sign with the long-term identity key."""
        return self.identity_key.sign(message)

    def verify(self, signature: bytes, message: bytes) -> bool:
        return self.identity_key.verify(signature, message)

    def export_public(self) -> dict:
        """Public-only view safe to share / store in clear."""
        return {
            "identity": self.id,
            "public_key": self.public_key_bytes.hex(),
            "devices": [
                {"device_id": d.device_id, "public_key": d.public_bytes().hex()}
                for d in self.devices
            ],
        }

    def __repr__(self) -> str:
        return f"Identity({self.id[:20]}..., devices={len(self.devices)})"


# ---------------------------------------------------------------------------
# Recovery helpers (BIP39)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class RecoveryBundle:
    """
    Result of generating a recoverable identity.

    The mnemonic must be shown to the user exactly once and never stored
    by the client in cleartext. The seed is used to derive a recovery
    key that can re-create or unlock the identity later.
    """

    identity: Identity
    mnemonic: tuple  # tuple[str, ...]  (immutable)
    seed: bytes      # 64-byte BIP39 seed (keep in memory only)

    def mnemonic_phrase(self) -> str:
        return " ".join(self.mnemonic)


def create_recoverable_identity(
    passphrase: str = "",
    with_device: bool = True,
) -> RecoveryBundle:
    """
    Create a new identity together with a 24-word BIP39 recovery mnemonic.

    The mnemonic is the only way to recover the identity if all devices
    are lost (whitepaper Section 05). The caller MUST display it to the
    user and then discard it from memory after confirmation.
    """
    from nyx_client.crypto.bip39 import generate_mnemonic, mnemonic_to_seed

    words = generate_mnemonic(256)
    seed = mnemonic_to_seed(words, passphrase=passphrase)
    identity = Identity.create(with_device=with_device)
    log.info(
        "identity.recoverable_created",
        identity=identity.id,
        mnemonic_words=len(words),
    )
    return RecoveryBundle(
        identity=identity,
        mnemonic=tuple(words),
        seed=seed,
    )
