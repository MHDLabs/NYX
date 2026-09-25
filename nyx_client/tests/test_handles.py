from pathlib import Path
from nyx_client.config.settings import *
from nyx_client.core.app import NyxApp
from nyx_client.crypto.aead import generate_key
from nyx_client.storage.handles import validate_handle_format


def test_handle_unique_and_format(tmp_path: Path) -> None:
    assert validate_handle_format("ab") is not None
    assert validate_handle_format("alice") is None
    s = Settings(
        storage=StorageSettings(data_dir=str(tmp_path), db_filename="t.db"),
        data_dir=tmp_path,
        network=NetworkSettings(),
        logging=LoggingSettings(level="ERROR"),
        updates=UpdateSettings(),
    )
    app = NyxApp.from_settings(settings=s, profile_key=generate_key())
    app.start()
    h = app.set_user_handle("alice_1")
    assert h == "alice_1"
    chk = app.check_handle_available("alice_1")
    assert chk["status"] == "yours"
    g = app.create_group("Team")
    app.set_room_handle(g.room_id, "team_ops")
    assert app.resolve_handle("team_ops") == g.room_id
    try:
        app.set_user_handle("team_ops")
        assert False, "should not allow duplicate"
    except ValueError:
        pass
    app.stop()


def test_handle_race_older_wins(tmp_path: Path) -> None:
    from nyx_client.storage import Database
    from nyx_client.storage.handles import HandleStore, HandleConflictError, KIND_USER
    db = Database(tmp_path / "h.db")
    db.connect()
    store = HandleStore(db)
    store.claim("twin", KIND_USER, "nyx1old", "nyx1old", claimed_at_ms=1000)
    try:
        store.claim("twin", KIND_USER, "nyx1new", "nyx1new", claimed_at_ms=2000)
        assert False, "newer must lose"
    except HandleConflictError as e:
        assert e.handle == "twin"
        assert "try again" in str(e).lower() or "already taken" in str(e).lower()
    # older claim against newer stored should win (clock skew)
    store.claim("skew", KIND_USER, "nyx1b", "nyx1b", claimed_at_ms=5000)
    store.claim("skew", KIND_USER, "nyx1a", "nyx1a", claimed_at_ms=1000)
    rec = store.get("skew")
    assert rec is not None and rec.target_id == "nyx1a"
    db.close()
