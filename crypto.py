"""
crypto.py — Cryptographic primitives for Project NYX.

Provides:
  - X25519 keypair generation (for ECDH key exchange)
  - Ed25519 keypair generation (for identity / device ID derivation)
  - ChaCha20-Poly1305 AEAD encrypt / decrypt
  - Passphrase-based private key encryption (AES-256-GCM + PBKDF2)
  - Message-level sealed-box encryption with ephemeral X25519 keys

All encryption/decryption happens on the client. The relay server never
sees plaintext or private keys.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from dataclasses import dataclass
from typing import Optional, Tuple

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM, ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PBKDF2_ITERATIONS = 600_000  # OWASP recommendation for PBKDF2-SHA256
PBKDF2_SALT_SIZE = 16
AES_NONCE_SIZE = 12
CHACHA_NONCE_SIZE = 12
HKDF_INFO = b"nyx-message-v1"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class IdentityKeys:
    """Holds a complete NYX identity: Ed25519 identity + X25519 encryption keys."""

    device_id: str
    # Ed25519 (identity / signing)
    ed25519_private: bytes  # raw 32-byte seed
    ed25519_public: bytes   # raw 32-byte public key
    # X25519 (encryption / ECDH)
    x25519_private: bytes   # raw 32-byte private key
    x25519_public: bytes    # raw 32-byte public key


@dataclass
class EncryptedMessage:
    """Result of encrypting a plaintext message for a recipient."""

    ciphertext_b64: str   # base64(ephemeral_pub || ciphertext_with_tag)
    nonce_b64: str        # base64(nonce)
    message_id: str       # unique message identifier


# ---------------------------------------------------------------------------
# Key generation
# ---------------------------------------------------------------------------

def generate_identity() -> IdentityKeys:
    """
    Generate a fresh NYX identity.

    Creates an Ed25519 keypair (for identity) and an X25519 keypair
    (for encryption). The device_id is derived from the Ed25519 public key
    as a truncated SHA-256 hex digest.
    """
    # Ed25519 identity key
    ed_priv = Ed25519PrivateKey.generate()
    ed_pub = ed_priv.public_key()
    ed_priv_bytes = ed_priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    ed_pub_bytes = ed_pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    # X25519 encryption key
    x_priv = X25519PrivateKey.generate()
    x_pub = x_priv.public_key()
    x_priv_bytes = x_priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    x_pub_bytes = x_pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    # Device ID: first 16 hex chars of SHA-256(ed25519_public)
    device_id = hashlib.sha256(ed_pub_bytes).hexdigest()[:16]

    return IdentityKeys(
        device_id=device_id,
        ed25519_private=ed_priv_bytes,
        ed25519_public=ed_pub_bytes,
        x25519_private=x_priv_bytes,
        x25519_public=x_pub_bytes,
    )


def public_key_bundle_b64(identity: IdentityKeys) -> str:
    """
    Encode the public key bundle as a base64 string for registration.

    Format: base64( ed25519_public (32) || x25519_public (32) )
    Total: 64 raw bytes → 88 base64 characters.
    """
    bundle = identity.ed25519_public + identity.x25519_public
    return base64.b64encode(bundle).decode("ascii")


def parse_public_key_bundle(bundle_b64: str) -> Tuple[bytes, bytes]:
    """
    Decode a public key bundle.

    Returns (ed25519_public, x25519_public) as raw bytes.
    """
    raw = base64.b64decode(bundle_b64)
    if len(raw) != 64:
        raise ValueError(f"Invalid public key bundle length: expected 64 bytes, got {len(raw)}")
    return raw[:32], raw[32:]


# ---------------------------------------------------------------------------
# Passphrase-based private key encryption (for local storage)
# ---------------------------------------------------------------------------

def encrypt_private_keys(
    identity: IdentityKeys,
    passphrase: str,
) -> Tuple[str, str, str]:
    """
    Encrypt the private keys with a user passphrase for local storage.

    Uses PBKDF2-HMAC-SHA256 to derive an AES-256 key, then AES-256-GCM
    to encrypt the concatenated private keys.

    Returns (encrypted_blob_b64, salt_b64, nonce_b64).
    """
    salt = os.urandom(PBKDF2_SALT_SIZE)
    nonce = os.urandom(AES_NONCE_SIZE)

    # Derive AES-256 key from passphrase
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    aes_key = kdf.derive(passphrase.encode("utf-8"))

    # Concatenate private keys: ed25519_priv (32) || x25519_priv (32)
    plaintext = identity.ed25519_private + identity.x25519_private

    aesgcm = AESGCM(aes_key)
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)

    return (
        base64.b64encode(ciphertext).decode("ascii"),
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(nonce).decode("ascii"),
    )


def decrypt_private_keys(
    encrypted_blob_b64: str,
    salt_b64: str,
    nonce_b64: str,
    passphrase: str,
    device_id: str,
    ed25519_public: bytes,
    x25519_public: bytes,
) -> IdentityKeys:
    """
    Decrypt private keys from local storage using the user passphrase.

    Returns a fully reconstructed IdentityKeys object.
    Raises cryptography.exceptions.InvalidTag on wrong passphrase.
    """
    salt = base64.b64decode(salt_b64)
    nonce = base64.b64decode(nonce_b64)
    ciphertext = base64.b64decode(encrypted_blob_b64)

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    aes_key = kdf.derive(passphrase.encode("utf-8"))

    aesgcm = AESGCM(aes_key)
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)

    if len(plaintext) != 64:
        raise ValueError("Decrypted private key blob has unexpected length")

    return IdentityKeys(
        device_id=device_id,
        ed25519_private=plaintext[:32],
        ed25519_public=ed25519_public,
        x25519_private=plaintext[32:],
        x25519_public=x25519_public,
    )


# ---------------------------------------------------------------------------
# Message encryption / decryption (sealed box with ephemeral X25519)
# ---------------------------------------------------------------------------

def encrypt_message(
    plaintext: str,
    recipient_x25519_public: bytes,
    sender_device_id: str,
) -> EncryptedMessage:
    """
    Encrypt a plaintext message for a recipient using sealed-box style encryption.

    Protocol:
      1. Generate an ephemeral X25519 keypair.
      2. Perform ECDH: shared_secret = ECDH(ephemeral_priv, recipient_pub).
      3. Derive a ChaCha20-Poly1305 key via HKDF-SHA256.
      4. Encrypt plaintext with a random nonce.
      5. Ciphertext blob = ephemeral_pub (32) || chacha_ciphertext_with_tag.

    The recipient uses their X25519 private key + the embedded ephemeral
    public key to recover the shared secret and decrypt.
    """
    # Generate ephemeral X25519 keypair
    eph_priv = X25519PrivateKey.generate()
    eph_pub = eph_priv.public_key()
    eph_pub_bytes = eph_pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    # ECDH with recipient's static public key
    recipient_pub = X25519PublicKey.from_public_bytes(recipient_x25519_public)
    shared_secret = eph_priv.exchange(recipient_pub)

    # Derive AEAD key via HKDF
    aead_key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=HKDF_INFO,
    ).derive(shared_secret)

    # Encrypt with ChaCha20-Poly1305
    nonce = os.urandom(CHACHA_NONCE_SIZE)
    chacha = ChaCha20Poly1305(aead_key)
    # Associated data binds the sender identity to the ciphertext
    aad = sender_device_id.encode("utf-8")
    ct = chacha.encrypt(nonce, plaintext.encode("utf-8"), aad)

    # Final ciphertext: ephemeral_pub || encrypted_payload
    full_ciphertext = eph_pub_bytes + ct

    # Generate a unique message ID
    message_id = secrets.token_hex(16)

    return EncryptedMessage(
        ciphertext_b64=base64.b64encode(full_ciphertext).decode("ascii"),
        nonce_b64=base64.b64encode(nonce).decode("ascii"),
        message_id=message_id,
    )


def decrypt_message(
    ciphertext_b64: str,
    nonce_b64: str,
    recipient_x25519_private: bytes,
    sender_device_id: str,
) -> str:
    """
    Decrypt a sealed-box message.

    Protocol (inverse of encrypt_message):
      1. Split ciphertext into ephemeral_pub (32) || chacha_ct.
      2. ECDH(recipient_priv, ephemeral_pub) → shared_secret.
      3. HKDF → AEAD key.
      4. ChaCha20-Poly1305 decrypt with AAD = sender_device_id.

    Returns the plaintext string.
    Raises cryptography.exceptions.InvalidTag on tampering / wrong key.
    """
    full_ct = base64.b64decode(ciphertext_b64)
    nonce = base64.b64decode(nonce_b64)

    if len(full_ct) < 33:
        raise ValueError("Ciphertext too short to contain ephemeral key + payload")

    eph_pub_bytes = full_ct[:32]
    chacha_ct = full_ct[32:]

    # Reconstruct keys
    eph_pub = X25519PublicKey.from_public_bytes(eph_pub_bytes)
    recip_priv = X25519PrivateKey.from_private_bytes(recipient_x25519_private)

    # ECDH
    shared_secret = recip_priv.exchange(eph_pub)

    # Derive AEAD key
    aead_key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=HKDF_INFO,
    ).derive(shared_secret)

    # Decrypt
    chacha = ChaCha20Poly1305(aead_key)
    aad = sender_device_id.encode("utf-8")
    plaintext_bytes = chacha.decrypt(nonce, chacha_ct, aad)

    return plaintext_bytes.decode("utf-8")


# ---------------------------------------------------------------------------
# NYXCrypto — high-level wrapper for REPL / commands
# ---------------------------------------------------------------------------

class NYXCrypto:
    """
    High-level cryptographic interface for the NYX client.

    Manages identity generation, persistence (to files), and
    encrypt/decrypt operations for the REPL layer.
    """

    def __init__(self, device_id_path: str, keys_path: str):
        self._device_id_path = device_id_path
        self._keys_path = keys_path
        self._identity: Optional[IdentityKeys] = None
        self._load()

    # -- persistence ----------------------------------------------------------

    def _load(self) -> None:
        """Try to load an existing identity from disk."""
        try:
            with open(self._device_id_path, "r") as f:
                device_id = f.read().strip()
        except FileNotFoundError:
            return

        try:
            import json
            with open(self._keys_path, "r") as f:
                keys = json.load(f)
        except (FileNotFoundError, Exception):
            return

        try:
            self._identity = IdentityKeys(
                device_id=device_id,
                ed25519_private=base64.b64decode(keys["ed25519_private"]),
                ed25519_public=base64.b64decode(keys["ed25519_public"]),
                x25519_private=base64.b64decode(keys["x25519_private"]),
                x25519_public=base64.b64decode(keys["x25519_public"]),
            )
        except Exception:
            pass

    def _save(self) -> None:
        """Persist the identity to disk."""
        if self._identity is None:
            return

        import json
        from pathlib import Path

        Path(self._device_id_path).parent.mkdir(parents=True, exist_ok=True)

        with open(self._device_id_path, "w") as f:
            f.write(self._identity.device_id)

        with open(self._keys_path, "w") as f:
            json.dump({
                "ed25519_private": base64.b64encode(self._identity.ed25519_private).decode(),
                "ed25519_public":  base64.b64encode(self._identity.ed25519_public).decode(),
                "x25519_private":  base64.b64encode(self._identity.x25519_private).decode(),
                "x25519_public":   base64.b64encode(self._identity.x25519_public).decode(),
            }, f, indent=2)

    # -- public API -----------------------------------------------------------

    def has_identity(self) -> bool:
        """Return True if an identity is loaded."""
        return self._identity is not None

    @property
    def device_id(self) -> str:
        """Return the device_id, or empty string if no identity."""
        return self._identity.device_id if self._identity else ""

    def generate_identity(self) -> IdentityKeys:
        """Generate a new identity and save it to disk."""
        self._identity = generate_identity()
        self._save()
        return self._identity

    def get_public_key_b64(self) -> str:
        """Return the base64 public key bundle."""
        if self._identity is None:
            return ""
        return public_key_bundle_b64(self._identity)

    def encrypt(self, plaintext: str, recipient_x25519_public: bytes) -> Tuple[str, str]:
        """
        Encrypt a message for a recipient.

        MUST accept recipient's X25519 public key (raw 32 bytes) as parameter.
        MUST call encrypt_message(plaintext, recipient_x25519_public, self._identity.device_id).

        Returns (ciphertext_b64, nonce_b64).
        """
        if self._identity is None:
            raise RuntimeError("No identity loaded")

        enc = encrypt_message(plaintext, recipient_x25519_public, self._identity.device_id)
        return enc.ciphertext_b64, enc.nonce_b64

    def decrypt(self, ciphertext_b64: str, nonce_b64: str, sender_device_id: str) -> Optional[str]:
        """
        Decrypt a message.

        MUST accept sender_device_id as parameter for AAD verification.
        MUST call decrypt_message(ciphertext_b64, nonce_b64, self._identity.x25519_private, sender_device_id).

        Returns the plaintext string, or None on failure.
        """
        if self._identity is None:
            return None

        try:
            plaintext = decrypt_message(
                ciphertext_b64,
                nonce_b64,
                self._identity.x25519_private,
                sender_device_id,
            )
            return plaintext
        except Exception:
            return None
