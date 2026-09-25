"""
Terminal UI for NYX — curses implementation.

Whitepaper Section 07: Terminal-first, keyboard-driven, monochrome-capable.
Textual is preferred when installed; this module provides a full curses TUI
with zero third-party dependencies so the client is usable immediately.
"""

from __future__ import annotations

try:
    import curses
except ImportError as _curses_err:  # Windows without windows-curses
    curses = None  # type: ignore[assignment]
    _CURSES_IMPORT_ERROR = _curses_err
else:
    _CURSES_IMPORT_ERROR = None

import textwrap
from typing import List, Optional

from nyx_client.core.app import NyxApp
from nyx_client.core.commands import registry
from nyx_client.config.logging import get_logger

log = get_logger(__name__)


class CursesTUI:
    """Minimal multi-pane terminal UI."""

    def __init__(self, app: NyxApp) -> None:
        self.app = app
        self.lines: List[str] = []
        self.input_buf = ""
        self.status = "ready"

    def _append(self, text: str) -> None:
        for line in text.splitlines() or [""]:
            self.lines.append(line)
        # Cap scrollback
        if len(self.lines) > 2000:
            self.lines = self.lines[-1500:]

    def run(self) -> int:
        if curses is None:
            print("Curses UI unavailable on this platform.")
            print("Install: pip install windows-curses")
            print("Or use:  python -m nyx_client.main --repl")
            if _CURSES_IMPORT_ERROR is not None:
                print("Detail:", _CURSES_IMPORT_ERROR)
            return 1
        try:
            return curses.wrapper(self._main)
        except curses.error as exc:
            log.error("tui.curses_error", error=str(exc))
            print("Curses UI unavailable:", exc)
            print("Use --repl instead.")
            return 1

    def _main(self, stdscr: "curses._CursesWindow") -> int:
        curses.curs_set(1)
        stdscr.nodelay(False)
        stdscr.keypad(True)
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_CYAN, -1)
            curses.init_pair(2, curses.COLOR_GREEN, -1)
            curses.init_pair(3, curses.COLOR_RED, -1)

        ident = self.app.identity.id if self.app.identity else "?"
        self._append("NYX Client — Terminal UI")
        self._append("Identity: " + ident)
        self._append("Type /help for commands, /exit to quit.")
        self._append("")

        while True:
            self._draw(stdscr)
            ch = stdscr.getch()
            if ch in (curses.KEY_ENTER, 10, 13):
                line = self.input_buf.strip()
                self.input_buf = ""
                if not line:
                    continue
                self._append("nyx> " + line)
                result = self.app.dispatch(line)
                if result.message == "__EXIT__":
                    self._append("Goodbye.")
                    self._draw(stdscr)
                    return 0
                if result.message:
                    prefix = "" if result.ok else "error: "
                    self._append(prefix + result.message)
                self.status = "ok" if result.ok else "error"
            elif ch in (curses.KEY_BACKSPACE, 127, 8):
                self.input_buf = self.input_buf[:-1]
            elif ch == curses.KEY_RESIZE:
                continue
            elif 32 <= ch <= 126:
                self.input_buf += chr(ch)

    def _draw(self, stdscr: "curses._CursesWindow") -> None:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        header = " NYX "
        if self.app.identity:
            header += self.app.identity.id[:28] + "… "
        try:
            stdscr.attron(curses.color_pair(1) | curses.A_BOLD)
            stdscr.addnstr(0, 0, header.ljust(w - 1), w - 1)
            stdscr.attroff(curses.color_pair(1) | curses.A_BOLD)
        except curses.error:
            pass

        # Message area: rows 1 .. h-3
        view_h = max(1, h - 3)
        visible = self.lines[-view_h:]
        for i, line in enumerate(visible):
            try:
                stdscr.addnstr(1 + i, 0, line[: w - 1], w - 1)
            except curses.error:
                pass

        # Status line
        try:
            stdscr.addnstr(h - 2, 0, ("[" + self.status + "]")[: w - 1], w - 1)
        except curses.error:
            pass

        # Input line
        prompt = "> " + self.input_buf
        try:
            stdscr.addnstr(h - 1, 0, prompt[: w - 1], w - 1)
            stdscr.move(h - 1, min(len(prompt), w - 2))
        except curses.error:
            pass
        stdscr.refresh()
