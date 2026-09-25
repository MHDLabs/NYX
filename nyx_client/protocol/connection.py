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
        if path.endswith("/auth/register"):
            if self.auth_handler:
                return self.auth_handler(body or {})
            return {
                "status": "ok",
                "registered": True,
                "identity": (body or {}).get("identity", ""),
                "server_identity": "nyx1mockserver000000000000000000000000",
            }
        if path.endswith("/auth/session"):
            if self.auth_handler:
                return self.auth_handler(body or {})
            return {
                "status": "ok",
                "session_token": "mock_token_" + (body or {}).get("identity", "")[:16],
                "server_identity": "nyx1mockserver000000000000000000000000",
                "expires_at": 9999999999,
            }
        if "messages/sync" in path:
            return {"messages": [], "next_since": 0}
        if "messages/ack" in path:
            return {"status": "ok"}
        if "messages/send" in path:
            return {"status": "ok", "queued": True, "message_id": (body or {}).get("message_id")}
        if path.endswith("/health"):
            return {"status": "ok", "uptime": 1, "protocol_version": 3}
        if "discovery/servers" in path or "trust/servers" in path:
            return {
                "servers": [
                    {
                        "id": "self",
                        "endpoint": self._server or "nyx://mock.local",
                        "trust_level": 2,
                        "reputation": 0.9,
                        "uptime": 0.99,
                        "capacity": 0.8,
                    }
                ]
            }
        if path.endswith("/profile") and method.upper() == "PUT":
            return {"status": "ok"}
        if "handles/check" in path:
            # offline mock: always available unless name is "taken"
            q = path.split("name=")[-1] if "name=" in path else ""
            return {"available": q not in ("taken", "admin"), "handle": q}
        if "handles/claim" in path:
            h = (body or {}).get("handle") or ""
            if h == "taken":
                return {
                    "status": "conflict",
                    "handle": h,
                    "winner_target": "nyx1other",
                    "winner_claimed_at_ms": 1,
                }
            return {"status": "ok", "handle": h}
        if "token/vouchers/pending" in path:
            return {"vouchers": []}
        if "token/broadcast" in path:
            return {"status": "ok", "txid": (body or {}).get("txid", ""), "confirmed": True}
        if "token/utxos" in path:
            return {"utxos": []}
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
        Connect, register identity, authenticate, ready for traffic.

        Flow:
          1. Transport connect + health
          2. POST /api/v3/auth/register  (idempotent identity+device announce)
          3. POST /api/v3/auth/session   (signed challenge → session_token)
          4. Attach Bearer token to transport for subsequent calls

        Raises TransportError on failure (caller may invoke reconnect loop).
        """
        target = server or self._network.default_server
        session = Session(server=target, identity=self._identity)
        session.mark_connecting()
        self._session = session
        timeout = float(self._network.connection_timeout)

        try:
            await self._transport.connect(target, timeout=timeout)
            session.mark_authenticating()
            payload = session.auth_payload()

            # 1) Register (idempotent) — public key + device on relay
            reg_body = dict(payload)
            reg_body["identity_public_key"] = self._identity.public_key_bytes.hex()
            try:
                reg = await self._transport.request(
                    "POST", "/api/v3/auth/register", body=reg_body, timeout=timeout
                )
                session.server_identity = reg.get("server_identity") or session.server_identity
                session.registered = True
                log.info(
                    "connection.registered",
                    identity=self._identity.id[:24],
                    server=target,
                )
            except TransportError as exc:
                # Older relays may only support session — continue
                log.warning("connection.register_skipped", error=str(exc))

            # 2) Authenticate session
            resp = await self._transport.request(
                "POST",
                "/api/v3/auth/session",
                body=payload,
                timeout=timeout,
            )
            token = resp.get("session_token")
            if not token:
                raise TransportError("server did not return session_token")
            session.mark_authenticated(token)
            if resp.get("server_identity"):
                session.server_identity = resp["server_identity"]
            if resp.get("expires_at"):
                session.expires_at = resp["expires_at"]

            # 3) Bearer on transport
            setter = getattr(self._transport, "set_session_token", None)
            if callable(setter):
                setter(token)

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

    async def put_prekeys(
        self,
        signed_prekey: str,
        signature: str,
        one_time_prekeys: list | None = None,
    ) -> dict:
        await self.ensure_connected()
        return await self._transport.request(
            "PUT",
            "/api/v3/prekeys",
            body={
                "signed_prekey": signed_prekey,
                "signature": signature,
                "one_time_prekeys": list(one_time_prekeys or []),
            },
        )

    async def get_prekeys(self, identity: str) -> dict:
        await self.ensure_connected()
        return await self._transport.request("GET", f"/api/v3/prekeys/{identity}")

    async def upload_media(self, filename: str, data: bytes, kind: str = "file") -> dict:
        import base64
        await self.ensure_connected()
        return await self._transport.request(
            "POST",
            "/api/v3/media/upload",
            body={
                "filename": filename,
                "kind": kind,
                "data_base64": base64.b64encode(data).decode("ascii"),
            },
        )

    async def send_receipts(self, message_ids: list) -> dict:
        await self.ensure_connected()
        return await self._transport.request(
            "POST",
            "/api/v3/messages/receipts",
            body={"message_ids": list(message_ids)},
        )




