"""
Unit tests for the configuration subsystem.

These tests require only the standard library + the nyx_client package.
They run without network and without third-party packages beyond pytest
(when available). A pure-stdlib fallback runner is also provided.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from nyx_client.config.defaults import DEFAULTS
from nyx_client.config.settings import (
    Settings,
    load_settings,
    ensure_directories,
    default_config_path,
    default_data_dir,
    default_config_dir,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_toml(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _toml_path(path: Path) -> str:
    """Format a filesystem path for TOML basic strings (Windows-safe).

    Backslashes are escape characters in TOML; use forward slashes so
    the same tests pass on Windows and POSIX.
    """
    return path.resolve().as_posix()


# ---------------------------------------------------------------------------
# Defaults & paths
# ---------------------------------------------------------------------------

def test_defaults_contain_required_sections() -> None:
    required = {
        "identity", "network", "security", "updates",
        "privacy", "ui", "logging", "storage",
    }
    assert required.issubset(DEFAULTS.keys())


def test_default_config_path_is_under_xdg_or_home() -> None:
    path = default_config_path()
    assert path.name == "config.toml"
    assert "nyx" in path.parts


def test_default_data_dir_is_under_xdg_or_home() -> None:
    path = default_data_dir()
    assert "nyx" in path.parts


# ---------------------------------------------------------------------------
# Loading pure defaults (no file)
# ---------------------------------------------------------------------------

def test_load_settings_without_file_uses_defaults(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.toml"
    s = load_settings(missing)
    assert isinstance(s, Settings)
    assert s.network.default_server == DEFAULTS["network"]["default_server"]
    assert s.privacy.send_read_receipts is False
    assert s.security.require_verified_relay is True
    assert s.logging.level == "INFO"
    assert s.config_path == missing


def test_settings_are_immutable() -> None:
    s = load_settings(Path("/nonexistent/nyx/config.toml"))
    with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
        s.logging.level = "DEBUG"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# File overrides
# ---------------------------------------------------------------------------

def test_file_override_merges_correctly(tmp_path: Path) -> None:
    cfg = _write_toml(
        tmp_path / "config.toml",
        """
[network]
default_server = "nyx://custom.relay"
connection_timeout = 7

[privacy]
send_read_receipts = true
""",
    )
    s = load_settings(cfg)
    assert s.network.default_server == "nyx://custom.relay"
    assert s.network.connection_timeout == 7
    # Unmentioned keys keep defaults
    assert s.network.max_relays == DEFAULTS["network"]["max_relays"]
    assert s.privacy.send_read_receipts is True
    assert s.privacy.share_online_status is False


def test_unknown_key_raises(tmp_path: Path) -> None:
    cfg = _write_toml(
        tmp_path / "bad.toml",
        """
[network]
totally_unknown = 42
""",
    )
    with pytest.raises(ValueError, match="unknown key"):
        load_settings(cfg)


def test_type_mismatch_raises(tmp_path: Path) -> None:
    cfg = _write_toml(
        tmp_path / "bad_type.toml",
        """
[network]
connection_timeout = "not-an-int"
""",
    )
    with pytest.raises(TypeError):
        load_settings(cfg)


def test_empty_file_loads_defaults(tmp_path: Path) -> None:
    cfg = _write_toml(tmp_path / "empty.toml", "")
    s = load_settings(cfg)
    assert s.network.default_server == DEFAULTS["network"]["default_server"]


# ---------------------------------------------------------------------------
# Environment overrides
# ---------------------------------------------------------------------------

def test_env_override_string(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NYX_NETWORK__DEFAULT_SERVER", "nyx://env.relay")
    s = load_settings(tmp_path / "none.toml")
    assert s.network.default_server == "nyx://env.relay"


def test_env_override_bool(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NYX_PRIVACY__SEND_READ_RECEIPTS", "true")
    s = load_settings(tmp_path / "none.toml")
    assert s.privacy.send_read_receipts is True


def test_env_override_int(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NYX_NETWORK__CONNECTION_TIMEOUT", "3")
    s = load_settings(tmp_path / "none.toml")
    assert s.network.connection_timeout == 3


def test_env_takes_precedence_over_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _write_toml(
        tmp_path / "config.toml",
        """
[logging]
level = "WARNING"
""",
    )
    monkeypatch.setenv("NYX_LOGGING__LEVEL", "ERROR")
    s = load_settings(cfg)
    assert s.logging.level == "ERROR"


# ---------------------------------------------------------------------------
# Storage path resolution
# ---------------------------------------------------------------------------

def test_storage_resolved_data_dir_default() -> None:
    s = load_settings(Path("/nonexistent.toml"))
    assert s.storage.resolved_data_dir() == default_data_dir()
    assert s.storage.database_path() == default_data_dir() / "nyx.db"


def test_storage_custom_data_dir(tmp_path: Path) -> None:
    custom = tmp_path / "custom_data"
    cfg = _write_toml(
        tmp_path / "config.toml",
        f"""
[storage]
data_dir = "{_toml_path(custom)}"
db_filename = "test.db"
""",
    )
    s = load_settings(cfg)
    assert s.storage.resolved_data_dir() == custom.resolve()
    assert s.storage.database_path().name == "test.db"


# ---------------------------------------------------------------------------
# Directory creation
# ---------------------------------------------------------------------------

def test_ensure_directories_creates_paths(tmp_path: Path) -> None:
    data = tmp_path / "data"
    cfg_file = tmp_path / "cfg" / "config.toml"
    cfg = _write_toml(
        cfg_file,
        f"""
[storage]
data_dir = "{_toml_path(data)}"
""",
    )
    s = load_settings(cfg)
    assert not data.exists()
    ensure_directories(s)
    assert data.is_dir()
    assert cfg_file.parent.is_dir()


# ---------------------------------------------------------------------------
# XDG environment respect
# ---------------------------------------------------------------------------

def test_xdg_config_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg_cfg"))
    assert default_config_dir() == tmp_path / "xdg_cfg" / "nyx"
    assert default_config_path() == tmp_path / "xdg_cfg" / "nyx" / "config.toml"


def test_xdg_data_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg_data"))
    assert default_data_dir() == tmp_path / "xdg_data" / "nyx"
