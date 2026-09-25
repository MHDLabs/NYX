"""
Presentation layer — REPL + professional multi-panel TUI.
"""

from nyx_client.ui.repl import ReplUI

__all__ = ["ReplUI"]

try:
    from nyx_client.ui.pro_tui import ProTUI, PanelApp
    __all__ += ["ProTUI", "PanelApp"]
except ImportError:
    ProTUI = None  # type: ignore
    PanelApp = None  # type: ignore
