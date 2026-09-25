#!/usr/bin/env python3
"""Release smoke test for group testing — runs offline without a live relay."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nyx_client import __version__
from nyx_client.config.settings import (
    Settings, StorageSettings, NetworkSettings, LoggingSettings, UpdateSettings,
)
from nyx_client.core.app import NyxApp
from nyx_client.crypto.aead import generate_key
from nyx_client.crypto.ratchet import DoubleRatchetSession
from nyx_client.crypto.keys import X25519KeyPair
from nyx_client.protocol.discovery import ServerDirectory, ServerInfo, composite_score
from nyx_client.update.updater import version_greater
import os


def section(title: str) -> None:
    print()
    print("== " + title)


def main() -> int:
    print("NYX Client smoke test — version", __version__)
    base = Path(tempfile.mkdtemp(prefix="nyx-smoke-"))
    fails = 0
    try:
        section("Identity + profile persistence")
        key = generate_key()
        data = base / "u1"
        data.mkdir()
        settings = Settings(
            storage=StorageSettings(data_dir=str(data), db_filename="nyx.db"),
            data_dir=data,
            network=NetworkSettings(default_server="nyx://demo.relay"),
            logging=LoggingSettings(level="WARNING"),
            updates=UpdateSettings(channel="stable"),
        )
        app = NyxApp.from_settings(settings=settings, profile_key=key)
        ident = app.start()
        assert ident.id.startswith("nyx1")
        mid = ident.id
        app.stop()
        app2 = NyxApp.from_settings(settings=settings, profile_key=key)
        assert app2.start().id == mid
        print("  OK", mid[:40] + "...")

        section("Double Ratchet E2EE")
        sk = os.urandom(32)
        bob_dh = X25519KeyPair.generate()
        a = DoubleRatchetSession.initiate(sk, bob_dh.public_bytes())
        b = DoubleRatchetSession.respond(sk, bob_dh)
        assert b.decrypt(a.encrypt(b"secret")) == b"secret"
        assert a.decrypt(b.encrypt(b"reply")) == b"reply"
        print("  OK bidirectional")

        section("DM + history persistence")
        key_b = generate_key()
        data_b = base / "u2"
        data_b.mkdir()
        sb = Settings(
            storage=StorageSettings(data_dir=str(data_b), db_filename="nyx.db"),
            data_dir=data_b,
            network=NetworkSettings(),
            logging=LoggingSettings(level="WARNING"),
            updates=UpdateSettings(),
        )
        bob = NyxApp.from_settings(settings=sb, profile_key=key_b)
        bob.start()
        alice = NyxApp.from_settings(settings=settings, profile_key=key)
        alice.start()
        assert alice.messaging and bob.messaging
        alice.messaging.register_peer_key(bob.identity.id, bob.messaging.dm_public_key)
        bob.messaging.register_peer_key(alice.identity.id, alice.messaging.dm_public_key)
        env = alice.messaging.send_dm(bob.identity.id, b"group-test-hello")
        dec = bob.messaging.ingest_envelope(env)
        assert dec.plaintext == b"group-test-hello"
        print("  OK Alice->Bob")

        section("Server directory scoring")
        d = ServerDirectory(base / "srv", bootstrap=[])
        d.upsert(ServerInfo(id="near", endpoint="nyx://near", latency_ms=30, reputation=0.8, trust_level=2, reachable=True, uptime=0.99))
        d.upsert(ServerInfo(id="far", endpoint="nyx://far", latency_ms=900, reputation=0.8, trust_level=2, reachable=True, uptime=0.99))
        best = d.best()
        assert best and best.id == "near"
        print("  OK preferred", best.endpoint, "score", round(best.score, 3))

        section("Update version logic")
        assert version_greater("0.2.0", "0.1.0")
        print("  OK 0.2.0 > 0.1.0")

        section("Commands")
        r = alice.dispatch("/status")
        assert r.ok
        r = alice.dispatch("/help")
        assert "/servers" in r.message and "/update" in r.message
        print("  OK /status /help")

        alice.stop()
        bob.stop()
        print()
        print("RESULT: ALL SMOKE CHECKS PASSED")
        return 0
    except Exception as exc:
        print("FAIL:", exc)
        return 1
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
