"""Bech32 for NYX identities (BIP-0173). Pure Python."""
from __future__ import annotations
from typing import List, Optional, Tuple

CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
GENERATOR = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]

def _polymod(values: List[int]) -> int:
    chk = 1
    for v in values:
        b = chk >> 25
        chk = ((chk & 0x1FFFFFF) << 5) ^ v
        for i in range(5):
            if (b >> i) & 1:
                chk ^= GENERATOR[i]
    return chk

def _hrp_expand(hrp: str) -> List[int]:
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]

def _create_checksum(hrp: str, data: List[int]) -> List[int]:
    values = _hrp_expand(hrp) + data + [0, 0, 0, 0, 0, 0]
    polymod = _polymod(values) ^ 1
    return [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]

def _verify_checksum(hrp: str, data: List[int]) -> bool:
    return _polymod(_hrp_expand(hrp) + data) == 1

def _convertbits(data, from_bits: int, to_bits: int, pad: bool = True) -> Optional[List[int]]:
    acc = 0
    bits = 0
    ret: List[int] = []
    maxv = (1 << to_bits) - 1
    max_acc = (1 << (from_bits + to_bits - 1)) - 1
    for value in data:
        if value < 0 or (value >> from_bits):
            return None
        acc = ((acc << from_bits) | value) & max_acc
        bits += from_bits
        while bits >= to_bits:
            bits -= to_bits
            ret.append((acc >> bits) & maxv)
    if pad:
        if bits:
            ret.append((acc << (to_bits - bits)) & maxv)
    elif bits >= from_bits or ((acc << (to_bits - bits)) & maxv):
        return None
    return ret

def encode(hrp: str, witver: int, witprog: bytes) -> str:
    if not (0 <= witver <= 16):
        raise ValueError("invalid witness version")
    converted = _convertbits(witprog, 8, 5)
    if converted is None:
        raise ValueError("bit conversion failed")
    data = [witver] + converted
    combined = data + _create_checksum(hrp, data)
    return hrp + "1" + "".join(CHARSET[d] for d in combined)

def decode(addr: str) -> Tuple[str, int, bytes]:
    if any(ord(c) < 33 or ord(c) > 126 for c in addr):
        raise ValueError("invalid character in bech32 string")
    if addr.lower() != addr and addr.upper() != addr:
        raise ValueError("mixed case bech32 string")
    addr = addr.lower()
    pos = addr.rfind("1")
    if pos < 1 or pos + 7 > len(addr) or len(addr) > 90:
        raise ValueError("invalid bech32 length or separator")
    if not all(c in CHARSET for c in addr[pos + 1 :]):
        raise ValueError("invalid bech32 character")
    hrp = addr[:pos]
    data = [CHARSET.find(c) for c in addr[pos + 1 :]]
    if not _verify_checksum(hrp, data):
        raise ValueError("invalid bech32 checksum")
    decoded = _convertbits(data[1:-6], 5, 8, False)
    if decoded is None or len(decoded) < 2 or len(decoded) > 40:
        raise ValueError("invalid bech32 data length")
    return hrp, data[0], bytes(decoded)
