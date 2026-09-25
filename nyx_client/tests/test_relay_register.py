
"""Relay register + session + discovery flow (mock transport)."""
from __future__ import annotations
from pathlib import Path
from nyx_client.config.settings import *
from nyx_client.core.app import NyxApp
from nyx_client.crypto.aead import generate_key
from nyx_client.protocol.connection import MockTransport, ConnectionManager


def test_register_and_session_mock(tmp_path: Path) -> None:
    s = Settings(
        storage=StorageSettings(data_dir=str(tmp_path), db_filename="t.db"),
        data_dir=tmp_path,
        network=NetworkSettings(default_server="nyx://mock.local"),
        logging=LoggingSettings(level="ERROR"),
        updates=UpdateSettings(),
    )
    app = NyxApp.from_settings(settings=s, profile_key=generate_key())
    app.start()
    app.prefs.set_display_name("RelayUser")
    session = app.connect_sync(endpoint="nyx://mock.local", use_http=False)
    assert session.is_authenticated()
    assert session.session_token
    meta = getattr(session, "sync_meta", {})
    assert meta.get("registered") is True or getattr(session, "registered", False)
    assert meta.get("servers_fetched", 0) >= 1
    state = app._load_relay_state()
    assert state.get("server") == "nyx://mock.local"
    assert state.get("identity") == app.identity.id
    app.stop()


def test_connection_manager_register_requests() -> None:
    from nyx_client.crypto import Identity
    ident = Identity.create()
    tr = MockTransport()
    net = NetworkSettings(default_server="nyx://mock.local")
    mgr = ConnectionManager(ident, net, tr)
    import asyncio
    session = asyncio.run(mgr.connect("nyx://mock.local"))
    paths = [p for (_, p, _) in tr.requests]
    assert any(p.endswith("/auth/register") for p in paths)
    assert any(p.endswith("/auth/session") for p in paths)
    assert session.is_authenticated()
