"""
Simple terminal transition effects (curses).
"""

from __future__ import annotations

import time
from typing import Any, Optional


def wipe_down(stdscr: Any, delay: float = 0.008) -> None:
    """Clear screen with a top-to-bottom wipe."""
    try:
        h, w = stdscr.getmaxyx()
        for y in range(h):
            try:
                stdscr.move(y, 0)
                stdscr.clrtoeol()
            except Exception:
                pass
            if y % 2 == 0:
                stdscr.refresh()
                time.sleep(delay)
        stdscr.erase()
        stdscr.refresh()
    except Exception:
        try:
            stdscr.erase()
            stdscr.refresh()
        except Exception:
            pass


def wipe_up(stdscr: Any, delay: float = 0.006) -> None:
    try:
        h, w = stdscr.getmaxyx()
        for y in range(h - 1, -1, -1):
            try:
                stdscr.move(y, 0)
                stdscr.clrtoeol()
            except Exception:
                pass
            if y % 3 == 0:
                stdscr.refresh()
                time.sleep(delay)
        stdscr.erase()
        stdscr.refresh()
    except Exception:
        try:
            stdscr.erase()
            stdscr.refresh()
        except Exception:
            pass


def flash_status(stdscr: Any, msg: str, pair: int = 3, ms: float = 0.15) -> None:
    """Brief status flash on bottom line."""
    try:
        h, w = stdscr.getmaxyx()
        stdscr.attron(pair)
        stdscr.addnstr(h - 1, 0, (" " + msg).ljust(w - 1)[: w - 1], w - 1)
        stdscr.attroff(pair)
        stdscr.refresh()
        time.sleep(ms)
    except Exception:
        pass
