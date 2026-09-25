"""
Protocol layer.

Whitepaper: Connection Mgr, Federation, Discovery, Routing.
MVP: message envelope, session auth, connection manager, reconnect.
"""

from nyx_client.protocol.types import (
    MessageEnvelope,
    RelayMetadata,
    ConversationType,
    MessageDirection,
    MessageStatus,
    PROTOCOL_VERSION,
    generate_uuidv7,
    message_id,
    conversation_id_for_dm,
)
from nyx_client.protocol.envelope import (
    build_envelope,
    verify_envelope,
    verify_hash_chain,
)
from nyx_client.protocol.session import Session, SessionState
from nyx_client.protocol.http_transport import HttpTransport
from nyx_client.protocol.discovery import ServerDirectory, ServerInfo, composite_score, measure_latency
from nyx_client.protocol.connection import (
    ConnectionManager,
    Transport,
    MockTransport,
    TransportError,
)

__all__ = [
    "MessageEnvelope",
    "RelayMetadata",
    "ConversationType",
    "MessageDirection",
    "MessageStatus",
    "PROTOCOL_VERSION",
    "generate_uuidv7",
    "message_id",
    "conversation_id_for_dm",
    "build_envelope",
    "verify_envelope",
    "verify_hash_chain",
    "Session",
    "SessionState",
    "ConnectionManager",
    "Transport",
    "MockTransport",
    "TransportError",
    "HttpTransport",
    "ServerDirectory",
    "ServerInfo",
    "composite_score",
    "measure_latency",
]
