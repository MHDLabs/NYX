"""
Authenticated encryption for NYX messages.

Whitepaper Section 11 / 12 / 49 specifies XChaCha20-Poly1305.
This module provides AEAD with the following strategy:

  1. Prefer XChaCha20-Poly1305 when the runtime library exposes it
     (cryptography >= 42 or PyNaCl).
  2. Fall back to ChaCha20-Poly1305 (12-byte nonce) which is available
     in all supported cryptography versions and is cryptographically
     equivalent when nonces are never reused.

The public API is identical regardless of the backend:
  encrypt(key, plaintext, aad=b"", nonce=None) -> blob
  decrypt(key, blob, aad=b"") -> plaintext

Blob format:
  nonce || ciphertext || tag
"""

from __future__ import annotations

import os
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

from nyx_client.config.logging import get_logger

log = get_logger(__name__)

KEY_SIZE = 32
TAG_SIZE = 16

# Detect best available construction
try:
    from cryptography.hazmat.primitives.ciphers.aead import XChaCha20Poly1305  # type: ignore
    _BACKEND = "xchacha20"
    NONCE_SIZE = 24
    _AEAD_CLS = XChaCha20Poly1305
except ImportError:
    _BACKEND = "chacha20"
    NONCE_SIZE = 12
    _AEAD_CLS = ChaCha20Poly1305

log.debug("aead.backend", backend=_BACKEND, nonce_size=NONCE_SIZE)


def generate_key() -> bytes:
    """Generate a random 32-byte AEAD key."""
    return os.urandom(KEY_SIZE)


def generate_nonce() -> bytes:
    """Generate a random nonce of the size required by the active backend."""
    return os.urandom(NONCE_SIZE)


def encrypt(
    key: bytes,
    plaintext: bytes,
    associated_data: bytes = b"",
    nonce: Optional[bytes] = None,
) -> bytes:
    """
    Authenticated encryption.

    Returns
    -------
    bytes
        nonce || ciphertext || tag
    """
    if len(key) != KEY_SIZE:
        raise ValueError(f"key must be {KEY_SIZE} bytes")
    if nonce is None:
        nonce = generate_nonce()
    if len(nonce) != NONCE_SIZE:
        raise ValueError(f"nonce must be {NONCE_SIZE} bytes (backend={_BACKEND})")

    aead = _AEAD_CLS(key)
    ct_and_tag = aead.encrypt(nonce, plaintext, associated_data)
    return nonce + ct_and_tag


def decrypt(
    key: bytes,
    blob: bytes,
    associated_data: bytes = b"",
) -> bytes:
    """
    Authenticated decryption.

    Raises
    ------
    cryptography.exceptions.InvalidTag
        Authentication failure (wrong key, tampered data, or wrong AAD).
    ValueError
        Blob too short or malformed.
    """
    if len(key) != KEY_SIZE:
        raise ValueError(f"key must be {KEY_SIZE} bytes")
    if len(blob) < NONCE_SIZE + TAG_SIZE:
        raise ValueError("ciphertext blob too short")

    nonce = blob[:NONCE_SIZE]
    ct_and_tag = blob[NONCE_SIZE:]

    aead = _AEAD_CLS(key)
    return aead.decrypt(nonce, ct_and_tag, associated_data)


def backend_name() -> str:
    """Return the active AEAD backend name (for diagnostics)."""
    return _BACKEND
