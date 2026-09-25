
from pathlib import Path
from nyx_client.config.settings import *
from nyx_client.core.app import NyxApp
from nyx_client.crypto.aead import generate_key
from nyx_client.crypto import Identity
from nyx_client.storage import Database
from nyx_client.storage.room_roles import RoomRoleStore, POLICY_OWNER_ONLY, POLICY_POSTERS
from nyx_client.storage.attachments import AttachmentStore
from nyx_client.core.wallet import Wallet, nyx_to_micro
from nyx_client.core.marketplace import Marketplace


def test_wallet_genesis_and_debit(tmp_path: Path) -> None:
    db = Database(tmp_path / "w.db")
    db.connect()
    ident = Identity.create()
    w = Wallet(db, ident.id)
    assert w.balance_nyx() >= 100.0
    w.debit(nyx_to_micro(1.5), memo="test")
    assert abs(w.balance_nyx() - 98.5) < 1e-6
    db.close()


def test_marketplace_buy(tmp_path: Path) -> None:
    db = Database(tmp_path / "m.db")
    db.connect()
    seller = Identity.create()
    buyer = Identity.create()
    ws = Wallet(db, seller.id)
    wb = Wallet(db, buyer.id)
    # seller lists via seller market instance
    ms = Marketplace(db, ws, seller.id)
    L = ms.create_listing("Cool Source", 5.0, category="source")
    mb = Marketplace(db, wb, buyer.id)
    before = wb.balance_micro()
    order = mb.buy(L.listing_id)
    assert order.order_id.startswith("ord_")
    assert wb.balance_micro() == before - L.price_micro
    db.close()


def test_channel_owner_only_post(tmp_path: Path) -> None:
    s = Settings(
        storage=StorageSettings(data_dir=str(tmp_path), db_filename="t.db"),
        data_dir=tmp_path,
        network=NetworkSettings(),
        logging=LoggingSettings(level="ERROR"),
        updates=UpdateSettings(),
    )
    app = NyxApp.from_settings(settings=s, profile_key=generate_key())
    app.start()
    ch = app.create_channel("News", public=True)
    assert app.room_roles.get_post_policy(ch.room_id) == POLICY_OWNER_ONLY
    # owner can post
    app.messaging.send_room_message(ch.room_id, b"hello from owner")
    # simulate other identity without role
    other = Identity.create()
    app.messaging._identity = other  # noqa: SLF001
    try:
        app.messaging.send_room_message(ch.room_id, b"spam")
        raised = False
    except PermissionError:
        raised = True
    assert raised
    app.stop()


def test_recovery_email(tmp_path: Path) -> None:
    s = Settings(
        storage=StorageSettings(data_dir=str(tmp_path), db_filename="t.db"),
        data_dir=tmp_path,
        network=NetworkSettings(),
        logging=LoggingSettings(level="ERROR"),
        updates=UpdateSettings(),
    )
    app = NyxApp.from_settings(settings=s, profile_key=generate_key())
    app.start()
    app.prefs.set_recovery_email("user@example.com")
    assert app.prefs.get_profile().recovery_email == "user@example.com"
    app.stop()


def test_attachment_store(tmp_path: Path) -> None:
    db = Database(tmp_path / "a.db")
    db.connect()
    media = tmp_path / "media"
    store = AttachmentStore(db, media)
    f = tmp_path / "sample.txt"
    f.write_text("hello nyx")
    att = store.store_file(f, "conv1", "msg1")
    assert att.size == 9
    assert Path(att.local_path).is_file()
    db.close()
