"""End-to-end tests: full app lifecycle with persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from nyx_client.config.settings import (
    Settings,
    StorageSettings,
    NetworkSettings,
    LoggingSettings,
)
from nyx_client.core.app import NyxApp
from nyx_client.crypto.aead import generate_key


def _settings(data_dir: Path) -> Settings:
    return Settings(
        storage=StorageSettings(data_dir=str(data_dir), db_filename="e2e.db"),
        data_dir=data_dir,
        network=NetworkSettings(default_server="nyx://e2e.relay"),
        logging=LoggingSettings(level="WARNING"),
    )


def test_app_create_and_reload_identity(tmp_path: Path) -> None:
    key = generate_key()
    data = tmp_path / "alice_data"
    data.mkdir()

    app1 = NyxApp.from_settings(settings=_settings(data), profile_key=key)
    id1 = app1.start()
    assert app1.last_mnemonic is not None  # first run
    mid = id1.id
    app1.stop()

    app2 = NyxApp.from_settings(settings=_settings(data), profile_key=key)
    id2 = app2.start()
    assert app2.last_mnemonic is None  # existing profile
    assert id2.id == mid
    # Signing still works after reload
    sig = id2.sign(b"persist")
    assert id2.verify(sig, b"persist")
    app2.stop()


def test_app_dm_persist_across_restart(tmp_path: Path) -> None:
    key_a = generate_key()
    key_b = generate_key()
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()

    alice = NyxApp.from_settings(settings=_settings(dir_a), profile_key=key_a)
    bob = NyxApp.from_settings(settings=_settings(dir_b), profile_key=key_b)
    alice.start()
    bob.start()
    assert alice.messaging and bob.messaging

    alice.messaging.register_peer_key(bob.identity.id, bob.messaging.dm_public_key)
    bob.messaging.register_peer_key(alice.identity.id, alice.messaging.dm_public_key)

    env = alice.messaging.send_dm(bob.identity.id, b"persisted hello")
    decrypted = bob.messaging.ingest_envelope(env)
    assert decrypted.plaintext == b"persisted hello"

    alice_id = alice.identity.id
    bob_id = bob.identity.id
    alice.stop()
    bob.stop()

    # Reload Alice — history must still decrypt
    alice2 = NyxApp.from_settings(settings=_settings(dir_a), profile_key=key_a)
    alice2.start()
    # Peer key is in-memory only for MVP; re-register
    # (production will persist peer keys in contacts/prekey store)
    bob2 = NyxApp.from_settings(settings=_settings(dir_b), profile_key=key_b)
    bob2.start()
    assert alice2.messaging and bob2.messaging
    alice2.messaging.register_peer_key(bob_id, bob2.messaging.dm_public_key)

    hist = alice2.messaging.history(bob_id)
    assert len(hist) >= 1
    assert hist[0].plaintext == b"persisted hello"
    alice2.stop()
    bob2.stop()


def test_app_dispatch_commands(tmp_path: Path) -> None:
    key = generate_key()
    data = tmp_path / "cmd"
    data.mkdir()
    app = NyxApp.from_settings(settings=_settings(data), profile_key=key)
    app.start()

    r = app.dispatch("/status")
    assert r.ok
    assert app.identity.id in r.message

    r = app.dispatch("/help")
    assert r.ok
    assert "/dm" in r.message

    r = app.dispatch("/exit")
    assert r.message == "__EXIT__"
    app.stop()


def test_wrong_profile_key_fails(tmp_path: Path) -> None:
    from cryptography.exceptions import InvalidTag

    data = tmp_path / "x"
    data.mkdir()
    key1 = generate_key()
    app = NyxApp.from_settings(settings=_settings(data), profile_key=key1)
    app.start()
    app.stop()

    key2 = generate_key()
    app2 = NyxApp.from_settings(settings=_settings(data), profile_key=key2)
    with pytest.raises(InvalidTag):
        app2.start()
