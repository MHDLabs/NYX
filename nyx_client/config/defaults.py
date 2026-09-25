"""
Default configuration values for the NYX client.

Source of truth: NYX Whitepaper v3.0 — Appendix A (Security Configuration)
and the design priorities listed in Sections 02-03.

All defaults are privacy-preserving and security-first.
User overrides live in ~/.config/nyx/config.toml.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Canonical default tree (mirrors the TOML structure users will write)
# ---------------------------------------------------------------------------

DEFAULTS: dict[str, Any] = {
    "identity": {
        "key_storage": "encrypted-sqlite",
        "auto_lock_timeout": 300,          # seconds
    },
    "network": {
        "default_server": "http://127.0.0.1:8080",
        "max_relays": 5,
        "connection_timeout": 10,          # seconds
        "reconnect_min_backoff": 1.0,      # seconds
        "reconnect_max_backoff": 60.0,     # seconds
        "reconnect_max_attempts": 0,       # 0 = unlimited
    },
    "security": {
        "require_verified_relay": False,
        "min_trust_level": 0,
        "check_revocations_on_startup": True,
        "pin_tls_certificates": False,
    },
    "updates": {
        "auto_check": True,
        "check_interval_hours": 24,
        "auto_install": False,
        "channel": "stable",
        "github_manifest_url": "",
        "release_keys_file": "",
    },
    "privacy": {
        "send_read_receipts": False,
        "share_online_status": False,
        "minimize_metadata": True,
    },
    "ui": {
        "theme": "nyx-dark",
        "compact_mode": False,
        "syntax_highlighting": True,
        "mouse_support": True,
    },
    "logging": {
        "level": "INFO",
        "json_logs": False,
    },
    "token": {
        "mint_pubkeys": "",
        "allow_local_mine": False,
        "max_claim_nyx": 1000000,
    },
    "storage": {
        "data_dir": "",                    # empty = platform default
        "db_filename": "nyx.db",
    },
}


# ---------------------------------------------------------------------------
# Known configuration paths (XDG-compliant)
# ---------------------------------------------------------------------------

CONFIG_DIR_NAME = "nyx"
CONFIG_FILE_NAME = "config.toml"
SHORTCUTS_FILE_NAME = "shortcuts.toml"
DATA_DIR_NAME = "nyx"
