"""
ui.py — Minimal shim for backward compatibility.

All display logic now lives in main.py (REPL loop) and commands.py.
This module exists only so `import ui` doesn't break if referenced anywhere.
"""

from __future__ import annotations

from rich.console import Console

console = Console()


def show_welcome() -> None:
    """No-op — the REPL banner is printed by main.py."""
    pass


def show_menu() -> None:
    """No-op — commands are typed in the REPL now."""
    pass