"""
NYX Cryptographic Identity using X25519/Ed25519/ChaCha20-Poly1305 via PyNaCl.

This replaces the old RSA-based implementation with modern, efficient
elliptic-curve cryptography suitable for end-to-end encryption.
"""

from __future__ import annotations

import hashlib
import base64
from typing import Optional
from nacl.public import PrivateKey, PublicKey, Box
from nacl.signing import SigningKey, VerifyKey
from nacl.utils import random
from nacl.exceptions import CryptoError


class NYXIdentity:
    """
    NYX cryptographic identity.
    
    Uses:
    - Ed25519 for signing/verification (identity key)
    - X25519 for key exchange (encryption key)
    - ChaCha20-Poly1305 for authenticated encryption (via Box)
    """
    
    def __init__(self):
        # Ed25519 signing key pair (for identity/signatures)
        self.signing_key = SigningKey.generate()
        self.verify_key = self.signing_key.verify_key
        
        # X25519 key pair (for encryption/key exchange)
        self.private_key = PrivateKey.generate()
        self.public_key = self.private_key.public_key
        
        self._id: Optional[str] = None
    
    @classmethod
    def generate(cls) -> 'NYXIdentity':
        """Create a new random identity."""
        return cls()
    
    @classmethod
    def from_seed(cls, seed: bytes) -> 'NYXIdentity':
        """Create identity from a 32-byte seed (for deterministic generation)."""
        identity = cls.__new__(cls)
        identity.signing_key = SigningKey(seed[:32])
        identity.verify_key = identity.signing_key.verify_key
        identity.private_key = PrivateKey(seed[32:64])
        identity.public_key = identity.private_key.public_key
        identity._id = None
        return identity
    
    @classmethod
    def load(cls, signing_key_bytes: bytes, private_key_bytes: bytes) -> 'NYXIdentity':
        """Load identity from raw key bytes."""
        identity = cls.__new__(cls)
        identity.signing_key = SigningKey(signing_key_bytes)
        identity.verify_key = identity.signing_key.verify_key
        identity.private_key = PrivateKey(private_key_bytes)
        identity.public_key = identity.private_key.public_key
        identity._id = None
        return identity
    
    @property
    def id(self) -> str:
        """Get the NYX identity ID (nyx1 + 32 hex chars from Ed25519 verify key)."""
        if self._id is None:
            # Identity is nyx1 + first 32 hex chars of SHA256(verify_key)
            digest = hashlib.sha256(self.verify_key.encode()).hexdigest()[:32]
            self._id = f"nyx1{digest}"
        return self._id
    
    @property
    def signing_key_bytes(self) -> bytes:
        """Get raw Ed25519 signing key bytes (32 bytes)."""
        return self.signing_key.encode()
    
    @property
    def verify_key_bytes(self) -> bytes:
        """Get raw Ed25519 verify key bytes (32 bytes)."""
        return self.verify_key.encode()
    
    @property
    def private_key_bytes(self) -> bytes:
        """Get raw X25519 private key bytes (32 bytes)."""
        return self.private_key.encode()
    
    @property
    def public_key_bytes(self) -> bytes:
        """Get raw X25519 public key bytes (32 bytes)."""
        return self.public_key.encode()
    
    @property
    def verify_key_hex(self) -> str:
        """Get Ed25519 verify key as hex string."""
        return self.verify_key_bytes.hex()
    
    @property
    def public_key_hex(self) -> str:
        """Get X25519 public key as hex string."""
        return self.public_key_bytes.hex()
    
    def sign(self, message: bytes) -> bytes:
        """Sign a message with Ed25519. Returns 64-byte signature."""
        return self.signing_key.sign(message).signature
    
    def verify(self, message: bytes, signature: bytes, verify_key_bytes: bytes) -> bool:
        """Verify an Ed25519 signature."""
        try:
            vk = VerifyKey(verify_key_bytes)
            vk.verify(message, signature)
            return True
        except CryptoError:
            return False
    
    def encrypt_for(self, message: bytes, recipient_public_key: bytes) -> bytes:
        """
        Encrypt a message for a recipient using X25519 + ChaCha20-Poly1305.
        
        Args:
            message: Plaintext message to encrypt
            recipient_public_key: 32-byte X25519 public key of recipient
            
        Returns:
            Ciphertext with nonce prepended (24-byte nonce + ciphertext + auth tag)
        """
        box = Box(self.private_key, PublicKey(recipient_public_key))
        # Box.encrypt automatically generates a random nonce and returns nonce+ciphertext
        return box.encrypt(message)
    
    def decrypt_from(self, ciphertext: bytes, sender_public_key: bytes) -> bytes:
        """
        Decrypt a message from a sender using X25519 + ChaCha20-Poly1305.
        
        Args:
            ciphertext: Encrypted message (with nonce prepended)
            sender_public_key: 32-byte X25519 public key of sender
            
        Returns:
            Decrypted plaintext
            
        Raises:
            CryptoError: If decryption fails (wrong key, tampered message, etc.)
        """
        box = Box(self.private_key, PublicKey(sender_public_key))
        return box.decrypt(ciphertext)
    
    def to_dict(self) -> dict:
        """Serialize identity to a dictionary for storage."""
        return {
            'id': self.id,
            'signing_key': base64.b64encode(self.signing_key_bytes).decode(),
            'verify_key': base64.b64encode(self.verify_key_bytes).decode(),
            'private_key': base64.b64encode(self.private_key_bytes).decode(),
            'public_key': base64.b64encode(self.public_key_bytes).decode(),
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'NYXIdentity':
        """Deserialize identity from a dictionary."""
        identity = cls.__new__(cls)
        identity.signing_key = SigningKey(base64.b64decode(data['signing_key']))
        identity.verify_key = VerifyKey(base64.b64decode(data['verify_key']))
        identity.private_key = PrivateKey(base64.b64decode(data['private_key']))
        identity.public_key = PublicKey(base64.b64decode(data['public_key']))
        identity._id = data.get('id')
        return identity
    
    def __repr__(self) -> str:
        return f"NYXIdentity(id={self.id})"


# ── Backward Compatibility Wrapper ──────────────────────────────────────────

class Identity:
    """
    Backward-compatible wrapper around NYXIdentity.
    
    This maintains the old API while using the new cryptographic primitives.
    """
    
    def __init__(self, private_key=None):
        if private_key is None:
            # Old API expected RSA key - generate new NYXIdentity instead
            self._identity = NYXIdentity.generate()
        elif isinstance(private_key, NYXIdentity):
            self._identity = private_key
        else:
            # Try to load from old PEM format (not supported anymore)
            raise ValueError("Old RSA-based Identity is no longer supported. Use NYXIdentity directly.")
    
    @classmethod
    def create(cls) -> 'Identity':
        """Create a new identity (backward compatible)."""
        return cls(NYXIdentity.generate())
    
    @classmethod
    def load(cls, private_key_bytes: bytes) -> 'Identity':
        """Load identity from bytes (backward compatible - expects new format)."""
        import json
        try:
            data = json.loads(private_key_bytes.decode())
            return cls(NYXIdentity.from_dict(data))
        except (json.JSONDecodeError, KeyError):
            raise ValueError("Invalid identity format. Expected new NYXIdentity JSON format.")
    
    @property
    def id(self) -> str:
        return self._identity.id
    
    @property
    def public_key_bytes(self) -> bytes:
        """Return X25519 public key as bytes (for compatibility)."""
        return self._identity.public_key_bytes
    
    @property
    def private_key_bytes(self) -> bytes:
        """Return X25519 private key as bytes (for compatibility)."""
        return self._identity.private_key_bytes
    
    def sign(self, message: bytes) -> bytes:
        """Sign message with Ed25519."""
        return self._identity.sign(message)
    
    def verify(self, message: bytes, signature: bytes, public_key_bytes: bytes) -> bool:
        """Verify signature. Expects Ed25519 verify key bytes."""
        return self._identity.verify(message, signature, public_key_bytes)
    
    def encrypt(self, message: bytes, public_key_bytes: bytes) -> bytes:
        """Encrypt message for recipient (X25519 + ChaCha20-Poly1305)."""
        return self._identity.encrypt_for(message, public_key_bytes)
    
    def decrypt(self, ciphertext: bytes) -> bytes:
        """Decrypt message (requires sender's public key which we don't have here)."""
        # This method signature is incompatible with new API
        # The new API requires sender's public key
        raise NotImplementedError("Use NYXIdentity.decrypt_from(ciphertext, sender_public_key) instead")
    
    def __repr__(self) -> str:
        return f"Identity(id={self.id})"