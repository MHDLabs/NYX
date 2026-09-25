"""
Application facade — wires all layers into a single lifecycle object.

This is the object the UI and CLI talk to. It owns:
  - Settings
  - Database / stores
  - Identity (loaded or created)
  - MessagingService
  - ConnectionManager (optional)
  - CommandContext

Whitepaper alignment: clean layer boundaries, single entry for startup/shutdown.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from nyx_client.config.settings import Settings, load_settings, ensure_directories
from nyx_client.config.logging import configure_logging, get_logger
from nyx_client.crypto import Identity, create_recoverable_identity
from nyx_client.crypto.aead import generate_key
from nyx_client.storage import Database, ProfileStore, MessageStore, ContactStore
from nyx_client.storage.rooms import RoomStore, Room
from nyx_client.storage.user_prefs import UserPrefs
from nyx_client.core.search import SearchService
from nyx_client.core.directory import Directory, PublicProfile
from nyx_client.core.wallet import Wallet
from nyx_client.core.utxo_ledger import UTXOLedger, MICRO as NYX_MICRO
from nyx_client.core.mint import (
    claim_voucher_to_ledger, load_voucher_file, issue_mint_voucher,
    save_voucher_file, default_mint_pubkey_hex, MintVoucher, mine_voucher,
)
from nyx_client.core.marketplace import Marketplace
from nyx_client.storage.room_roles import RoomRoleStore
from nyx_client.storage.attachments import AttachmentStore
from nyx_client.storage.handles import (
    HandleStore, HandleConflictError, validate_handle_format, normalize_handle,
    KIND_USER, KIND_GROUP, KIND_CHANNEL,
)
from nyx_client.core.media_sessions import MediaSessionStore
from nyx_client.core.messaging import MessagingService
from nyx_client.core.commands import CommandContext, registry, CommandResult
from nyx_client.protocol.connection import ConnectionManager, MockTransport
from nyx_client.protocol.http_transport import HttpTransport
from nyx_client.protocol.discovery import ServerDirectory, ServerInfo
from nyx_client.update.updater import UpdateClient, UpdateCheckResult

log = get_logger(__name__)


class NyxApp:
    """Process-level application object."""

    def __init__(self, settings: Settings, profile_key: bytes) -> None:
        self.settings = settings
        self._profile_key = profile_key
        self.db = Database(settings.storage.database_path())
        self.identity: Optional[Identity] = None
        self.messaging: Optional[MessagingService] = None
        self.contacts: Optional[ContactStore] = None
        self.messages: Optional[MessageStore] = None
        self.connection: Optional[ConnectionManager] = None
        self._profile: Optional[ProfileStore] = None
        self._started = False
        self.last_mnemonic: Optional[str] = None
        self.directory: Optional[ServerDirectory] = None
        self.updater: Optional[UpdateClient] = None
        self.rooms: Optional[RoomStore] = None
        self.search: Optional[SearchService] = None
        self.prefs: Optional[UserPrefs] = None
        self.user_directory: Optional[Directory] = None
        self.wallet: Optional[Wallet] = None
        self.utxo: Optional[UTXOLedger] = None
        self.marketplace: Optional[Marketplace] = None
        self.room_roles: Optional[RoomRoleStore] = None
        self.attachments: Optional[AttachmentStore] = None
        self.media_sessions: Optional[MediaSessionStore] = None
        self.handles: Optional[HandleStore] = None
        self.is_new_identity: bool = False

    @classmethod
    def from_settings(
        cls,
        settings: Optional[Settings] = None,
        profile_key: Optional[bytes] = None,
        profile_key_path: Optional[Path] = None,
    ) -> "NyxApp":
        if settings is None:
            settings = load_settings()
        ensure_directories(settings)
        configure_logging(
            level=settings.logging.level,
            json_logs=settings.logging.json_logs,
        )
        key = profile_key
        if key is None:
            key = cls._load_or_create_profile_key(
                profile_key_path or (settings.data_dir / ".profile_key")
            )
        return cls(settings, key)

    @staticmethod
    def _load_or_create_profile_key(path: Path) -> bytes:
        if path.is_file():
            data = path.read_bytes()
            if len(data) != 32:
                raise ValueError("profile key file must be exactly 32 bytes")
            return data
        key = generate_key()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(key)
        path.chmod(0o600)
        return key

    def start(self) -> Identity:
        """Open DB, load/create identity, wire services. Idempotent."""
        if self._started:
            assert self.identity is not None
            return self.identity

        self.db.connect()
        self._profile = ProfileStore(self.db, self._profile_key)
        self.messages = MessageStore(self.db)
        self.contacts = ContactStore(self.db)
        self.rooms = RoomStore(self.db)
        self.prefs = UserPrefs(self.db)
        self.search = SearchService(self.contacts, self.rooms, self.messages)
        self.user_directory = Directory(self.contacts, self.prefs)

        if self._profile.has_profile():
            identity = self._profile.load_identity()
            if identity is None:
                raise RuntimeError("corrupt profile: decrypt returned None")
            self.last_mnemonic = None
            self.is_new_identity = False
        else:
            bundle = create_recoverable_identity()
            self._profile.save_identity(bundle.identity, recovery_seed=bundle.seed)
            identity = bundle.identity
            self.last_mnemonic = bundle.mnemonic_phrase()
            self.is_new_identity = True
            log.info("app.identity_created", identity=identity.id)

        self.identity = identity
        if self.user_directory is not None:
            self.user_directory.set_self(identity.id, self.prefs)
        self.utxo = UTXOLedger(self.db, identity)
        self.wallet = Wallet(self.db, identity.id, utxo_ledger=self.utxo)
        self.marketplace = Marketplace(
            self.db, self.wallet, identity.id,
            media_root=self.settings.data_dir / "media",
        )
        self.room_roles = RoomRoleStore(self.db)
        media = self.settings.data_dir / "media"
        self.attachments = AttachmentStore(self.db, media)
        self.media_sessions = MediaSessionStore(self.db)
        self.handles = HandleStore(self.db)
        self.messaging = MessagingService(
            identity, self.messages, self.contacts
        )
        self.messaging._room_roles = self.room_roles
        self.directory = ServerDirectory(self.settings.data_dir)
        # Optional preferred server from config
        pref = self.settings.network.default_server
        if pref:
            from nyx_client.protocol.discovery import ServerInfo
            self.directory.upsert(
                ServerInfo(id="config-default", endpoint=pref, trust_level=1, source="config")
            )
            self.directory.save()

        release_keys = self._load_release_keys()
        self.updater = UpdateClient(
            data_dir=self.settings.data_dir,
            channel=self.settings.updates.channel,
            github_manifest_url=getattr(
                self.settings.updates, "github_manifest_url", ""
            ) or "",
            release_public_keys=release_keys,
            auto_install=self.settings.updates.auto_install,
            current_version=__import__("nyx_client", fromlist=["__version__"]).__version__,
        )
        # Extra bootstrap servers from config
        for ep in getattr(self.settings.network, "bootstrap_servers", ()) or ():
            if ep:
                self.directory.upsert(
                    ServerInfo(
                        id="bootstrap-" + str(ep)[:24],
                        endpoint=str(ep),
                        trust_level=1,
                        source="config",
                    )
                )
        self.directory.save()
        self._started = True
        log.info("app.started", identity=identity.id)
        return identity

    def connect_mock(self) -> None:
        """Attach a MockTransport connection (for tests / offline demo)."""
        if self.identity is None:
            raise RuntimeError("call start() first")
        transport = MockTransport()
        self.connection = ConnectionManager(
            self.identity, self.settings.network, transport=transport
        )
        if self.messaging is not None:
            self.messaging._connection = self.connection  # noqa: SLF001

    def command_context(self) -> CommandContext:
        if self.identity is None:
            raise RuntimeError("call start() first")
        connected = bool(
            self.connection
            and self.connection.session
            and self.connection.session.is_authenticated()
        )
        return CommandContext(
            identity_id=self.identity.id,
            server=self.settings.network.default_server,
            connected=connected,
            services={
                "messaging": self.messaging,
                "contacts": self.contacts,
                "app": self,
            },
        )

    def dispatch(self, line: str) -> CommandResult:
        return registry.dispatch(self.command_context(), line)

    def refresh_servers(self, probe: bool = True) -> list:
        """Probe known servers and optionally pull discovery lists from reachable ones."""
        if self.directory is None:
            raise RuntimeError("call start() first")
        if probe:
            self.directory.probe_all(
                timeout=float(self.settings.network.connection_timeout)
            )
        # Ask top reachable relays for more servers
        for s in self.directory.ranked(only_reachable=True)[:3]:
            try:
                self.directory.fetch_from_relay(
                    s.endpoint, timeout=float(self.settings.network.connection_timeout)
                )
            except Exception:
                pass
        if probe:
            self.directory.probe_all(
                timeout=float(self.settings.network.connection_timeout)
            )
        return self.directory.ranked()

    def select_best_server(self) -> Optional[str]:
        if self.directory is None:
            return self.settings.network.default_server
        best = self.directory.best(min_trust=self.settings.security.min_trust_level)
        if best:
            return best.endpoint
        return self.settings.network.default_server

    async def connect_best(self, use_http: bool = True):
        """Connect to the highest-scoring reachable relay."""
        if self.identity is None:
            raise RuntimeError("call start() first")
        endpoint = self.select_best_server()
        transport = HttpTransport(verify_tls=self.settings.security.pin_tls_certificates) if use_http else MockTransport()
        self.connection = ConnectionManager(
            self.identity, self.settings.network, transport=transport
        )
        if self.messaging is not None:
            self.messaging._connection = self.connection
        return await self.connection.connect(endpoint)

    def _load_release_keys(self) -> dict:
        """Load {key_id: raw 32-byte Ed25519 public key} from JSON hex map."""
        path_str = getattr(self.settings.updates, "release_keys_file", "") or ""
        if not path_str:
            return {}
        path = Path(path_str).expanduser()
        if not path.is_file():
            log.warning("update.keys_file_missing", path=str(path))
            return {}
        try:
            import json
            data = json.loads(path.read_text())
            out = {}
            for kid, hex_key in data.items():
                raw = bytes.fromhex(hex_key) if isinstance(hex_key, str) else bytes(hex_key)
                if len(raw) == 32:
                    out[str(kid)] = raw
            return out
        except Exception as exc:
            log.warning("update.keys_load_failed", error=str(exc))
            return {}

    def fetch_relay_update_manifest(self, endpoint: Optional[str] = None) -> Optional[dict]:
        import json
        import urllib.request
        from nyx_client.protocol.discovery import normalize_endpoint
        ep = endpoint or self.select_best_server()
        if not ep:
            return None
        base = normalize_endpoint(ep).rstrip("/")
        url = base + "/api/v3/updates/manifest"
        try:
            req = urllib.request.Request(
                url,
                headers={"Accept": "application/json", "User-Agent": "nyx-client/0.2.0"},
            )
            with urllib.request.urlopen(
                req, timeout=float(self.settings.network.connection_timeout)
            ) as resp:
                return json.loads(resp.read().decode())
        except Exception as exc:
            log.debug("update.relay_manifest_fetch_failed", error=str(exc))
            return None

    def check_updates(self, relay_manifest: Optional[dict] = None) -> UpdateCheckResult:
        if self.updater is None:
            raise RuntimeError("call start() first")
        manifest = relay_manifest
        if manifest is None:
            try:
                manifest = self.fetch_relay_update_manifest()
            except Exception:
                manifest = None
        return self.updater.check(
            relay_manifest=manifest,
            fetch_github=bool(self.updater.github_manifest_url),
        )

    def apply_update(self, manifest_dict: Optional[dict] = None) -> str:
        if self.updater is None:
            raise RuntimeError("call start() first")
        result = self.check_updates(relay_manifest=manifest_dict)
        if not result.update_available or result.candidate is None:
            return "already-current:" + result.current_version
        path = self.updater.download_and_verify(result.candidate)
        self.updater.install(path, result.candidate)
        return result.candidate.version

    def connect_sync(self, endpoint: Optional[str] = None, use_http: bool = True):
        """
        Connect to relay: register identity, authenticate, fetch servers, push profile.
        """
        import asyncio
        if endpoint is None:
            try:
                endpoint = self.select_best_server()
            except Exception:
                endpoint = self.settings.network.default_server

        async def _run():
            if self.identity is None:
                raise RuntimeError("call start() first")
            transport = (
                HttpTransport(verify_tls=self.settings.security.pin_tls_certificates)
                if use_http
                else MockTransport()
            )
            self.connection = ConnectionManager(
                self.identity, self.settings.network, transport
            )
            if self.messaging is not None:
                self.messaging._connection = self.connection
            session = await self.connection.connect(endpoint)
            try:
                session.sync_meta = await self.post_connect_sync(session)
            except Exception as exc:
                log.warning("app.post_connect_sync_failed", error=str(exc))
                session.sync_meta = {}
            return session

        return asyncio.run(_run())


    def create_group(self, title: str, description: str = "") -> Room:
        if not self.identity or not self.rooms:
            raise RuntimeError("not started")
        room = self.rooms.create(
            room_type="private_group",
            title=title,
            description=description,
            owner_id=self.identity.id,
            is_public=False,
        )
        if self.room_roles is not None:
            self.room_roles.set_owner(room.room_id, self.identity.id)
        return room

    def create_channel(self, title: str, description: str = "", public: bool = True) -> Room:
        if not self.identity or not self.rooms:
            raise RuntimeError("not started")
        room = self.rooms.create(
            room_type="public_channel" if public else "private_channel",
            title=title,
            description=description,
            owner_id=self.identity.id,
            is_public=public,
        )
        if self.room_roles is not None:
            self.room_roles.set_owner(room.room_id, self.identity.id)
            # channels default owner_only posting
            from nyx_client.storage.room_roles import POLICY_OWNER_ONLY
            self.room_roles.set_post_policy(room.room_id, POLICY_OWNER_ONLY)
        return room

    def update_room(
        self,
        room_id: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        is_public: Optional[bool] = None,
    ) -> Room:
        if not self.rooms:
            raise RuntimeError("not started")
        if self.room_roles is not None and self.identity is not None:
            self.room_roles.require_owner(room_id, self.identity.id)
        return self.rooms.update_settings(
            room_id, title=title, description=description, is_public=is_public
        )

    def search_directory(self, query: str):
        if not self.search:
            raise RuntimeError("not started")
        return self.search.search(query)



    def _persist_relay_state(self, session) -> None:
        """Save registration/session metadata locally for resume."""
        if self.db is None:
            return
        import time
        import json
        blob = json.dumps({
            "server": session.server,
            "server_identity": getattr(session, "server_identity", None),
            "registered": bool(getattr(session, "registered", False)),
            "expires_at": getattr(session, "expires_at", None),
            "identity": self.identity.id if self.identity else "",
            "saved_at": int(time.time()),
        }).encode("utf-8")
        self.db.execute(
            "INSERT OR REPLACE INTO session_state(key, value, updated_at) VALUES (?, ?, ?)",
            ("relay.registration", blob, int(time.time())),
        )
        if session.session_token:
            self.db.execute(
                "INSERT OR REPLACE INTO session_state(key, value, updated_at) VALUES (?, ?, ?)",
                ("relay.session_token", session.session_token.encode("utf-8"), int(time.time())),
            )
        self.db.commit()

    def _load_relay_state(self) -> dict:
        if self.db is None:
            return {}
        import json
        row = self.db.execute(
            "SELECT value FROM session_state WHERE key = ?", ("relay.registration",)
        ).fetchone()
        if not row:
            return {}
        val = row["value"]
        if isinstance(val, memoryview):
            val = bytes(val)
        if isinstance(val, bytes):
            try:
                return json.loads(val.decode("utf-8"))
            except Exception:
                return {}
        return {}

    async def post_connect_sync(self, session) -> dict:
        """After auth: pull server list, push local profile, persist registration."""
        result = {
            "registered": bool(getattr(session, "registered", False)),
            "servers_fetched": 0,
            "profile_pushed": False,
            "messages_synced": 0,
        }
        timeout = float(self.settings.network.connection_timeout)
        transport = self.connection.transport if self.connection else None
        if transport is None:
            return result

        for path in ("/api/v3/discovery/servers", "/api/v3/trust/servers"):
            try:
                data = await transport.request("GET", path, timeout=timeout)
                servers = data.get("servers") or []
                if self.directory is not None:
                    from nyx_client.protocol.discovery import ServerInfo
                    for s in servers:
                        ep = s.get("endpoint") or ""
                        if not ep:
                            continue
                        self.directory.upsert(
                            ServerInfo(
                                id=str(s.get("id") or ep)[:64],
                                endpoint=ep,
                                trust_level=int(s.get("trust_level") or 1),
                                reputation=float(s.get("reputation") or 0.5),
                                uptime=float(s.get("uptime") or 0.0),
                                capacity=float(s.get("capacity") or 0.5),
                                source="relay",
                            )
                        )
                    self.directory.save()
                result["servers_fetched"] = len(servers)
                break
            except Exception as exc:
                log.warning("app.discovery_fetch_failed", path=path, error=str(exc))

        try:
            if self.prefs is not None and self.identity is not None:
                profile = self.prefs.get_profile()
                body = {
                    "identity": self.identity.id,
                    "display_name": profile.display_name or "",
                    "bio": profile.bio or "",
                    "public_key": self.identity.public_key_bytes.hex(),
                    "recovery_email": profile.recovery_email or "",
                }
                await transport.request("PUT", "/api/v3/profile", body=body, timeout=timeout)
                result["profile_pushed"] = True
        except Exception as exc:
            log.warning("app.profile_push_failed", error=str(exc))

        # One pull of stored messages since cursor (relay is store-only, no push queue)
        if self.messaging is not None:
            try:
                self.messaging._connection = self.connection
                sync_res = self.messaging.sync_inbox()
                result["messages_synced"] = int(sync_res.get("ingested") or 0)
                result["messages_pulled"] = int(sync_res.get("pulled") or 0)
            except Exception as exc:
                log.warning("app.message_sync_failed", error=str(exc))

        self._persist_relay_state(session)
        return result


    def set_user_handle(self, handle: str, claimed_at_ms: int | None = None) -> str:
        if not self.handles or not self.identity:
            raise RuntimeError("not started")
        import time as _t
        ts = claimed_at_ms if claimed_at_ms is not None else int(_t.time() * 1000)
        rec = self.handles.claim(
            handle, KIND_USER, self.identity.id, self.identity.id, claimed_at_ms=ts
        )
        self._push_handle_to_relay(
            rec.handle, rec.kind, rec.target_id, claimed_at_ms=rec.claimed_at_ms
        )
        return rec.handle

    def set_room_handle(
        self, room_id: str, handle: str, claimed_at_ms: int | None = None
    ) -> str:
        if not self.handles or not self.identity or not self.rooms or not self.room_roles:
            raise RuntimeError("not started")
        room = self.rooms.get(room_id)
        if room is None:
            raise ValueError("room not found")
        self.room_roles.require_owner(room_id, self.identity.id)
        kind = KIND_GROUP if "group" in room.room_type else KIND_CHANNEL
        import time as _t
        ts = claimed_at_ms if claimed_at_ms is not None else int(_t.time() * 1000)
        rec = self.handles.claim(
            handle, kind, room_id, self.identity.id, claimed_at_ms=ts
        )
        self._push_handle_to_relay(
            rec.handle, rec.kind, rec.target_id, claimed_at_ms=rec.claimed_at_ms
        )
        return rec.handle

    def check_handle_available(self, handle: str) -> dict:
        """
        Live validation result for UI:
          ok / format_error / taken_local / taken_remote / available
        """
        err = validate_handle_format(handle)
        if err:
            return {"status": "invalid", "message": err, "handle": normalize_handle(handle)}
        h = normalize_handle(handle)
        if self.handles and self.handles.get(h):
            rec = self.handles.get(h)
            mine = bool(self.identity and rec and rec.target_id in (
                self.identity.id,
            ))
            # also if room owned
            return {
                "status": "taken_local" if not mine else "yours",
                "message": "already registered locally" if not mine else "this is your handle",
                "handle": h,
            }
        # remote check
        remote = self._relay_check_handle(h)
        if remote.get("available") is False:
            return {"status": "taken_remote", "message": "taken on relay", "handle": h}
        if remote.get("error"):
            return {"status": "unknown", "message": remote["error"], "handle": h}
        return {"status": "available", "message": "available", "handle": h}

    def resolve_handle(self, name: str) -> str:
        if self.handles:
            return self.handles.resolve(name) or name
        return name

    def _relay_check_handle(self, handle: str) -> dict:
        if not (self.connection and self.connection.session and self.connection.session.is_authenticated()):
            return {"available": True, "offline": True}
        try:
            import asyncio
            async def _c():
                return await self.connection.transport.request(
                    "GET",
                    f"/api/v3/handles/check?name={handle}",
                    timeout=float(self.settings.network.connection_timeout),
                )
            data = asyncio.run(_c())
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            return {"error": str(exc)}

    def _push_handle_to_relay(
        self,
        handle: str,
        kind: str,
        target_id: str,
        claimed_at_ms: int = 0,
    ) -> None:
        if not (self.connection and self.connection.session and self.connection.session.is_authenticated()):
            return
        try:
            import asyncio
            body = {
                "handle": handle,
                "kind": kind,
                "target_id": target_id,
                "owner_id": self.identity.id if self.identity else "",
                "public_key": self.identity.public_key_bytes.hex() if self.identity else "",
                "claimed_at_ms": claimed_at_ms,
            }
            async def _p():
                return await self.connection.transport.request(
                    "POST", "/api/v3/handles/claim", body=body,
                    timeout=float(self.settings.network.connection_timeout),
                )
            resp = asyncio.run(_p())
            if isinstance(resp, dict) and resp.get("status") in ("conflict", "taken"):
                raise HandleConflictError(
                    handle,
                    str(resp.get("winner_target") or ""),
                    int(resp.get("winner_claimed_at_ms") or 0),
                )
        except HandleConflictError:
            raise
        except Exception as exc:
            log.warning("handle.push_failed", error=str(exc))

    def trusted_mint_pubkeys(self) -> set:
        from nyx_client.core.mint import default_mint_pubkey_hex
        keys = {default_mint_pubkey_hex()}
        raw = getattr(self.settings, "token", None)
        if raw is not None and getattr(raw, "mint_pubkeys", ""):
            for part in str(raw.mint_pubkeys).split(","):
                part = part.strip()
                if part:
                    keys.add(part)
        return keys

    def claim_mint_voucher(self, voucher_path_or_dict) -> dict:
        """Claim a mined/signed mint voucher into the UTXO wallet."""
        if self.utxo is None or self.identity is None:
            raise RuntimeError("wallet not ready")
        from pathlib import Path as P
        if isinstance(voucher_path_or_dict, (str, P)):
            voucher = load_voucher_file(P(voucher_path_or_dict))
        elif isinstance(voucher_path_or_dict, dict):
            voucher = MintVoucher.from_dict(voucher_path_or_dict)
        else:
            voucher = voucher_path_or_dict
        trusted = self.trusted_mint_pubkeys()
        # anti-spam: amount cap
        max_nyx = float(getattr(getattr(self.settings, "token", None), "max_claim_nyx", 1_000_000) or 1_000_000)
        if voucher.amount_micro / NYX_MICRO > max_nyx:
            raise ValueError("voucher amount exceeds max_claim_nyx policy")
        txid = claim_voucher_to_ledger(
            self.utxo, self.identity, voucher, trusted_mint_pubkeys=trusted
        )
        return {
            "txid": txid,
            "amount_nyx": voucher.amount_micro / NYX_MICRO,
            "balance": self.wallet.format_balance() if self.wallet else "",
            "mint_pubkey": voucher.mint_pubkey[:16] + "…",
        }

    def issue_test_mint(self, amount_nyx: float, memo: str = "mined") -> dict:
        """
        Local self-mint — DISABLED unless settings.token.allow_local_mine=true.

        Public users must obtain a server-signed voucher (relay mined for their
        identity) and /claimmint it. This prevents free spam coin creation.
        """
        if self.identity is None:
            raise RuntimeError("no identity")
        tok = getattr(self.settings, "token", None)
        if tok is None or not getattr(tok, "allow_local_mine", False):
            raise PermissionError(
                "local mining disabled — import a server-signed voucher with /claimmint "
                "or set token.allow_local_mine=true only on operator nodes"
            )
        v = mine_voucher(
            amount_nyx=amount_nyx,
            recipient_id=self.identity.id,
            memo=memo,
            difficulty_bits=10,
        )
        path = self.settings.data_dir / "wallet" / f"{v.voucher_id}.voucher.json"
        save_voucher_file(v, path)
        claimed = self.claim_mint_voucher(v)
        claimed["voucher_path"] = str(path)
        return claimed

    def fetch_mint_from_relay(self) -> dict:
        """
        Ask the connected relay for vouchers mined for this identity.
        Relay: GET /api/v3/token/vouchers/pending
        """
        if not (self.connection and self.connection.session and self.connection.session.is_authenticated()):
            raise RuntimeError("connect to relay first")
        if self.identity is None:
            raise RuntimeError("no identity")
        import asyncio
        transport = self.connection.transport
        token = self.connection.session.session_token
        headers = {"Authorization": f"Bearer {token}"} if token else {}

        async def _pull():
            return await transport.request(
                "GET",
                "/api/v3/token/vouchers/pending",
                headers=headers,
                timeout=float(self.settings.network.connection_timeout),
            )
        data = asyncio.run(_pull())
        vouchers = (data or {}).get("vouchers") or []
        claimed = []
        for raw in vouchers:
            try:
                r = self.claim_mint_voucher(raw)
                claimed.append(r)
            except Exception as exc:
                log.warning("token.voucher_claim_failed", error=str(exc))
        return {"claimed": len(claimed), "items": claimed, "balance": self.wallet.format_balance() if self.wallet else ""}

    def transfer_nyx(self, to_identity: str, amount_nyx: float, memo: str = "") -> dict:
        """Build, sign, apply UTXO transfer; broadcast to relay when connected."""
        if self.utxo is None:
            raise RuntimeError("UTXO ledger not ready")
        to_identity = self.resolve_handle(to_identity)
        amount_micro = int(round(float(amount_nyx) * NYX_MICRO))
        stx = self.utxo.transfer(to_identity, amount_micro, memo=memo)
        # best-effort relay broadcast
        broadcast_ok = False
        if self.connection and self.connection.session and self.connection.session.is_authenticated():
            try:
                import asyncio
                async def _bc():
                    return await self.connection.transport.request(
                        "POST",
                        "/api/v3/token/broadcast",
                        body=stx.raw,
                        timeout=float(self.settings.network.connection_timeout),
                    )
                asyncio.run(_bc())
                broadcast_ok = True
            except Exception as exc:
                log.warning("token.broadcast_failed", error=str(exc))
        return {"txid": stx.txid, "broadcast": broadcast_ok, "raw": stx.raw}

    def buy_listing(self, listing_id: str):
        if self.marketplace is None or self.utxo is None:
            raise RuntimeError("marketplace not ready")
        def _spend(seller, amount_micro, memo):
            self.utxo.transfer(seller, amount_micro, memo=memo)
        return self.marketplace.buy(listing_id, spend_fn=_spend)


    def get_user_profile(self, identity_id: str) -> PublicProfile:
        if self.user_directory is None:
            raise RuntimeError("not started")
        return self.user_directory.profile(identity_id)

    def set_contact_profile(
        self,
        identity_id: str,
        display_name: Optional[str] = None,
        bio: Optional[str] = None,
    ) -> None:
        if self.user_directory is None:
            raise RuntimeError("not started")
        self.user_directory.set_remote_profile(
            identity_id, display_name=display_name, bio=bio
        )

    def stop(self) -> None:

        if self.connection is not None:
            # Best-effort sync disconnect mark
            if self.connection.session:
                self.connection.session.mark_disconnected("app_stop")
        self.db.close()
        self._started = False
        log.info("app.stopped")
