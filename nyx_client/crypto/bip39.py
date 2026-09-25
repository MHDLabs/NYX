"""
BIP39 mnemonic generation and seed derivation for NYX recovery keys.

Whitepaper Section 05:
  Recovery: 24-word BIP39 mnemonic -> derives recovery key
  All devices lost: Recovery key required; without it, identity is unrecoverable

Algorithm follows BIP-0039 exactly:
  - 256-bit entropy -> 24 words (with SHA-256 checksum)
  - PBKDF2-HMAC-SHA512 (2048 iterations) -> 64-byte seed

The wordlist file (bip39_wordlist.txt) must contain exactly 2048 words.
In production this is the official English BIP39 list; the test suite
ships a deterministic placeholder list so the algorithm can be verified
without external packages.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import List, Optional, Sequence

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from nyx_client.crypto.keys import generate_random_bytes
from nyx_client.config.logging import get_logger

log = get_logger(__name__)

_WORDLIST_PATH = Path(__file__).with_name("bip39_wordlist.txt")
_WORDLIST: Optional[List[str]] = None

PBKDF2_ITERATIONS = 2048
ENTROPY_BITS_24 = 256
MNEMONIC_WORDS_24 = 24


def _load_wordlist() -> List[str]:
    global _WORDLIST
    if _WORDLIST is not None:
        return _WORDLIST
    if not _WORDLIST_PATH.is_file():
        raise FileNotFoundError(
            f"BIP39 wordlist not found at {_WORDLIST_PATH}. "
            "Place the official English BIP39 list (2048 words) there."
        )
    words = [
        line.strip()
        for line in _WORDLIST_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(words) != 2048:
        raise ValueError(f"wordlist must contain 2048 words, got {len(words)}")
    _WORDLIST = words
    return words


def _entropy_to_indices(entropy: bytes) -> List[int]:
    """Convert entropy bytes to BIP39 word indices (with checksum)."""
    if len(entropy) not in (16, 20, 24, 28, 32):
        raise ValueError("entropy must be 128-256 bits (16-32 bytes)")
    ent_bits = len(entropy) * 8
    cs_bits = ent_bits // 32
    h = hashlib.sha256(entropy).digest()
    # Build bit string: entropy bits + checksum bits
    ent_int = int.from_bytes(entropy, "big")
    cs_int = h[0] >> (8 - cs_bits)
    total = (ent_int << cs_bits) | cs_int
    total_bits = ent_bits + cs_bits
    n_words = total_bits // 11
    indices = []
    for i in range(n_words - 1, -1, -1):
        indices.append((total >> (i * 11)) & 0x7FF)
    return indices


def _indices_to_entropy(indices: Sequence[int]) -> bytes:
    """Reverse: indices -> entropy (validates checksum)."""
    n_words = len(indices)
    if n_words not in (12, 15, 18, 21, 24):
        raise ValueError("mnemonic must be 12/15/18/21/24 words")
    total_bits = n_words * 11
    cs_bits = total_bits // 33
    ent_bits = total_bits - cs_bits
    total = 0
    for idx in indices:
        if not (0 <= idx < 2048):
            raise ValueError(f"invalid word index: {idx}")
        total = (total << 11) | idx
    cs_int = total & ((1 << cs_bits) - 1)
    ent_int = total >> cs_bits
    entropy = ent_int.to_bytes(ent_bits // 8, "big")
    h = hashlib.sha256(entropy).digest()
    expected_cs = h[0] >> (8 - cs_bits)
    if cs_int != expected_cs:
        raise ValueError("invalid mnemonic checksum")
    return entropy


def generate_mnemonic(strength: int = 256) -> List[str]:
    """
    Generate a new BIP39 mnemonic.

    Parameters
    ----------
    strength:
        Entropy bits. 256 -> 24 words (whitepaper default).
    """
    if strength not in (128, 160, 192, 224, 256):
        raise ValueError("strength must be 128/160/192/224/256")
    entropy = generate_random_bytes(strength // 8)
    indices = _entropy_to_indices(entropy)
    wordlist = _load_wordlist()
    words = [wordlist[i] for i in indices]
    log.info("bip39.mnemonic_generated", words=len(words))
    return words


def mnemonic_to_seed(
    words: Sequence[str],
    passphrase: str = "",
) -> bytes:
    """
    Derive the 64-byte BIP39 seed from a mnemonic + optional passphrase.

    Uses PBKDF2-HMAC-SHA512 with 2048 iterations (BIP39 standard).
    """
    wordlist = _load_wordlist()
    word_to_idx = {w: i for i, w in enumerate(wordlist)}
    try:
        indices = [word_to_idx[w] for w in words]
    except KeyError as exc:
        raise ValueError(f"unknown word in mnemonic: {exc}") from exc
    # Validate checksum
    _indices_to_entropy(indices)

    mnemonic_str = " ".join(words)
    salt = ("mnemonic" + passphrase).encode("utf-8")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA512(),
        length=64,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    seed = kdf.derive(mnemonic_str.encode("utf-8"))
    return seed


def validate_mnemonic(words: Sequence[str]) -> bool:
    """Return True if the mnemonic has a valid checksum and known words."""
    try:
        wordlist = _load_wordlist()
        word_to_idx = {w: i for i, w in enumerate(wordlist)}
        indices = [word_to_idx[w] for w in words]
        _indices_to_entropy(indices)
        return True
    except (ValueError, KeyError):
        return False


def mnemonic_from_string(phrase: str) -> List[str]:
    """Split and normalize a mnemonic string into a word list."""
    words = phrase.strip().lower().split()
    if not words:
        raise ValueError("empty mnemonic")
    return words
