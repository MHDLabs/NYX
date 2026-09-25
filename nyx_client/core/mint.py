"""
NYX mint / mined-coin claim.

Real coins enter the UTXO set only via:
  1) Protocol genesis allocation (network bootstrap)
  2) A **mint voucher** signed by the configured mint authority
     (what a miner/relay issues after proof-of-work or allocation)

A voucher is NOT free credit. Without a valid Ed25519 signature over the
canonical voucher body, claim is rejected. Spent outpoints cannot be reused.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from nyx_client.crypto.keys import IdentityKeyPair
from nyx_client.crypto.identity import Identity
from nyx_client.core.utxo_ledger import UTXOLedger, MICRO, TOKEN_PROTOCOL

# Development/bootstrap mint authority seed material is deterministic so relays
# and clients share the same verify key when using the default network.
# Production deployments MUST replace via config `token.mint_pubkey_hex`.
_DEFAULT_MINT_SEED = hashlib.sha256(b"nyx-mint-authority-v1-bootstrap").digest()


def default_mint_keypair() -> IdentityKeyPair:
    return IdentityKeyPair.from_private_bytes(_DEFAULT_MINT_SEED)


def default_mint_pubkey_hex() -> str:
    return default_mint_keypair().public_bytes().hex()


@dataclass(frozen=True)
class MintVoucher:
    voucher_id: str
    amount_micro: int
    recipient_id: str
    issued_at: int
    expiry: int
    memo: str
    mint_pubkey: str
    signature: str

    def body_for_sign(self) -> bytes:
        body = {
            "voucher_id": self.voucher_id,
            "amount_micro": self.amount_micro,
            "recipient_id": self.recipient_id,
            "issued_at": self.issued_at,
            "expiry": self.expiry,
            "memo": self.memo,
            "mint_pubkey": self.mint_pubkey,
            "protocol": TOKEN_PROTOCOL,
        }
        return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "voucher_id": self.voucher_id,
            "amount_micro": self.amount_micro,
            "recipient_id": self.recipient_id,
            "issued_at": self.issued_at,
            "expiry": self.expiry,
            "memo": self.memo,
            "mint_pubkey": self.mint_pubkey,
            "signature": self.signature,
            "protocol": TOKEN_PROTOCOL,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MintVoucher":
        return cls(
            voucher_id=str(data["voucher_id"]),
            amount_micro=int(data["amount_micro"]),
            recipient_id=str(data["recipient_id"]),
            issued_at=int(data["issued_at"]),
            expiry=int(data.get("expiry") or 0),
            memo=str(data.get("memo") or ""),
            mint_pubkey=str(data["mint_pubkey"]),
            signature=str(data["signature"]),
        )


def issue_mint_voucher(
    *,
    amount_nyx: float,
    recipient_id: str,
    memo: str = "mined",
    valid_hours: int = 24 * 365,
    mint_sk: Optional[IdentityKeyPair] = None,
) -> MintVoucher:
    """Miner / relay side: create a signed voucher (for tests and real mint nodes)."""
    sk = mint_sk or default_mint_keypair()
    now = int(time.time())
    amount_micro = int(round(float(amount_nyx) * MICRO))
    if amount_micro <= 0:
        raise ValueError("amount must be positive")
    voucher_id = "mnt_" + hashlib.sha256(
        f"{recipient_id}|{amount_micro}|{now}|{memo}".encode()
    ).hexdigest()[:24]
    unsigned = MintVoucher(
        voucher_id=voucher_id,
        amount_micro=amount_micro,
        recipient_id=recipient_id,
        issued_at=now,
        expiry=now + int(valid_hours * 3600),
        memo=memo[:120],
        mint_pubkey=sk.public_bytes().hex(),
        signature="",
    )
    sig = sk.sign(unsigned.body_for_sign()).hex()
    return MintVoucher(
        voucher_id=unsigned.voucher_id,
        amount_micro=unsigned.amount_micro,
        recipient_id=unsigned.recipient_id,
        issued_at=unsigned.issued_at,
        expiry=unsigned.expiry,
        memo=unsigned.memo,
        mint_pubkey=unsigned.mint_pubkey,
        signature=sig,
    )


def verify_voucher(voucher: MintVoucher, trusted_mint_pubkeys: set[str]) -> None:
    if voucher.mint_pubkey not in trusted_mint_pubkeys:
        raise ValueError("untrusted mint authority")
    if voucher.expiry and int(time.time()) > int(voucher.expiry):
        raise ValueError("voucher expired")
    if voucher.amount_micro <= 0:
        raise ValueError("invalid amount")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.exceptions import InvalidSignature
    pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(voucher.mint_pubkey))
    try:
        pub.verify(bytes.fromhex(voucher.signature), voucher.body_for_sign())
    except (InvalidSignature, ValueError) as exc:
        raise ValueError("invalid mint signature") from exc


def claim_voucher_to_ledger(
    ledger: UTXOLedger,
    identity: Identity,
    voucher: MintVoucher,
    trusted_mint_pubkeys: Optional[set[str]] = None,
) -> str:
    """
    Verify voucher and create a real unspent UTXO for the recipient.
    Returns the mint txid (outpoint base).
    """
    trusted = trusted_mint_pubkeys or {default_mint_pubkey_hex()}
    verify_voucher(voucher, trusted)
    if voucher.recipient_id != identity.id:
        raise ValueError("voucher recipient does not match this identity")
    # prevent double-claim of same voucher_id
    return ledger.apply_mint(
        mint_id=voucher.voucher_id,
        amount_micro=voucher.amount_micro,
        owner_id=identity.id,
        memo=voucher.memo or "mint",
    )


def load_voucher_file(path: Path) -> MintVoucher:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return MintVoucher.from_dict(data)


def save_voucher_file(voucher: MintVoucher, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(voucher.to_dict(), indent=2), encoding="utf-8")
    return path





def mine_voucher(
    amount_nyx: float,
    recipient_id: str,
    memo: str = "mined",
    difficulty_bits: int = 12,
    mint_sk: Optional[IdentityKeyPair] = None,
) -> MintVoucher:
    """
    CPU proof-of-work then sign a mint voucher with the network mint key.

    This is real computational work. The resulting voucher is a signed
    authorization to create a UTXO — claim it with claim_voucher_to_ledger.
    """
    import secrets
    amount_micro = int(round(float(amount_nyx) * MICRO))
    if amount_micro <= 0:
        raise ValueError("amount must be positive")
    if amount_micro > 1_000_000 * MICRO:
        raise ValueError("amount exceeds per-mine cap (1e6 NYX)")
    bits = int(difficulty_bits + min(8, max(0, amount_nyx) ** 0.5))
    voucher_core = secrets.token_hex(12)
    nonce = 0
    while True:
        work = f"{voucher_core}|{amount_micro}|{recipient_id}|{nonce}".encode()
        digest = hashlib.sha256(work).digest()
        val = int.from_bytes(digest, "big")
        lead = 256 - val.bit_length() if val else 256
        if lead >= bits:
            break
        nonce += 1
        if nonce > 8_000_000:
            raise RuntimeError("PoW search exceeded limit")
    return issue_mint_voucher(
        amount_nyx=amount_nyx,
        recipient_id=recipient_id,
        memo=f"{memo}|pow_nonce={nonce}|bits={bits}|core={voucher_core}",
        mint_sk=mint_sk,
    )
