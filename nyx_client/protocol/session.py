"""
Session and authentication state.

Whitepaper Section 13 / 53:
  - Registration announces identity + capabilities (signed)
  - Session established with device key signature
  - Session token used for subsequent API calls
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from nyx_client.crypto.identity import Identity
from nyx_client.config.logging import get_logger

log = get_logger(__name__)


class SessionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    AUTHENTICATING = "authenticating"
    AUTHENTICATED = "authenticated"
    RECONNECTING = "reconnecting"
    FAILED = "failed"


@dataclass
class Session:
    """
    In-memory session with a single relay.

    Multi-relay scoring (whitepaper Section 14) is an extension point;
    MVP holds one active session.
    """

    server: str
    identity: Identity
    state: SessionState = SessionState.DISCONNECTED
    session_token: Optional[str] = None
    server_identity_key: Optional[bytes] = None
    connected_at: Optional[float] = None
    last_error: Optional[str] = None
    reconnect_attempt: int = 0

    def is_authenticated(self) -> bool:
        return self.state == SessionState.AUTHENTICATED and bool(self.session_token)

    def mark_connecting(self) -> None:
        self.state = SessionState.CONNECTING
        self.last_error = None

    def mark_authenticating(self) -> None:
        self.state = SessionState.AUTHENTICATING

    def mark_authenticated(self, token: str) -> None:
        self.session_token = token
        self.state = SessionState.AUTHENTICATED
        self.connected_at = time.time()
        self.reconnect_attempt = 0
        self.last_error = None
        log.info("session.authenticated", server=self.server, identity=self.identity.id)

    def mark_disconnected(self, reason: str = "") -> None:
        self.state = SessionState.DISCONNECTED
        self.session_token = None
        self.connected_at = None
        if reason:
            self.last_error = reason
        log.info("session.disconnected", server=self.server, reason=reason or "clean")

    def mark_reconnecting(self) -> None:
        self.state = SessionState.RECONNECTING
        self.reconnect_attempt += 1
        self.session_token = None

    def mark_failed(self, error: str) -> None:
        self.state = SessionState.FAILED
        self.last_error = error
        self.session_token = None
        log.error("session.failed", server=self.server, error=error)

    def auth_payload(self) -> dict[str, Any]:
        """
        Build the signed authentication payload for /api/v3/auth/session.

        The server verifies the signature against the claimed identity.
        """
        device = self.identity.primary_device()
        if device is None:
            raise RuntimeError("no active device for authentication")

        ts = int(time.time() * 1000)
        body = {
            "identity": self.identity.id,
            "device_id": device.device_id,
            "device_public_key": device.public_bytes().hex(),
            "timestamp": ts,
            "protocol_version": 3,
        }
        # Sign canonical form with identity key
        canonical = (
            f"{body['identity']}|{body['device_id']}|"
            f"{body['device_public_key']}|{body['timestamp']}|{body['protocol_version']}"
        ).encode()
        body["signature"] = self.identity.sign(canonical).hex()
        return body
