"""
HTTP transport for NYX relay communication.

Whitepaper Section 53 API:
  POST /api/v3/auth/session
  POST /api/v3/messages/send
  GET  /api/v3/messages/sync
  GET  /api/v3/health

Uses stdlib urllib so the MVP runs without aiohttp.
When aiohttp is installed, AsyncHttpTransport can replace this.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

from nyx_client.protocol.connection import Transport, TransportError
from nyx_client.config.logging import get_logger

log = get_logger(__name__)


def _normalize_base(server: str) -> str:
    """
    Accept nyx://host, https://host, http://host:port.
    Default scheme for nyx:// is https.
    """
    if server.startswith("nyx://"):
        server = "https://" + server[len("nyx://"):]
    if not server.startswith("http://") and not server.startswith("https://"):
        server = "https://" + server
    if not server.endswith("/"):
        server += "/"
    return server


class HttpTransport(Transport):
    """Synchronous HTTP/HTTPS transport (thread-friendly, no extra deps)."""

    def __init__(self, verify_tls: bool = True, user_agent: str = "nyx-client/0.1.0") -> None:
        self._base: Optional[str] = None
        self._connected = False
        self._verify_tls = verify_tls
        self._ua = user_agent
        self._ctx = ssl.create_default_context()
        if not verify_tls:
            self._ctx.check_hostname = False
            self._ctx.verify_mode = ssl.CERT_NONE

    async def connect(self, server: str, timeout: float) -> None:
        self._base = _normalize_base(server)
        # Health probe
        try:
            await self.request("GET", "/api/v3/health", timeout=timeout)
        except TransportError:
            # Some relays may not expose health yet — still mark connected
            log.warning("transport.health_probe_failed", server=self._base)
        self._connected = True
        log.info("transport.connected", base=self._base)

    async def close(self) -> None:
        self._connected = False
        self._base = None

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
        if not self._base:
            raise TransportError("not connected")
        url = urljoin(self._base, path.lstrip("/"))
        data = None
        hdrs = {"User-Agent": self._ua, "Accept": "application/json"}
        if headers:
            hdrs.update(headers)
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=self._ctx) as resp:
                raw = resp.read()
                if not raw:
                    return {"status": "ok"}
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise TransportError(f"HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise TransportError(f"network error: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise TransportError(f"invalid JSON response: {exc}") from exc
