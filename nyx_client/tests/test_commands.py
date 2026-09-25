"""Unit tests for the command system and REPL dispatch."""

from __future__ import annotations

from pathlib import Path

import pytest

from nyx_client.crypto import Identity
from nyx_client.core.commands import CommandContext, registry
from nyx_client.core.messaging import MessagingService
from nyx_client.storage import Database, MessageStore, ContactStore


@pytest.fixture
def ctx(tmp_path: Path) -> CommandContext:
    alice = Identity.create()
    bob = Identity.create()
    db = Database(tmp_path / "cmd.db")
    db.connect()
    messaging = MessagingService(alice, MessageStore(db), ContactStore(db))
    # Register a peer so /dm works
    bob_svc = MessagingService(bob, MessageStore(db), ContactStore(db))
    messaging.register_peer_key(bob.id, bob_svc.dm_public_key)
    messaging.ensure_contact(bob.id, display_name="Bob")
    return CommandContext(
        identity_id=alice.id,
        server="nyx://test",
        connected=False,
        services={
            "messaging": messaging,
            "contacts": ContactStore(db),
            "_bob_id": bob.id,
            "_db": db,
        },
    )


def test_help(ctx: CommandContext) -> None:
    r = registry.dispatch(ctx, "/help")
    assert r.ok
    assert "/status" in r.message
    assert "/dm" in r.message


def test_status(ctx: CommandContext) -> None:
    r = registry.dispatch(ctx, "/status")
    assert r.ok
    assert ctx.identity_id in r.message


def test_identity(ctx: CommandContext) -> None:
    r = registry.dispatch(ctx, "/identity")
    assert r.ok
    assert ctx.identity_id in r.message


def test_contacts(ctx: CommandContext) -> None:
    r = registry.dispatch(ctx, "/contacts")
    assert r.ok
    assert "Bob" in r.message


def test_dm_send_and_history(ctx: CommandContext) -> None:
    bob_id = ctx.services["_bob_id"]
    r = registry.dispatch(ctx, "/dm " + bob_id + " hello there")
    assert r.ok
    assert "sent seq=" in r.message
    r2 = registry.dispatch(ctx, "/dm " + bob_id)
    assert r2.ok
    assert "hello there" in r2.message


def test_unknown_command(ctx: CommandContext) -> None:
    r = registry.dispatch(ctx, "/nosuch")
    assert not r.ok
    assert "unknown" in r.message


def test_exit(ctx: CommandContext) -> None:
    r = registry.dispatch(ctx, "/exit")
    assert r.ok
    assert r.message == "__EXIT__"


def test_non_command(ctx: CommandContext) -> None:
    r = registry.dispatch(ctx, "hello")
    assert not r.ok
