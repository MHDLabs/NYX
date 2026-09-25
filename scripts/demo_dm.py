#!/usr/bin/env python3
"""
Offline DM demo for NYX Client 0.1.0-MVP.

Creates two isolated users (Alice & Bob), exchanges X25519 keys,
sends a signed E2EE message, verifies on the other side, and shows
that history survives process restart.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

# Allow running from repo root without install
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nyx_client.config.settings import (
    Settings,
    StorageSettings,
    NetworkSettings,
    LoggingSettings,
)
from nyx_client.core.app import NyxApp
from nyx_client.crypto.aead import generate_key
from nyx_client.protocol.envelope import verify_envelope


def make_app(label: str, base: Path) -> NyxApp:
    data = base / label
    data.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        storage=StorageSettings(data_dir=str(data), db_filename="nyx.db"),
        data_dir=data,
        network=NetworkSettings(default_server="nyx://demo.relay"),
        logging=LoggingSettings(level="WARNING"),
    )
    key = generate_key()
    (data / ".profile_key").write_bytes(key)
    (data / ".profile_key").chmod(0o600)
    app = NyxApp.from_settings(settings=settings, profile_key=key)
    app.start()
    return app


def main() -> int:
    base = Path(tempfile.mkdtemp(prefix="nyx-demo-"))
    print("=" * 56)
    print("  NYX Client 0.1.0-MVP — Offline DM Demo")
    print("=" * 56)
    print("Workspace:", base)
    print()

    try:
        alice = make_app("alice", base)
        bob = make_app("bob", base)
        assert alice.identity and bob.identity
        assert alice.messaging and bob.messaging

        print("[1] Identities")
        print("    Alice:", alice.identity.id)
        print("    Bob  :", bob.identity.id)
        print()

        # Exchange DM public keys (simulates prekey / out-of-band exchange)
        alice.messaging.register_peer_key(bob.identity.id, bob.messaging.dm_public_key)
        bob.messaging.register_peer_key(alice.identity.id, alice.messaging.dm_public_key)
        alice.messaging.ensure_contact(bob.identity.id, display_name="Bob")
        bob.messaging.ensure_contact(alice.identity.id, display_name="Alice")

        print("[2] Alice sends E2EE DM to Bob")
        plaintext = b"Hello Bob - this is an end-to-end encrypted NYX message."
        env = alice.messaging.send_dm(bob.identity.id, plaintext)
        print("    message_id :", env.message_id)
        print("    sequence   :", env.sequence)
        print("    signature  : valid" if verify_envelope(env) else "INVALID")
        print()

        print("[3] Bob ingests envelope")
        dec = bob.messaging.ingest_envelope(env)
        print("    decrypted  :", dec.plaintext.decode())
        print("    verified   :", dec.verified)
        print()

        print("[4] Persistence check (restart Alice)")
        alice_id = alice.identity.id
        bob_id = bob.identity.id
        alice_key = (base / "alice" / ".profile_key").read_bytes()
        alice.stop()

        settings = Settings(
            storage=StorageSettings(
                data_dir=str(base / "alice"), db_filename="nyx.db"
            ),
            data_dir=base / "alice",
            network=NetworkSettings(default_server="nyx://demo.relay"),
            logging=LoggingSettings(level="WARNING"),
        )
        alice2 = NyxApp.from_settings(settings=settings, profile_key=alice_key)
        alice2.start()
        assert alice2.messaging
        hist = alice2.messaging.history(bob_id)
        ok = hist and hist[0].plaintext == plaintext
        print("    history after restart:", "OK" if ok else "FAILED")
        if hist:
            print("    plaintext           :", hist[0].plaintext.decode())
        alice2.stop()
        bob.stop()

        print()
        if ok and dec.plaintext == plaintext:
            print("RESULT: PASS — local E2EE DM path works end-to-end.")
            return 0
        print("RESULT: FAIL")
        return 1
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
