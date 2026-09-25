"""
Cryptography layer.

Whitepaper responsibilities:
  - Ed25519 signatures (Identity + Device keys)
  - X25519 key agreement primitives
  - BIP39 recovery mnemonics
  - X3DH / Double Ratchet (future milestones)
  - No invented cryptography
"""

from nyx_client.crypto.keys import (
    IdentityKeyPair,
    DeviceKeyPair,
    X25519KeyPair,
    generate_random_bytes,
)
from nyx_client.crypto.identity import (
    Identity,
    RecoveryBundle,
    create_recoverable_identity,
)
from nyx_client.crypto.bech32 import encode as bech32_encode, decode as bech32_decode
from nyx_client.crypto.aead import encrypt as aead_encrypt, decrypt as aead_decrypt, generate_key as aead_generate_key
from nyx_client.crypto.ratchet import DoubleRatchetSession, RatchetHeader
from nyx_client.crypto.bip39 import (
    generate_mnemonic,
    mnemonic_to_seed,
    validate_mnemonic,
    mnemonic_from_string,
)

__all__ = [
    "IdentityKeyPair",
    "DeviceKeyPair",
    "X25519KeyPair",
    "Identity",
    "RecoveryBundle",
    "create_recoverable_identity",
    "generate_random_bytes",
    "bech32_encode",
    "bech32_decode",
    "generate_mnemonic",
    "mnemonic_to_seed",
    "validate_mnemonic",
    "mnemonic_from_string",
    "aead_encrypt",
    "aead_decrypt",
    "aead_generate_key",
    "DoubleRatchetSession",
    "RatchetHeader",
]
