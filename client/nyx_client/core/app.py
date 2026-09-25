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
        self.messaging = MessagingService(
            identity, self.messages, self.contacts
        )
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
        import asyncio
        if endpoint is None:
            endpoint = self.select_best_server()

        async def _run():
            if self.identity is None:
                raise RuntimeError("call start() first")
            transport = (
                HttpTransport(verify_tls=self.settings.security.pin_tls_certificates)
                if use_http
                else MockTransport()
            )
            self.connection = ConnectionManager(
                self.identity, self.settings.network, transport=transport
            )
            if self.messaging is not None:
                self.messaging._connection = self.connection
            return await self.connection.connect(endpoint)

        return asyncio.run(_run())

    def create_group(self, title: str, description: str = "") -> Room:
        if not self.identity or not self.rooms:
            raise RuntimeError("not started")
        return self.rooms.create(
            room_type="private_group",
            title=title,
            description=description,
            owner_id=self.identity.id,
            is_public=False,
        )

    def create_channel(self, title: str, description: str = "", public: bool = True) -> Room:
        if not self.identity or not self.rooms:
            raise RuntimeError("not started")
        return self.rooms.create(
            room_type="public_channel" if public else "private_channel",
            title=title,
            description=description,
            owner_id=self.identity.id,
            is_public=public,
        )

    def update_room(
        self,
        room_id: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        is_public: Optional[bool] = None,
    ) -> Room:
        if not self.rooms:
            raise RuntimeError("not started")
        return self.rooms.update_settings(
            room_id, title=title, description=description, is_public=is_public
        )

    def search_directory(self, query: str):
        if not self.search:
            raise RuntimeError("not started")
        return self.search.search(query)


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
