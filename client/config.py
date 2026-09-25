"""
config.py — Client configuration management for NYX.

Stores server URL and configures database path.
Config location: ~/.nyx/config.json
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from rich.console import Console

console = Console()

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

NYX_HOME = Path.home() / ".nyx"
CONFIG_PATH = NYX_HOME / "config.json"
LOCAL_DB_PATH = NYX_HOME / "nyx_local.db"

DEFAULT_CONFIG = {
    "server_url": "http://localhost:8000",
}


# ---------------------------------------------------------------------------
# Config class
# ---------------------------------------------------------------------------

class NYXConfig:
    """Manages the local NYX configuration file."""

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or CONFIG_PATH
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict = {}

    def exists(self) -> bool:
        """Return True if the config file exists."""
        return self.config_path.is_file()

    def load(self) -> dict:
        """Load the config from disk. Returns default if missing."""
        if not self.exists():
            self._data = dict(DEFAULT_CONFIG)
            return self._data
        try:
            raw = self.config_path.read_text(encoding="utf-8")
            self._data = json.loads(raw)
        except (json.JSONDecodeError, OSError):
            self._data = dict(DEFAULT_CONFIG)
        return self._data

    def save(self) -> None:
        """Persist the current config to disk."""
        self.config_path.write_text(
            json.dumps(self._data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def get(self, key: str, default=None):
        """Get a config value."""
        if not self._data:
            self.load()
        return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        """Set a config value and save immediately."""
        if not self._data:
            self.load()
        self._data[key] = value
        self.save()

    @property
    def server_url(self) -> str:
        return self.get("server_url", DEFAULT_CONFIG["server_url"])

    @property
    def db_path(self) -> Path:
        env = os.environ.get("NYX_DB_PATH")
        if env:
            return Path(env)
        return LOCAL_DB_PATH