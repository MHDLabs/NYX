"""
Professional multi-panel Terminal UI for NYX.

Navigation model (whitepaper Section 07 — keyboard-driven):
  Up / Down   : move selection
  Enter       : open / confirm
  Esc / q     : back / quit panel
  1           : all chats (sorted by last message)
  2           : DMs only
  3           : groups only
  4           : channels only
  s           : settings
  p           : profile (name / bio)
  /           : command line (escape hatch)
  ?           : help

Works with stdlib curses. On Windows install windows-curses.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, List, Optional, Sequence

from nyx_client.config.logging import get_logger
from nyx_client.core.app import NyxApp

log = get_logger(__name__)

try:
    import curses
except ImportError as _err:
    curses = None  # type: ignore[assignment]
    _CURSES_ERR = _err
else:
    _CURSES_ERR = None


class Screen(Enum):
    HOME = auto()
    CHAT = auto()
    SETTINGS = auto()
    PROFILE = auto()
    HELP = auto()
    COMPOSE = auto()
    REGISTER = auto()
    CREATE = auto()
    SEARCH = auto()
    ROOM_SETTINGS = auto()
    USER_PROFILE = auto()


@dataclass
class MenuItem:
    key: str
    label: str
    meta: str = ""
    data: Any = None


def _fmt_time(ts: int) -> str:
    if ts <= 0:
        return ""
    # ts may be ms or seconds
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
    }.get(conv_type, conv_type[:3].upper() or "???")


class PanelApp:
    """Main keyboard-driven panel application."""

    def __init__(self, app: NyxApp) -> None:
        self.app = app
        self.screen = Screen.HOME
        self.filter_type: Optional[str] = None  # None = all
        self.selected = 0
        self.status = "ready"
        self.items: List[MenuItem] = []
        self.chat_peer: Optional[str] = None
        self.chat_lines: List[str] = []
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
        self._register_ack = False
        self._profile_user: Optional[str] = None

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
            contact = None
            if peer:
                contact = self.app.contacts.get(peer)
            name = (
                (contact.display_name if contact and contact.display_name else None)
                or title
                or (peer[:20] + "..." if peer else c["conversation_id"][:24])
            )
            badge = _type_badge(str(c.get("type") or "dm"))
            when = _fmt_time(int(c.get("updated_at") or 0))
            seq = c.get("last_sequence") or 0
            meta = f"{badge}  {when}  #{seq}"
            items.append(
                MenuItem(
                    key=c["conversation_id"],
                    label=name,
                    meta=meta,
                    data=c,
                )
            )
        self.items = items
        if self.selected >= len(self.items):
            self.selected = max(0, len(self.items) - 1)

    def load_chat(self, peer_id: str) -> None:
        self.chat_peer = peer_id
        self.chat_lines = []
        if self.app.messaging is None:
            return
        try:
            hist = self.app.messaging.history(peer_id, limit=80)
        except Exception as exc:
            self.chat_lines = ["(history error: " + str(exc) + ")"]
            return
        for m in hist:
            arrow = "->" if m.direction.value == "out" else "<-"
            try:
                text = m.plaintext.decode("utf-8", errors="replace")
            except Exception:
                text = "[binary]"
            name = m.sender_id[:10]
            if self.app.user_directory is not None:
                name = self.app.user_directory.display_name(m.sender_id)
            self.chat_lines.append(" {0} {1}: {2}".format(arrow, name, text))

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def open_selected(self) -> None:
        if self.screen == Screen.HOME:
            if not self.items:
                self.status = "no conversations yet"
                return
            item = self.items[self.selected]
            data = item.data or {}
            peer = data.get("peer_id") or ""
            if not peer:
                self.status = "conversation has no peer"
                return
            self.load_chat(peer)
            self.screen = Screen.CHAT
            self.status = peer[:28]
        elif self.screen == Screen.SETTINGS:
            self._settings_action()
        elif self.screen == Screen.PROFILE:
            self._profile_action()

    def _settings_action(self) -> None:
        opts = self._settings_items()
        if not opts:
            return
        key = opts[self._settings_index].key
        if key == "servers":
            self.status = "use /servers in command mode"
        elif key == "update":
            try:
                r = self.app.check_updates()
                if r.update_available and r.candidate:
                    self.status = f"update {r.candidate.version} available"
                else:
                    self.status = f"up to date ({r.current_version})"
            except Exception as exc:
                self.status = str(exc)[:60]
        elif key == "back":
            self.screen = Screen.HOME
            self.reload_home()

    def _profile_action(self) -> None:
        opts = self._profile_items()
        if not opts:
            return
        key = opts[self._profile_index].key
        if key == "name":
            self._start_input("Display name: ", self._save_name)
        elif key == "bio":
            self._start_input("Bio: ", self._save_bio)
        elif key == "back":
            self.screen = Screen.HOME
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

    def _prefs(self):
        from nyx_client.storage.user_prefs import UserPrefs
        if self.app.db is None:
            return None
        return UserPrefs(self.app.db)

    def _start_input(self, prompt: str, cb: Callable[[str], None]) -> None:
        self.input_mode = True
        self.input_buf = ""
        self.input_prompt = prompt
        self.input_callback = cb

    def _settings_items(self) -> List[MenuItem]:
        return [
            MenuItem("servers", "Server list & ranking", "latency / trust"),
            MenuItem("update", "Check for updates", "signed manifests"),
            MenuItem("back", "< Back to chats", ""),
        ]

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

    # ------------------------------------------------------------------
    # Curses loop
    # ------------------------------------------------------------------

    def run(self) -> int:
        if curses is None:
            print("Curses UI unavailable.")
            print("Install: pip install windows-curses")
            print("Or use:  python -m nyx_client.main --repl")
            if _CURSES_ERR:
                print("Detail:", _CURSES_ERR)
            return 1
        if getattr(self.app, "is_new_identity", False) or getattr(self.app, "last_mnemonic", None):
            self.screen = Screen.REGISTER
        else:
            self.reload_home()
        try:
            return curses.wrapper(self._main)
        except curses.error as exc:
            print("Curses error:", exc)
            return 1

    def _main(self, stdscr: Any) -> int:
        curses.curs_set(0)
        stdscr.keypad(True)
        stdscr.timeout(200)
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_CYAN, -1)      # header
            curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_CYAN)  # selected
            curses.init_pair(3, curses.COLOR_GREEN, -1)     # ok
            curses.init_pair(4, curses.COLOR_YELLOW, -1)    # meta
            curses.init_pair(5, curses.COLOR_WHITE, -1)     # normal
            curses.init_pair(6, curses.COLOR_MAGENTA, -1)   # badge area

        while True:
            self._draw(stdscr)
            try:
                ch = stdscr.getch()
            except KeyboardInterrupt:
                return 0
            if ch == -1:
                continue
            if self.input_mode:
                if not self._handle_input(ch):
                    return 0
                continue
            if not self._handle_key(ch):
                return 0

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
        if ch == 27:  # Esc cancel
            self.input_mode = False
            self.input_buf = ""
            self.input_callback = None
            self.status = "cancelled"
            return True
        if ch in (curses.KEY_BACKSPACE, 127, 8):
            self.input_buf = self.input_buf[:-1]
            return True
        if 32 <= ch <= 126:
            if len(self.input_buf) < 200:
                self.input_buf += chr(ch)
        return True

    def _handle_key(self, ch: int) -> bool:
        # Global
        if ch in (ord("q"),):
            if self.screen == Screen.HOME:
                return False
            self.screen = Screen.HOME
            self.reload_home()
            return True
        if ch == 27:  # Esc
            if self.screen != Screen.HOME:
                self.screen = Screen.HOME
                self.reload_home()
            return True
        if ch == ord("?"):
            self.screen = Screen.HELP
            return True
        if ch == ord("s"):
            self.screen = Screen.SETTINGS
            self._settings_index = 0
            return True
        if ch == ord("p"):
            self.screen = Screen.PROFILE
            self._profile_index = 0
            return True
        if ch == ord("1"):
            self.filter_type = None
            self.screen = Screen.HOME
            self.selected = 0
            self.reload_home()
            self.status = "all chats"
            return True
        if ch == ord("2"):
            self.filter_type = "dm"
            self.screen = Screen.HOME
            self.selected = 0
            self.reload_home()
            self.status = "DMs only"
            return True
        if ch == ord("3"):
            self.filter_type = "private_group"
            self.screen = Screen.HOME
            self.selected = 0
            self.reload_home()
            self.status = "groups only"
            return True
        if ch == ord("4"):
            self.filter_type = "private_channel"
            self.screen = Screen.HOME
            self.selected = 0
            self.reload_home()
            self.status = "channels only"
            return True
        if ch == ord("r"):
            self.reload_home()
            self.status = "refreshed"
            return True
        if ch == ord("n"):
            self.screen = Screen.CREATE
            self._create_index = 0
            return True
        if ch == ord("/") or ch == ord("f"):
            self._start_input("Search: ", self._do_search)
            return True
        if ch == ord("i"):
            # View profile of selected chat / search hit / peer
            target = None
            if self.screen == Screen.CHAT and self.chat_peer:
                target = self.chat_peer
            elif self.screen == Screen.HOME and self.items:
                data = self.items[self.selected].data or {}
                target = data.get("peer_id")
            elif self.screen == Screen.SEARCH and self._search_hits:
                hit = self._search_hits[self._search_index]
                if hit.kind == "user":
                    target = hit.id
            if target:
                self._profile_user = target
                self.screen = Screen.USER_PROFILE
                self.status = "profile"
            return True

        # Screen-specific navigation
        if self.screen == Screen.HOME:
            n = len(self.items)
            if ch == curses.KEY_UP and n:
                self.selected = (self.selected - 1) % n
            elif ch == curses.KEY_DOWN and n:
                self.selected = (self.selected + 1) % n
            elif ch in (10, 13, curses.KEY_ENTER):
                self.open_selected()
            elif ch == ord("m") and n:
                # compose message to selected peer
                item = self.items[self.selected]
                peer = (item.data or {}).get("peer_id")
                if peer:
                    self.chat_peer = peer
                    self._start_input("Message: ", self._send_msg)

        elif self.screen == Screen.CHAT:
            if ch in (10, 13, curses.KEY_ENTER, ord("m")):
                if self.chat_peer:
                    self._start_input("Message: ", self._send_msg)
            elif ch == curses.KEY_LEFT or ch == ord("b"):
                self.screen = Screen.HOME
                self.reload_home()

        elif self.screen == Screen.SETTINGS:
            opts = self._settings_items()
            n = len(opts)
            if ch == curses.KEY_UP and n:
                self._settings_index = (self._settings_index - 1) % n
            elif ch == curses.KEY_DOWN and n:
                self._settings_index = (self._settings_index + 1) % n
            elif ch in (10, 13, curses.KEY_ENTER):
                self.open_selected()

        elif self.screen == Screen.PROFILE:
            opts = self._profile_items()
            n = len(opts)
            if ch == curses.KEY_UP and n:
                self._profile_index = (self._profile_index - 1) % n
            elif ch == curses.KEY_DOWN and n:
                self._profile_index = (self._profile_index + 1) % n
            elif ch in (10, 13, curses.KEY_ENTER):
                self.open_selected()

        elif self.screen == Screen.HELP:
            if ch in (10, 13, 27, ord("b"), ord("q")):
                self.screen = Screen.HOME
                self.reload_home()

        elif self.screen == Screen.USER_PROFILE:
            if ch in (10, 13, 27, ord("b"), ord("q"), curses.KEY_LEFT):
                if self.chat_peer:
                    self.screen = Screen.CHAT
                else:
                    self.screen = Screen.HOME
                    self.reload_home()
            elif ch == ord("e") and self._profile_user:
                # edit local notes/name for this contact
                self._start_input("Set display name: ", self._save_contact_name)


        elif self.screen == Screen.REGISTER:
            if ch in (10, 13, curses.KEY_ENTER):
                self._register_ack = True
                self.screen = Screen.PROFILE
                self._profile_index = 0
                self.status = "set your display name"
            elif ch == ord("c"):
                # copy-ish: just acknowledge keys generated
                self.status = "keys stored encrypted locally"

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

        return True

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


    def _create_items(self) -> List[MenuItem]:
        return [
            MenuItem("group", "Create private group", "members by invite"),
            MenuItem("channel_pub", "Create public channel", "discoverable title"),
            MenuItem("channel_priv", "Create private channel", "invite only"),
            MenuItem("back", "< Back", ""),
        ]

    def _create_action(self) -> None:
        opts = self._create_items()
        key = opts[self._create_index].key
        if key == "back":
            self.screen = Screen.HOME
            self.reload_home()
            return
        if key == "group":
            self._start_input("Group name: ", lambda t: self._finish_create("group", t))
        elif key == "channel_pub":
            self._start_input("Channel name: ", lambda t: self._finish_create("channel_pub", t))
        elif key == "channel_priv":
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
            self.screen = Screen.ROOM_SETTINGS
            self._room_settings_index = 0
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
        self.screen = Screen.SEARCH
        self.status = f"{len(self._search_hits)} results"

    def _open_search_hit(self) -> None:
        if not self._search_hits:
            return
        hit = self._search_hits[self._search_index]
        if hit.kind == "user":
            self.load_chat(hit.id)
            self.screen = Screen.CHAT
        elif hit.kind in ("group", "channel"):
            self._room_focus = hit.id
            self.screen = Screen.ROOM_SETTINGS
            self._room_settings_index = 0
        else:
            self.load_chat(hit.id)
            self.screen = Screen.CHAT

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
        opts = self._room_settings_items()
        key = opts[self._room_settings_index].key
        if key == "back":
            self.screen = Screen.HOME
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

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def _draw(self, stdscr: Any) -> None:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        self._draw_header(stdscr, w)
        if self.screen == Screen.HOME:
            self._draw_list(stdscr, h, w, self.items, self.selected, empty="No conversations yet — messages will appear here.")
        elif self.screen == Screen.CHAT:
            self._draw_chat(stdscr, h, w)
        elif self.screen == Screen.SETTINGS:
            self._draw_list(stdscr, h, w, self._settings_items(), self._settings_index, title="Settings")
        elif self.screen == Screen.PROFILE:
            self._draw_list(stdscr, h, w, self._profile_items(), self._profile_index, title="Profile")
        elif self.screen == Screen.HELP:
            self._draw_help(stdscr, h, w)

        elif self.screen == Screen.REGISTER:
            self._draw_register(stdscr, h, w)
        elif self.screen == Screen.CREATE:
            self._draw_list(stdscr, h, w, self._create_items(), self._create_index, title="Create")
        elif self.screen == Screen.SEARCH:
            items = [
                MenuItem(h.id, f"[{h.kind}] {h.title}", h.subtitle, data=h)
                for h in self._search_hits
            ]
            self._draw_list(
                stdscr, h, w, items, self._search_index,
                empty="No results.",
                title=f"Search: {self._search_query}",
            )
        elif self.screen == Screen.ROOM_SETTINGS:
            self._draw_list(
                stdscr, h, w, self._room_settings_items(), self._room_settings_index,
                title="Room settings",
            )
        elif self.screen == Screen.USER_PROFILE:
            self._draw_user_profile(stdscr, h, w)
        self._draw_footer(stdscr, h, w)
        if self.input_mode:
            self._draw_input(stdscr, h, w)
        stdscr.refresh()

    def _draw_header(self, stdscr: Any, w: int) -> None:
        ident = ""
        if self.app.identity:
            ident = self.app.identity.id
            if len(ident) > 28:
                ident = ident[:26] + ".."
        prefs = self._prefs()
        name = ""
        if prefs:
            name = prefs.get_profile().display_name
        title = " NYX "
        if name:
            title += f"· {name} "
        title += f"· {ident} "
        filt = {None: "ALL", "dm": "DM", "private_group": "GRP", "private_channel": "CHN"}.get(
            self.filter_type, "ALL"
        )
        if self.screen == Screen.HOME:
            title += f"[{filt}] "
        try:
            stdscr.attron(curses.color_pair(1) | curses.A_BOLD)
            stdscr.addnstr(0, 0, title.ljust(max(0, w - 1)), max(0, w - 1))
            stdscr.attroff(curses.color_pair(1) | curses.A_BOLD)
        except curses.error:
            pass

    def _draw_list(
        self,
        stdscr: Any,
        h: int,
        w: int,
        items: Sequence[MenuItem],
        selected: int,
        empty: str = "(empty)",
        title: str = "",
    ) -> None:
        row0 = 1
        if title:
            try:
                stdscr.attron(curses.color_pair(4))
                stdscr.addnstr(1, 1, title, w - 2)
                stdscr.attroff(curses.color_pair(4))
            except curses.error:
                pass
            row0 = 2

        body_h = max(1, h - row0 - 2)
        if not items:
            try:
                stdscr.addnstr(row0 + 1, 2, empty[: w - 4], w - 4)
            except curses.error:
                pass
            return

        # scroll window around selection
        start = 0
        if selected >= body_h:
            start = selected - body_h + 1
        for i in range(start, min(len(items), start + body_h)):
            item = items[i]
            y = row0 + (i - start)
            label = item.label
            meta = item.meta
            # layout: label left, meta right
            meta_w = min(len(meta) + 1, w // 3)
            label_w = max(8, w - meta_w - 4)
            line = f" {label[:label_w-1].ljust(label_w-1)} {meta[:meta_w]}"
            try:
                if i == selected:
                    stdscr.attron(curses.color_pair(2) | curses.A_BOLD)
                    stdscr.addnstr(y, 0, line.ljust(w - 1)[: w - 1], w - 1)
                    stdscr.attroff(curses.color_pair(2) | curses.A_BOLD)
                else:
                    stdscr.addnstr(y, 0, line[: w - 1], w - 1)
            except curses.error:
                pass

    def _draw_chat(self, stdscr: Any, h: int, w: int) -> None:
        peer = self.chat_peer or ""
        try:
            stdscr.attron(curses.color_pair(4))
            stdscr.addnstr(1, 1, f"Chat · {peer[: w - 10]}", w - 2)
            stdscr.attroff(curses.color_pair(4))
        except curses.error:
            pass
        body_h = max(1, h - 4)
        lines = self.chat_lines[-body_h:]
        for i, line in enumerate(lines):
            try:
                stdscr.addnstr(2 + i, 1, line[: w - 2], w - 2)
            except curses.error:
                pass
        try:
            stdscr.addnstr(h - 3, 1, "[Enter] message  [Esc] back", w - 2)
        except curses.error:
            pass

    def _draw_help(self, stdscr: Any, h: int, w: int) -> None:
        lines = [
            "Keyboard shortcuts",
            "",
            "  ↑ ↓       Move selection",
            "  Enter     Open / confirm",
            "  Esc       Back",
            "  1         All chats (by last message)",
            "  2         Direct messages only",
            "  3         Groups only",
            "  4         Channels only",
            "  s         Settings",
            "  p         Profile (name / bio)",
            "  m         Compose message",
            "  n         Create group / channel",
            "  / or f    Search users, groups, channels",
            "  i         View user profile (name + bio)",
            "  r         Refresh list",
            "  ?         This help",
            "  q         Quit (from home)",
        ]
        for i, line in enumerate(lines):
            if i + 1 >= h - 2:
                break
            try:
                stdscr.addnstr(1 + i, 2, line[: w - 4], w - 4)
            except curses.error:
                pass

    def _draw_footer(self, stdscr: Any, h: int, w: int) -> None:
        bar = f" {self.status} "
        hints = " |Up/Dn|Enter|1-4 filter|n New|/ Search|s Set|p Profile|?|q"
        line = (bar + hints)[: max(0, w - 1)]
        try:
            stdscr.attron(curses.A_REVERSE)
            stdscr.addnstr(h - 1, 0, line.ljust(w - 1), w - 1)
            stdscr.attroff(curses.A_REVERSE)
        except curses.error:
            pass

    def _draw_input(self, stdscr: Any, h: int, w: int) -> None:
        curses.curs_set(1)
        line = f" {self.input_prompt}{self.input_buf}"
        try:
            stdscr.attron(curses.color_pair(3))
            stdscr.addnstr(h - 2, 0, line.ljust(w - 1)[: w - 1], w - 1)
            stdscr.attroff(curses.color_pair(3))
            stdscr.move(h - 2, min(len(line), w - 2))
        except curses.error:
            pass
        curses.curs_set(0)
