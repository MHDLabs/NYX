"""
Typed configuration model and loader for the NYX client.

Design decisions (validated against whitepaper):
  - Immutable after load (frozen dataclasses) so that no component can
    accidentally mutate shared state.
  - Strict validation: unknown keys are rejected, types are checked.
  - XDG Base Directory compliance for config and data paths.
  - Environment variable overrides for CI / container deployments
    (NYX_ prefix).
  - Zero external TOML parser dependency at import time; tomllib is
    stdlib on Python 3.11+.

Extension points:
  - Additional sections can be added without breaking existing callers.
  - Future ConfigWatcher can re-load without changing the public API.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Mapping, TypeVar, get_args, get_origin, get_type_hints

from nyx_client.config.defaults import (
    CONFIG_DIR_NAME,
    CONFIG_FILE_NAME,
    DATA_DIR_NAME,
    DEFAULTS,
    SHORTCUTS_FILE_NAME,
)
from nyx_client.config.logging import get_logger

log = get_logger(__name__)

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Typed sections (frozen = immutable after construction)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class IdentitySettings:
    key_storage: str = "encrypted-sqlite"
    auto_lock_timeout: int = 300


@dataclass(frozen=True, slots=True)
class NetworkSettings:
    default_server: str = "nyx://relay1.nyx.network"
    max_relays: int = 5
    connection_timeout: int = 10
    reconnect_min_backoff: float = 1.0
    reconnect_max_backoff: float = 60.0
    reconnect_max_attempts: int = 0
    bootstrap_servers: tuple = ()  # extra endpoints from config


@dataclass(frozen=True, slots=True)
class SecuritySettings:
    require_verified_relay: bool = True
    min_trust_level: int = 1
    check_revocations_on_startup: bool = True
    pin_tls_certificates: bool = True


@dataclass(frozen=True, slots=True)
class UpdateSettings:
    auto_check: bool = True
    check_interval_hours: int = 24
    auto_install: bool = False
    channel: str = "stable"
    github_manifest_url: str = ""
    release_keys_file: str = ""  # path to JSON {key_id: hex_pubkey}


@dataclass(frozen=True, slots=True)
class PrivacySettings:
    send_read_receipts: bool = False
    share_online_status: bool = False
    minimize_metadata: bool = True


@dataclass(frozen=True, slots=True)
class UISettings:
    theme: str = "nyx-dark"
    compact_mode: bool = False
    syntax_highlighting: bool = True
    mouse_support: bool = True


@dataclass(frozen=True, slots=True)
class LoggingSettings:
    level: str = "INFO"
    json_logs: bool = False


@dataclass(frozen=True, slots=True)
class StorageSettings:
    data_dir: str = ""
    db_filename: str = "nyx.db"

    def resolved_data_dir(self) -> Path:
        """Return the absolute data directory (platform default if empty)."""
        if self.data_dir:
            return Path(self.data_dir).expanduser().resolve()
        return default_data_dir()

    def database_path(self) -> Path:
        return self.resolved_data_dir() / self.db_filename


@dataclass(frozen=True, slots=True)
class Settings:
    """Root configuration object. All sections are immutable."""

    identity: IdentitySettings = field(default_factory=IdentitySettings)
    network: NetworkSettings = field(default_factory=NetworkSettings)
    security: SecuritySettings = field(default_factory=SecuritySettings)
    updates: UpdateSettings = field(default_factory=UpdateSettings)
    privacy: PrivacySettings = field(default_factory=PrivacySettings)
    ui: UISettings = field(default_factory=UISettings)
    logging: LoggingSettings = field(default_factory=LoggingSettings)
    storage: StorageSettings = field(default_factory=StorageSettings)

    # Resolved paths (computed once at load time)
    config_path: Path = field(default_factory=lambda: Path())
    data_dir: Path = field(default_factory=lambda: Path())


# ---------------------------------------------------------------------------
# Path helpers (XDG)
# ---------------------------------------------------------------------------

def default_config_dir() -> Path:
    """XDG_CONFIG_HOME/nyx or ~/.config/nyx."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / CONFIG_DIR_NAME
    return Path.home() / ".config" / CONFIG_DIR_NAME


def default_data_dir() -> Path:
    """XDG_DATA_HOME/nyx or ~/.local/share/nyx."""
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / DATA_DIR_NAME
    return Path.home() / ".local" / "share" / DATA_DIR_NAME


def default_config_path() -> Path:
    return default_config_dir() / CONFIG_FILE_NAME


def default_shortcuts_path() -> Path:
    return default_config_dir() / SHORTCUTS_FILE_NAME


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively merge override into a copy of base."""
    result = dict(base)
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, Mapping)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _coerce(value: Any, expected: type) -> Any:
    """Coerce a raw TOML value to the expected Python type."""
    origin = get_origin(expected)
    if origin is not None:
        # We only use plain types in our dataclasses for MVP.
        expected = origin

    if expected is bool:
        if isinstance(value, bool):
            return value
        raise TypeError(f"expected bool, got {type(value).__name__}")
    if expected is int:
        if isinstance(value, bool):
            raise TypeError("bool is not acceptable for int field")
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        raise TypeError(f"expected int, got {type(value).__name__}")
    if expected is float:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        raise TypeError(f"expected float, got {type(value).__name__}")
    if expected is str:
        if isinstance(value, str):
            return value
        raise TypeError(f"expected str, got {type(value).__name__}")
    return value


def _build_section(cls: type[T], raw: Mapping[str, Any]) -> T:
    """Instantiate a frozen dataclass section from a raw dict."""
    hints = get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    known = {f.name for f in fields(cls)}

    unknown = set(raw.keys()) - known
    if unknown:
        raise ValueError(
            f"unknown key(s) in [{cls.__name__.replace('Settings', '').lower()}]: "
            f"{', '.join(sorted(unknown))}"
        )

    for f in fields(cls):
        if f.name in raw:
            expected = hints.get(f.name, f.type)
            kwargs[f.name] = _coerce(raw[f.name], expected)

    return cls(**kwargs)  # type: ignore[call-arg]


def _load_toml(path: Path) -> dict[str, Any]:
    """Load a TOML file. Returns empty dict if file does not exist."""
    if not path.is_file():
        return {}

    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib  # type: ignore[no-redef]

    with path.open("rb") as fh:
        data = tomllib.load(fh)

    if not isinstance(data, dict):
        raise ValueError(f"config root must be a table, got {type(data).__name__}")
    return data


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    """
    Apply environment variable overrides.

    Format: NYX_<SECTION>__<KEY>=value  (double underscore)
    Example: NYX_LOGGING__LEVEL=DEBUG
    """
    prefix = "NYX_"
    result = dict(data)

    for env_key, env_val in os.environ.items():
        if not env_key.startswith(prefix):
            continue
        rest = env_key[len(prefix):]
        if "__" not in rest:
            continue
        section, key = rest.split("__", 1)
        section = section.lower()
        key = key.lower()

        if section not in result:
            result[section] = {}
        if not isinstance(result[section], dict):
            continue

        # Simple type inference from existing default
        default_val = DEFAULTS.get(section, {}).get(key)
        if isinstance(default_val, bool):
            coerced: Any = env_val.lower() in ("1", "true", "yes", "on")
        elif isinstance(default_val, int):
            coerced = int(env_val)
        elif isinstance(default_val, float):
            coerced = float(env_val)
        else:
            coerced = env_val

        result[section][key] = coerced
        log.debug("config.env_override", section=section, key=key)

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_settings(config_path: Path | None = None) -> Settings:
    """
    Load and validate configuration.

    Order of precedence (highest last):
      1. Built-in DEFAULTS
      2. File at config_path (or XDG default)
      3. Environment variables (NYX_SECTION__KEY)

    Raises
    ------
    ValueError
        On unknown keys or type mismatches.
    OSError
        On unreadable config file.
    """
    path = config_path if config_path is not None else default_config_path()
    log.debug("config.loading", path=str(path))

    file_data = _load_toml(path)
    merged = _deep_merge(DEFAULTS, file_data)
    merged = _apply_env_overrides(merged)

    # Build typed sections
    identity = _build_section(IdentitySettings, merged.get("identity", {}))
    network = _build_section(NetworkSettings, merged.get("network", {}))
    security = _build_section(SecuritySettings, merged.get("security", {}))
    updates = _build_section(UpdateSettings, merged.get("updates", {}))
    privacy = _build_section(PrivacySettings, merged.get("privacy", {}))
    ui = _build_section(UISettings, merged.get("ui", {}))
    logging_cfg = _build_section(LoggingSettings, merged.get("logging", {}))
    storage = _build_section(StorageSettings, merged.get("storage", {}))

    data_dir = storage.resolved_data_dir()

    settings = Settings(
        identity=identity,
        network=network,
        security=security,
        updates=updates,
        privacy=privacy,
        ui=ui,
        logging=logging_cfg,
        storage=storage,
        config_path=path,
        data_dir=data_dir,
    )

    log.info(
        "config.loaded",
        path=str(path),
        exists=path.is_file(),
        data_dir=str(data_dir),
        log_level=logging_cfg.level,
    )
    return settings


def ensure_directories(settings: Settings) -> None:
    """Create config and data directories if they do not exist."""
    cfg_dir = settings.config_path.parent
    cfg_dir.mkdir(parents=True, exist_ok=True)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    log.debug(
        "config.directories_ready",
        config_dir=str(cfg_dir),
        data_dir=str(settings.data_dir),
    )
