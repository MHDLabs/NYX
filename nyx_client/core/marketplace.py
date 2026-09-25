"""
NYX marketplace — listings paid only in verified NYX UTXO.

Digital delivery: seller attaches a local product file; after successful
payment the file is copied into the buyer purchase folder (and recorded).
Ratings: buyers can rate completed orders (1–5).
"""

from __future__ import annotations

import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from nyx_client.core.wallet import Wallet, MICRO, nyx_to_micro
from nyx_client.storage.db import Database


@dataclass
class Listing:
    listing_id: str
    seller_id: str
    title: str
    description: str
    category: str
    price_micro: int
    active: bool
    created_at: int
    updated_at: int
    product_path: str = ""
    avg_rating: float = 0.0
    rating_count: int = 0

    @property
    def price_nyx(self) -> float:
        return self.price_micro / MICRO


@dataclass
class Order:
    order_id: str
    listing_id: str
    buyer_id: str
    seller_id: str
    price_micro: int
    created_at: int
    delivery_path: str = ""
    title: str = ""


class Marketplace:
    CATEGORIES = ("source", "asset", "service", "digital", "other")

    def __init__(self, db: Database, wallet: Wallet, owner_id: str, media_root: Optional[Path] = None) -> None:
        self._db = db
        self._wallet = wallet
        self._owner = owner_id
        self._media = Path(media_root) if media_root else None
        self._ensure()

    def _ensure(self) -> None:
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS market_listings (
                listing_id TEXT PRIMARY KEY,
                seller_id TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT 'other',
                price_micro INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                product_path TEXT NOT NULL DEFAULT ''
            )
            """
        )
        # migrate product_path
        cols = set()
        for row in self._db.execute("PRAGMA table_info(market_listings)").fetchall():
            try:
                cols.add(row["name"])
            except Exception:
                cols.add(row[1])
        if "product_path" not in cols:
            try:
                self._db.execute(
                    "ALTER TABLE market_listings ADD COLUMN product_path TEXT NOT NULL DEFAULT ''"
                )
            except Exception:
                pass
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS market_orders (
                order_id TEXT PRIMARY KEY,
                listing_id TEXT NOT NULL,
                buyer_id TEXT NOT NULL,
                seller_id TEXT NOT NULL,
                price_micro INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                delivery_path TEXT NOT NULL DEFAULT ''
            )
            """
        )
        ocols = set()
        for row in self._db.execute("PRAGMA table_info(market_orders)").fetchall():
            try:
                ocols.add(row["name"])
            except Exception:
                ocols.add(row[1])
        if "delivery_path" not in ocols:
            try:
                self._db.execute(
                    "ALTER TABLE market_orders ADD COLUMN delivery_path TEXT NOT NULL DEFAULT ''"
                )
            except Exception:
                pass
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS market_ratings (
                rating_id TEXT PRIMARY KEY,
                listing_id TEXT NOT NULL,
                order_id TEXT NOT NULL,
                buyer_id TEXT NOT NULL,
                score INTEGER NOT NULL,
                comment TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL,
                UNIQUE(order_id)
            )
            """
        )
        self._db.commit()

    def categories(self) -> list:
        rows = self._db.execute(
            """
            SELECT category, COUNT(*) AS c FROM market_listings
            WHERE active = 1 GROUP BY category ORDER BY c DESC
            """
        ).fetchall()
        return [(r["category"], int(r["c"])) for r in rows]

    def search(self, query: str, category: Optional[str] = None, limit: int = 50) -> List[Listing]:
        q = f"%{(query or '').strip().lower()}%"
        if category:
            rows = self._db.execute(
                """
                SELECT * FROM market_listings
                WHERE active = 1 AND category = ?
                  AND (lower(title) LIKE ? OR lower(description) LIKE ?)
                ORDER BY updated_at DESC LIMIT ?
                """,
                (category, q, q, limit),
            ).fetchall()
        else:
            rows = self._db.execute(
                """
                SELECT * FROM market_listings
                WHERE active = 1
                  AND (lower(title) LIKE ? OR lower(description) LIKE ? OR lower(category) LIKE ?)
                ORDER BY updated_at DESC LIMIT ?
                """,
                (q, q, q, limit),
            ).fetchall()
        return [self._enrich(_row(r)) for r in rows]

    def list_active(self, category: Optional[str] = None, limit: int = 50) -> List[Listing]:
        if category:
            rows = self._db.execute(
                """
                SELECT * FROM market_listings WHERE active = 1 AND category = ?
                ORDER BY updated_at DESC LIMIT ?
                """,
                (category, limit),
            ).fetchall()
        else:
            rows = self._db.execute(
                """
                SELECT * FROM market_listings WHERE active = 1
                ORDER BY updated_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._enrich(_row(r)) for r in rows]

    def create_listing(
        self,
        title: str,
        price_nyx: float,
        description: str = "",
        category: str = "other",
        product_path: str = "",
    ) -> Listing:
        title = title.strip()
        if not title:
            raise ValueError("title required")
        price = nyx_to_micro(price_nyx)
        if price <= 0:
            raise ValueError("price must be > 0 NYX")
        category = (category or "other").strip().lower()[:32]
        if category not in self.CATEGORIES:
            category = "other"
        product_path = (product_path or "").strip()
        if product_path:
            p = Path(product_path)
            if not p.is_file():
                raise ValueError("product file not found: " + product_path)
            product_path = str(p.resolve())
        now = int(time.time())
        lid = "lst_" + uuid.uuid4().hex[:16]
        self._db.execute(
            """
            INSERT INTO market_listings(
                listing_id, seller_id, title, description, category,
                price_micro, active, created_at, updated_at, product_path
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (
                lid, self._owner, title[:120], description[:2000], category,
                price, now, now, product_path[:512],
            ),
        )
        self._db.commit()
        return self.get(lid)  # type: ignore

    def get(self, listing_id: str) -> Optional[Listing]:
        row = self._db.execute(
            "SELECT * FROM market_listings WHERE listing_id = ?", (listing_id,)
        ).fetchone()
        return self._enrich(_row(row)) if row else None

    def deactivate(self, listing_id: str) -> None:
        row = self.get(listing_id)
        if row is None or row.seller_id != self._owner:
            raise ValueError("listing not found or not owned")
        self._db.execute(
            "UPDATE market_listings SET active = 0, updated_at = ? WHERE listing_id = ?",
            (int(time.time()), listing_id),
        )
        self._db.commit()

    def buy(self, listing_id: str, spend_fn=None) -> Order:
        """Pay seller; deliver product file to buyer if attached."""
        listing = self.get(listing_id)
        if listing is None or not listing.active:
            raise ValueError("listing unavailable")
        if listing.seller_id == self._owner:
            raise ValueError("cannot buy own listing")
        # already bought?
        prev = self._db.execute(
            "SELECT order_id FROM market_orders WHERE listing_id = ? AND buyer_id = ?",
            (listing_id, self._owner),
        ).fetchone()
        if prev:
            raise ValueError("already purchased this listing")

        if spend_fn is not None:
            spend_fn(listing.seller_id, listing.price_micro, f"buy:{listing.title}")
        else:
            self._wallet.debit(
                listing.price_micro,
                memo=f"buy:{listing.title}",
                counterparty=listing.seller_id,
                kind="purchase",
            )

        oid = "ord_" + uuid.uuid4().hex[:16]
        now = int(time.time())
        delivery = ""
        if listing.product_path and Path(listing.product_path).is_file():
            dest_dir = (self._media or Path("nyx_data/media")) / "purchases" / self._owner[:16]
            dest_dir.mkdir(parents=True, exist_ok=True)
            src = Path(listing.product_path)
            dest = dest_dir / f"{oid}_{src.name}"
            shutil.copy2(src, dest)
            delivery = str(dest)

        self._db.execute(
            """
            INSERT INTO market_orders(
                order_id, listing_id, buyer_id, seller_id, price_micro, created_at, delivery_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (oid, listing_id, self._owner, listing.seller_id, listing.price_micro, now, delivery),
        )
        self._db.commit()
        return Order(
            order_id=oid,
            listing_id=listing_id,
            buyer_id=self._owner,
            seller_id=listing.seller_id,
            price_micro=listing.price_micro,
            created_at=now,
            delivery_path=delivery,
            title=listing.title,
        )

    def my_orders(self, limit: int = 50) -> List[Order]:
        rows = self._db.execute(
            """
            SELECT o.*, l.title FROM market_orders o
            LEFT JOIN market_listings l ON l.listing_id = o.listing_id
            WHERE o.buyer_id = ?
            ORDER BY o.created_at DESC LIMIT ?
            """,
            (self._owner, limit),
        ).fetchall()
        out = []
        for r in rows:
            out.append(
                Order(
                    order_id=r["order_id"],
                    listing_id=r["listing_id"],
                    buyer_id=r["buyer_id"],
                    seller_id=r["seller_id"],
                    price_micro=int(r["price_micro"]),
                    created_at=int(r["created_at"]),
                    delivery_path=r["delivery_path"] or "",
                    title=r["title"] or "",
                )
            )
        return out

    def rate(self, order_id: str, score: int, comment: str = "") -> None:
        score = int(score)
        if score < 1 or score > 5:
            raise ValueError("score must be 1..5")
        row = self._db.execute(
            "SELECT * FROM market_orders WHERE order_id = ? AND buyer_id = ?",
            (order_id, self._owner),
        ).fetchone()
        if row is None:
            raise ValueError("order not found")
        rid = "rt_" + uuid.uuid4().hex[:12]
        self._db.execute(
            """
            INSERT INTO market_ratings(rating_id, listing_id, order_id, buyer_id, score, comment, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rid, row["listing_id"], order_id, self._owner, score,
                (comment or "")[:500], int(time.time()),
            ),
        )
        self._db.commit()

    def _ratings_for(self, listing_id: str) -> tuple:
        row = self._db.execute(
            """
            SELECT AVG(score) AS a, COUNT(*) AS c FROM market_ratings WHERE listing_id = ?
            """,
            (listing_id,),
        ).fetchone()
        if not row or not row["c"]:
            return 0.0, 0
        return float(row["a"] or 0), int(row["c"])

    def _enrich(self, L: Listing) -> Listing:
        avg, cnt = self._ratings_for(L.listing_id)
        L.avg_rating = avg
        L.rating_count = cnt
        return L


def _row(r) -> Listing:
    try:
        pp = r["product_path"] or ""
    except Exception:
        pp = ""
    return Listing(
        listing_id=r["listing_id"],
        seller_id=r["seller_id"],
        title=r["title"],
        description=r["description"] or "",
        category=r["category"] or "other",
        price_micro=int(r["price_micro"]),
        active=bool(r["active"]),
        created_at=int(r["created_at"]),
        updated_at=int(r["updated_at"]),
        product_path=pp,
    )
