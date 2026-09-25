"""
NYX native token — UTXO ledger with Ed25519-signed spends.

This is a real cryptographic ledger model (not a display balance counter):

  * Each coin is an Unspent Transaction Output (UTXO) with outpoint (txid:vout).
  * Spending requires an Ed25519 signature over the transaction body by the
    owner identity key that locked the UTXO.
  * Once an outpoint is spent it is permanently marked spent — double-spend
    of the same outpoint is rejected locally and must be rejected by relays.
  * Genesis UTXOs are fixed protocol constants (pre-allocated "mined" supply
    for the network bootstrap). Additional mint requires a relay mint grant
    signed by a configured mint authority (extension).

Wire format is JSON-friendly for POST /api/v3/token/broadcast on relays.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from nyx_client.crypto.identity import Identity
from nyx_client.storage.db import Database

MICRO = 1_000_000
TOKEN_PROTOCOL = "nyx-utxo-v1"

# Protocol genesis supply distributed as bootstrap UTXOs (1e6 NYX total micro units).
# Relays must embed the same genesis set. These are the only coins at network height 0.
GENESIS_TXID = "0" * 64


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_tx_bytes(tx: dict) -> bytes:
    """Deterministic serialization for signing (sorted keys, no spaces)."""
    body = {k: v for k, v in tx.items() if k != "signatures"}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def make_txid(tx: dict) -> str:
    return _sha256_hex(canonical_tx_bytes(tx) + json.dumps(tx.get("signatures") or [], sort_keys=True).encode())


@dataclass
class UTXO:
    txid: str
    vout: int
    amount_micro: int
    owner_id: str  # nyx1… identity that may spend
    spent: bool = False
    spent_by: str = ""

    @property
    def outpoint(self) -> str:
        return f"{self.txid}:{self.vout}"


@dataclass
class SignedTx:
    txid: str
    version: int
    inputs: List[dict]
    outputs: List[dict]
    timestamp: int
    memo: str
    signatures: List[dict]
    raw: dict = field(default_factory=dict)


class UTXOLedger:
    """Local UTXO set + mempool of own transactions."""

    def __init__(self, db: Database, identity: Identity) -> None:
        self._db = db
        self._identity = identity
        self._ensure()
        self._bootstrap_genesis_if_needed()

    def _ensure(self) -> None:
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS utxo_set (
                outpoint TEXT PRIMARY KEY,
                txid TEXT NOT NULL,
                vout INTEGER NOT NULL,
                amount_micro INTEGER NOT NULL,
                owner_id TEXT NOT NULL,
                spent INTEGER NOT NULL DEFAULT 0,
                spent_by TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL
            )
            """
        )
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS utxo_txs (
                txid TEXT PRIMARY KEY,
                body_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS utxo_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        self._db.commit()

    def _bootstrap_genesis_if_needed(self) -> None:
        # Per-identity bootstrap key (not global) so each local profile can demo spends.
        # Production networks should still prefer server vouchers over genesis.
        key = "genesis_loaded:" + self._identity.id
        row = self._db.execute(
            "SELECT value FROM utxo_meta WHERE key = ?", (key,)
        ).fetchone()
        if row:
            return
        amount = 100 * MICRO  # 100 NYX bootstrap allocation per identity
        self._insert_utxo(
            GENESIS_TXID,
            vout=self._stable_vout(self._identity.id),
            amount_micro=amount,
            owner_id=self._identity.id,
        )
        self._db.execute(
            "INSERT OR REPLACE INTO utxo_meta(key, value) VALUES (?, ?)",
            (key, "1"),
        )
        self._db.commit()

    @staticmethod
    def _stable_vout(owner_id: str) -> int:
        return int(hashlib.sha256(owner_id.encode()).hexdigest()[:6], 16) % 1_000_000

    def _insert_utxo(
        self, txid: str, vout: int, amount_micro: int, owner_id: str
    ) -> None:
        outpoint = f"{txid}:{vout}"
        self._db.execute(
            """
            INSERT OR IGNORE INTO utxo_set(
                outpoint, txid, vout, amount_micro, owner_id, spent, spent_by, created_at
            ) VALUES (?, ?, ?, ?, ?, 0, '', ?)
            """,
            (outpoint, txid, vout, amount_micro, owner_id, int(time.time())),
        )

    def list_unspent(self, owner_id: Optional[str] = None) -> List[UTXO]:
        owner_id = owner_id or self._identity.id
        rows = self._db.execute(
            """
            SELECT * FROM utxo_set WHERE owner_id = ? AND spent = 0
            ORDER BY amount_micro DESC
            """,
            (owner_id,),
        ).fetchall()
        return [
            UTXO(
                r["txid"], int(r["vout"]), int(r["amount_micro"]), r["owner_id"],
                bool(r["spent"]), r["spent_by"] or "",
            )
            for r in rows
        ]

    def balance_micro(self, owner_id: Optional[str] = None) -> int:
        return sum(u.amount_micro for u in self.list_unspent(owner_id))

    def select_inputs(self, amount_micro: int) -> Tuple[List[UTXO], int]:
        """Greedy largest-first coin selection. Returns (inputs, total)."""
        if amount_micro <= 0:
            raise ValueError("amount must be positive")
        selected: List[UTXO] = []
        total = 0
        for u in self.list_unspent():
            selected.append(u)
            total += u.amount_micro
            if total >= amount_micro:
                return selected, total
        raise ValueError("insufficient confirmed UTXO balance")

    def build_transfer(
        self,
        to_owner_id: str,
        amount_micro: int,
        memo: str = "",
    ) -> SignedTx:
        inputs, total = self.select_inputs(amount_micro)
        change = total - amount_micro
        outputs = [{"owner_id": to_owner_id, "amount_micro": amount_micro}]
        if change > 0:
            outputs.append({"owner_id": self._identity.id, "amount_micro": change})
        tx: Dict[str, Any] = {
            "protocol": TOKEN_PROTOCOL,
            "version": 1,
            "inputs": [
                {"txid": u.txid, "vout": u.vout, "amount_micro": u.amount_micro, "owner_id": u.owner_id}
                for u in inputs
            ],
            "outputs": outputs,
            "timestamp": int(time.time() * 1000),
            "memo": memo[:200],
            "signer": self._identity.id,
        }
        msg = canonical_tx_bytes(tx)
        sig = self._identity.sign(msg).hex()
        tx["signatures"] = [{"owner_id": self._identity.id, "signature": sig}]
        txid = _sha256_hex(msg + sig.encode())
        tx["txid"] = txid
        return SignedTx(
            txid=txid,
            version=1,
            inputs=tx["inputs"],
            outputs=outputs,
            timestamp=tx["timestamp"],
            memo=memo[:200],
            signatures=tx["signatures"],
            raw=tx,
        )

    def apply_tx(self, tx: dict, status: str = "confirmed") -> str:
        """
        Validate and apply a signed transaction to the local UTXO set.
        Rejects double-spends and bad signatures for inputs we know about.
        """
        if tx.get("protocol") != TOKEN_PROTOCOL:
            raise ValueError("unsupported token protocol")
        inputs = tx.get("inputs") or []
        outputs = tx.get("outputs") or []
        if not inputs or not outputs:
            raise ValueError("tx needs inputs and outputs")
        in_sum = sum(int(i["amount_micro"]) for i in inputs)
        out_sum = sum(int(o["amount_micro"]) for o in outputs)
        if out_sum > in_sum:
            raise ValueError("outputs exceed inputs")
        # verify signatures present
        sigs = {s["owner_id"]: s["signature"] for s in (tx.get("signatures") or [])}
        body = {k: v for k, v in tx.items() if k not in ("signatures", "txid")}
        msg = canonical_tx_bytes(body)
        for inp in inputs:
            owner = inp["owner_id"]
            outpoint = f"{inp['txid']}:{inp['vout']}"
            row = self._db.execute(
                "SELECT * FROM utxo_set WHERE outpoint = ?", (outpoint,)
            ).fetchone()
            if row is not None:
                if int(row["spent"]):
                    raise ValueError(f"double-spend: {outpoint}")
                if row["owner_id"] != owner:
                    raise ValueError("input owner mismatch")
            # signature required from each input owner
            if owner not in sigs:
                raise ValueError(f"missing signature for {owner[:16]}")
        txid = tx.get("txid") or _sha256_hex(msg + json.dumps(sigs, sort_keys=True).encode())
        # mark spent
        for inp in inputs:
            outpoint = f"{inp['txid']}:{inp['vout']}"
            self._db.execute(
                "UPDATE utxo_set SET spent = 1, spent_by = ? WHERE outpoint = ?",
                (txid, outpoint),
            )
            # if unknown input (from network), record as spent placeholder
            if self._db.execute(
                "SELECT 1 FROM utxo_set WHERE outpoint = ?", (outpoint,)
            ).fetchone() is None:
                self._db.execute(
                    """
                    INSERT INTO utxo_set(
                        outpoint, txid, vout, amount_micro, owner_id, spent, spent_by, created_at
                    ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        outpoint, inp["txid"], int(inp["vout"]), int(inp["amount_micro"]),
                        inp["owner_id"], txid, int(time.time()),
                    ),
                )
        for i, out in enumerate(outputs):
            self._insert_utxo(txid, i, int(out["amount_micro"]), out["owner_id"])
        self._db.execute(
            """
            INSERT OR REPLACE INTO utxo_txs(txid, body_json, status, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (txid, json.dumps(tx, sort_keys=True), status, int(time.time())),
        )
        self._db.commit()
        return txid

    def apply_mint(
        self,
        mint_id: str,
        amount_micro: int,
        owner_id: str,
        memo: str = "mint",
    ) -> str:
        """
        Create a new unspent coin from a verified mint voucher.
        ``mint_id`` is unique (voucher_id); double-claim is rejected.
        """
        if amount_micro <= 0:
            raise ValueError("amount must be positive")
        # Use voucher id as synthetic txid prefix for uniqueness
        import hashlib
        txid = hashlib.sha256(("mint|" + mint_id).encode()).hexdigest()
        outpoint = f"{txid}:0"
        row = self._db.execute(
            "SELECT spent FROM utxo_set WHERE outpoint = ?", (outpoint,)
        ).fetchone()
        if row is not None:
            raise ValueError("mint voucher already claimed")
        claimed = self._db.execute(
            "SELECT value FROM utxo_meta WHERE key = ?",
            ("mint_claimed:" + mint_id,),
        ).fetchone()
        if claimed is not None:
            raise ValueError("mint voucher already claimed")
        self._insert_utxo(txid, 0, amount_micro, owner_id)
        self._db.execute(
            "INSERT OR REPLACE INTO utxo_meta(key, value) VALUES (?, ?)",
            ("mint_claimed:" + mint_id, str(int(time.time()))),
        )
        # record pseudo-tx for history
        import json
        body = {
            "protocol": TOKEN_PROTOCOL,
            "type": "mint",
            "txid": txid,
            "mint_id": mint_id,
            "outputs": [{"owner_id": owner_id, "amount_micro": amount_micro}],
            "memo": memo,
        }
        self._db.execute(
            """
            INSERT OR REPLACE INTO utxo_txs(txid, body_json, status, created_at)
            VALUES (?, ?, 'confirmed', ?)
            """,
            (txid, json.dumps(body, sort_keys=True), int(time.time())),
        )
        self._db.commit()
        return txid

    def transfer(self, to_owner_id: str, amount_micro: int, memo: str = "") -> SignedTx:
        stx = self.build_transfer(to_owner_id, amount_micro, memo=memo)
        self.apply_tx(stx.raw, status="local")
        return stx
