
from pathlib import Path
from nyx_client.config.settings import *
from nyx_client.core.app import NyxApp
from nyx_client.crypto.aead import generate_key
from nyx_client.crypto import Identity
from nyx_client.storage import Database
from nyx_client.core.wallet import Wallet
from nyx_client.core.marketplace import Marketplace
from nyx_client.core.utxo_ledger import UTXOLedger


def test_buy_delivers_file_and_rating(tmp_path: Path) -> None:
    db = Database(tmp_path / "m.db")
    db.connect()
    seller = Identity.create()
    buyer = Identity.create()
    # seller utxo
    ls = UTXOLedger(db, seller)
    lb = UTXOLedger(db, buyer)
    ws = Wallet(db, seller.id, utxo_ledger=ls)
    wb = Wallet(db, buyer.id, utxo_ledger=lb)
    media = tmp_path / "media"
    ms = Marketplace(db, ws, seller.id, media_root=media)
    product = tmp_path / "payload.bin"
    product.write_bytes(b"SECRET_CONTENT")
    L = ms.create_listing("Pack", 1.0, category="digital", product_path=str(product))
    mb = Marketplace(db, wb, buyer.id, media_root=media)
    before = wb.balance_micro()
    order = mb.buy(L.listing_id, spend_fn=lambda seller_id, amt, memo: lb.transfer(seller_id, amt, memo=memo))
    assert order.delivery_path
    assert Path(order.delivery_path).read_bytes() == b"SECRET_CONTENT"
    assert wb.balance_micro() == before - L.price_micro
    mb.rate(order.order_id, 5, "great")
    L2 = mb.get(L.listing_id)
    assert L2.rating_count == 1 and L2.avg_rating == 5.0
    db.close()


def test_public_cannot_mine(tmp_path: Path) -> None:
    s = Settings(
        storage=StorageSettings(data_dir=str(tmp_path), db_filename="t.db"),
        data_dir=tmp_path,
        network=NetworkSettings(),
        logging=LoggingSettings(level="ERROR"),
        updates=UpdateSettings(),
        token=TokenSettings(allow_local_mine=False),
    )
    app = NyxApp.from_settings(settings=s, profile_key=generate_key())
    app.start()
    r = app.dispatch("/mine 1")
    assert not r.ok
    app.stop()
