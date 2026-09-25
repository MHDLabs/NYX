"""
NYX Professional TUI — animated panels, themes, fixed chrome, scroll body.

Layout:
  [ HEADER  fixed ]
  [ BODY    scrollable ]
  [ STATUS  fixed ]
  [ INPUT   fixed when composing ]

Keyboard model matches previous PanelApp + enhancements.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Callable, List, Optional, Sequence

from nyx_client.config.logging import get_logger
from nyx_client.core.app import NyxApp
from nyx_client.ui.banner import banner_lines, SUBTITLE
from nyx_client.ui.theme import THEMES, get_theme, list_themes, Theme, DEFAULT_THEME_ID
from nyx_client.ui.animations import wipe_down, wipe_up

log = get_logger(__name__)

try:
    import curses
except ImportError as _err:
    curses = None  # type: ignore[assignment]
    _CURSES_ERR = _err
else:
    _CURSES_ERR = None


class Screen(Enum):
    SPLASH = auto()
    REGISTER = auto()
    HOME = auto()
    CHAT = auto()
    SETTINGS = auto()
    PROFILE = auto()
    HELP = auto()
    CREATE = auto()
    SEARCH = auto()
    ROOM_SETTINGS = auto()
    USER_PROFILE = auto()
    THEMES = auto()
    CONTACTS = auto()
    MARKET = auto()
    WALLET = auto()


@dataclass
class MenuItem:
    key: str
    label: str
    meta: str = ""
    data: Any = None


def _fmt_time(ts: int) -> str:
    if ts <= 0:
        return ""
    if ts > 10_000_000_000:
        ts = ts // 1000
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(ts))
    except (OverflowError, OSError, ValueError):
        return ""


def _ctrl(ch: int, letter: str) -> bool:
    """True if ch is Ctrl+letter (case-insensitive)."""
    if not letter or len(letter) != 1:
        return False
    code = ord(letter.lower()) - ord("a") + 1
    return ch == code


def _type_badge(conv_type: str) -> str:
    return {
        "dm": "DM",
        "private_group": "GRP",
        "private_channel": "CHN",
        "public_channel": "CHN",
        "group": "GRP",
        "channel": "CHN",
    }.get(conv_type, (conv_type or "???")[:3].upper())


# curses color name -> constant
_COLOR_MAP = {
    "black": 0,
    "red": 1,
    "green": 2,
    "yellow": 3,
    "blue": 4,
    "magenta": 5,
    "cyan": 6,
    "white": 7,
}


class ProTUI:
    """Professional multi-panel NYX terminal UI."""

    def __init__(self, app: NyxApp) -> None:
        self.app = app
        self.screen = Screen.SPLASH
        self._contacts_index = 0
        self._market_index = 0
        self._wallet_index = 0
        self._market_category: Optional[str] = None
        self._market_query: str = ""
        self.filter_type: Optional[str] = None
        self.selected = 0
        self.status = "Ctrl+W wallet · Ctrl+M market · Ctrl+H help"
        self.items: List[MenuItem] = []
        self.chat_peer: Optional[str] = None
        self.chat_lines: List[str] = []
        self.chat_scroll = 0  # offset from bottom
        self.chat_id: Optional[str] = None  # conversation_id
        self.chat_kind: str = "dm"  # dm | room
        self.chat_title: str = ""
        self.input_mode = False
        self.input_buf = ""
        self.input_prompt = ""
        self.input_callback: Optional[Callable[[str], None]] = None
        self._settings_index = 0
        self._profile_index = 0
        self._create_index = 0
        self._search_index = 0
        self._search_hits: List[Any] = []
        self._search_query = ""
        self._room_focus: Optional[str] = None
        self._room_settings_index = 0
        self._theme_index = 0
        self._profile_user: Optional[str] = None
        self._splash_ticks = 0
        self._theme_id = DEFAULT_THEME_ID
        self._pairs: dict = {}
        self._transition = True

    # ------------------------------------------------------------------
    # Theme / prefs
    # ------------------------------------------------------------------

    def _prefs(self):
        from nyx_client.storage.user_prefs import UserPrefs
        if self.app.db is None:
            return None
        return UserPrefs(self.app.db)

    def _load_theme(self) -> None:
        prefs = self._prefs()
        if prefs:
            self._theme_id = prefs.get_theme_id()
        self._theme = get_theme(self._theme_id)

    def _init_colors(self) -> None:
        if not curses.has_colors():
            return
        curses.start_color()
        curses.use_default_colors()
        th = self._theme

        def pair(n: int, fg: str, bg: str) -> None:
            fi = _COLOR_MAP.get(fg, 7)
            bi = _COLOR_MAP.get(bg, -1) if bg != "black" else -1
            try:
                curses.init_pair(n, fi, bi if bi >= 0 else -1)
            except curses.error:
                try:
                    curses.init_pair(n, fi, 0)
                except curses.error:
                    pass

        pair(1, th.header_fg, th.header_bg)      # header
        pair(2, th.selected_fg, th.selected_bg)  # selected
        pair(3, th.success, "black")
        pair(4, th.accent, "black")
        pair(5, th.muted, "black")
        pair(6, th.warning, "black")
        pair(7, th.error, "black")
        pair(8, th.border, "black")
        pair(9, th.input_fg, th.input_bg)
        pair(10, "black", th.header_fg)  # inverse header strip

    def _goto(self, screen: Screen, animate: bool = True) -> None:
        if animate and self._transition and curses is not None:
            # wipe is applied by caller with stdscr; flag for next draw
            self._need_wipe = True
        else:
            self._need_wipe = False
        self.screen = screen

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def reload_home(self) -> None:
        assert self.app.messages is not None
        assert self.app.contacts is not None
        convs = self.app.messages.list_conversations(limit=200)
        ft = self.filter_type
        if ft == "dm":
            convs = [c for c in convs if c.get("type") == "dm"]
        elif ft == "group":
            convs = [c for c in convs if "group" in str(c.get("type") or "")]
        elif ft == "channel":
            convs = [c for c in convs if "channel" in str(c.get("type") or "")]
        elif ft:
            convs = [c for c in convs if c.get("type") == ft]
        items: List[MenuItem] = []
        for c in convs:
            peer = c.get("peer_id") or ""
            title = c.get("title") or ""
            contact = self.app.contacts.get(peer) if peer else None
            name = (
                (contact.display_name if contact and contact.display_name else None)
                or title
                or (
                    self.app.user_directory.display_name(peer)
                    if peer and self.app.user_directory
                    else None
                )
                or (peer[:20] + "..." if peer else c["conversation_id"][:24])
            )
            badge = _type_badge(str(c.get("type") or "dm"))
            when = _fmt_time(int(c.get("updated_at") or 0))
            unread = int(c.get("unread") or 0)
            unread_s = f" ({unread} new)" if unread else ""
            label = str(name) + unread_s
            items.append(
                MenuItem(
                    key=c["conversation_id"],
                    label=label,
                    meta=f"{badge}  {when}",
                    data=c,
                )
            )
        self.items = items
        if self.selected >= len(self.items):
            self.selected = max(0, len(self.items) - 1)

    def load_chat(
        self,
        peer_id: Optional[str] = None,
        *,
        conversation_id: Optional[str] = None,
        kind: str = "dm",
        title: str = "",
    ) -> None:
        """Open DM (peer_id) or room (conversation_id) chat view."""
        self.chat_kind = kind
        self.chat_scroll = 0
        self.chat_lines = []
        if kind == "dm":
            self.chat_peer = peer_id
            self.chat_id = None
            if peer_id and self.app.user_directory:
                self.chat_title = title or self.app.user_directory.display_name(peer_id)
            else:
                self.chat_title = title or (peer_id or "DM")
        else:
            self.chat_peer = None
            self.chat_id = conversation_id
            self.chat_title = title or (conversation_id or "Room")[:40]

        if self.app.messaging is None or self.app.messages is None:
            return
        try:
            if kind == "dm" and peer_id:
                hist = self.app.messaging.history(peer_id, limit=120)
                conv = None
                from nyx_client.protocol.types import conversation_id_for_dm
                if self.app.identity:
                    conv = conversation_id_for_dm(self.app.identity.id, peer_id)
                if conv:
                    self.app.messages.mark_read(conv)
            else:
                cid = conversation_id or ""
                hist = self.app.messaging.history_conversation(cid, limit=120)
                if cid:
                    self.app.messages.mark_read(cid)
        except Exception as exc:
            self.chat_lines = ["(history error: " + str(exc) + ")"]
            return

        me = self.app.identity.id if self.app.identity else ""
        for m in hist:
            is_out = m.direction.value == "out" or m.sender_id == me
            arrow = ">>" if is_out else "<<"
            try:
                text = m.plaintext.decode("utf-8", errors="replace")
            except Exception:
                text = "[binary]"
            name = "You" if is_out else m.sender_id[:12]
            if not is_out and self.app.user_directory is not None:
                name = self.app.user_directory.display_name(m.sender_id)
            ts = ""
            if m.timestamp:
                raw = m.timestamp
                if raw > 10_000_000_000:
                    raw = raw // 1000
                try:
                    ts = time.strftime("%H:%M", time.localtime(raw))
                except Exception:
                    ts = ""
            prefix = f" [{ts}]" if ts else ""
            self.chat_lines.append(f" {arrow}{prefix} {name}: {text}")

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self) -> int:
        if curses is None:
            print("Curses UI unavailable.")
            print("Install: pip install windows-curses")
            print("Or use:  python -m nyx_client.main --repl")
            if _CURSES_ERR:
                print("Detail:", _CURSES_ERR)
            return 1
        self._load_theme()
        if getattr(self.app, "is_new_identity", False) or getattr(self.app, "last_mnemonic", None):
            self.screen = Screen.SPLASH
        else:
            self.screen = Screen.SPLASH
        self._need_wipe = False
        try:
            return curses.wrapper(self._main)
        except curses.error as exc:
            print("Curses error:", exc)
            return 1

    def _main(self, stdscr: Any) -> int:
        curses.curs_set(0)
        stdscr.keypad(True)
        stdscr.timeout(80)
        self._init_colors()
        self.reload_home()

        while True:
            if getattr(self, "_need_wipe", False):
                wipe_down(stdscr, delay=0.004)
                self._need_wipe = False
            self._draw(stdscr)
            try:
                ch = stdscr.getch()
            except KeyboardInterrupt:
                return 0
            if ch == -1:
                if self.screen == Screen.SPLASH:
                    self._splash_ticks += 1
                    if self._splash_ticks > 18:  # ~1.5s
                        if getattr(self.app, "is_new_identity", False) or getattr(
                            self.app, "last_mnemonic", None
                        ):
                            self._goto(Screen.REGISTER)
                        else:
                            self._goto(Screen.HOME)
                            self.reload_home()
                continue
            if self.input_mode:
                if not self._handle_input(ch):
                    return 0
                continue
            if not self._handle_key(ch, stdscr):
                return 0

    # ------------------------------------------------------------------
    # Input line
    # ------------------------------------------------------------------

    def _start_input(self, prompt: str, cb: Callable[[str], None]) -> None:
        self.input_mode = True
        self.input_buf = ""
        self.input_prompt = prompt
        self.input_callback = cb

    def _handle_input(self, ch: int) -> bool:
        if ch in (10, 13, curses.KEY_ENTER):
            cb = self.input_callback
            val = self.input_buf
            self.input_mode = False
            self.input_buf = ""
            self.input_callback = None
            if cb:
                cb(val)
            return True
        if ch == 27:
            self.input_mode = False
            self.input_buf = ""
            self.input_callback = None
            self.status = "cancelled"
            return True
        if ch in (curses.KEY_BACKSPACE, 127, 8):
            self.input_buf = self.input_buf[:-1]
            return True
        if 32 <= ch <= 126 and len(self.input_buf) < 240:
            self.input_buf += chr(ch)
        return True

    # ------------------------------------------------------------------
    # Keys
    # ------------------------------------------------------------------

    def _handle_key(self, ch: int, stdscr: Any) -> bool:
        # ---- Global chord shortcuts (work on almost every screen) ----
        # Ctrl+Q quit
        if _ctrl(ch, "q") or ch == 3:  # Ctrl+C also soft-quit from home
            if self.screen in (Screen.HOME, Screen.SPLASH) or _ctrl(ch, "q"):
                wipe_up(stdscr, 0.004)
                return False
        # Ctrl+H or ? help
        if _ctrl(ch, "h") or ch == ord("?"):
            self._goto(Screen.HELP)
            return True
        # Ctrl+S settings
        if _ctrl(ch, "s"):
            self._goto(Screen.SETTINGS)
            self._settings_index = 0
            return True
        # Ctrl+P profile
        if _ctrl(ch, "p"):
            self._goto(Screen.PROFILE)
            self._profile_index = 0
            return True
        # Ctrl+T themes
        if _ctrl(ch, "t"):
            self._goto(Screen.THEMES)
            self._theme_index = 0
            return True
        # Ctrl+N create
        if _ctrl(ch, "n"):
            self._goto(Screen.CREATE)
            self._create_index = 0
            return True
        # Ctrl+F search
        if _ctrl(ch, "f"):
            self._goto(Screen.SEARCH)
            self._search_hits = []
            self._search_index = 0
            self._start_input("Search: ", self._do_search)
            return True
        # Ctrl+B contacts list
        if _ctrl(ch, "b"):
            self._contacts_index = 0
            self._goto(Screen.CONTACTS)
            self.status = "contacts"
            return True
        # Ctrl+M marketplace
        if _ctrl(ch, "m"):
            self._market_index = 0
            self._goto(Screen.MARKET)
            self.status = "marketplace"
            return True
        # Ctrl+W wallet
        if _ctrl(ch, "w"):
            self._wallet_index = 0
            self._goto(Screen.WALLET)
            self.status = "wallet"
            return True
        # Ctrl+R refresh home list
        if _ctrl(ch, "r"):
            self.reload_home()
            self.status = "refreshed"
            return True
        # Ctrl+Y sync inbox (when connected)
        if _ctrl(ch, "y"):
            try:
                if self.app.connection is None:
                    self.status = "not connected — Ctrl+O to connect"
                else:
                    self.app.messaging._connection = self.app.connection
                    res = self.app.messaging.sync_inbox()
                    self.status = f"sync pulled={res.get('pulled', 0)} new={res.get('ingested', 0)}"
                    if self.screen == Screen.CHAT and self.chat_peer:
                        self.load_chat(self.chat_peer, self.chat_title or self.chat_peer)
                    self.reload_home()
            except Exception as exc:
                self.status = f"sync error: {exc}"[:60]
            return True
        # Ctrl+O connect best / default server
        if _ctrl(ch, "o"):
            try:
                ep = None
                if self.app.settings and self.app.settings.network.default_server:
                    ep = self.app.settings.network.default_server
                sess = self.app.connect_sync(endpoint=ep, use_http=True)
                meta = getattr(sess, "sync_meta", {}) or {}
                self.status = (
                    f"online · inbox +{meta.get('messages_synced', 0)}"
                )
                self.reload_home()
            except Exception as exc:
                self.status = f"connect failed: {exc}"[:60]
            return True
        # Ctrl+K compose (chat or selected home item)
        if _ctrl(ch, "k"):
            if self.screen == Screen.CHAT and self.chat_peer:
                self._start_input("Message: ", self._send_msg)
            elif self.screen == Screen.HOME and self.items:
                it = self.items[self.selected]
                self.load_chat(str(it.data), it.label)
                self._goto(Screen.CHAT)
                self._start_input("Message: ", self._send_msg)
            else:
                self.status = "open a chat first"
            return True
        # Ctrl+I user profile for selection
        if _ctrl(ch, "i") or ch == ord("i"):
            target = None
            if self.screen == Screen.CHAT and self.chat_peer:
                target = self.chat_peer
            elif self.screen == Screen.HOME and self.items:
                it = self.items[self.selected]
                target = it.data
            elif self.screen == Screen.SEARCH and getattr(self, "_search_hits", None):
                if self._search_hits:
                    h = self._search_hits[self._search_index]
                    target = h.get("id")
            if target:
                self._profile_user = str(target)
                self._goto(Screen.USER_PROFILE)
                self.status = "profile"
            else:
                self.status = "nothing selected"
            return True
        # Filters 1-4 and Ctrl+1.. (ctrl+1 may not work on all terminals — digits still work)
        if ch == ord("1"):
            self.filter_type = None
            self._goto(Screen.HOME)
            self.selected = 0
            self.reload_home()
            self.status = "filter: all"
            return True
        if ch == ord("2"):
            self.filter_type = "dm"
            self._goto(Screen.HOME)
            self.selected = 0
            self.reload_home()
            self.status = "filter: DM"
            return True
        if ch == ord("3"):
            self.filter_type = "group"
            self._goto(Screen.HOME)
            self.selected = 0
            self.reload_home()
            self.status = "filter: groups"
            return True
        if ch == ord("4"):
            self.filter_type = "channel"
            self._goto(Screen.HOME)
            self.selected = 0
            self.reload_home()
            self.status = "filter: channels"
            return True
        # q = back or quit on home
        if ch == ord("q"):
            if self.screen in (Screen.HOME, Screen.SPLASH):
                wipe_up(stdscr, 0.004)
                return False
            self._goto(Screen.HOME)
            self.reload_home()
            return True
        # Esc = back
        if ch == 27:
            if self.screen != Screen.HOME:
                self._goto(Screen.HOME)
                self.reload_home()
            return True


        if self.screen == Screen.SPLASH:
            if ch in (10, 13, curses.KEY_ENTER, ord(" ")):
                if getattr(self.app, "is_new_identity", False) or getattr(
                    self.app, "last_mnemonic", None
                ):
                    self._goto(Screen.REGISTER)
                else:
                    self._goto(Screen.HOME)
                    self.reload_home()
            return True

        if self.screen == Screen.HOME:
            n = len(self.items)
            if ch == curses.KEY_UP and n:
                self.selected = (self.selected - 1) % n
            elif ch == curses.KEY_DOWN and n:
                self.selected = (self.selected + 1) % n
            elif ch in (10, 13, curses.KEY_ENTER):
                self._open_selected()
            elif ch == ord("m") and n:
                item = self.items[self.selected]
                peer = (item.data or {}).get("peer_id")
                if peer:
                    self.chat_peer = peer
                    self._start_input("Message: ", self._send_msg)
            elif ch == ord("n"):
                self._create_index = 0
                self._goto(Screen.CREATE)
            elif ch in (ord("/"), ord("f")):
                self._search_hits = []
                self._search_index = 0
                self._goto(Screen.SEARCH)
                self._start_input("Search: ", self._do_search)
            elif ch == ord("s"):
                self._settings_index = 0
                self._goto(Screen.SETTINGS)
            elif ch == ord("w"):
                self._wallet_index = 0
                self._goto(Screen.WALLET)

        elif self.screen == Screen.CHAT:
            if ch == curses.KEY_UP:
                self.chat_scroll = min(self.chat_scroll + 1, max(0, len(self.chat_lines) - 1))
            elif ch == curses.KEY_DOWN:
                self.chat_scroll = max(0, self.chat_scroll - 1)
            elif ch == curses.KEY_PPAGE:
                self.chat_scroll = min(self.chat_scroll + 10, max(0, len(self.chat_lines) - 1))
            elif ch == curses.KEY_NPAGE:
                self.chat_scroll = max(0, self.chat_scroll - 10)
            elif ch in (10, 13, curses.KEY_ENTER, ord("m")):
                if self.chat_peer or self.chat_id:
                    self._start_input("Message: ", self._send_msg)
            elif ch == ord("o") and self.chat_kind == "room" and self.chat_id:
                # room options / settings
                self._room_focus = self.chat_id
                self._goto(Screen.ROOM_SETTINGS)
                self._room_settings_index = 0
            elif ch in (curses.KEY_LEFT, ord("b")):
                self._goto(Screen.HOME)
                self.reload_home()

        elif self.screen == Screen.SETTINGS:
            opts = self._settings_items()
            n = len(opts)
            if ch == curses.KEY_UP and n:
                self._settings_index = (self._settings_index - 1) % n
            elif ch == curses.KEY_DOWN and n:
                self._settings_index = (self._settings_index + 1) % n
            elif ch in (10, 13, curses.KEY_ENTER):
                self._settings_action()

        elif self.screen == Screen.THEMES:
            themes = list_themes()
            n = len(themes)
            if ch == curses.KEY_UP and n:
                self._theme_index = (self._theme_index - 1) % n
            elif ch == curses.KEY_DOWN and n:
                self._theme_index = (self._theme_index + 1) % n
            elif ch in (10, 13, curses.KEY_ENTER):
                th = themes[self._theme_index]
                self._theme_id = th.id
                prefs = self._prefs()
                if prefs:
                    prefs.set_theme_id(th.id)
                self._load_theme()
                self._init_colors()
                self.status = "theme: " + th.name
                self._goto(Screen.SETTINGS)

        elif self.screen == Screen.PROFILE:
            opts = self._profile_items()
            n = len(opts)
            if ch == curses.KEY_UP and n:
                self._profile_index = (self._profile_index - 1) % n
            elif ch == curses.KEY_DOWN and n:
                self._profile_index = (self._profile_index + 1) % n
            elif ch in (10, 13, curses.KEY_ENTER):
                self._profile_action()

        elif self.screen == Screen.REGISTER:
            if ch in (10, 13, curses.KEY_ENTER):
                self._goto(Screen.PROFILE)
                self._profile_index = 0
                self.status = "set your display name"

        elif self.screen == Screen.CREATE:
            opts = self._create_items()
            n = len(opts)
            if ch == curses.KEY_UP and n:
                self._create_index = (self._create_index - 1) % n
            elif ch == curses.KEY_DOWN and n:
                self._create_index = (self._create_index + 1) % n
            elif ch in (10, 13, curses.KEY_ENTER):
                self._create_action()

        elif self.screen == Screen.SEARCH:
            n = len(self._search_hits)
            if ch == curses.KEY_UP and n:
                self._search_index = (self._search_index - 1) % n
            elif ch == curses.KEY_DOWN and n:
                self._search_index = (self._search_index + 1) % n
            elif ch in (10, 13, curses.KEY_ENTER) and n:
                self._open_search_hit()

        elif self.screen == Screen.ROOM_SETTINGS:
            opts = self._room_settings_items()
            n = len(opts)
            if ch == curses.KEY_UP and n:
                self._room_settings_index = (self._room_settings_index - 1) % n
            elif ch == curses.KEY_DOWN and n:
                self._room_settings_index = (self._room_settings_index + 1) % n
            elif ch in (10, 13, curses.KEY_ENTER):
                self._room_settings_action()

        elif self.screen == Screen.USER_PROFILE:
            if ch in (10, 13, 27, curses.KEY_LEFT):
                if self.chat_peer:
                    self._goto(Screen.CHAT)
                else:
                    self._goto(Screen.HOME)
                    self.reload_home()
            elif ch == ord("e") and self._profile_user:
                self._start_input("Set display name: ", self._save_contact_name)
            elif ch == ord("y") and self._profile_user:
                self._start_input("Set bio: ", self._save_contact_bio)
            elif ch == ord("c") and self._profile_user:
                self._save_current_as_contact()

        elif self.screen == Screen.CONTACTS:
            items = self._contact_items()
            n = len(items)
            if ch == curses.KEY_UP and n:
                self._contacts_index = (self._contacts_index - 1) % n
            elif ch == curses.KEY_DOWN and n:
                self._contacts_index = (self._contacts_index + 1) % n
            elif ch in (10, 13, curses.KEY_ENTER) and n:
                it = items[self._contacts_index]
                if it.key == "add":
                    self._start_input(
                        "Add contact (id or @handle) [name]: ",
                        self._add_contact_line,
                    )
                elif it.key == "back":
                    self._goto(Screen.HOME)
                    self.reload_home()
                elif it.data:
                    peer = str(it.data)
                    self.load_chat(peer, kind="dm", title=it.label)
                    self._goto(Screen.CHAT)
                    self.status = "DM " + it.label[:24]
            elif ch == ord("a"):
                self._start_input(
                    "Add contact (id or @handle) [name]: ",
                    self._add_contact_line,
                )
            elif ch == ord("d") and n:
                it = items[self._contacts_index]
                if it.data and self.app.contacts:
                    self.app.contacts.delete(str(it.data))
                    self.status = "contact removed"
                    self._contacts_index = max(0, self._contacts_index - 1)

        elif self.screen == Screen.MARKET:
            items = self._market_items()
            n = len(items)
            if ch == curses.KEY_UP and n:
                self._market_index = (self._market_index - 1) % n
            elif ch == curses.KEY_DOWN and n:
                self._market_index = (self._market_index + 1) % n
            elif ch in (10, 13, curses.KEY_ENTER) and n:
                self._market_action(items[self._market_index])
            elif ch == ord("s"):
                self._start_input(
                    "Sell: price title  (e.g. 2.5 MySource)",
                    self._market_sell_line,
                )

        elif self.screen == Screen.WALLET:
            items = self._wallet_menu_items()
            n = len(items)
            if ch == curses.KEY_UP and n:
                self._wallet_index = (self._wallet_index - 1) % n
            elif ch == curses.KEY_DOWN and n:
                self._wallet_index = (self._wallet_index + 1) % n
            elif ch in (10, 13, curses.KEY_ENTER) and n:
                self._wallet_action(items[self._wallet_index])
            elif ch == ord("p"):
                self._start_input("Pay: <id|@handle> <amount> [memo]: ", self._wallet_pay_line)
            elif ch == ord("c"):
                self._start_input("Claim voucher path or 'relay': ", self._claim_line)
            elif ch == ord("r"):
                self.status = self._wallet_refresh_status()

        elif self.screen == Screen.HELP:
            if ch in (10, 13, 27, ord("b"), ord("q")):
                self._goto(Screen.HOME)
                self.reload_home()

        return True

    def _open_selected(self) -> None:
        if not self.items:
            self.status = "no conversations yet"
            return
        item = self.items[self.selected]
        data = item.data or {}
        peer = data.get("peer_id") or ""
        ctype = str(data.get("type") or "dm")
        cid = data.get("conversation_id") or ""
        title = data.get("title") or item.label.split(" (")[0]
        if peer and ctype == "dm":
            self.load_chat(peer, kind="dm", title=title)
            self._goto(Screen.CHAT)
            self.status = "DM"
        elif ctype in ("private_group", "private_channel", "public_channel", "group", "channel"):
            self._room_focus = cid
            self.load_chat(
                None,
                conversation_id=cid,
                kind="room",
                title=title,
            )
            self._goto(Screen.CHAT)
            self.status = _type_badge(ctype)
        elif peer:
            self.load_chat(peer, kind="dm", title=title)
            self._goto(Screen.CHAT)
        else:
            self.status = "cannot open"

    # ------------------------------------------------------------------
    # Actions (settings / profile / create / search / room)
    # ------------------------------------------------------------------

    def _market_items(self) -> List[MenuItem]:
        items: List[MenuItem] = []
        if self.app.marketplace is not None:
            for L in self.app.marketplace.list_active(limit=40):
                price = f"{L.price_nyx:.4f} NYX"
                items.append(
                    MenuItem(
                        L.listing_id,
                        f"{L.title}",
                        f"{price} · {L.category} · {L.listing_id[:10]}",
                        data=L.listing_id,
                    )
                )
        items.append(MenuItem("sell", "+ Sell item", "price + title", data=None))
        items.append(MenuItem("back", "< Back", "", data=None))
        return items

    def _market_action(self, item: MenuItem) -> None:
        if item.key == "back":
            self._goto(Screen.SETTINGS)
            return
        if item.key == "sell":
            self._start_input(
                "Sell: price title  (e.g. 2.5 MySource)",
                self._market_sell_line,
            )
            return
        if item.data and self.app.marketplace is not None:
            try:
                order = self.app.buy_listing(str(item.data)) if hasattr(self.app, "buy_listing") else self.app.marketplace.buy(str(item.data))
                bal = self.app.wallet.format_balance() if self.app.wallet else ""
                oid = getattr(order, "order_id", str(order))
                deliv = getattr(order, "delivery_path", "") or ""
                self.status = f"bought {oid[:12]} · {bal}" + (" · file saved" if deliv else "")
            except Exception as exc:
                self.status = str(exc)[:60]

    def _market_search_line(self, line: str) -> None:
        self.screen = Screen.MARKET
        self._market_query = (line or "").strip()
        self._market_index = 0
        self.status = f"search: {self._market_query or '(cleared)'}"

    def _market_sell_line(self, line: str) -> None:
        self.screen = Screen.MARKET
        line = (line or "").strip()
        if not line:
            self.status = "cancelled"
            return
        product = ""
        if "|" in line:
            line, product = line.split("|", 1)
            product = product.strip().strip('"')
        parts = line.split()
        if len(parts) < 2:
            self.status = "usage: <price> [category] <title> | file"
            return
        try:
            price = float(parts[0])
            cats = set(getattr(self.app.marketplace, "CATEGORIES", ()) or ())
            if len(parts) >= 3 and parts[1].lower() in cats:
                category = parts[1].lower()
                title = " ".join(parts[2:])
            else:
                category = "digital" if product else "other"
                title = " ".join(parts[1:])
            if self.app.marketplace is None:
                self.status = "marketplace unavailable"
                return
            L = self.app.marketplace.create_listing(
                title, price_nyx=price, category=category, product_path=product
            )
            extra = " +file" if L.product_path else ""
            self.status = f"listed {L.listing_id[:12]} @ {L.price_nyx:.4f}{extra}"
            self._market_index = 0
        except Exception as exc:
            self.status = str(exc)[:60]

    def _contact_items(self) -> List[MenuItem]:
        items: List[MenuItem] = []
        if self.app.contacts:
            for c in self.app.contacts.list_all():
                name = c.display_name or c.identity_id[:20]
                meta = c.identity_id[:16] + "…"
                if c.trusted:
                    meta += " ★"
                items.append(MenuItem(c.identity_id, name, meta, data=c.identity_id))
        items.append(MenuItem("add", "+ Add contact", "id / @handle", data=None))
        items.append(MenuItem("back", "< Back", "", data=None))
        return items

    def _add_contact_line(self, line: str) -> None:
        line = (line or "").strip()
        self.screen = Screen.CONTACTS
        if not line:
            self.status = "cancelled"
            return
        parts = line.split(None, 1)
        raw_id = parts[0].lstrip("@")
        name = parts[1] if len(parts) > 1 else None
        try:
            peer = raw_id
            if hasattr(self.app, "resolve_handle"):
                peer = self.app.resolve_handle(raw_id)
            if self.app.messaging is not None:
                c = self.app.messaging.ensure_contact(peer, display_name=name)
            elif self.app.contacts is not None:
                c = self.app.contacts.upsert(peer, display_name=name)
            else:
                self.status = "contacts unavailable"
                return
            label = c.display_name or c.identity_id[:20]
            self.status = "saved " + label
            self._contacts_index = 0
        except Exception as exc:
            self.status = str(exc)[:60]

    def _save_current_as_contact(self) -> None:
        uid = self._profile_user
        if not uid or not self.app.contacts:
            self.status = "no user"
            return
        name = None
        try:
            prof = self.app.get_user_profile(uid)
            name = prof.display_name or None
        except Exception:
            pass
        if self.app.messaging is not None:
            self.app.messaging.ensure_contact(uid, display_name=name)
        else:
            self.app.contacts.upsert(uid, display_name=name)
        self.status = "contact saved"


    def _wallet_refresh_status(self) -> str:
        if not self.app.wallet:
            return "no wallet"
        n = len(self.app.wallet.list_utxos())
        return f"{self.app.wallet.format_balance()} · {n} UTXO(s)"

    def _wallet_menu_items(self) -> List[MenuItem]:
        return [
            MenuItem("pay", "Send NYX", "transfer to identity / @handle"),
            MenuItem("claim", "Add balance (voucher)", "server-signed file or relay"),
            MenuItem("claimrelay", "Pull from relay", "pending server mints"),
            MenuItem("refresh", "Refresh balance", "reload UTXO set"),
            MenuItem("copy", "Show full address", "receive address"),
            MenuItem("back", "< Back", ""),
        ]

    def _wallet_action(self, item: MenuItem) -> None:
        key = item.key
        if key == "back":
            self._goto(Screen.SETTINGS)
            return
        if key == "pay":
            self._start_input("Pay: <id|@handle> <amount> [memo]: ", self._wallet_pay_line)
            return
        if key == "claim":
            self._start_input("Claim voucher path or 'relay': ", self._claim_line)
            return
        if key == "claimrelay":
            try:
                r = self.app.fetch_mint_from_relay()
                self.status = f"claimed {r.get('claimed', 0)} · {r.get('balance', '')}"
            except Exception as e:
                self.status = str(e)[:60]
            return
        if key == "refresh":
            self.status = self._wallet_refresh_status()
            return
        if key == "copy":
            if self.app.wallet:
                self.status = self.app.wallet.info().address
            return

    def _wallet_pay_line(self, line: str) -> None:
        self.screen = Screen.WALLET
        line = (line or "").strip()
        if not line:
            self.status = "cancelled"
            return
        parts = line.split()
        if len(parts) < 2:
            self.status = "usage: <id|@handle> <amount> [memo]"
            return
        try:
            dest = parts[0]
            amount = float(parts[1])
            memo = " ".join(parts[2:])
            if hasattr(self.app, "resolve_handle"):
                dest = self.app.resolve_handle(dest.lstrip("@"))
            result = self.app.transfer_nyx(dest, amount, memo=memo)
            bal = self.app.wallet.format_balance() if self.app.wallet else ""
            self.status = f"sent · tx {result.get('txid', '')[:16]}… · {bal}"
        except Exception as e:
            self.status = str(e)[:60]

    def _draw_wallet(self, stdscr: Any, top: int, body_h: int, w: int) -> None:
        """Professional wallet panel: balance, address, UTXOs, history, menu."""
        lines: List[str] = []
        if not self.app.wallet:
            lines = ["  Wallet unavailable"]
        else:
            info = self.app.wallet.info()
            bal = self.app.wallet.format_balance()
            addr = info.address
            utxos = self.app.wallet.list_utxos()
            hist = self.app.wallet.history(6)

            lines.append("  ╔══════════════════════════════════════════════════╗")
            lines.append("  ║              NYX WALLET                          ║")
            lines.append("  ╚══════════════════════════════════════════════════╝")
            lines.append("")
            lines.append(f"  Balance   {bal}")
            lines.append(f"  Coins     {len(utxos)} UTXO(s)")
            lines.append("")
            lines.append("  Receive address")
            lines.append(f"  {addr}")
            lines.append("")
            lines.append("  ── Unspent outputs ──────────────────────────────")
            if not utxos:
                lines.append("  (none)")
            else:
                for u in utxos[:6]:
                    nyx = u.amount_micro / 1_000_000
                    op = u.outpoint
                    if len(op) > 28:
                        op = op[:12] + "…" + op[-10:]
                    lines.append(f"  · {nyx:>12.6f} NYX   {op}")
            lines.append("")
            lines.append("  ── Recent activity ─────────────────────────────")
            if not hist:
                lines.append("  (no transactions yet)")
            else:
                for tx in hist:
                    sign = "+" if tx.kind in ("credit", "sale", "import", "mint", "transfer_in") else "−"
                    nyx = tx.amount_micro / 1_000_000
                    memo = (tx.memo or tx.kind)[:28]
                    lines.append(f"  {sign}{nyx:>10.6f}  {memo}")
            lines.append("")
            lines.append("  ── Actions ─────────────────────────────────────")

        # draw static panel
        for i, line in enumerate(lines):
            if i >= body_h - 8:
                break
            try:
                if i < 3:
                    stdscr.attron(curses.color_pair(3) | curses.A_BOLD)
                    stdscr.addnstr(top + i, 0, line[: w - 1], w - 1)
                    stdscr.attroff(curses.color_pair(3) | curses.A_BOLD)
                elif "Balance" in line:
                    stdscr.attron(curses.color_pair(6) | curses.A_BOLD)
                    stdscr.addnstr(top + i, 0, line[: w - 1], w - 1)
                    stdscr.attroff(curses.color_pair(6) | curses.A_BOLD)
                else:
                    stdscr.addnstr(top + i, 0, line[: w - 1], w - 1)
            except curses.error:
                pass

        # menu at bottom of body
        menu_top = top + min(len(lines) + 1, max(1, body_h - 7))
        items = self._wallet_menu_items()
        for j, it in enumerate(items):
            if menu_top + j >= top + body_h:
                break
            label = f"  {it.label}"
            if it.meta:
                label = f"  {it.label}  —  {it.meta}"
            try:
                if j == self._wallet_index:
                    stdscr.attron(curses.color_pair(2) | curses.A_BOLD)
                    stdscr.addnstr(menu_top + j, 1, label.ljust(w - 3)[: w - 3], w - 3)
                    stdscr.attroff(curses.color_pair(2) | curses.A_BOLD)
                else:
                    stdscr.addnstr(menu_top + j, 1, label[: w - 3], w - 3)
            except curses.error:
                pass


    def _claim_line(self, value: str) -> None:
        self.screen = Screen.WALLET
        value = (value or "").strip()
        if not value:
            self.status = self._wallet_refresh_status()
            return
        try:
            if value.lower() == "relay":
                r = self.app.fetch_mint_from_relay()
                self.status = f"claimed {r.get('claimed', 0)} · {r.get('balance', '')}"
            else:
                r = self.app.claim_mint_voucher(value)
                self.status = f"+{r['amount_nyx']:.4f} NYX · {r['balance']}"
        except Exception as e:
            self.status = str(e)[:60]

    def _settings_items(self) -> List[MenuItem]:
        bal = ""
        if self.app.wallet:
            bal = self.app.wallet.format_balance()
        return [
            MenuItem("theme", "Color theme", get_theme(self._theme_id).name),
            MenuItem("wallet", "NYX wallet", bal or "—"),
            MenuItem("market", "Marketplace", "buy / sell with NYX"),
            MenuItem("email", "Recovery email", (
                (self._prefs().get_profile().recovery_email if self._prefs() else "") or "(not set)"
            )),
            MenuItem("contacts", "Contacts", "Ctrl+B"),
            MenuItem("connect", "Connect & sync inbox", "Ctrl+O"),
            MenuItem("servers", "Servers & ranking", "latency / trust"),
            MenuItem("update", "Check for updates", "signed manifests"),
            MenuItem("back", "< Back to chats", ""),
        ]

    def _settings_action(self) -> None:
        key = self._settings_items()[self._settings_index].key
        if key == "theme":
            self._goto(Screen.THEMES)
            themes = list_themes()
            for i, th in enumerate(themes):
                if th.id == self._theme_id:
                    self._theme_index = i
                    break
        elif key == "wallet":
            self._wallet_index = 0
            self._goto(Screen.WALLET)
        elif key == "market":
            self._market_index = 0
            self._goto(Screen.MARKET)
        elif key == "email":
            cur = ""
            prefs = self._prefs()
            if prefs:
                cur = prefs.get_profile().recovery_email or ""
            prompt = f"Recovery email [{cur}]: " if cur else "Recovery email: "
            self._email_return = Screen.SETTINGS
            self._start_input(prompt, self._save_email)
        elif key == "update":
            try:
                r = self.app.check_updates()
                if r.update_available and r.candidate:
                    self.status = f"update {r.candidate.version} available"
                else:
                    self.status = f"up to date ({r.current_version})"
            except Exception as exc:
                self.status = str(exc)[:60]
        elif key == "contacts":
            self._contacts_index = 0
            self._goto(Screen.CONTACTS)
        elif key == "connect":
            try:
                ep = getattr(self.app.settings.network, "default_server", None) or None
                sess = self.app.connect_sync(endpoint=ep or None, use_http=True)
                meta = getattr(sess, "sync_meta", {}) or {}
                self.status = f"online · synced {meta.get('messages_synced', 0)} msg"
            except Exception as exc:
                self.status = str(exc)[:60]
        elif key == "servers":
            self.status = "use /servers refresh in --repl for full probe"
        elif key == "back":
            self._goto(Screen.HOME)
            self.reload_home()

    def _profile_items(self) -> List[MenuItem]:
        prefs = self._prefs()
        profile = prefs.get_profile() if prefs else None
        name = (profile.display_name if profile else "") or "(not set)"
        bio = (profile.bio if profile else "") or "(not set)"
        if len(bio) > 40:
            bio = bio[:37] + "..."
        handle = "(not set)"
        if self.app.handles and self.app.identity:
            rec = self.app.handles.get_by_target(self.app.identity.id)
            if rec:
                handle = "@" + rec.handle
        return [
            MenuItem("name", "Display name", name),
            MenuItem("bio", "Bio", bio),
            MenuItem("handle", "Username @id", handle),
            MenuItem(
                "email",
                "Recovery email",
                (profile.recovery_email if profile and profile.recovery_email else "(not set)"),
            ),
            MenuItem("back", "< Back to chats", ""),
        ]

    def _profile_action(self) -> None:
        key = self._profile_items()[self._profile_index].key
        if key == "name":
            self._start_input("Display name: ", self._save_name)
        elif key == "bio":
            self._start_input("Bio: ", self._save_bio)
        elif key == "handle":
            self._start_input("@username: ", self._save_user_handle)
        elif key == "email":
            cur = ""
            prefs = self._prefs()
            if prefs:
                cur = prefs.get_profile().recovery_email or ""
            prompt = f"Recovery email [{cur}]: " if cur else "Recovery email: "
            self._email_return = Screen.SETTINGS
            self._start_input(prompt, self._save_email)
        elif key == "back":
            self._goto(Screen.HOME)
            self.reload_home()


    def _save_email(self, value: str) -> None:
        ret = getattr(self, "_email_return", Screen.SETTINGS)
        self.screen = ret
        value = (value or "").strip()
        if not value:
            self.status = "email unchanged"
            return
        try:
            prefs = self._prefs()
            if prefs is None:
                self.status = "prefs unavailable"
                return
            prefs.set_recovery_email(value)
            self.status = "recovery email saved: " + value
        except Exception as e:
            self.status = "email: " + str(e)[:50]


    def _save_user_handle(self, value: str) -> None:
        self.screen = Screen.PROFILE
        value = (value or "").strip().lstrip("@")
        if not value:
            self.status = "cancelled"
            return
        try:
            chk = self.app.check_handle_available(value)
            if chk.get("status") in ("taken_local", "taken_remote", "invalid"):
                self.status = str(chk.get("status")) + ": " + str(chk.get("message", ""))
                return
            h = self.app.set_user_handle(value)
            self.status = "username @" + h
        except Exception as e:
            self.status = str(e)[:60]

    def _save_room_handle(self, value: str) -> None:
        self.screen = Screen.ROOM_SETTINGS
        if not self._room_focus:
            self.status = "no room"
            return
        value = (value or "").strip().lstrip("@")
        if not value:
            self.status = "cancelled"
            return
        try:
            chk = self.app.check_handle_available(value)
            if chk.get("status") in ("taken_local", "taken_remote", "invalid"):
                self.status = str(chk.get("status")) + ": " + str(chk.get("message", ""))
                return
            h = self.app.set_room_handle(self._room_focus, value)
            self.status = "room @" + h
        except Exception as e:
            self.status = str(e)[:60]

    def _save_name(self, value: str) -> None:
        prefs = self._prefs()
        if prefs:
            prefs.set_display_name(value)
            self.status = "name saved"
        self.screen = Screen.PROFILE

    def _save_bio(self, value: str) -> None:
        prefs = self._prefs()
        if prefs:
            prefs.set_bio(value)
            self.status = "bio saved"
        self.screen = Screen.PROFILE

    def _save_contact_name(self, value: str) -> None:
        if self._profile_user and value.strip():
            self.app.set_contact_profile(self._profile_user, display_name=value.strip())
            self.status = "name saved for contact"
        self.screen = Screen.USER_PROFILE

    def _save_contact_bio(self, value: str) -> None:
        if self._profile_user:
            self.app.set_contact_profile(self._profile_user, bio=value)
            self.status = "bio saved for contact"
        self.screen = Screen.USER_PROFILE

    def _create_items(self) -> List[MenuItem]:
        return [
            MenuItem("group", "Create private group", "members by invite"),
            MenuItem("channel_pub", "Create public channel", "discoverable"),
            MenuItem("channel_priv", "Create private channel", "invite only"),
            MenuItem("back", "< Back", ""),
        ]

    def _create_action(self) -> None:
        key = self._create_items()[self._create_index].key
        if key == "back":
            self._goto(Screen.HOME)
            self.reload_home()
            return
        if key == "group":
            self._start_input("Group name: ", lambda t: self._finish_create("group", t))
        elif key == "channel_pub":
            self._start_input("Channel name: ", lambda t: self._finish_create("channel_pub", t))
        else:
            self._start_input("Channel name: ", lambda t: self._finish_create("channel_priv", t))

    def _finish_create(self, kind: str, title: str) -> None:
        if not title.strip():
            self.status = "title required"
            self.screen = Screen.CREATE
            return
        try:
            if kind == "group":
                room = self.app.create_group(title.strip())
            elif kind == "channel_pub":
                room = self.app.create_channel(title.strip(), public=True)
            else:
                room = self.app.create_channel(title.strip(), public=False)
            self.status = "created " + room.title
            self._room_focus = room.room_id
            self.reload_home()
            self.load_chat(
                None,
                conversation_id=room.room_id,
                kind="room",
                title=room.title,
            )
            self._goto(Screen.CHAT)
        except Exception as exc:
            self.status = str(exc)[:60]
            self.screen = Screen.CREATE

    def _do_search(self, query: str) -> None:
        self._search_query = query
        try:
            self._search_hits = self.app.search_directory(query)
        except Exception:
            self._search_hits = []
        self._search_index = 0
        self._goto(Screen.SEARCH)
        self.status = f"{len(self._search_hits)} results"

    def _open_search_hit(self) -> None:
        if not self._search_hits:
            return
        hit = self._search_hits[self._search_index]
        if hit.kind == "user":
            self.load_chat(hit.id, kind="dm", title=hit.title)
            self._goto(Screen.CHAT)
        elif hit.kind in ("group", "channel"):
            self._room_focus = hit.id
            self.load_chat(None, conversation_id=hit.id, kind="room", title=hit.title)
            self._goto(Screen.CHAT)
        else:
            self.load_chat(hit.id, kind="dm", title=hit.title)
            self._goto(Screen.CHAT)

    def _room_settings_items(self) -> List[MenuItem]:
        room = None
        if self._room_focus and self.app.rooms:
            room = self.app.rooms.get(self._room_focus)
        if room is None:
            return [MenuItem("back", "< Back", "room not found")]
        policy = "members"
        if self.app.room_roles:
            policy = self.app.room_roles.get_post_policy(room.room_id)
        return [
            MenuItem("title", "Title", room.title),
            MenuItem("desc", "Description", (room.description or "(empty)")[:40]),
            MenuItem("vis", "Visibility", "public" if room.is_public else "private"),
            MenuItem("policy", "Who can post", policy),
            MenuItem("handle", "Public @id", (
                (self.app.handles.get_by_target(room.room_id).handle
                 if self.app.handles and self.app.handles.get_by_target(room.room_id)
                 else "(not set)")
            )),
            MenuItem("id", "Room ID", room.room_id[:28] + "..."),
            MenuItem("back", "< Back to chats", ""),
        ]

    def _room_settings_action(self) -> None:
        key = self._room_settings_items()[self._room_settings_index].key
        if key == "back":
            self._goto(Screen.HOME)
            self.reload_home()
        elif key == "title":
            self._start_input("New title: ", self._save_room_title)
        elif key == "desc":
            self._start_input("Description: ", self._save_room_desc)
        elif key == "vis":
            if self._room_focus and self.app.rooms:
                room = self.app.rooms.get(self._room_focus)
                if room:
                    self.app.update_room(self._room_focus, is_public=not room.is_public)
                    self.status = "visibility toggled"
        elif key == "handle":
            if self._room_focus:
                self._start_input("Room @id: ", self._save_room_handle)
        elif key == "policy":
            if self._room_focus and self.app.room_roles and self.app.identity:
                if self.app.room_roles.get_role(self._room_focus, self.app.identity.id) != "owner":
                    self.status = "only owner can change policy"
                else:
                    from nyx_client.storage.room_roles import (
                        POLICY_OWNER_ONLY, POLICY_POSTERS, POLICY_MEMBERS,
                    )
                    cur = self.app.room_roles.get_post_policy(self._room_focus)
                    cycle = [POLICY_OWNER_ONLY, POLICY_POSTERS, POLICY_MEMBERS]
                    nxt = cycle[(cycle.index(cur) + 1) % len(cycle)] if cur in cycle else POLICY_OWNER_ONLY
                    self.app.room_roles.set_post_policy(self._room_focus, nxt)
                    self.status = "post policy: " + nxt

    def _save_room_title(self, value: str) -> None:
        if self._room_focus and value.strip():
            self.app.update_room(self._room_focus, title=value.strip())
            self.status = "title updated"
        self.screen = Screen.ROOM_SETTINGS

    def _save_room_desc(self, value: str) -> None:
        if self._room_focus:
            self.app.update_room(self._room_focus, description=value)
            self.status = "description updated"
        self.screen = Screen.ROOM_SETTINGS

    def _send_msg(self, text: str) -> None:
        if not text.strip() or not self.app.messaging:
            self.status = "empty message"
            self.screen = Screen.CHAT
            return
        try:
            raw = text.encode("utf-8")
            if self.chat_kind == "room" and self.chat_id:
                self.app.messaging.send_room_message(self.chat_id, raw)
                self.load_chat(
                    None,
                    conversation_id=self.chat_id,
                    kind="room",
                    title=self.chat_title,
                )
            elif self.chat_peer:
                self.app.messaging.send_dm(self.chat_peer, raw)
                try:
                    self.app.messaging.ensure_contact(
                        self.chat_peer,
                        display_name=self.chat_title if self.chat_title != self.chat_peer else None,
                    )
                except Exception:
                    pass
                self.load_chat(self.chat_peer, kind="dm", title=self.chat_title)
            else:
                self.status = "no chat target"
                self.screen = Screen.CHAT
                return
            self.screen = Screen.CHAT
            self.status = "sent"
            self.reload_home()
        except Exception as exc:
            self.status = str(exc)[:70]
            self.screen = Screen.CHAT

    # ------------------------------------------------------------------
    # Drawing — fixed header / scroll body / fixed footer
    # ------------------------------------------------------------------

    def _draw(self, stdscr: Any) -> None:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        if h < 8 or w < 40:
            try:
                stdscr.addnstr(0, 0, "Terminal too small", w - 1)
            except curses.error:
                pass
            stdscr.refresh()
            return

        header_h = 3
        # status row + shortcut row (+ input row when composing)
        footer_h = 3 if not self.input_mode else 4
        body_top = header_h
        body_h = max(1, h - header_h - footer_h)

        self._draw_header(stdscr, w, header_h)

        if self.screen == Screen.SPLASH:
            self._draw_splash(stdscr, body_top, body_h, w)
        elif self.screen == Screen.REGISTER:
            self._draw_register(stdscr, body_top, body_h, w)
        elif self.screen == Screen.HOME:
            self._draw_list(
                stdscr, body_top, body_h, w, self.items, self.selected,
                empty="No conversations yet — send a DM or create a group (n).",
            )
        elif self.screen == Screen.CHAT:
            self._draw_chat_body(stdscr, body_top, body_h, w)
        elif self.screen == Screen.SETTINGS:
            self._draw_list(
                stdscr, body_top, body_h, w, self._settings_items(), self._settings_index,
                title="Settings",
            )
        elif self.screen == Screen.THEMES:
            items = [
                MenuItem(th.id, th.name, "active" if th.id == self._theme_id else "")
                for th in list_themes()
            ]
            self._draw_list(
                stdscr, body_top, body_h, w, items, self._theme_index, title="Themes"
            )
        elif self.screen == Screen.PROFILE:
            self._draw_list(
                stdscr, body_top, body_h, w, self._profile_items(), self._profile_index,
                title="Your profile",
            )
        elif self.screen == Screen.CREATE:
            self._draw_list(
                stdscr, body_top, body_h, w, self._create_items(), self._create_index,
                title="Create",
            )
        elif self.screen == Screen.SEARCH:
            items = [
                MenuItem(h.id, f"[{h.kind}] {h.title}", h.subtitle, data=h)
                for h in self._search_hits
            ]
            self._draw_list(
                stdscr, body_top, body_h, w, items, self._search_index,
                empty="No results.",
                title=f"Search: {self._search_query}",
            )
        elif self.screen == Screen.ROOM_SETTINGS:
            self._draw_list(
                stdscr, body_top, body_h, w, self._room_settings_items(),
                self._room_settings_index, title="Room settings",
            )
        elif self.screen == Screen.USER_PROFILE:
            self._draw_user_profile(stdscr, body_top, body_h, w)
        elif self.screen == Screen.MARKET:
            self._draw_list(
                stdscr, body_top, body_h, w, self._market_items(),
                self._market_index,
                empty="No listings — press s to sell",
                title="Marketplace (NYX)",
            )
        elif self.screen == Screen.CONTACTS:
            self._draw_list(
                stdscr, body_top, body_h, w, self._contact_items(),
                self._contacts_index,
                empty="No contacts — press a to add",
                title="Contacts",
            )
        elif self.screen == Screen.WALLET:
            self._draw_wallet(stdscr, body_top, body_h, w)
        elif self.screen == Screen.HELP:
            self._draw_help(stdscr, body_top, body_h, w)

        self._draw_footer(stdscr, h, w)
        if self.input_mode:
            self._draw_input(stdscr, h, w)
        stdscr.refresh()

    def _draw_header(self, stdscr: Any, w: int, header_h: int) -> None:
        ident = ""
        if self.app.identity:
            ident = self.app.identity.id
            if len(ident) > 22:
                ident = ident[:10] + ".." + ident[-8:]
        prefs = self._prefs()
        name = ""
        if prefs:
            name = prefs.get_profile().display_name
        left = " NYX "
        if name:
            left += f" {name} "
        screen_tag = {
            Screen.HOME: "CHATS",
            Screen.CHAT: "CHAT",
            Screen.SETTINGS: "SETTINGS",
            Screen.THEMES: "THEMES",
            Screen.PROFILE: "PROFILE",
            Screen.SEARCH: "SEARCH",
            Screen.CREATE: "CREATE",
            Screen.REGISTER: "REGISTER",
            Screen.HELP: "HELP",
            Screen.ROOM_SETTINGS: "ROOM",
            Screen.USER_PROFILE: "USER",
            Screen.SPLASH: "NYX",
        }.get(self.screen, "")
        filt = {None: "ALL", "dm": "DM", "group": "GRP", "channel": "CHN"}.get(
            self.filter_type, "ALL"
        )
        bal = ""
        if self.app.wallet is not None:
            try:
                bal = " " + self.app.wallet.format_balance()
            except Exception:
                bal = ""
        right = f" [{screen_tag}]"
        if self.screen == Screen.HOME:
            right += f" {filt}"
        right += f"{bal}  {ident} "
        # keep header compact; hints live in status/help

        line1 = (left + right.rjust(max(0, w - len(left) - 1)))[: max(0, w - 1)]
        try:
            stdscr.attron(curses.color_pair(1) | curses.A_BOLD)
            stdscr.addnstr(0, 0, line1.ljust(w - 1), w - 1)
            stdscr.attroff(curses.color_pair(1) | curses.A_BOLD)
        except curses.error:
            pass
        # separator
        try:
            stdscr.attron(curses.color_pair(8))
            stdscr.addnstr(1, 0, ("=" * (w - 1))[: w - 1], w - 1)
            stdscr.attroff(curses.color_pair(8))
        except curses.error:
            try:
                stdscr.addnstr(1, 0, ("-" * (w - 1))[: w - 1], w - 1)
            except curses.error:
                pass
        # context line
        ctx = self.status
        if self.screen == Screen.CHAT:
            kind_l = "DM" if self.chat_kind == "dm" else "ROOM"
            ctx = f" {self.chat_title}  [{kind_l}]  ·  {self.status}"
        try:
            stdscr.attron(curses.color_pair(4))
            stdscr.addnstr(2, 0, ctx[: w - 1].ljust(w - 1), w - 1)
            stdscr.attroff(curses.color_pair(4))
        except curses.error:
            pass

    def _draw_footer(self, stdscr: Any, h: int, w: int) -> None:
        # status on penultimate row, chord hints on last row
        status_y = h - 2 if not self.input_mode else h - 3
        hint_y = h - 1 if not self.input_mode else h - 2
        if status_y >= 0:
            try:
                stdscr.attron(curses.color_pair(5))
                stdscr.addnstr(status_y, 0, (" " + (self.status or ""))[: w - 1].ljust(w - 1), w - 1)
                stdscr.attroff(curses.color_pair(5))
            except curses.error:
                pass
        hints = " ^N new ^B contacts ^M market ^F search ^K msg ^O connect ^Y sync ^H help ^Q "
        try:
            stdscr.attron(curses.A_REVERSE)
            stdscr.addnstr(hint_y, 0, hints.ljust(w - 1)[: w - 1], w - 1)
            stdscr.attroff(curses.A_REVERSE)
        except curses.error:
            pass

    def _draw_input(self, stdscr: Any, h: int, w: int) -> None:
        try:
            curses.curs_set(1)
        except curses.error:
            pass
        line = f" {self.input_prompt}{self.input_buf}"
        try:
            stdscr.attron(curses.color_pair(9) | curses.A_BOLD)
            stdscr.addnstr(h - 1, 0, line.ljust(w - 1)[: w - 1], w - 1)
            stdscr.attroff(curses.color_pair(9) | curses.A_BOLD)
            stdscr.move(h - 1, min(len(line), w - 2))
        except curses.error:
            pass

    def _draw_list(
        self,
        stdscr: Any,
        top: int,
        body_h: int,
        w: int,
        items: Sequence[MenuItem],
        selected: int,
        empty: str = "(empty)",
        title: str = "",
    ) -> None:
        row0 = top
        if title:
            try:
                stdscr.attron(curses.color_pair(6) | curses.A_BOLD)
                stdscr.addnstr(top, 2, title[: w - 4], w - 4)
                stdscr.attroff(curses.color_pair(6) | curses.A_BOLD)
            except curses.error:
                pass
            row0 = top + 1
            body_h = max(1, body_h - 1)

        if not items:
            try:
                stdscr.attron(curses.color_pair(5))
                stdscr.addnstr(row0 + 1, 2, empty[: w - 4], w - 4)
                stdscr.attroff(curses.color_pair(5))
            except curses.error:
                pass
            return

        start = 0
        if selected >= body_h:
            start = selected - body_h + 1
        for i in range(start, min(len(items), start + body_h)):
            item = items[i]
            y = row0 + (i - start)
            meta_w = min(len(item.meta) + 2, max(12, w // 3))
            label_w = max(8, w - meta_w - 3)
            marker = ">" if i == selected else " "
            line = f"{marker} {item.label[: label_w - 1].ljust(label_w - 1)} {item.meta[:meta_w]}"
            try:
                if i == selected:
                    stdscr.attron(curses.color_pair(2) | curses.A_BOLD)
                    stdscr.addnstr(y, 0, line.ljust(w - 1)[: w - 1], w - 1)
                    stdscr.attroff(curses.color_pair(2) | curses.A_BOLD)
                else:
                    stdscr.addnstr(y, 0, line[: w - 1], w - 1)
            except curses.error:
                pass

    def _draw_chat_body(self, stdscr: Any, top: int, body_h: int, w: int) -> None:
        """Scrollable message area; header/footer stay fixed outside."""
        total = len(self.chat_lines)
        if total == 0:
            try:
                stdscr.attron(curses.color_pair(5))
                stdscr.addnstr(top + 1, 2, "No messages yet.", w - 4)
                stdscr.addnstr(top + 3, 2, "Press Enter to write a message.", w - 4)
                if self.chat_kind == "room":
                    stdscr.addnstr(top + 4, 2, "Press o for room settings.", w - 4)
                stdscr.attroff(curses.color_pair(5))
            except curses.error:
                pass
            return
        # visible window ending near bottom, shifted by chat_scroll
        end = total - self.chat_scroll
        start = max(0, end - body_h)
        visible = self.chat_lines[start:end]
        for i, line in enumerate(visible):
            try:
                stdscr.addnstr(top + i, 1, line[: w - 2], w - 2)
            except curses.error:
                pass
        if self.chat_scroll > 0:
            try:
                stdscr.attron(curses.color_pair(6))
                stdscr.addnstr(top, w - 12, f" +{self.chat_scroll} ", 11)
                stdscr.attroff(curses.color_pair(6))
            except curses.error:
                pass

    def _draw_splash(self, stdscr: Any, top: int, body_h: int, w: int) -> None:
        lines = banner_lines(wide=(w >= 72))
        lines.append("")
        lines.append("  " + SUBTITLE)
        lines.append("")
        lines.append("  Press Enter to continue...")
        start_y = top + max(0, (body_h - len(lines)) // 2)
        for i, line in enumerate(lines):
            if start_y + i >= top + body_h:
                break
            try:
                stdscr.attron(curses.color_pair(4) | curses.A_BOLD)
                # center-ish
                x = max(0, (w - len(line)) // 2)
                stdscr.addnstr(start_y + i, x, line[: w - 1], w - 1)
                stdscr.attroff(curses.color_pair(4) | curses.A_BOLD)
            except curses.error:
                pass

    def _draw_register(self, stdscr: Any, top: int, body_h: int, w: int) -> None:
        ident = self.app.identity
        lines = [
            "  Identity Registration",
            "  " + ("-" * min(40, w - 6)),
            "",
            "  Cryptographic identity created automatically.",
            "",
        ]
        if ident:
            pub = ident.public_key_bytes.hex()
            lines += [
                f"  Identity : {ident.id}",
                f"  Public   : {pub[:36]}...",
                "  Private  : encrypted on this device only",
            ]
        if getattr(self.app, "last_mnemonic", None):
            lines += [
                "",
                "  Recovery mnemonic (store offline — never share):",
                f"  {self.app.last_mnemonic}",
            ]
        lines += ["", "  Press Enter to set your display name."]
        for i, line in enumerate(lines):
            if i >= body_h:
                break
            try:
                stdscr.addnstr(top + i, 1, line[: w - 2], w - 2)
            except curses.error:
                pass

    def _draw_user_profile(self, stdscr: Any, top: int, body_h: int, w: int) -> None:
        uid = self._profile_user or ""
        try:
            prof = self.app.get_user_profile(uid)
            lines = [
                "  User Profile",
                "  " + ("-" * min(40, w - 6)),
                "",
                f"  Name     : {prof.display_name or '(unknown)'}",
                f"  Identity : {prof.identity_id}",
                f"  Bio      : {prof.bio or '(empty)'}",
                f"  Trusted  : {'yes' if prof.trusted else 'no'}",
                f"  Self     : {'yes' if prof.is_self else 'no'}",
                "",
                "  [c] save contact   [e] name   [y] bio   [Esc] back",
            ]
        except Exception as exc:
            lines = [f"  Profile error: {exc}"]
        for i, line in enumerate(lines):
            if i >= body_h:
                break
            try:
                stdscr.addnstr(top + i, 1, line[: w - 2], w - 2)
            except curses.error:
                pass

    def _draw_help(self, stdscr: Any, top: int, body_h: int, w: int) -> None:
        lines = [
            "  NYX TUI — Keyboard shortcuts",
            "  " + ("-" * min(48, w - 6)),
            "  Navigation",
            "    Up/Down       Move / scroll",
            "    Enter         Open / confirm",
            "    Esc / q       Back (q quits on home)",
            "  Global chords",
            "    Ctrl+N        New group / channel",
            "    Ctrl+F        Search",
            "    Ctrl+B        Contacts list",
            "    Ctrl+M        Marketplace",
            "    Ctrl+W        Wallet",
            "    Ctrl+K        Compose message",
            "    Ctrl+P        Your profile",
            "    Ctrl+S        Settings",
            "    Ctrl+T        Themes",
            "    Ctrl+O        Connect + auto-sync inbox",
            "    Ctrl+Y        Sync inbox now",
            "    Ctrl+R        Refresh chat list",
            "    Ctrl+I        Profile of selection",
            "    Ctrl+H / ?    This help",
            "    Ctrl+Q        Quit",
            "  Home filters",
            "    1  all    2  DM    3  groups    4  channels",
            "  Chat",
            "    m / Enter     Compose",
            "    o             Room settings (owner)",
            "    PgUp/PgDn     Scroll faster",
            "",
            "  Esc to go back",
        ]
        for i, line in enumerate(lines):
            if i >= body_h:
                break
            try:
                stdscr.addnstr(top + i, 1, line[: w - 2], w - 2)
            except curses.error:
                pass


# Backward-compatible alias
# Backward-compatible alias
PanelApp = ProTUI
