from pathlib import Path
from nyx_client.config.settings import *
from nyx_client.core.app import NyxApp
from nyx_client.crypto.aead import generate_key
from nyx_client.crypto import Identity
from nyx_client.storage import Database
from nyx_client.storage.room_roles import RoomRoleStore, ROLE_ADMIN, ROLE_MEMBER
from nyx_client.core.wallet import Wallet


def test_mute_kick_and_owner_settings(tmp_path: Path) -> None:
    s = Settings(
        storage=StorageSettings(data_dir=str(tmp_path), db_filename="t.db"),
        data_dir=tmp_path,
        network=NetworkSettings(),
        logging=LoggingSettings(level="ERROR"),
        updates=UpdateSettings(),
    )
    app = NyxApp.from_settings(settings=s, profile_key=generate_key())
    app.start()
    g = app.create_group("Ops")
    other = Identity.create()
    app.room_roles.upsert_member(g.room_id, other.id, ROLE_MEMBER)
    app.room_roles.mute(g.room_id, app.identity.id, other.id, duration_sec=60, reason="spam")
    assert app.room_roles.is_muted(g.room_id, other.id)
    assert not app.room_roles.can_post(g.room_id, other.id)
    app.room_roles.unmute(g.room_id, app.identity.id, other.id)
    assert not app.room_roles.is_muted(g.room_id, other.id)
    admin = Identity.create()
    app.room_roles.upsert_member(g.room_id, admin.id, ROLE_ADMIN)
    # admin cannot change settings
    try:
        app.room_roles.require_owner(g.room_id, admin.id)
        assert False
    except PermissionError:
        pass
    app.room_roles.kick(g.room_id, app.identity.id, other.id)
    assert app.room_roles.get_role(g.room_id, other.id) is None
    app.stop()


def test_wallet_export_import_fund(tmp_path: Path) -> None:
    db = Database(tmp_path / "w.db")
    db.connect()
    ident = Identity.create()
    w = Wallet(db, ident.id)  # accounting ledger without UTXO for keystore test
    w.credit(25 * 1_000_000, memo="test")
    out = tmp_path / "backup"
    path = w.export_keystore(out, "secretpass")
    assert path.is_file()
    w2 = Wallet(db, ident.id)
    info = w2.import_keystore(path, "secretpass", replace_ledger=True)
    assert info.balance_nyx >= 25.0
    db.close()


