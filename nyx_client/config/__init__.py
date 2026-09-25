"""
Configuration subsystem.

Responsible for loading and validating:
  - ~/.config/nyx/config.toml (user settings)
  - ~/.config/nyx/shortcuts.toml (keyboard shortcuts) — later task
  - Environment overrides (NYX_SECTION__KEY)
  - Default values defined by the whitepaper (Appendix A)

Public API:
  load_settings()       -> Settings
  ensure_directories()  -> None
  default_config_path() -> Path
  default_data_dir()    -> Path
  configure_logging()
  get_logger()
"""

from nyx_client.config.logging import configure_logging, get_logger
from nyx_client.config.settings import (
    Settings,
    IdentitySettings,
    NetworkSettings,
    SecuritySettings,
    UpdateSettings,
    PrivacySettings,
    UISettings,
    LoggingSettings,
    StorageSettings,
    load_settings,
    ensure_directories,
    default_config_dir,
    default_config_path,
    default_data_dir,
    default_shortcuts_path,
)

__all__ = [
    "configure_logging",
    "get_logger",
    "Settings",
    "IdentitySettings",
    "NetworkSettings",
    "SecuritySettings",
    "UpdateSettings",
    "PrivacySettings",
    "UISettings",
    "LoggingSettings",
    "StorageSettings",
    "load_settings",
    "ensure_directories",
    "default_config_dir",
    "default_config_path",
    "default_data_dir",
    "default_shortcuts_path",
]
