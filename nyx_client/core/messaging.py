"""
Messaging service — application layer for direct messages.

Orchestrates:
  crypto (E2EE) → protocol (envelope) → storage (history) → transport (send)

Whitepaper MVP scope: DMs only. Groups/channels are extension points.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from nyx_client.crypto.identity import Identity
from nyx_client.crypto.keys import X25519KeyPair
from nyx_client.core.e2ee import DMSession, open_dm_session, generate_dm_keypair
from nyx_client.protocol.types import (
    MessageEnvelope,
    MessageDirection,
    MessageStatus,
    conversation_id_for_dm,
)
from nyx_client.protocol.envelope import build_envelope, verify_envelope, verify_hash_chain
from nyx_client.protocol.connection import ConnectionManager, TransportError
from nyx_client.storage.messages import MessageStore, StoredMessage
from nyx_client.storage.contacts import ContactStore, Contact
from nyx_client.config.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DecryptedMessage:
    """Application-level view of a message after verification + decryption."""

    message_id: str
    conversation_id: str
    sender_id: str
    plaintext: bytes
    sequence: int
    timestamp: int
    direction: MessageDirection
    verified: bool


class MessagingService:
    """
    High-level DM API used by the UI and command layer.

    Responsibilities:
      - Resolve / create conversations
      - Manage per-conversation E2EE sessions
      - Build signed envelopes
      - Persist history
      - Send via ConnectionManager transport
      - Ingest inbound envelopes (verify → decrypt → store)
    """

    def __init__(
        self,
        identity: Identity,
        message_store: MessageStore,
        contact_store: ContactStore,
        connection: Optional[ConnectionManager] = None,
    ) -> None:
        self._identity = identity
        self._messages = message_store
        self._contacts = contact_store
        self._connection = connection
        self._sessions: Dict[str, DMSession] = {}
        # Local X25519 key for DM key agreement — persisted in session_state
        self._dm_x25519 = self._load_or_create_dm_key()
        # Peer X25519 public keys (contact_id -> raw bytes)
        self._peer_keys: Dict[str, bytes] = {}
        self._load_peer_keys()
        self._plaintext_cache: Dict[str, bytes] = {}
        self._load_plaintext_cache()

    @property
    def dm_public_key(self) -> bytes:
        """Local X25519 public key to publish / share with peers."""
        return self._dm_x25519.public_bytes()


    def _load_or_create_dm_key(self) -> X25519KeyPair:
        """Load persisted DM X25519 key or generate and store a new one."""
        import time
        row = self._messages._db.execute(
            "SELECT value FROM session_state WHERE key = ?",
            ("dm_x25519_private",),
        ).fetchone()
        if row is not None:
            return X25519KeyPair.from_private_bytes(bytes(row["value"]))
        kp = generate_dm_keypair()
        now = int(time.time())
        self._messages._db.execute(
            "INSERT OR REPLACE INTO session_state(key, value, updated_at) VALUES (?, ?, ?)",
            ("dm_x25519_private", kp.private_bytes(), now),
        )
        self._messages._db.commit()
        log.info("messaging.dm_key_created")
        return kp

    def _load_peer_keys(self) -> None:
        """Load peer X25519 keys stored as contact public metadata (MVP: session_state)."""
        rows = self._messages._db.execute(
            "SELECT key, value FROM session_state WHERE key LIKE ?",
            ("peer_x25519:%",),
        ).fetchall()
        for row in rows:
            peer_id = row["key"].split(":", 1)[1]
            self._peer_keys[peer_id] = bytes(row["value"])


    def _load_plaintext_cache(self) -> None:
        rows = self._messages._db.execute(
            "SELECT key, value FROM session_state WHERE key LIKE ?",
            ("pt:%",),
        ).fetchall()
        for row in rows:
            mid = row["key"][3:]
            self._plaintext_cache[mid] = bytes(row["value"])

    def _cache_plaintext(self, message_id: str, plaintext: bytes) -> None:
        import time
        self._plaintext_cache[message_id] = plaintext
        self._messages._db.execute(
            "INSERT OR REPLACE INTO session_state(key, value, updated_at) VALUES (?, ?, ?)",
            ("pt:" + message_id, plaintext, int(time.time())),
        )
        self._messages._db.commit()

    def register_peer_key(self, peer_identity: str, x25519_public: bytes) -> None:
        """
        Register a peer's X25519 public key (from contact exchange or prekey).

        Required before sending encrypted DMs to that peer.
        Persisted in session_state so history remains decryptable after restart.
        """
        if len(x25519_public) != 32:
            raise ValueError("X25519 public key must be 32 bytes")
        self._peer_keys[peer_identity] = x25519_public
        import time
        now = int(time.time())
        self._messages._db.execute(
            "INSERT OR REPLACE INTO session_state(key, value, updated_at) VALUES (?, ?, ?)",
            ("peer_x25519:" + peer_identity, x25519_public, now),
        )
        self._messages._db.commit()
        conv = conversation_id_for_dm(self._identity.id, peer_identity)
        self._sessions.pop(conv, None)
        log.info("messaging.peer_key_registered", peer=peer_identity[:24])

    def _get_or_create_session(
        self, peer_identity: str, *, initiator: bool = True
    ) -> DMSession:
        conv = conversation_id_for_dm(self._identity.id, peer_identity)
        if conv in self._sessions:
            return self._sessions[conv]
        peer_pub = self._peer_keys.get(peer_identity)
        if peer_pub is None:
            # Provisional local key so DM/history/files work before a real
            # X25519 exchange (profile push / prekey). Replaced via register_peer_key.
            import hashlib
            from nyx_client.crypto.keys import X25519KeyPair
            seed = hashlib.sha256(
                b"nyx-provisional-x25519-v1|" + peer_identity.encode("utf-8")
            ).digest()
            peer_pub = X25519KeyPair.from_private_bytes(seed).public_bytes()
            self.register_peer_key(peer_identity, peer_pub)
        session = open_dm_session(
            conversation_id=conv,
            local_identity=self._identity.id,
            peer_identity=peer_identity,
            local_x25519=self._dm_x25519,
            peer_x25519_public=peer_pub,
            initiator=initiator,
        )
        self._sessions[conv] = session
        return session

    def ensure_contact(
        self,
        peer_identity: str,
        display_name: Optional[str] = None,
        public_key: Optional[bytes] = None,
    ) -> Contact:
        return self._contacts.upsert(
            identity_id=peer_identity,
            display_name=display_name,
            public_key=public_key,
        )

    def send_dm(self, peer_identity: str, plaintext: bytes) -> MessageEnvelope:
        """
        Encrypt, sign, store, and optionally transmit a direct message.

        Returns the signed envelope. Raises RuntimeError if peer key missing.
        """
        if not peer_identity:
            raise ValueError("peer_identity is required")
        if not plaintext:
            raise ValueError("plaintext must not be empty")

        session = self._get_or_create_session(peer_identity)
        conv = session.conversation_id
        self._messages.ensure_conversation(conv, peer_id=peer_identity, conv_type="dm")

        sequence = self._messages.last_sequence(conv) + 1
        prev_hash = self._previous_hash(conv)

        ciphertext = session.encrypt(plaintext, sequence)
        envelope = build_envelope(
            identity=self._identity,
            conversation_id=conv,
            ciphertext=ciphertext,
            sequence=sequence,
            previous_hash=prev_hash,
        )
        envelope.status = MessageStatus.SENT

        self._persist(envelope, direction=MessageDirection.OUT)
        self._cache_plaintext(envelope.message_id, plaintext)
        log.info(
            "messaging.dm_sent",
            message_id=envelope.message_id,
            peer=peer_identity[:24],
            sequence=sequence,
        )

        # Best-effort network send (offline-capable: already stored)
        if self._connection is not None:
            try:
                self._transmit(envelope, recipient_id=peer_identity)
            except TransportError as exc:
                envelope.status = MessageStatus.FAILED
                log.warning("messaging.transmit_failed", error=str(exc))

        return envelope


    def send_room_message(self, room_id: str, plaintext: bytes) -> MessageEnvelope:
        """
        Post a message into a local group/channel conversation.

        MVP: content is stored for local display; multi-member E2EE sender
        keys are an extension point. Signature still binds the sender identity.
        """
        if not room_id:
            raise ValueError("room_id is required")
        if not plaintext:
            raise ValueError("plaintext must not be empty")
        if getattr(self, "_room_roles", None) is not None:
            if not self._room_roles.can_post(room_id, self._identity.id):
                raise PermissionError("you are not allowed to post in this room")

        conv_type = "private_group"
        # Prefer title/type already in conversations table
        self._messages.ensure_conversation(room_id, conv_type=conv_type)
        sequence = self._messages.last_sequence(room_id) + 1
        prev_hash = self._previous_hash(room_id)

        # Room MVP payload: plaintext framed (not peer-E2EE yet)
        # Prefix so history loader can detect room local messages
        framed = b"nyx-room-v1:" + plaintext
        envelope = build_envelope(
            identity=self._identity,
            conversation_id=room_id,
            ciphertext=framed,
            sequence=sequence,
            previous_hash=prev_hash,
        )
        envelope.status = MessageStatus.SENT
        self._persist(envelope, direction=MessageDirection.OUT)
        self._cache_plaintext(envelope.message_id, plaintext)
        log.info(
            "messaging.room_sent",
            message_id=envelope.message_id,
            room_id=room_id[:24],
            sequence=sequence,
        )
        if self._connection is not None:
            try:
                self._transmit(envelope)
            except TransportError as exc:
                envelope.status = MessageStatus.FAILED
                log.warning("messaging.room_transmit_failed", error=str(exc))
        return envelope

    def history_conversation(
        self,
        conversation_id: str,
        limit: int = 80,
        peer_identity: Optional[str] = None,
    ) -> List[DecryptedMessage]:
        """Load history for DM (needs peer) or room (local framed payload)."""
        stored = self._messages.history(conversation_id, limit=limit)
        if not stored:
            return []
        result: List[DecryptedMessage] = []
        session = None
        if peer_identity:
            try:
                session = self._get_or_create_session(peer_identity)
            except RuntimeError:
                session = None
        for sm in stored:
            plaintext = self._plaintext_cache.get(sm.message_id, b"")
            verified = bool(plaintext)
            if not plaintext and sm.payload.startswith(b"nyx-room-v1:"):
                plaintext = sm.payload[len(b"nyx-room-v1:"):]
                verified = True
                self._cache_plaintext(sm.message_id, plaintext)
            elif not plaintext and session is not None:
                try:
                    plaintext = session.decrypt(sm.payload, sm.sequence)
                    verified = True
                    self._cache_plaintext(sm.message_id, plaintext)
                except Exception:
                    plaintext = b"[encrypted]"
            elif not plaintext:
                plaintext = b"[encrypted]"
            result.append(
                DecryptedMessage(
                    message_id=sm.message_id,
                    conversation_id=sm.conversation_id,
                    sender_id=sm.sender_id,
                    plaintext=plaintext,
                    sequence=sm.sequence,
                    timestamp=sm.timestamp,
                    direction=MessageDirection(sm.direction),
                    verified=verified,
                )
            )
        return result

    def ingest_envelope(
        self,
        envelope: MessageEnvelope,
        peer_x25519_public: Optional[bytes] = None,
    ) -> DecryptedMessage:
        """
        Verify, decrypt, and store an inbound envelope.

        peer_x25519_public may be supplied if not already registered.
        """
        if not verify_envelope(envelope):
            raise ValueError("envelope signature verification failed")

        peer = envelope.sender_id
        if peer_x25519_public is not None:
            self.register_peer_key(peer, peer_x25519_public)

        # Hash-chain check against last stored message
        hist = self._messages.history(envelope.conversation_id, limit=1)
        prev_env = None
        if hist:
            prev_env = self._stored_to_envelope(hist[-1])
            if not verify_hash_chain(envelope, prev_env):
                log.warning(
                    "messaging.hash_chain_break",
                    message_id=envelope.message_id,
                )
                # Still accept but mark; full policy can reject later

        session = self._get_or_create_session(peer, initiator=False)
        plaintext = session.decrypt(envelope.ciphertext, envelope.sequence)

        envelope.direction = MessageDirection.IN
        envelope.status = MessageStatus.DELIVERED
        self._persist(envelope, direction=MessageDirection.IN)
        self._cache_plaintext(envelope.message_id, plaintext)
        try:
            self.ensure_contact(peer)
        except Exception:
            pass

        log.info(
            "messaging.dm_received",
            message_id=envelope.message_id,
            sender=peer[:24],
            sequence=envelope.sequence,
        )
        return DecryptedMessage(
            message_id=envelope.message_id,
            conversation_id=envelope.conversation_id,
            sender_id=envelope.sender_id,
            plaintext=plaintext,
            sequence=envelope.sequence,
            timestamp=envelope.timestamp,
            direction=MessageDirection.IN,
            verified=True,
        )

    def history(
        self,
        peer_identity: str,
        limit: int = 50,
    ) -> List[DecryptedMessage]:
        """Return decrypted history for a DM conversation (newest limit)."""
        conv = conversation_id_for_dm(self._identity.id, peer_identity)
        stored = self._messages.history(conv, limit=limit)
        if not stored:
            return []

        session: Optional[DMSession] = None
        try:
            session = self._get_or_create_session(peer_identity)
        except RuntimeError:
            pass  # peer key unknown — return undecrypted markers

        result: List[DecryptedMessage] = []
        for sm in stored:
            verified = False
            plaintext = self._plaintext_cache.get(sm.message_id, b"")
            if plaintext:
                verified = True
            else:
                plaintext = b"[encrypted - open session to read]"

            result.append(
                DecryptedMessage(
                    message_id=sm.message_id,
                    conversation_id=sm.conversation_id,
                    sender_id=sm.sender_id,
                    plaintext=plaintext,
                    sequence=sm.sequence,
                    timestamp=sm.timestamp,
                    direction=MessageDirection(sm.direction),
                    verified=verified,
                )
            )
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _previous_hash(self, conversation_id: str) -> Optional[str]:
        hist = self._messages.history(conversation_id, limit=1)
        if not hist:
            return None
        env = self._stored_to_envelope(hist[-1])
        return env.content_hash()

    def _persist(self, envelope: MessageEnvelope, direction: MessageDirection) -> None:
        self._messages.ensure_conversation(
            envelope.conversation_id,
            peer_id=(
                envelope.sender_id
                if direction == MessageDirection.IN
                else None
            ),
        )
        self._messages.insert(
            StoredMessage(
                message_id=envelope.message_id,
                conversation_id=envelope.conversation_id,
                sender_id=envelope.sender_id,
                device_id=envelope.device_id,
                payload=envelope.ciphertext,
                signature=envelope.signature,
                previous_hash=envelope.previous_hash,
                sequence=envelope.sequence,
                timestamp=envelope.timestamp,
                direction=direction.value,
                status=envelope.status.value,
                protocol_version=envelope.protocol_version,
            )
        )

    def _stored_to_envelope(self, sm: StoredMessage) -> MessageEnvelope:
        return MessageEnvelope(
            message_id=sm.message_id,
            sender_id=sm.sender_id,
            device_id=sm.device_id or "",
            conversation_id=sm.conversation_id,
            timestamp=sm.timestamp,
            sequence=sm.sequence,
            ciphertext=sm.payload,
            signature=sm.signature or b"",
            previous_hash=sm.previous_hash,
            protocol_version=sm.protocol_version,
            direction=MessageDirection(sm.direction),
            status=MessageStatus(sm.status),
        )

    def _transmit(self, envelope: MessageEnvelope, recipient_id: str | None = None) -> None:
        """
        POST envelope to relay for durable storage.

        Relay only appends the message to conversation history (no online
        presence, no per-user delivery queue). Recipients pull on connect.
        """
        if self._connection is None or self._connection.session is None:
            raise TransportError("no active connection")
        if not self._connection.session.is_authenticated():
            raise TransportError("session not authenticated")

        transport = self._connection.transport
        token = self._connection.session.session_token
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        body = envelope.to_wire_dict()
        if recipient_id:
            body["recipient_id"] = recipient_id

        async def _send():
            return await transport.request(
                "POST",
                "/api/v3/messages/send",
                body=body,
                headers=headers,
                timeout=30.0,
            )

        _run_coro(_send())
        log.info("messaging.transmitted", message_id=envelope.message_id)

    def _sync_cursor(self) -> int:
        row = self._messages._db.execute(
            "SELECT value FROM session_state WHERE key = ?",
            ("sync.messages_since",),
        ).fetchone()
        if not row:
            return 0
        val = row["value"]
        if isinstance(val, memoryview):
            val = bytes(val)
        if isinstance(val, bytes):
            val = val.decode("utf-8", errors="replace")
        try:
            return int(val)
        except ValueError:
            return 0

    def _set_sync_cursor(self, since: int) -> None:
        import time
        self._messages._db.execute(
            "INSERT OR REPLACE INTO session_state(key, value, updated_at) VALUES (?, ?, ?)",
            ("sync.messages_since", str(int(since)).encode("utf-8"), int(time.time())),
        )
        self._messages._db.commit()


    def sync_inbox(self, limit: int = 200) -> dict:
        """
        Pull all new envelopes since the local cursor and ingest them.

        Called automatically once on every successful connect. The relay is a
        simple store: it does not track online users or run delivery queues.
        Each client is responsible for pulling when it comes online.
        """
        if self._connection is None or self._connection.session is None:
            return {"pulled": 0, "ingested": 0, "error": "not connected"}
        if not self._connection.session.is_authenticated():
            return {"pulled": 0, "ingested": 0, "error": "not authenticated"}

        since = self._sync_cursor()
        transport = self._connection.transport
        token = self._connection.session.session_token
        headers = {"Authorization": f"Bearer {token}"} if token else {}

        try:
            data = _run_coro(
                transport.request(
                    "GET",
                    f"/api/v3/messages/sync?since={since}&limit={limit}",
                    headers=headers,
                    timeout=30.0,
                )
            )
        except Exception as exc:
            log.warning("messaging.sync_failed", error=str(exc))
            return {"pulled": 0, "ingested": 0, "error": str(exc)}

        if not isinstance(data, dict):
            data = {}
        messages = data.get("messages") or []
        ingested = 0
        acked: list = []
        max_ts = since
        from nyx_client.protocol.types import MessageEnvelope, MessageDirection, MessageStatus

        for raw in messages:
            if not isinstance(raw, dict):
                continue
            try:
                env = MessageEnvelope.from_wire_dict(raw)
                env.direction = MessageDirection.IN
                env.status = MessageStatus.DELIVERED
                self.ingest_envelope(env)
                ingested += 1
                mid = raw.get("message_id") or env.message_id
                if mid:
                    acked.append(mid)
                ts = int(raw.get("timestamp") or 0)
                if ts > max_ts:
                    max_ts = ts
            except Exception as exc:
                log.warning("messaging.ingest_failed", error=str(exc))

        next_since = int(data.get("next_since") or max_ts or since)
        if next_since > since:
            self._set_sync_cursor(next_since)
        if acked:
            try:
                _run_coro(
                    transport.request(
                        "POST",
                        "/api/v3/messages/ack",
                        body={"message_ids": acked},
                        headers=headers,
                        timeout=15.0,
                    )
                )
            except Exception as exc:
                log.warning("messaging.ack_failed", error=str(exc))
        log.info("messaging.sync_done", pulled=len(messages), ingested=ingested)
        return {"pulled": len(messages), "ingested": ingested, "since": next_since}



def _run_coro(coro):
    import asyncio
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result(timeout=60)
