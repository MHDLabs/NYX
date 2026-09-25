"""
Terminal color themes for the NYX TUI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class Theme:
    id: str
    name: str
    # logical roles -> curses color name
    header_fg: str
    header_bg: str
    selected_fg: str
    selected_bg: str
    accent: str
    success: str
    warning: str
    error: str
    muted: str
    border: str
    input_fg: str
    input_bg: str


THEMES: Dict[str, Theme] = {
    "midnight": Theme(
        id="midnight",
        name="Midnight (default)",
        header_fg="cyan",
        header_bg="black",
        selected_fg="black",
        selected_bg="cyan",
        accent="cyan",
        success="green",
        warning="yellow",
        error="red",
        muted="white",
        border="cyan",
        input_fg="green",
        input_bg="black",
    ),
    "ember": Theme(
        id="ember",
        name="Ember",
        header_fg="red",
        header_bg="black",
        selected_fg="black",
        selected_bg="red",
        accent="yellow",
        success="green",
        warning="yellow",
        error="red",
        muted="white",
        border="red",
        input_fg="yellow",
        input_bg="black",
    ),
    "forest": Theme(
        id="forest",
        name="Forest",
        header_fg="green",
        header_bg="black",
        selected_fg="black",
        selected_bg="green",
        accent="green",
        success="green",
        warning="yellow",
        error="red",
        muted="white",
        border="green",
        input_fg="green",
        input_bg="black",
    ),
    "violet": Theme(
        id="violet",
        name="Violet",
        header_fg="magenta",
        header_bg="black",
        selected_fg="black",
        selected_bg="magenta",
        accent="magenta",
        success="green",
        warning="yellow",
        error="red",
        muted="white",
        border="magenta",
        input_fg="magenta",
        input_bg="black",
    ),
    "mono": Theme(
        id="mono",
        name="Mono",
        header_fg="white",
        header_bg="black",
        selected_fg="black",
        selected_bg="white",
        accent="white",
        success="white",
        warning="white",
        error="white",
        muted="white",
        border="white",
        input_fg="white",
        input_bg="black",
    ),
    "ocean": Theme(
        id="ocean",
        name="Ocean",
        header_fg="blue",
        header_bg="black",
        selected_fg="white",
        selected_bg="blue",
        accent="cyan",
        success="green",
        warning="yellow",
        error="red",
        muted="white",
        border="blue",
        input_fg="cyan",
        input_bg="black",
    ),
}

DEFAULT_THEME_ID = "midnight"


def get_theme(theme_id: str) -> Theme:
    return THEMES.get(theme_id, THEMES[DEFAULT_THEME_ID])


def list_themes() -> list:
    return list(THEMES.values())



def register_custom_theme(theme: Theme) -> None:
    """Allow user/plugin packs to register a theme at runtime."""
    THEMES[theme.id] = theme


def theme_from_dict(data: dict) -> Theme:
    return Theme(
        id=str(data.get("id") or "custom")[:32],
        name=str(data.get("name") or "Custom")[:64],
        header_fg=str(data.get("header_fg") or "cyan"),
        header_bg=str(data.get("header_bg") or "black"),
        selected_fg=str(data.get("selected_fg") or "black"),
        selected_bg=str(data.get("selected_bg") or "cyan"),
        accent=str(data.get("accent") or "cyan"),
        success=str(data.get("success") or "green"),
        warning=str(data.get("warning") or "yellow"),
        error=str(data.get("error") or "red"),
        muted=str(data.get("muted") or "white"),
        border=str(data.get("border") or "cyan"),
        input_fg=str(data.get("input_fg") or "green"),
        input_bg=str(data.get("input_bg") or "black"),
    )
