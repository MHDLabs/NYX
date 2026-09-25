"""Unit tests for cryptography and identity layers."""

from __future__ import annotations

import pytest

from nyx_client.crypto import (
    Identity,
    IdentityKeyPair,
    DeviceKeyPair,
    X25519KeyPair,
    create_recoverable_identity,
    generate_mnemonic,
    mnemonic_to_seed,
    validate_mnemonic,
    bech32_encode,
    bech32_decode,
)
from nyx_client.crypto.bip39 import _entropy_to_indices, _indices_to_entropy
from nyx_client.crypto.keys import generate_random_bytes


# ---------------------------------------------------------------------------
# Bech32
# ---------------------------------------------------------------------------

def test_bech32_roundtrip() -> None:
    data = generate_random_bytes(32)
    addr = bech32_encode("nyx", 0, data)
    assert addr.startswith("nyx1")
    hrp, ver, prog = bech32_decode(addr)
    assert hrp == "nyx"
    assert ver == 0
    assert prog == data


def test_bech32_rejects_bad_checksum() -> None:
    data = generate_random_bytes(32)
    addr = bech32_encode("nyx", 0, data)
    # Flip one character
    bad = addr[:-1] + ("a" if addr[-1] != "a" else "b")
    with pytest.raises(ValueError):
        bech32_decode(bad)


# ---------------------------------------------------------------------------
# Ed25519 keys
# ---------------------------------------------------------------------------

def test_identity_key_sign_verify() -> None:
    kp = IdentityKeyPair.generate()
    msg = b"nyx protocol message"
    sig = kp.sign(msg)
    assert kp.verify(sig, msg)
    assert not kp.verify(sig, b"tampered")


def test_identity_string_roundtrip() -> None:
    kp = IdentityKeyPair.generate()
    ident = kp.identity_string()
    assert ident.startswith("nyx1")
    pub = IdentityKeyPair.public_key_from_identity(ident)
    # Verify a signature using only the identity string
    msg = b"verify via identity"
    sig = kp.sign(msg)
    assert IdentityKeyPair.verify_with_identity(ident, sig, msg)
    assert not IdentityKeyPair.verify_with_identity(ident, sig, b"x")


def test_device_key_has_id() -> None:
    dev = DeviceKeyPair.generate()
    assert dev.device_id.startswith("dev_")
    assert len(dev.device_id) > 8


def test_x25519_exchange() -> None:
    a = X25519KeyPair.generate()
    b = X25519KeyPair.generate()
    shared_a = a.exchange(b.public_bytes())
    shared_b = b.exchange(a.public_bytes())
    assert shared_a == shared_b
    assert len(shared_a) == 32


# ---------------------------------------------------------------------------
# Identity object
# ---------------------------------------------------------------------------

def test_identity_create() -> None:
    ident = Identity.create()
    assert ident.id.startswith("nyx1")
    assert len(ident.devices) == 1
    assert ident.primary_device() is not None


def test_identity_add_device() -> None:
    ident = Identity.create(with_device=False)
    assert len(ident.devices) == 0
    d1 = ident.add_device()
    d2 = ident.add_device()
    assert len(ident.devices) == 2
    assert d1.device_id != d2.device_id


def test_identity_export_public() -> None:
    ident = Identity.create()
    pub = ident.export_public()
    assert pub["identity"] == ident.id
    assert "public_key" in pub
    assert len(pub["devices"]) == 1
    # Must not contain private material
    assert "private" not in str(pub).lower()


# ---------------------------------------------------------------------------
# BIP39
# ---------------------------------------------------------------------------

def test_bip39_generate_24_words() -> None:
    words = generate_mnemonic(256)
    assert len(words) == 24
    assert validate_mnemonic(words)


def test_bip39_checksum_rejects_tampered() -> None:
    words = list(generate_mnemonic(256))
    # Swap two words
    words[0], words[1] = words[1], words[0]
    assert not validate_mnemonic(words)


def test_bip39_seed_deterministic() -> None:
    words = generate_mnemonic(256)
    seed1 = mnemonic_to_seed(words, passphrase="")
    seed2 = mnemonic_to_seed(words, passphrase="")
    assert seed1 == seed2
    assert len(seed1) == 64
    # Different passphrase -> different seed
    seed3 = mnemonic_to_seed(words, passphrase="nyx")
    assert seed3 != seed1


def test_bip39_entropy_roundtrip() -> None:
    entropy = generate_random_bytes(32)
    indices = _entropy_to_indices(entropy)
    assert len(indices) == 24
    recovered = _indices_to_entropy(indices)
    assert recovered == entropy


def test_create_recoverable_identity() -> None:
    bundle = create_recoverable_identity()
    assert bundle.identity.id.startswith("nyx1")
    assert len(bundle.mnemonic) == 24
    assert len(bundle.seed) == 64
    assert validate_mnemonic(bundle.mnemonic)
    phrase = bundle.mnemonic_phrase()
    assert len(phrase.split()) == 24


# ---------------------------------------------------------------------------
# AEAD
# ---------------------------------------------------------------------------

def test_aead_roundtrip() -> None:
    from nyx_client.crypto.aead import encrypt, decrypt, generate_key, backend_name
    key = generate_key()
    pt = b"secret nyx message"
    aad = b"conversation_id=conv_abc"
    blob = encrypt(key, pt, associated_data=aad)
    assert decrypt(key, blob, associated_data=aad) == pt
    print("backend:", backend_name())


def test_aead_tamper_detected() -> None:
    from nyx_client.crypto.aead import encrypt, decrypt, generate_key
    from cryptography.exceptions import InvalidTag
    key = generate_key()
    blob = bytearray(encrypt(key, b"hello", b"aad"))
    blob[-1] ^= 0x01  # flip last bit of tag
    with pytest.raises(InvalidTag):
        decrypt(key, bytes(blob), b"aad")


def test_aead_wrong_aad_fails() -> None:
    from nyx_client.crypto.aead import encrypt, decrypt, generate_key
    from cryptography.exceptions import InvalidTag
    key = generate_key()
    blob = encrypt(key, b"hello", b"aad-correct")
    with pytest.raises(InvalidTag):
        decrypt(key, blob, b"aad-wrong")


def test_aead_wrong_key_fails() -> None:
    from nyx_client.crypto.aead import encrypt, decrypt, generate_key
    from cryptography.exceptions import InvalidTag
    blob = encrypt(generate_key(), b"hello")
    with pytest.raises(InvalidTag):
        decrypt(generate_key(), blob)
