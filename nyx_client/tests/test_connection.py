"""Unit tests for session auth and connection manager."""

from __future__ import annotations

import asyncio

import pytest

from nyx_client.crypto import Identity
from nyx_client.config.settings import NetworkSettings
from nyx_client.protocol import (
    ConnectionManager,
    MockTransport,
    SessionState,
    TransportError,
)


def _identity() -> Identity:
    return Identity.create()


def _network() -> NetworkSettings:
    return NetworkSettings(
        default_server="nyx://test.relay",
        connection_timeout=5,
        reconnect_min_backoff=0.01,
        reconnect_max_backoff=0.05,
        reconnect_max_attempts=5,
    )


def test_connect_and_authenticate() -> None:
    async def _run() -> None:
        transport = MockTransport()
        mgr = ConnectionManager(_identity(), _network(), transport=transport)
        session = await mgr.connect()
        assert session.is_authenticated()
        assert session.state == SessionState.AUTHENTICATED
        assert session.session_token
        assert transport.connected
        assert any(p.endswith("/auth/session") for _, p, _ in transport.requests)

    asyncio.run(_run())


def test_auth_payload_signed() -> None:
    async def _run() -> None:
        identity = _identity()
        transport = MockTransport()
        mgr = ConnectionManager(identity, _network(), transport=transport)
        await mgr.connect()
        body = transport.requests[0][2]
        assert body is not None
        assert body["identity"] == identity.id
        assert "signature" in body
        assert body["device_id"] == identity.primary_device().device_id  # type: ignore

    asyncio.run(_run())


def test_connect_failure() -> None:
    async def _run() -> None:
        transport = MockTransport()
        transport.fail_next = 1
        mgr = ConnectionManager(_identity(), _network(), transport=transport)
        with pytest.raises(TransportError):
            await mgr.connect()
        assert mgr.session is not None
        assert mgr.session.state == SessionState.FAILED

    asyncio.run(_run())


def test_reconnect_succeeds() -> None:
    async def _run() -> None:
        transport = MockTransport()
        transport.fail_next = 2
        mgr = ConnectionManager(_identity(), _network(), transport=transport)
        session = await mgr.reconnect_loop(max_attempts=5)
        assert session.is_authenticated()
        assert session.reconnect_attempt == 0

    asyncio.run(_run())


def test_reconnect_gives_up() -> None:
    async def _run() -> None:
        transport = MockTransport()
        transport.fail_next = 100
        mgr = ConnectionManager(_identity(), _network(), transport=transport)
        with pytest.raises(TransportError, match="gave up"):
            await mgr.reconnect_loop(max_attempts=3)

    asyncio.run(_run())


def test_disconnect() -> None:
    async def _run() -> None:
        transport = MockTransport()
        mgr = ConnectionManager(_identity(), _network(), transport=transport)
        await mgr.connect()
        await mgr.disconnect()
        assert mgr.session is not None
        assert mgr.session.state == SessionState.DISCONNECTED
        assert not transport.connected

    asyncio.run(_run())
