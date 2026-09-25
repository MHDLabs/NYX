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
