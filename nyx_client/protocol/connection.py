"""
Connection manager and reconnect logic.

Whitepaper Section 14 / 15 / Failure Modes:
  - Connect + failover on failure
  - Exponential backoff reconnect
  - Composite scoring is an extension point (MVP: single server)

Transport is abstracted so the manager can be tested without network
and later wired to aiohttp / WebSocket without API changes.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Optional

from nyx_client.crypto.identity import Identity
from nyx_client.protocol.session import Session, SessionState
from nyx_client.config.logging import get_logger
from nyx_client.config.settings import NetworkSettings

log = get_logger(__name__)


class TransportError(Exception):
    """Raised when the transport cannot complete a request."""


class Transport(ABC):
    """Abstract transport for relay communication."""

    @abstractmethod
    async def connect(self, server: str, timeout: float) -> None:
        ...

    @abstractmethod
    async def close(self) -> None:
        ...

    @abstractmethod
    async def request(
        self,
        method: str,
        path: str,
        body: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        ...

    @property
    @abstractmethod
    def connected(self) -> bool:
        ...


class MockTransport(Transport):
    """
    In-memory transport for unit tests and offline development.

    Accepts any auth payload and returns a fake session token.
    """

    def __init__(self) -> None:
        self._connected = False
        self._server: Optional[str] = None
        self.requests: list[tuple[str, str, Optional[dict]]] = []
        self.fail_next: int = 0  # fail the next N requests
        self.auth_handler: Optional[Callable[[dict], dict]] = None

    async def connect(self, server: str, timeout: float) -> None:
        if self.fail_next > 0:
            self.fail_next -= 1
            raise TransportError("mock connect failure")
        self._server = server
        self._connected = True

    async def close(self) -> None:
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    async def request(
        self,
        method: str,
        path: str,
        body: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        if not self._connected:
            raise TransportError("not connected")
        if self.fail_next > 0:
            self.fail_next -= 1
            raise TransportError("mock request failure")
        self.requests.append((method, path, body))
        if path.endswith("/auth/session") or path.endswith("/auth/register"):
            if self.auth_handler:
                return self.auth_handler(body or {})
            return {
                "status": "ok",
                "session_token": "mock_token_" + (body or {}).get("identity", "")[:16],
                "server_identity": "nyx1mockserver000000000000000000000000",
            }
        if path.endswith("/health"):
            return {"status": "ok", "uptime": 1}
        return {"status": "ok"}


class ConnectionManager:
    """
    Manages a session lifecycle: connect → authenticate → maintain → reconnect.
    """

    def __init__(
        self,
        identity: Identity,
        network: NetworkSettings,
        transport: Optional[Transport] = None,
    ) -> None:
        self._identity = identity
        self._network = network
        self._transport = transport or MockTransport()
        self._session: Optional[Session] = None
        self._stop = False

    @property
    def session(self) -> Optional[Session]:
        return self._session

    @property
    def transport(self) -> Transport:
        return self._transport

    def _backoff_delay(self, attempt: int) -> float:
        """Exponential backoff clamped to configured min/max."""
        base = self._network.reconnect_min_backoff
        max_d = self._network.reconnect_max_backoff
        delay = min(max_d, base * (2 ** max(0, attempt - 1)))
        return delay

    async def connect(self, server: Optional[str] = None) -> Session:
        """
        Connect and authenticate to a relay.

        Raises TransportError on failure (caller may invoke reconnect loop).
        """
        target = server or self._network.default_server
        session = Session(server=target, identity=self._identity)
        session.mark_connecting()
        self._session = session

        try:
            await self._transport.connect(
                target, timeout=float(self._network.connection_timeout)
            )
            session.mark_authenticating()
            payload = session.auth_payload()
            resp = await self._transport.request(
                "POST",
                "/api/v3/auth/session",
                body=payload,
                timeout=float(self._network.connection_timeout),
            )
            token = resp.get("session_token")
            if not token:
                raise TransportError("server did not return session_token")
            session.mark_authenticated(token)
            return session
        except Exception as exc:
            session.mark_failed(str(exc))
            await self._safe_close()
            raise

    async def disconnect(self) -> None:
        self._stop = True
        if self._session:
            self._session.mark_disconnected("user_request")
        await self._safe_close()

    async def _safe_close(self) -> None:
        try:
            await self._transport.close()
        except Exception:
            pass

    async def reconnect_loop(
        self,
        server: Optional[str] = None,
        max_attempts: Optional[int] = None,
    ) -> Session:
        """
        Retry connect with exponential backoff until success or limit.

        max_attempts=None uses network.reconnect_max_attempts (0 = unlimited).
        """
        limit = (
            max_attempts
            if max_attempts is not None
            else self._network.reconnect_max_attempts
        )
        attempt = 0
        self._stop = False

        while not self._stop:
            attempt += 1
            if limit and attempt > limit:
                raise TransportError(f"reconnect gave up after {limit} attempts")

            if self._session:
                self._session.mark_reconnecting()

            delay = self._backoff_delay(attempt)
            log.info(
                "connection.reconnect_attempt",
                attempt=attempt,
                delay=delay,
            )
            if attempt > 1:
                await asyncio.sleep(delay)

            try:
                return await self.connect(server)
            except TransportError as exc:
                log.warning(
                    "connection.reconnect_failed",
                    attempt=attempt,
                    error=str(exc),
                )
                continue

        raise TransportError("reconnect stopped")

    async def ensure_connected(self) -> Session:
        """Return current authenticated session or connect."""
        if self._session and self._session.is_authenticated() and self._transport.connected:
            return self._session
        return await self.connect()
