"""
Encrypted identity profile store.

Private key material is encrypted with AEAD before being written to SQLite.
The encryption key (profile key) is derived from a user passphrase or held
in memory for the session. MVP uses an in-memory profile key supplied at
unlock time.

Whitepaper: Private Data at Rest — encrypted local DB / SQLCipher.
"""

from __future__ import annotations

import time
from typing import Optional

from nyx_client.crypto.aead import encrypt, decrypt, generate_key
from nyx_client.crypto.keys import IdentityKeyPair, DeviceKeyPair
from nyx_client.crypto.identity import Identity
from nyx_client.storage.db import Database
from nyx_client.config.logging import get_logger

log = get_logger(__name__)

# AAD strings bind ciphertext to purpose (prevents cross-context reuse)
_AAD_IDENTITY = b"nyx-profile-identity-key-v1"
_AAD_DEVICE = b"nyx-profile-device-key-v1"
_AAD_RECOVERY = b"nyx-profile-recovery-seed-v1"


class ProfileStore:
    """Load / save the local identity profile."""

    def __init__(self, db: Database, profile_key: bytes) -> None:
        if len(profile_key) != 32:
            raise ValueError("profile_key must be 32 bytes")
        self._db = db
        self._key = profile_key

    def has_profile(self) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM identity_profile WHERE id = 1"
        ).fetchone()
        return row is not None

    def save_identity(
        self,
        identity: Identity,
        recovery_seed: Optional[bytes] = None,
    ) -> None:
        """Persist identity + devices. Overwrites existing profile."""
        now = int(time.time())
        enc_ik = encrypt(
            self._key,
            identity.identity_key.private_bytes(),
            associated_data=_AAD_IDENTITY,
        )
        enc_recovery = None
        if recovery_seed is not None:
            enc_recovery = encrypt(
                self._key, recovery_seed, associated_data=_AAD_RECOVERY
            )

        with self._db.transaction() as conn:
            conn.execute("DELETE FROM devices")
            conn.execute("DELETE FROM identity_profile")
            conn.execute(
                """
                INSERT INTO identity_profile(
                    id, identity_id, public_key, enc_identity_key,
                    enc_recovery_seed, created_at, updated_at
                ) VALUES (1, ?, ?, ?, ?, ?, ?)
                """,
                (
                    identity.id,
                    identity.public_key_bytes,
                    enc_ik,
                    enc_recovery,
                    now,
                    now,
                ),
            )
            for dev in identity.devices:
                enc_dk = encrypt(
                    self._key,
                    dev.private_bytes(),
                    associated_data=_AAD_DEVICE + dev.device_id.encode(),
                )
                conn.execute(
                    """
                    INSERT INTO devices(
                        device_id, public_key, enc_private_key,
                        name, created_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        dev.device_id,
                        dev.public_bytes(),
                        enc_dk,
                        None,
                        now,
                        now,
                    ),
                )
        log.info("profile.saved", identity=identity.id, devices=len(identity.devices))

    def load_identity(self) -> Optional[Identity]:
        """Load and decrypt the stored identity. Returns None if absent."""
        row = self._db.execute(
            "SELECT * FROM identity_profile WHERE id = 1"
        ).fetchone()
        if row is None:
            return None

        priv = decrypt(
            self._key, row["enc_identity_key"], associated_data=_AAD_IDENTITY
        )
        ik = IdentityKeyPair.from_private_bytes(priv)

        # Sanity: public key must match
        if ik.public_bytes() != bytes(row["public_key"]):
            raise ValueError("identity public key mismatch after decrypt")

        devices: list[DeviceKeyPair] = []
        for drow in self._db.execute("SELECT * FROM devices WHERE revoked_at IS NULL"):
            dpriv = decrypt(
                self._key,
                drow["enc_private_key"],
                associated_data=_AAD_DEVICE + drow["device_id"].encode(),
            )
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PrivateKey,
            )
            from cryptography.hazmat.primitives import serialization

            priv_key = Ed25519PrivateKey.from_private_bytes(dpriv)
            pub_key = priv_key.public_key()
            devices.append(
                DeviceKeyPair(
                    private_key=priv_key,
                    public_key=pub_key,
                    device_id=drow["device_id"],
                )
            )

        identity = Identity(identity_key=ik, devices=devices)
        log.info("profile.loaded", identity=identity.id, devices=len(devices))
        return identity

    def load_recovery_seed(self) -> Optional[bytes]:
        row = self._db.execute(
            "SELECT enc_recovery_seed FROM identity_profile WHERE id = 1"
        ).fetchone()
        if row is None or row["enc_recovery_seed"] is None:
            return None
        return decrypt(
            self._key, row["enc_recovery_seed"], associated_data=_AAD_RECOVERY
        )


def derive_profile_key_from_passphrase(passphrase: str, salt: bytes) -> bytes:
    """
    Derive a 32-byte profile key from a user passphrase (PBKDF2-HMAC-SHA256).
    Salt must be stored alongside the database (not secret, but unique).
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=600_000,
    )
    return kdf.derive(passphrase.encode("utf-8"))
