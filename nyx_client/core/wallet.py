"""
NYX token wallet — Bitcoin-inspired local custody model.

Concepts:
  - Address: deterministic receive address (nyxw1…)
  - Balance: micro-NYX (1 NYX = 1_000_000 micro)
  - Ledger: append-only txs
  - Export/Import encrypted keystore (.nks) to a folder
  - Fund/charge deposits
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from nyx_client.crypto.aead import encrypt as aead_encrypt, decrypt as aead_decrypt
from nyx_client.storage.db import Database

MICRO = 1_000_000
DEFAULT_GENESIS_MICRO = 100 * MICRO
KEYSTORE_VERSION = 1
_AAD = b"nyx-wallet-keystore-v1"


@dataclass(frozen=True)
class WalletTx:
    tx_id: str
    kind: str
    amount_micro: int
    counterparty: str
    memo: str
    created_at: int
    balance_after: int


@dataclass
class WalletInfo:
    owner_id: str
    address: str
    balance_micro: int
    balance_nyx: float
    tx_count: int


def nyx_to_micro(nyx: float) -> int:
    return int(round(float(nyx) * MICRO))


def micro_to_nyx(micro: int) -> float:
    return micro / MICRO


def derive_wallet_address(owner_id: str) -> str:
    h = hashlib.sha256(owner_id.encode("utf-8")).hexdigest()[:40]
    return "nyxw1" + h


def _kdf_passphrase(passphrase: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256", passphrase.encode("utf-8"), salt, iterations=200_000, dklen=32
    )


class Wallet:
    def __init__(self, db: Database, owner_id: str, utxo_ledger=None) -> None:
        self._db = db
        self._owner = owner_id
        self._address = derive_wallet_address(owner_id)
        self._utxo = utxo_ledger
        self._ensure()

    def attach_utxo(self, ledger) -> None:
        """Bind cryptographic UTXO ledger (source of truth for spendable balance)."""
        self._utxo = ledger

    @property
    def address(self) -> str:
        return self._address

    @property
    def owner_id(self) -> str:
        return self._owner

    def _ensure(self) -> None:
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS wallet_balance (
                owner_id TEXT PRIMARY KEY,
                balance_micro INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL
            )
            """
        )
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS wallet_ledger (
                tx_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                amount_micro INTEGER NOT NULL,
                counterparty TEXT NOT NULL DEFAULT '',
                memo TEXT NOT NULL DEFAULT '',
                balance_after INTEGER NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        self._db.commit()
        row = self._db.execute(
            "SELECT balance_micro FROM wallet_balance WHERE owner_id = ?",
            (self._owner,),
        ).fetchone()
        if row is None:
            now = int(time.time())
            self._db.execute(
                "INSERT INTO wallet_balance(owner_id, balance_micro, updated_at) VALUES (?, ?, ?)",
                (self._owner, DEFAULT_GENESIS_MICRO, now),
            )
            self._db.execute(
                """
                INSERT INTO wallet_ledger(
                    tx_id, owner_id, kind, amount_micro, counterparty, memo, balance_after, created_at
                ) VALUES (?, ?, 'credit', ?, '', 'genesis', ?, ?)
                """,
                (
                    f"tx_genesis_{self._owner[:16]}_{now}",
                    self._owner,
                    DEFAULT_GENESIS_MICRO,
                    DEFAULT_GENESIS_MICRO,
                    now,
                ),
            )
            self._db.commit()

    def info(self) -> WalletInfo:
        rows = self._db.execute(
            "SELECT COUNT(*) AS c FROM wallet_ledger WHERE owner_id = ?",
            (self._owner,),
        ).fetchone()
        return WalletInfo(
            owner_id=self._owner,
            address=self._address,
            balance_micro=self.balance_micro(),
            balance_nyx=self.balance_nyx(),
            tx_count=int(rows["c"]) if rows else 0,
        )

    def balance_micro(self) -> int:
        if self._utxo is not None:
            return int(self._utxo.balance_micro(self._owner))
        row = self._db.execute(
            "SELECT balance_micro FROM wallet_balance WHERE owner_id = ?",
            (self._owner,),
        ).fetchone()
        return int(row["balance_micro"]) if row else 0

    def balance_nyx(self) -> float:
        return self.balance_micro() / MICRO

    def format_balance(self) -> str:
        return f"{self.balance_nyx():.6f} NYX"

    def list_utxos(self) -> list:
        if self._utxo is None:
            return []
        return self._utxo.list_unspent(self._owner)

    def _set_balance(self, new_bal: int) -> None:
        self._db.execute(
            "UPDATE wallet_balance SET balance_micro = ?, updated_at = ? WHERE owner_id = ?",
            (new_bal, int(time.time()), self._owner),
        )

    def _tx(self, kind: str, amount: int, counterparty: str, memo: str, balance_after: int) -> WalletTx:
        now = int(time.time())
        tx_id = "tx_" + uuid.uuid4().hex[:20]
        self._db.execute(
            """
            INSERT INTO wallet_ledger(
                tx_id, owner_id, kind, amount_micro, counterparty, memo, balance_after, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (tx_id, self._owner, kind, amount, counterparty, memo[:200], balance_after, now),
        )
        self._db.commit()
        return WalletTx(tx_id, kind, amount, counterparty, memo[:200], now, balance_after)

    def credit(self, amount_micro: int, memo: str = "", counterparty: str = "") -> WalletTx:
        if amount_micro <= 0:
            raise ValueError("amount must be positive")
        bal = self.balance_micro() + amount_micro
        self._set_balance(bal)
        return self._tx("credit", amount_micro, counterparty, memo, bal)

    def debit(self, amount_micro: int, memo: str = "", counterparty: str = "", kind: str = "debit") -> WalletTx:
        if amount_micro <= 0:
            raise ValueError("amount must be positive")
        bal = self.balance_micro()
        if bal < amount_micro:
            raise ValueError("insufficient NYX balance")
        bal -= amount_micro
        self._set_balance(bal)
        return self._tx(kind, amount_micro, counterparty, memo, bal)

    def fund(self, amount_nyx: float, memo: str = "deposit") -> WalletTx:
        """Local accounting credit only when UTXO ledger is not attached.
        With UTXO attached, new coins must come from network mint/transfer."""
        if self._utxo is not None:
            raise ValueError(
                "UTXO mode: cannot mint locally; receive a signed transfer or relay mint"
            )
        return self.credit(nyx_to_micro(amount_nyx), memo=memo, counterparty="deposit")

    def history(self, limit: int = 30) -> List[WalletTx]:
        if self._utxo is not None:
            rows = self._db.execute(
                """
                SELECT txid, body_json, status, created_at FROM utxo_txs
                ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
            out: List[WalletTx] = []
            for r in rows:
                import json
                try:
                    body = json.loads(r["body_json"])
                except Exception:
                    body = {}
                outs = body.get("outputs") or []
                ins = body.get("inputs") or []
                amount = 0
                kind = body.get("type") or "transfer"
                if kind == "mint":
                    amount = int((outs[0] or {}).get("amount_micro") or 0) if outs else 0
                    kind = "credit"
                else:
                    # amount sent by us = sum inputs owned - change to us
                    for o in outs:
                        if o.get("owner_id") != self._owner:
                            amount += int(o.get("amount_micro") or 0)
                    if amount == 0 and outs:
                        amount = int(outs[0].get("amount_micro") or 0)
                    kind = "debit" if ins else "credit"
                out.append(
                    WalletTx(
                        r["txid"][:24],
                        kind,
                        amount,
                        "",
                        (body.get("memo") or "")[:80],
                        int(r["created_at"]),
                        self.balance_micro(),
                    )
                )
            return out
        rows = self._db.execute(
            """
            SELECT * FROM wallet_ledger WHERE owner_id = ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (self._owner, limit),
        ).fetchall()
        return [
            WalletTx(
                r["tx_id"], r["kind"], int(r["amount_micro"]), r["counterparty"] or "",
                r["memo"] or "", int(r["created_at"]), int(r["balance_after"]),
            )
            for r in rows
        ]


    def export_keystore(self, directory: Path, passphrase: str) -> Path:
        if not passphrase or len(passphrase) < 8:
            raise ValueError("passphrase must be at least 8 characters")
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        salt = secrets.token_bytes(16)
        key = _kdf_passphrase(passphrase, salt)
        payload = {
            "version": KEYSTORE_VERSION,
            "owner_id": self._owner,
            "address": self._address,
            "balance_micro": self.balance_micro(),
            "exported_at": int(time.time()),
            "ledger": [
                {
                    "tx_id": t.tx_id, "kind": t.kind, "amount_micro": t.amount_micro,
                    "counterparty": t.counterparty, "memo": t.memo,
                    "created_at": t.created_at, "balance_after": t.balance_after,
                }
                for t in self.history(500)
            ],
        }
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        sealed = aead_encrypt(key, raw, associated_data=_AAD)
        blob = {
            "format": "nyx-keystore",
            "version": KEYSTORE_VERSION,
            "salt": salt.hex(),
            "ciphertext": sealed.hex(),
            "address": self._address,
        }
        name = f"nyx-wallet-{self._address[5:13]}-{int(time.time())}.nks"
        path = directory / name
        path.write_text(json.dumps(blob, indent=2), encoding="utf-8")
        self._tx("export", 0, "", f"keystore:{path.name}", self.balance_micro())
        return path

    @staticmethod
    def load_keystore(path: Path, passphrase: str) -> dict:
        path = Path(path)
        blob = json.loads(path.read_text(encoding="utf-8"))
        if blob.get("format") != "nyx-keystore":
            raise ValueError("not a NYX keystore file")
        salt = bytes.fromhex(blob["salt"])
        key = _kdf_passphrase(passphrase, salt)
        sealed = bytes.fromhex(blob["ciphertext"])
        raw = aead_decrypt(key, sealed, associated_data=_AAD)
        return json.loads(raw.decode("utf-8"))

    def import_keystore(self, path: Path, passphrase: str, replace_ledger: bool = False) -> WalletInfo:
        data = self.load_keystore(path, passphrase)
        ks_bal = int(data.get("balance_micro") or 0)
        if replace_ledger and data.get("owner_id") == self._owner:
            self._db.execute("DELETE FROM wallet_ledger WHERE owner_id = ?", (self._owner,))
            self._set_balance(0)
            self._db.commit()
            for item in reversed(list(data.get("ledger") or [])):
                self._db.execute(
                    """
                    INSERT OR REPLACE INTO wallet_ledger(
                        tx_id, owner_id, kind, amount_micro, counterparty, memo, balance_after, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item["tx_id"], self._owner, item["kind"], int(item["amount_micro"]),
                        item.get("counterparty") or "", item.get("memo") or "",
                        int(item["balance_after"]), int(item["created_at"]),
                    ),
                )
            self._set_balance(ks_bal)
            self._db.commit()
            self._tx("import", 0, "", f"keystore:{Path(path).name}", self.balance_micro())
        else:
            cur = self.balance_micro()
            if ks_bal > cur:
                self.credit(ks_bal - cur, memo=f"import:{Path(path).name}", counterparty="keystore")
            else:
                self._tx("import", 0, "", f"keystore:{Path(path).name}", cur)
        return self.info()
