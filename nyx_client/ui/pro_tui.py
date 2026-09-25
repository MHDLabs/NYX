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
        self.filter_type: Optional[str] = None
        self.selected = 0
        self.status = "ready"
        self.items: List[MenuItem] = []
        self.chat_peer: Optional[str] = None
        self.chat_lines: List[str] = []
        self.chat_scroll = 0  # offset from bottom
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
        convs = self.app.messages.list_conversations(
            conv_type=self.filter_type, limit=200
        )
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
            seq = c.get("last_sequence") or 0
            items.append(
                MenuItem(
                    key=c["conversation_id"],
                    label=str(name),
                    meta=f"{badge}  {when}  #{seq}",
                    data=c,
                )
            )
        self.items = items
        if self.selected >= len(self.items):
            self.selected = max(0, len(self.items) - 1)

    def load_chat(self, peer_id: str) -> None:
        self.chat_peer = peer_id
        self.chat_lines = []
        self.chat_scroll = 0
        if self.app.messaging is None:
            return
        try:
            hist = self.app.messaging.history(peer_id, limit=120)
        except Exception as exc:
            self.chat_lines = ["(history error: " + str(exc) + ")"]
            return
        for m in hist:
            arrow = ">>" if m.direction.value == "out" else "<<"
            try:
                text = m.plaintext.decode("utf-8", errors="replace")
            except Exception:
                text = "[binary]"
            name = m.sender_id[:10]
            if self.app.user_directory is not None:
                name = self.app.user_directory.display_name(m.sender_id)
            self.chat_lines.append(f" {arrow} {name}: {text}")

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
        if ch in (ord("q"),):
            if self.screen in (Screen.HOME, Screen.SPLASH):
                wipe_up(stdscr, 0.004)
                return False
            self._goto(Screen.HOME)
            self.reload_home()
            return True
        if ch == 27:
            if self.screen != Screen.HOME:
                self._goto(Screen.HOME)
                self.reload_home()
            return True
        if ch == ord("?"):
            self._goto(Screen.HELP)
            return True
        if ch == ord("s"):
            self._goto(Screen.SETTINGS)
            self._settings_index = 0
            return True
        if ch == ord("p"):
            self._goto(Screen.PROFILE)
            self._profile_index = 0
            return True
        if ch == ord("t"):
            self._goto(Screen.THEMES)
            self._theme_index = 0
            return True
        if ch == ord("1"):
            self.filter_type = None
            self._goto(Screen.HOME)
            self.selected = 0
            self.reload_home()
            self.status = "all chats"
            return True
        if ch == ord("2"):
            self.filter_type = "dm"
            self._goto(Screen.HOME)
            self.selected = 0
            self.reload_home()
            self.status = "DMs only"
            return True
        if ch == ord("3"):
            self.filter_type = "private_group"
            self._goto(Screen.HOME)
            self.selected = 0
            self.reload_home()
            self.status = "groups only"
            return True
        if ch == ord("4"):
            self.filter_type = "private_channel"
            self._goto(Screen.HOME)
            self.selected = 0
            self.reload_home()
            self.status = "channels only"
            return True
        if ch == ord("r"):
            self.reload_home()
            self.status = "refreshed"
            return True
        if ch == ord("n"):
            self._goto(Screen.CREATE)
            self._create_index = 0
            return True
        if ch in (ord("/"), ord("f")):
            self._start_input("Search: ", self._do_search)
            return True
        if ch == ord("i"):
            target = None
            if self.screen == Screen.CHAT and self.chat_peer:
                target = self.chat_peer
            elif self.screen == Screen.HOME and self.items:
                data = self.items[self.selected].data or {}
                target = data.get("peer_id")
            elif self.screen == Screen.SEARCH and self._search_hits:
                hit = self._search_hits[self._search_index]
                if getattr(hit, "kind", "") == "user":
                    target = hit.id
            if target:
                self._profile_user = target
                self._goto(Screen.USER_PROFILE)
                self.status = "profile"
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
                if self.chat_peer:
                    self._start_input("Message: ", self._send_msg)
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

        elif self.screen == Screen.HELP:
            if ch in (10, 13, 27, ord("b")):
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
        if peer:
            self.load_chat(peer)
            self._goto(Screen.CHAT)
            self.status = peer[:28]
        elif ctype in ("private_group", "private_channel", "public_channel"):
            self._room_focus = data.get("conversation_id")
            self._goto(Screen.ROOM_SETTINGS)
            self._room_settings_index = 0
        else:
            self.status = "cannot open"

    # ------------------------------------------------------------------
    # Actions (settings / profile / create / search / room)
    # ------------------------------------------------------------------

    def _settings_items(self) -> List[MenuItem]:
        return [
            MenuItem("theme", "Color theme", get_theme(self._theme_id).name),
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
        elif key == "update":
            try:
                r = self.app.check_updates()
                if r.update_available and r.candidate:
                    self.status = f"update {r.candidate.version} available"
                else:
                    self.status = f"up to date ({r.current_version})"
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
        return [
            MenuItem("name", "Display name", name),
            MenuItem("bio", "Bio", bio),
            MenuItem("back", "< Back to chats", ""),
        ]

    def _profile_action(self) -> None:
        key = self._profile_items()[self._profile_index].key
        if key == "name":
            self._start_input("Display name: ", self._save_name)
        elif key == "bio":
            self._start_input("Bio: ", self._save_bio)
        elif key == "back":
            self._goto(Screen.HOME)
            self.reload_home()

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
            self._goto(Screen.ROOM_SETTINGS)
            self.reload_home()
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
            self.load_chat(hit.id)
            self._goto(Screen.CHAT)
        elif hit.kind in ("group", "channel"):
            self._room_focus = hit.id
            self._goto(Screen.ROOM_SETTINGS)
        else:
            self.load_chat(hit.id)
            self._goto(Screen.CHAT)

    def _room_settings_items(self) -> List[MenuItem]:
        room = None
        if self._room_focus and self.app.rooms:
            room = self.app.rooms.get(self._room_focus)
        if room is None:
            return [MenuItem("back", "< Back", "room not found")]
        return [
            MenuItem("title", "Title", room.title),
            MenuItem("desc", "Description", (room.description or "(empty)")[:40]),
            MenuItem("vis", "Visibility", "public" if room.is_public else "private"),
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
        if not text.strip() or not self.chat_peer or not self.app.messaging:
            self.status = "empty message"
            if self.chat_peer:
                self.screen = Screen.CHAT
            return
        try:
            self.app.messaging.send_dm(self.chat_peer, text.encode("utf-8"))
            self.load_chat(self.chat_peer)
            self.screen = Screen.CHAT
            self.status = "sent"
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
        footer_h = 2 if not self.input_mode else 3
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
        filt = {None: "ALL", "dm": "DM", "private_group": "GRP", "private_channel": "CHN"}.get(
            self.filter_type, "ALL"
        )
        right = f" [{screen_tag}]"
        if self.screen == Screen.HOME:
            right += f" {filt}"
        right += f"  {ident} "
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
        if self.screen == Screen.CHAT and self.chat_peer:
            peer_name = self.chat_peer
            if self.app.user_directory:
                peer_name = self.app.user_directory.display_name(self.chat_peer)
            ctx = f" {peer_name}  ·  {self.status}"
        try:
            stdscr.attron(curses.color_pair(4))
            stdscr.addnstr(2, 0, ctx[: w - 1].ljust(w - 1), w - 1)
            stdscr.attroff(curses.color_pair(4))
        except curses.error:
            pass

    def _draw_footer(self, stdscr: Any, h: int, w: int) -> None:
        y = h - 1
        if self.input_mode:
            y = h - 2
        hints = " Up/Dn Enter | 1-4 filter | n New | / Search | i Profile | s Set | t Theme | ? | q "
        try:
            stdscr.attron(curses.A_REVERSE)
            stdscr.addnstr(y, 0, hints.ljust(w - 1)[: w - 1], w - 1)
            stdscr.attroff(curses.A_REVERSE)
        except curses.error:
            pass

    def _draw_input(self, stdscr: Any, h: int, w: int) -> None:
        curses.curs_set(1)
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
                stdscr.addnstr(top + 1, 2, "No messages yet. Press Enter to compose.", w - 4)
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
                "  [e] edit name   [y] edit bio   [Esc] back",
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
            "  Keyboard",
            "  " + ("-" * min(40, w - 6)),
            "  Up/Down     Move selection / scroll chat",
            "  Enter       Open / confirm / compose",
            "  Esc         Back",
            "  1..4        Filter chats / DM / group / channel",
            "  n           Create group or channel",
            "  / or f      Search users, groups, channels",
            "  i           View user profile (name + bio)",
            "  p           Your profile",
            "  s           Settings",
            "  t           Themes",
            "  m           Compose message",
            "  r           Refresh list",
            "  ?           Help",
            "  q           Quit",
        ]
        for i, line in enumerate(lines):
            if i >= body_h:
                break
            try:
                stdscr.addnstr(top + i, 1, line[: w - 2], w - 2)
            except curses.error:
                pass


# Backward-compatible alias
PanelApp = ProTUI
