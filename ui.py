from __future__ import annotations

"""NYX Client UI - Merged Textual TUI and REPL UI"""

import asyncio
import hashlib
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, TextIO

from rich.align import Align
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Input, Label, ListItem, ListView, Static

# Ensure client directory is on sys.path for direct script execution and language servers
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from commands import CommandContext, CommandRegistry, registry
    from config import get_logger
except ImportError:
    from .commands import CommandContext, CommandRegistry, registry
    from .config import get_logger

log = get_logger(__name__)

VERSION = "0.0.6"


def _conversation_id(id1: str, id2: str) -> str:
    """Generate deterministic conversation ID matching db.ensure_conversation()."""
    import json
    sorted_ids = sorted([id1, id2])
    conv_hash = hashlib.sha256(json.dumps(sorted_ids).encode()).hexdigest()[:16]
    return f"conv_{conv_hash}"


# =============================================================================
# Theme System
# =============================================================================


@dataclass(frozen=True)
class Theme:
    id: str
    name: str
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
}

DEFAULT_THEME_ID = "midnight"
THEME_ORDER = ["midnight"]


def get_theme(theme_id: str) -> Theme:
    return THEMES.get(theme_id, THEMES[DEFAULT_THEME_ID])


# =============================================================================
# REPL UI
# =============================================================================


BANNER = """
+------------------------------------------+
|          NYX Client  REPL                |
|  Type /help for commands, /exit to quit  |
+------------------------------------------+
"""


class ReplUI:
    """
    Simple line-oriented terminal interface.

    Provides a REPL (Read-Eval-Print Loop) for the NYX client that works
    with zero extra dependencies. All commands are dispatched through the
    shared CommandRegistry, ensuring consistency with the Textual TUI.
    """

    def __init__(
        self,
        ctx: CommandContext,
        commands: Optional[CommandRegistry] = None,
        stdin: Optional[TextIO] = None,
        stdout: Optional[TextIO] = None,
    ) -> None:
        self.ctx = ctx
        self.commands = commands or registry
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout

    def print(self, text: str) -> None:
        self.stdout.write(text + "\n")
        self.stdout.flush()

    def run_line(self, line: str) -> bool:
        result = self.commands.dispatch(self.ctx, line)

        if result.message == "EXIT":
            self.print("Goodbye.")
            return False

        if result.message:
            prefix = "" if result.ok else "error: "
            self.print(prefix + result.message)

        return True

    def run(self) -> int:
        self.print(BANNER)

        if self.ctx.identity_id:
            self.print("  Identity: " + self.ctx.identity_id)

        self.print("")

        while True:
            try:
                self.stdout.write("nyx > ")
                self.stdout.flush()

                line = self.stdin.readline()
                if line == "":
                    break

                line = line.strip()
                if not line:
                    continue

                if not self.run_line(line):
                    break

            except KeyboardInterrupt:
                self.print("\n(interrupted — type /exit to quit)")
            except EOFError:
                break

        return 0


# =============================================================================
# TUI Classes
# =============================================================================


class HeaderBar(Static):
    """Custom header for NYX."""

    status = reactive("Disconnected")
    identity = reactive("Unknown")
    avatar = reactive("👤")

    def render(self) -> str:
        status_icon = "🟢" if self.status == "Connected" else "🔴"
        return (
            f" [bold]NYX[/bold] v{VERSION} │ "
            f"{status_icon} {self.status} │ "
            f"{self.avatar} {self.identity} "
        )


class ContactItem(ListItem):
    """An item in the contact list (contact or group)."""

    def __init__(self, contact: Dict[str, Any], unread_count: int = 0, is_group: bool = False):
        super().__init__()
        self.contact = contact
        self.is_group = is_group

        if is_group:
            self.device_id = contact.get("room_id", "") or contact.get("conversation_id", "")
            self.alias = contact.get("title") or contact.get("name") or "Unnamed Group"
        else:
            self.device_id = contact.get("identity_id", "")
            self.alias = (
                contact.get("name")
                or contact.get("display_name")
                or (self.device_id[:12] if self.device_id else "Unknown")
            )

        self.unread_count = unread_count

    def compose(self) -> ComposeResult:
        unread_badge = f" [bold red]({self.unread_count})[/bold red]" if self.unread_count > 0 else ""
        prefix = "👥" if self.is_group else "👤"
        yield Label(f"{prefix} {self.alias}{unread_badge}")


class ChatMessage(Static):
    """A single message bubble with markup injection protection."""

    def __init__(
        self,
        sender: str,
        content: str,
        timestamp: datetime,
        is_self: bool = False,
        is_group: bool = False,
    ):
        super().__init__()
        self.sender = sender
        self.content = content
        self.timestamp = timestamp
        self.is_self = is_self
        self.is_group = is_group

    def render(self) -> Align:
        time_str = self.timestamp.strftime("%H:%M")
        text = Text()

        if self.is_self:
            text.append("You: ", style="bold green")
        elif self.is_group:
            text.append(f"{self.sender}: ", style="bold blue")

        text.append(self.content)
        text.append(f"\n{time_str}", style="dim")

        return Align.right(text) if self.is_self else Align.left(text)


class HelpScreen(ModalScreen[None]):
    """Help overlay showing all key bindings."""

    BINDINGS = [("escape", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        help_text = f"""
╔═══════════════════════════════════════════════════════════╗
║           NYX v{VERSION} - KEY BINDINGS REFERENCE            ║
╠═══════════════════════════════════════════════════════════╣
║ GLOBAL BINDINGS:                                         ║
║   Ctrl+Q     - Quit application                          ║
║   Ctrl+H     - Show this help                            ║
║   Ctrl+R     - Force sync messages                       ║
║   Ctrl+N     - New (Contact/Group)                       ║
║   Ctrl+F     - Toggle fullscreen chat                    ║
║   Ctrl+Shift+S - Save screenshot                        ║
║   Ctrl+L     - Clear chat display                        ║
║   Ctrl+D     - Delete selected contact/group             ║
║                                                          ║
║ CONTACT LIST:                                            ║
║   Up/Down    - Navigate contacts                         ║
║   Enter      - Open chat with contact                    ║
║   Delete     - Delete selected contact                   ║
║                                                          ║
║ NAVIGATION:                                              ║
║   Tab        - Focus next widget                         ║
║   Shift+Tab  - Focus previous widget                     ║
║   Escape     - Go back/Cancel                            ║
╚═══════════════════════════════════════════════════════════╝
"""
        with Container(id="help_modal"):
            yield Static(help_text, id="help_content")
            yield Button("Close [Esc]", id="help_close_btn")

    @on(Button.Pressed, "#help_close_btn")
    def close_help(self):
        self.dismiss()


class ServerSettingsScreen(ModalScreen[Optional[str]]):
    """Server settings dialog."""

    BINDINGS = [("escape", "dismiss", "Cancel")]

    def __init__(self, current_url: str):
        super().__init__()
        self.current_url = current_url

    def compose(self) -> ComposeResult:
        with Container(id="server_modal"):
            yield Static("╔═══════════ SERVER SETTINGS ════════════╗", id="server_title")
            yield Label(f"Current: {self.current_url}")
            yield Input(placeholder="Enter new server URL", id="server_url_input", value=self.current_url)
            yield Static("", id="test_result")

            with Horizontal(id="server_buttons"):
                yield Button("Test Connection", id="test_btn", variant="primary")
                yield Button("Save & Reconnect", id="save_btn", variant="success")
                yield Button("Cancel [Esc]", id="cancel_btn")

    @on(Button.Pressed, "#test_btn")
    async def test_connection(self):
        input_widget = self.query_one("#server_url_input", Input)
        url = input_widget.value.strip()

        if not url:
            self.query_one("#test_result", Static).update("⚠ Please enter a URL")
            return

        result_widget = self.query_one("#test_result", Static)
        result_widget.update("⏳ Testing connection...")

        def _test():
            req = urllib.request.Request(f"{url}/api/v3/health", method="GET")
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status

        try:
            status = await asyncio.to_thread(_test)
            if status == 200:
                result_widget.update("[green]✓ Connected successfully[/green]")
            else:
                result_widget.update(f"[red]✗ Failed: HTTP {status}[/red]")
        except urllib.error.URLError as e:
            result_widget.update(f"[red]✗ Failed: {str(e)[:50]}[/red]")
        except Exception as e:
            result_widget.update(f"[red]✗ Failed: {str(e)[:50]}[/red]")

    @on(Button.Pressed, "#save_btn")
    def save_settings(self):
        input_widget = self.query_one("#server_url_input", Input)
        url = input_widget.value.strip()

        if url:
            self.dismiss(url)
        else:
            self.query_one("#test_result", Static).update("⚠ Please enter a URL")

    @on(Button.Pressed, "#cancel_btn")
    def cancel(self):
        self.dismiss(None)


class AddContactScreen(ModalScreen[Optional[Dict[str, str]]]):
    """Add contact dialog."""

    BINDINGS = [("escape", "dismiss", "Cancel")]

    def compose(self) -> ComposeResult:
        with Container(id="add_contact_modal"):
            yield Static("╔═══════════ ADD NEW CONTACT ════════════╗", id="add_contact_title")
            yield Input(placeholder="Paste nyx1... address", id="contact_address_input")
            yield Input(placeholder="Alias (optional)", id="contact_alias_input")

            with Horizontal(id="add_contact_buttons"):
                yield Button("Add Contact", id="add_btn", variant="success")
                yield Button("Cancel [Esc]", id="cancel_btn")

    @on(Button.Pressed, "#add_btn")
    def add_contact(self):
        address = self.query_one("#contact_address_input", Input).value.strip()
        alias = self.query_one("#contact_alias_input", Input).value.strip()

        if address:
            self.dismiss({"address": address, "alias": alias})
        else:
            self.notify("Please enter a nyx address", severity="warning")

    @on(Button.Pressed, "#cancel_btn")
    def cancel(self):
        self.dismiss(None)

    def on_mount(self) -> None:
        self.query_one("#contact_address_input", Input).focus()


class CreateGroupScreen(ModalScreen[Optional[str]]):
    """Create group dialog."""

    BINDINGS = [("escape", "dismiss", "Cancel")]

    def compose(self) -> ComposeResult:
        with Container(id="create_group_modal"):
            yield Static("╔═══════════ CREATE NEW GROUP ════════════╗", id="create_group_title")
            yield Input(placeholder="Group name", id="group_name_input")
            yield Input(placeholder="Description (optional)", id="group_desc_input")

            with Horizontal(id="create_group_buttons"):
                yield Button("Create Group", id="create_btn", variant="success")
                yield Button("Cancel [Esc]", id="cancel_btn")

    @on(Button.Pressed, "#create_btn")
    def create_group(self):
        name = self.query_one("#group_name_input", Input).value.strip()
        try:
            desc = self.query_one("#group_desc_input", Input).value.strip()
        except Exception:
            desc = ""

        if name:
            self.dismiss({"name": name, "description": desc})
        else:
            self.notify("Please enter a group name", severity="warning")

    @on(Button.Pressed, "#cancel_btn")
    def cancel(self):
        self.dismiss(None)

    def on_mount(self) -> None:
        self.query_one("#group_name_input", Input).focus()


class JoinGroupScreen(ModalScreen[Optional[Dict[str, str]]]):
    """Join group dialog."""

    BINDINGS = [("escape", "dismiss", "Cancel")]

    def compose(self) -> ComposeResult:
        with Container(id="join_group_modal"):
            yield Static("╔═══════════ JOIN GROUP ════════════╗", id="join_group_title")
            yield Input(placeholder="Group ID / invite code", id="join_group_id_input")
            yield Input(placeholder="Display name (optional)", id="join_group_alias_input")

            with Horizontal(id="join_group_buttons"):
                yield Button("Join Group", id="join_group_btn", variant="success")
                yield Button("Cancel [Esc]", id="join_group_cancel_btn")

    @on(Button.Pressed, "#join_group_btn")
    def join_group(self):
        group_id = self.query_one("#join_group_id_input", Input).value.strip()
        alias = self.query_one("#join_group_alias_input", Input).value.strip()

        if group_id:
            self.dismiss({"group_id": group_id, "alias": alias})
        else:
            self.notify("Please enter a group ID", severity="warning")

    @on(Button.Pressed, "#join_group_cancel_btn")
    def cancel(self):
        self.dismiss(None)

    def on_mount(self) -> None:
        self.query_one("#join_group_id_input", Input).focus()


class NewMenuScreen(ModalScreen[None]):
    """Small popup menu for creating or joining entities."""

    BINDINGS = [("escape", "dismiss", "Cancel")]

    def compose(self) -> ComposeResult:
        with Container(id="new_menu_modal"):
            yield Static("╔═══════════ NEW ═══════════╗", id="new_menu_title")
            yield Button("Add Contact", id="new_add_contact_btn", variant="primary")
            yield Button("Create Group", id="new_create_group_btn", variant="success")
            yield Button("Join Group", id="new_join_group_btn", variant="warning")
            yield Button("Cancel [Esc]", id="new_cancel_btn")

    def _open_target(self, method_name: str) -> None:
        callback = getattr(self.app, method_name, None)
        self.dismiss()

        if callback is None:
            return

        try:
            self.app.set_timer(0.05, callback)
        except AttributeError:
            callback()

    @on(Button.Pressed, "#new_add_contact_btn")
    def open_add_contact(self):
        self._open_target("_open_add_contact")

    @on(Button.Pressed, "#new_create_group_btn")
    def open_create_group(self):
        self._open_target("_open_create_group")

    @on(Button.Pressed, "#new_join_group_btn")
    def open_join_group(self):
        self._open_target("_open_join_group")

    @on(Button.Pressed, "#new_cancel_btn")
    def cancel(self):
        self.dismiss()


class ConfirmDialog(ModalScreen[bool]):
    """Generic confirmation dialog."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, message: str, title: str = "Confirm"):
        super().__init__()
        self.message = message
        self.title = title

    def compose(self) -> ComposeResult:
        with Container(id="confirm_modal"):
            yield Static(f"╔═══════════ {self.title.upper()} ════════════╗", id="confirm_title")
            yield Label(self.message)

            with Horizontal(id="confirm_buttons"):
                yield Button("Yes", id="yes_btn", variant="error")
                yield Button("No [Esc]", id="no_btn", variant="primary")

    @on(Button.Pressed, "#yes_btn")
    def confirm_yes(self):
        self.dismiss(True)

    @on(Button.Pressed, "#no_btn")
    def confirm_no(self):
        self.dismiss(False)

    def action_cancel(self):
        self.dismiss(False)

    def on_mount(self) -> None:
        self.query_one("#no_btn", Button).focus()


class GroupMembersScreen(ModalScreen[None]):
    """Group members list."""

    BINDINGS = [("escape", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        with Container(id="group_members_modal"):
            yield Static("╔═══════════ GROUP MEMBERS ════════════╗", id="members_title")
            yield ListView(id="members_list")
            yield Button("Close [Esc]", id="members_close_btn")

    @on(Button.Pressed, "#members_close_btn")
    def close(self):
        self.dismiss()

    def on_mount(self) -> None:
        self._load_members()

    def _load_members(self) -> None:
        members_list = self.query_one("#members_list", ListView)
        members_list.clear()

        try:
            ctx = getattr(self.app, "ctx", None)
            active_contact = getattr(self.app, "active_contact", None)

            if ctx and active_contact and "room_id" in active_contact:
                room_id = active_contact["room_id"]
                members = []

                if ctx.db and hasattr(ctx.db, "get_group_members"):
                    members = ctx.db.get_group_members(room_id)

                if not members:
                    members_list.append(ListItem(Label("No members found")))
                else:
                    for m in members:
                        alias = m.get("display_name") or m.get("identity_id", "Unknown")[:12]
                        members_list.append(ListItem(Label(f"👤 {alias}")))
            else:
                members_list.append(ListItem(Label("No active group")))

        except Exception as e:
            log.error("%s: %s", "load_members.failed", e)
            members_list.append(ListItem(Label("Failed to load members")))


class ProfileScreen(ModalScreen[None]):
    """Profile editing dialog."""

    BINDINGS = [("escape", "dismiss", "Cancel")]

    def _profile_context(self):
        ctx = getattr(self.app, "ctx", None)
        identity = getattr(ctx, "identity", None) if ctx else None

        identity_id = (
            getattr(ctx, "identity_id", None)
            or getattr(identity, "id", "")
            or ""
        )

        if identity_id:
            short_id = identity_id if len(identity_id) <= 16 else identity_id[:16] + "..."
        else:
            short_id = "Unknown"

        display_name = ""
        avatar = "👤"

        if ctx and getattr(ctx, "db", None) and identity_id:
            try:
                p = ctx.db.get_profile(identity_id)
                display_name = p.get("display_name") or ""
                avatar = p.get("avatar") or "👤"
            except Exception:
                pass

        if not display_name:
            display_name = getattr(identity, "display_name", None) or short_id

        if not avatar:
            avatar = getattr(identity, "avatar", None) or "👤"

        return identity_id, short_id, display_name, avatar

    def compose(self) -> ComposeResult:
        _, short_id, display_name, avatar = self._profile_context()

        with Container(id="profile_modal"):
            yield Static("╔═══════════ EDIT PROFILE ════════════╗", id="profile_title")
            yield Label(f"Identity: {short_id}")
            yield Input(
                placeholder="Display Name",
                id="profile_display_name",
                value=display_name,
            )
            yield Input(
                placeholder="Avatar (emoji or short text)",
                id="profile_avatar",
                value=avatar,
            )
            yield Static("", id="profile_result")

            with Horizontal(id="profile_buttons"):
                yield Button("Save", id="profile_save_btn", variant="success")
                yield Button("Cancel [Esc]", id="profile_cancel_btn", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#profile_display_name", Input).focus()

    @on(Button.Pressed, "#profile_save_btn")
    def save_profile(self):
        display_name = self.query_one("#profile_display_name", Input).value.strip()
        avatar = self.query_one("#profile_avatar", Input).value.strip() or "👤"
        result = self.query_one("#profile_result", Static)

        if not display_name:
            result.update("[red]⚠ Display name cannot be empty[/red]")
            return

        ctx = getattr(self.app, "ctx", None)
        if not ctx or not getattr(ctx, "db", None):
            result.update("[red]⚠ No backend connected[/red]")
            return

        identity_id = getattr(ctx, "identity_id", "") or ""
        try:
            ctx.db.save_profile(identity_id, display_name, avatar)
            identity = getattr(ctx, "identity", None)
            if identity is not None:
                try:
                    identity.display_name = display_name
                    identity.avatar = avatar
                except Exception:
                    pass

            header_bar = getattr(self.app, "header_bar", None)
            if header_bar:
                header_bar.identity = display_name
                header_bar.avatar = avatar
                header_bar.refresh()

            result.update(f"[green]✓ Saved: {display_name}[/green]")
            self.notify(f"Profile saved: {display_name}", severity="success")
            # Best-effort push via command
            try:
                from commands import registry
                registry.dispatch(ctx, f"/profile {display_name} {avatar}")
            except Exception:
                pass
        except Exception as e:
            result.update(f"[red]✗ Failed: {e}[/red]")


    @on(Button.Pressed, "#profile_cancel_btn")
    def cancel_profile(self):
        self.dismiss()


class SettingsScreen(ModalScreen[None]):
    """Comprehensive settings screen with server, data reset, and identity management."""

    BINDINGS = [("escape", "dismiss", "Cancel")]

    def compose(self) -> ComposeResult:
        ctx = getattr(self.app, "ctx", None)
        current_url = getattr(ctx, "server", "http://localhost:8000") if ctx else "http://localhost:8000"

        identity_id = ""
        if ctx:
            identity_id = (
                getattr(ctx, "identity_id", "")
                or getattr(getattr(ctx, "identity", None), "id", "")
                or ""
            )

        short_id = identity_id[:16] + "..." if len(identity_id) > 16 else identity_id or "Unknown"

        with Container(id="settings_modal"):
            # ── Section 1: Server Settings ──
            yield Static("╔═══════════ SERVER CONFIGURATION ════════════╗", id="settings_server_title")
            yield Label(f"Current: {current_url}")
            yield Input(placeholder="Enter new server URL", id="settings_server_url_input", value=current_url)
            yield Static("", id="settings_test_result")

            with Horizontal(id="settings_server_buttons"):
                yield Button("Test Connection", id="settings_test_btn", variant="primary")
                yield Button("Save Server", id="settings_save_server_btn", variant="success")

            yield Static(" ")  # Spacer

            # ── Section 2: Reset Data ──
            yield Static("╔═══════════ RESET DATA (DANGER) ════════════╗", id="settings_reset_title")

            with Horizontal(id="settings_reset_buttons"):
                yield Button("Delete All Chats", id="settings_delete_chats_btn", variant="warning")
                yield Button("Delete All Contacts", id="settings_delete_contacts_btn", variant="warning")
                yield Button("Factory Reset", id="settings_factory_reset_btn", variant="error")

            yield Static(" ")  # Spacer

            # ── Section 3: Identity ──
            yield Static("╔═══════════ IDENTITY MANAGEMENT ════════════╗", id="settings_identity_title")
            yield Label(f"Current Identity: {short_id}")

            with Horizontal(id="settings_identity_buttons"):
                yield Button("Generate New Identity", id="settings_new_identity_btn", variant="error")
                yield Button("Export Identity", id="settings_export_identity_btn", variant="primary")

            yield Static("", id="settings_result")
            yield Button("Close [Esc]", id="settings_close_btn")

    # ── Server Actions ──

    @on(Button.Pressed, "#settings_test_btn")
    async def test_connection(self):
        input_widget = self.query_one("#settings_server_url_input", Input)
        url = input_widget.value.strip()

        if not url:
            self.query_one("#settings_test_result", Static).update("⚠ Please enter a URL")
            return

        result_widget = self.query_one("#settings_test_result", Static)
        result_widget.update("⏳ Testing connection...")

        def _test():
            req = urllib.request.Request(f"{url}/api/v3/health", method="GET")
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status

        try:
            status = await asyncio.to_thread(_test)
            if status == 200:
                result_widget.update("[green]✓ Connected successfully[/green]")
            else:
                result_widget.update(f"[red]✗ Failed: HTTP {status}[/red]")
        except urllib.error.URLError as e:
            result_widget.update(f"[red]✗ Failed: {str(e)[:50]}[/red]")
        except Exception as e:
            result_widget.update(f"[red]✗ Failed: {str(e)[:50]}[/red]")

    @on(Button.Pressed, "#settings_save_server_btn")
    def save_server(self):
        input_widget = self.query_one("#settings_server_url_input", Input)
        url = input_widget.value.strip()

        if not url:
            self.notify("Please enter a valid URL", severity="warning")
            return

        ctx = getattr(self.app, "ctx", None)

        if ctx:
            try:
                ctx.server = url

                header_bar = getattr(self.app, "header_bar", None)
                if header_bar:
                    header_bar.status = f"Server: {url[:20]}"

                self.notify(f"✓ Server updated: {url}", severity="success")
            except Exception as e:
                self.notify(f"Failed to update server: {e}", severity="error")
        else:
            self.notify("No backend connected", severity="error")

    # ── Reset Data Actions ──

    @on(Button.Pressed, "#settings_delete_chats_btn")
    def delete_all_chats(self):
        def on_dismiss(confirm):
            if confirm:
                ctx = getattr(self.app, "ctx", None)

                if ctx and ctx.db:
                    try:
                        ctx.db.execute("DELETE FROM messages")

                        try:
                            ctx.db.execute("DELETE FROM conversations")
                        except Exception:
                            pass

                        ctx.db.commit()

                        self.notify("✓ All chats deleted", severity="success")

                        if hasattr(self.app, "refresh_contacts"):
                            self.app.refresh_contacts()

                        if hasattr(self.app, "refresh_chat"):
                            self.app.refresh_chat()

                    except Exception as e:
                        self.notify(f"Failed to delete chats: {e}", severity="error")

        self.app.push_screen(
            ConfirmDialog(
                "Delete ALL chat messages and conversations?\nThis cannot be undone.",
                title="Delete All Chats",
            ),
            callback=on_dismiss,
        )

    @on(Button.Pressed, "#settings_delete_contacts_btn")
    def delete_all_contacts(self):
        def on_dismiss(confirm):
            if confirm:
                ctx = getattr(self.app, "ctx", None)

                if ctx and ctx.db:
                    try:
                        ctx.db.execute("DELETE FROM contacts")
                        ctx.db.commit()

                        self.notify("✓ All contacts deleted", severity="success")

                        if hasattr(self.app, "refresh_contacts"):
                            self.app.refresh_contacts()

                        if hasattr(self.app, "refresh_chat"):
                            self.app.refresh_chat()

                    except Exception as e:
                        self.notify(f"Failed to delete contacts: {e}", severity="error")

        self.app.push_screen(
            ConfirmDialog(
                "Delete ALL contacts?\nThis cannot be undone.",
                title="Delete All Contacts",
            ),
            callback=on_dismiss,
        )

    @on(Button.Pressed, "#settings_factory_reset_btn")
    def factory_reset(self):
        def on_dismiss(confirm):
            if confirm:
                ctx = getattr(self.app, "ctx", None)

                if ctx and ctx.db:
                    try:
                        for table in ("messages", "conversations", "contacts", "profile"):
                            try:
                                ctx.db.execute(f"DELETE FROM {table}")
                            except Exception:
                                pass

                        ctx.db.commit()

                        self.notify("✓ Factory reset complete", severity="success")

                        if hasattr(self.app, "refresh_contacts"):
                            self.app.refresh_contacts()

                        if hasattr(self.app, "refresh_chat"):
                            self.app.refresh_chat()

                    except Exception as e:
                        self.notify(f"Factory reset failed: {e}", severity="error")

        self.app.push_screen(
            ConfirmDialog(
                "⚠ FACTORY RESET ⚠\nDelete ALL chats, contacts, groups, and profile data?\nIdentity will be preserved.",
                title="Factory Reset",
            ),
            callback=on_dismiss,
        )

    # ── Identity Actions ──

    @on(Button.Pressed, "#settings_new_identity_btn")
    def generate_new_identity(self):
        def on_dismiss(confirm):
            if confirm:
                ctx = getattr(self.app, "ctx", None)

                if ctx:
                    try:
                        # Clear all data first
                        if ctx.db:
                            for table in ("messages", "conversations", "contacts", "profile"):
                                try:
                                    ctx.db.execute(f"DELETE FROM {table}")
                                except Exception:
                                    pass

                            ctx.db.commit()

                        # Generate new identity ID
                        new_id = "nyx1" + hashlib.sha256(
                            str(datetime.now().timestamp()).encode()
                        ).hexdigest()[:32]

                        # Update context
                        if hasattr(ctx, "identity_id"):
                            ctx.identity_id = new_id

                        if hasattr(ctx, "identity") and ctx.identity:
                            try:
                                ctx.identity.id = new_id
                                ctx.identity.display_name = new_id[:16] + "..."
                                ctx.identity.avatar = "👤"
                            except Exception:
                                pass

                        # Update header
                        header_bar = getattr(self.app, "header_bar", None)
                        if header_bar:
                            header_bar.identity = new_id[:16] + "..."
                            header_bar.avatar = "👤"
                            header_bar.refresh()

                        if hasattr(self.app, "refresh_contacts"):
                            self.app.refresh_contacts()

                        if hasattr(self.app, "refresh_chat"):
                            self.app.refresh_chat()

                        self.notify(
                            "✓ New identity generated. Restart app to fully apply.",
                            severity="success",
                        )

                    except Exception as e:
                        self.notify(f"Failed to generate identity: {e}", severity="error")

        self.app.push_screen(
            ConfirmDialog(
                "Generate a NEW identity?\nThis will delete ALL data (chats, contacts, groups, profile).",
                title="Generate New Identity",
            ),
            callback=on_dismiss,
        )

    @on(Button.Pressed, "#settings_export_identity_btn")
    def export_identity(self):
        self.notify("Export Identity feature coming soon", severity="information")

    @on(Button.Pressed, "#settings_close_btn")
    def close_settings(self):
        self.dismiss()


class NyxTUI(App):
    """Main Textual application for NYX."""

    TITLE = f"NYX v{VERSION}"

    CSS = """
    Screen {
        background: $surface;
    }

    HeaderBar {
        height: 1;
        background: $primary;
        color: $text;
        text-style: bold;
        padding: 0 1;
    }

    #main_container {
        layout: horizontal;
    }

    #sidebar {
        width: 30%;
        max-width: 40;
        min-width: 20;
        background: $panel;
        border-right: tall $primary;
    }

    #sidebar_title,
    #sidebar_title_groups,
    #sidebar_title_menu {
        height: 1;
        background: $primary;
        color: $text;
        text-align: center;
        text-style: bold;
    }

    #contact_list,
    #group_list {
        height: 1fr;
        border: none;
    }

    #menu_list {
        height: auto;
        border: none;
    }

    #chat_area {
        width: 1fr;
        layout: vertical;
    }

    #chat_header {
        height: 1;
        background: $panel;
        padding: 0 1;
        border-bottom: solid $primary;
        text-style: bold;
    }

    #chat_messages {
        height: 1fr;
        overflow-y: scroll;
        padding: 1;
        border-bottom: solid $primary;
    }

    #input_bar {
        height: 3;
        margin: 0 1;
    }

    #members_list {
        height: 1fr;
        border: none;
    }

    #profile_modal,
    #group_members_modal,
    #server_modal,
    #add_contact_modal,
    #create_group_modal,
    #join_group_modal,
    #confirm_modal,
    #help_modal,
    #settings_modal,
    #new_menu_modal {
        width: 70;
        height: auto;
        background: $panel;
        border: thick $primary;
        padding: 1 2;
    }

    #new_menu_modal {
        width: 46;
    }

    #join_group_modal {
        width: 60;
    }

    #settings_modal {
        width: 80;
        max-height: 40;
        overflow-y: auto;
    }

    #settings_server_title,
    #settings_reset_title,
    #settings_identity_title {
        text-style: bold;
        margin-top: 1;
        color: $warning;
    }

    #settings_server_buttons,
    #settings_reset_buttons,
    #settings_identity_buttons {
        width: 100%;
        height: auto;
        margin-top: 1;
        align: center middle;
    }

    #settings_test_result,
    #settings_result {
        height: 1;
        margin-top: 1;
    }

    #settings_close_btn {
        margin-top: 2;
        width: 100%;
    }

    #help_content {
        width: 100%;
    }

    #server_buttons,
    #add_contact_buttons,
    #create_group_buttons,
    #join_group_buttons,
    #confirm_buttons,
    #profile_buttons {
        width: 100%;
        height: auto;
        margin-top: 1;
        align: center middle;
    }

    Button {
        margin: 0 1;
    }

    #new_menu_modal Button {
        width: 100%;
        margin: 1 0;
    }

    ChatMessage {
        padding: 1 2;
        margin: 0 0 1 0;
    }

    #test_result,
    #profile_result {
        height: 1;
        margin-top: 1;
    }

    .chat-fullscreen #sidebar {
        display: none;
    }

    .chat-fullscreen #chat_area {
        width: 100%;
    }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit_confirm", "Quit", show=True),
        Binding("ctrl+h", "show_help", "Help", show=True),
        Binding("ctrl+r", "sync", "Sync", show=True),
        Binding("ctrl+n", "new_menu", "New", show=True),
        Binding("ctrl+f", "toggle_fullscreen", "Fullscreen", show=True),
        Binding("ctrl+shift+s", "screenshot", "Screenshot", show=True),
        Binding("ctrl+l", "clear_chat", "Clear", show=True),
        Binding("delete", "delete_contact", "Delete", show=False),
        Binding("ctrl+d", "delete_item", "Delete", show=True),
        Binding("ctrl+m", "group_members", "Members", show=False),
    ]

    active_contact = reactive(None)

    def __init__(self, ctx: Optional[CommandContext] = None):
        super().__init__()
        self.ctx = ctx
        self.header_bar = HeaderBar()
        self.chat_fullscreen = False
        self._last_message_counts: Dict[str, int] = {}

    def compose(self) -> ComposeResult:
        yield self.header_bar

        with Horizontal(id="main_container"):
            with Vertical(id="sidebar"):
                # ── MENU section at the TOP of sidebar ──
                yield Label(" MENU ", id="sidebar_title_menu")

                with ListView(id="menu_list"):
                    yield ListItem(Label("⚙️ Settings"))
                    yield ListItem(Label("👤 Profile"))

                # ── CONTACTS section ──
                yield Label(" CONTACTS ", id="sidebar_title")
                yield ListView(id="contact_list")

                # ── CHANNELS / GROUPS section ──
                yield Label(" CHANNELS / GROUPS ", id="sidebar_title_groups")
                yield ListView(id="group_list")

            with Vertical(id="chat_area"):
                yield Static("Select a contact to start chatting", id="chat_header")
                yield Vertical(id="chat_messages")
                yield Input(placeholder="Type a message... (Enter to send)", id="input_bar")

        yield Footer()

    async def on_mount(self) -> None:
        if self.ctx:
            ident = self.ctx.identity

            if ident:
                ident_id = getattr(ident, "id", None) or getattr(self.ctx, "identity_id", "") or ""
                display = getattr(ident, "display_name", None)
                avatar = getattr(ident, "avatar", None) or "👤"
                if self.ctx.db and ident_id and hasattr(self.ctx.db, "get_profile"):
                    try:
                        p = self.ctx.db.get_profile(ident_id)
                        display = p.get("display_name") or display
                        avatar = p.get("avatar") or avatar
                    except Exception:
                        pass
                self.header_bar.identity = display or (ident_id[:16] + "..." if ident_id else "Unknown")
                self.header_bar.avatar = avatar or "👤"

            self.header_bar.status = "Connected" if self.ctx.connected else "Disconnected"

            self.refresh_contacts()
            self.run_background_sync()
        else:
            self.header_bar.status = "No Backend"

        # Apply default theme after widgets are mounted
        self._apply_theme(get_theme(DEFAULT_THEME_ID))

        try:
            self.query_one("#input_bar", Input).focus()
        except NoMatches:
            pass

    def refresh_contacts(self) -> None:
        if not self.ctx:
            return

        try:
            contact_list = self.query_one("#contact_list", ListView)
            contact_list.clear()

            contacts = self.ctx.db.list_contacts() if self.ctx.db else []

            for contact in contacts:
                item = ContactItem(contact, is_group=False)
                contact_list.append(item)

        except Exception as e:
            log.error("%s: %s", "refresh_contacts.failed", e)
            self.notify("Failed to load contacts", severity="error")

        try:
            group_list = self.query_one("#group_list", ListView)
            group_list.clear()

            if self.ctx.db and hasattr(self.ctx.db, "list_groups"):
                for group in self.ctx.db.list_groups():
                    group_item = ContactItem(group, is_group=True)
                    group_list.append(group_item)

        except Exception as e:
            log.error("%s: %s", "refresh_groups.failed", e)

    @work(exclusive=True)
    async def run_background_sync(self) -> None:
        """Background task to periodically sync messages with circuit-breaking."""
        sleep_time = 5

        while True:
            try:
                if not self.ctx or not self.ctx.connected:
                    await asyncio.sleep(30)
                    continue

                registry.dispatch(self.ctx, "/sync")
                self.call_from_thread(self.refresh_contacts)

                if self.active_contact:
                    peer_id = self.active_contact.get("identity_id", "")

                    if peer_id and self.ctx.identity_id:
                        try:
                            conv_id = self.ctx.db.ensure_conversation(
                                [self.ctx.identity_id, peer_id]
                            )
                        except Exception:
                            conv_id = _conversation_id(self.ctx.identity_id, peer_id)

                        try:
                            messages = self.ctx.db.get_messages(conv_id)
                            current_count = len(messages)
                            last_count = self._last_message_counts.get(conv_id, -1)

                            if current_count != last_count:
                                self._last_message_counts[conv_id] = current_count
                                self.call_from_thread(self.refresh_chat)

                        except Exception as e:
                            log.error("%s: %s", "sync.get_messages.failed", e)

                sleep_time = 5

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("%s: %s", "background_sync.failed", e)
                sleep_time = min(sleep_time * 2, 60)

            await asyncio.sleep(sleep_time)

    @on(ListView.Selected)
    def on_listview_selected(self, event: ListView.Selected) -> None:
        # Handle Menu List Selections
        if event.list_view.id == "menu_list":
            index = event.list_view.index

            if index == 0:
                self.action_settings()
            elif index == 1:
                self.action_profile()

            return

        # Handle Contact/Group Selections
        if isinstance(event.item, ContactItem):
            self.active_contact = event.item.contact
            self.refresh_chat()

            try:
                self.query_one("#input_bar", Input).focus()
            except NoMatches:
                pass

    def refresh_chat(self) -> None:
        if not self.active_contact or not self.ctx:
            return

        is_group = "room_id" in self.active_contact

        try:
            chat_header = self.query_one("#chat_header", Static)
        except NoMatches:
            return

        if is_group:
            title = self.active_contact.get("title", "Unnamed Group")
            room_type = self.active_contact.get("type", "private_group")
            chat_header.update(f"👥 Group: {title} ({room_type})")
        else:
            identity_id = self.active_contact.get("identity_id", "")
            alias = (
                self.active_contact.get("name")
                or self.active_contact.get("display_name")
                or (identity_id[:12] if identity_id else "Unknown")
            )
            chat_header.update(f"👤 Chat: {alias}")

        try:
            chat_messages = self.query_one("#chat_messages", Vertical)
        except NoMatches:
            return

        for child in chat_messages.children:
            child.remove()

        if is_group:
            room_id = self.active_contact.get("room_id", "")
            title = self.active_contact.get("title") or room_id
            if not room_id:
                chat_messages.mount(Static("Invalid group"))
            else:
                try:
                    messages = self.ctx.db.get_messages(room_id)
                    if not messages:
                        chat_messages.mount(Static(f"No messages in {title} yet."))
                    else:
                        for msg in messages:
                            from_id = msg.get("from_id", "") if isinstance(msg, dict) else ""
                            content = msg.get("content", "") if isinstance(msg, dict) else ""
                            raw_ts = msg.get("timestamp") if isinstance(msg, dict) else None
                            is_self = from_id == self.ctx.identity_id
                            sender_name = "You" if is_self else (from_id[:12] if from_id else "?")
                            ts = datetime.now()
                            if isinstance(raw_ts, str):
                                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
                                    try:
                                        ts = datetime.strptime(raw_ts, fmt)
                                        break
                                    except ValueError:
                                        continue
                            chat_messages.mount(
                                ChatMessage(sender_name, str(content), ts, is_self, True)
                            )
                except Exception as e:
                    log.error("%s: %s", "refresh_group_chat.failed", e)
                    chat_messages.mount(Static("Failed to load group messages"))
        else:
            peer_id = self.active_contact.get("identity_id", "")

            if not peer_id or not self.ctx.identity_id:
                chat_messages.mount(Static("No identity loaded"))
            else:
                # Prefer DB helper so IDs always match what was used on send
                try:
                    conv_id = self.ctx.db.ensure_conversation(
                        [self.ctx.identity_id, peer_id]
                    )
                except Exception:
                    conv_id = _conversation_id(self.ctx.identity_id, peer_id)

                try:
                    messages = self.ctx.db.get_messages(conv_id)

                    if not messages:
                        chat_messages.mount(Static("No messages yet. Say hello! 👋"))
                    else:
                        peer_alias = (
                            self.active_contact.get("name")
                            or self.active_contact.get("display_name")
                            or peer_id[:12]
                        )
                        for msg in messages:
                            # Messages are plain dicts from sqlite
                            from_id = msg.get("from_id", "") if isinstance(msg, dict) else getattr(msg, "from_id", "")
                            content = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
                            raw_ts = msg.get("timestamp") if isinstance(msg, dict) else getattr(msg, "timestamp", None)

                            is_self = from_id == self.ctx.identity_id
                            sender_name = "You" if is_self else peer_alias

                            ts = datetime.now()
                            if raw_ts is not None:
                                try:
                                    if isinstance(raw_ts, (int, float)):
                                        ts = datetime.fromtimestamp(raw_ts)
                                    elif isinstance(raw_ts, str):
                                        for fmt in (
                                            "%Y-%m-%d %H:%M:%S",
                                            "%Y-%m-%d %H:%M:%S.%f",
                                            "%Y-%m-%dT%H:%M:%S",
                                            "%Y-%m-%dT%H:%M:%S.%f",
                                        ):
                                            try:
                                                ts = datetime.strptime(raw_ts, fmt)
                                                break
                                            except ValueError:
                                                continue
                                except Exception:
                                    pass

                            if isinstance(content, bytes):
                                content_text = content.decode("utf-8", errors="replace")
                            else:
                                content_text = str(content) if content else ""

                            chat_messages.mount(
                                ChatMessage(sender_name, content_text, ts, is_self, is_group)
                            )

                except Exception as e:
                    log.error("%s: %s", "refresh_chat.failed", e)
                    chat_messages.mount(Static("Failed to load messages"))

        chat_messages.scroll_end(animate=False)

    @on(Input.Submitted, "#input_bar")
    async def on_message_submitted(self, event: Input.Submitted) -> None:
        content = event.value.strip()

        if not content:
            return

        if not self.active_contact:
            self.notify("Select a contact first", severity="warning")
            return

        if not self.ctx:
            self.notify("Not connected to backend", severity="error")
            return

        if content.startswith("/"):
            try:
                result = registry.dispatch(self.ctx, content)

                if result.message:
                    self.notify(
                        result.message,
                        severity="information" if result.ok else "error",
                    )

            except Exception as e:
                self.notify(f"Command failed: {e}", severity="error")

            event.input.value = ""
            return

        is_group = "room_id" in self.active_contact

        try:
            if is_group:
                room_id = self.active_contact.get("room_id", "")
                if not room_id:
                    self.notify("Invalid group", severity="error")
                    return
                result = registry.dispatch(self.ctx, f"/gsend {room_id} {content}")
            else:
                identity_id = self.active_contact.get("identity_id", "")
                if not identity_id:
                    self.notify("Invalid contact", severity="error")
                    return
                result = registry.dispatch(self.ctx, f"/send {identity_id} {content}")

            event.input.value = ""

            if not result.ok:
                self.notify(f"Send failed: {result.message}", severity="error")
            else:
                self.refresh_chat()

        except Exception as e:
            log.error("%s: %s", "send_message.failed", e)
            self.notify(f"Failed to send message: {e}", severity="error")

    def action_sync(self) -> None:
        if self.ctx is None:
            self.notify("No backend connected", severity="error")
            return

        try:
            registry.dispatch(self.ctx, "/sync")
            self.refresh_contacts()
            self.refresh_chat()
            self.notify("✓ Synced with server", severity="information")
        except Exception as e:
            self.notify(f"Sync failed: {e}", severity="error")

    def action_show_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_profile(self) -> None:
        if self.ctx is None:
            self.notify("No backend connected", severity="error")
            return

        self.push_screen(ProfileScreen())

    def action_settings(self) -> None:
        """Open the comprehensive settings screen."""
        if self.ctx is None:
            self.notify("No backend connected", severity="error")
            return

        self.push_screen(SettingsScreen())

    async def action_screenshot(self) -> None:
        """Save an SVG screenshot of the current application screen."""
        try:
            directory = Path.home() / ".local" / "share" / "nyx" / "screenshots"
            directory.mkdir(parents=True, exist_ok=True)

            filename = datetime.now().strftime("nyx_screenshot_%Y%m%d_%H%M%S.svg")
            full_path = directory / filename

            if hasattr(self, "export_screenshot"):
                svg = self.export_screenshot()

                if asyncio.iscoroutine(svg):
                    svg = await svg

                if isinstance(svg, bytes):
                    full_path.write_bytes(svg)
                else:
                    full_path.write_text(str(svg), encoding="utf-8")
            else:
                try:
                    result = self.save_screenshot(filename=filename, path=str(directory))
                except TypeError:
                    result = self.save_screenshot(str(full_path))

                if asyncio.iscoroutine(result):
                    await result

            self.notify(f"✓ Screenshot saved: {full_path}", severity="success")

        except Exception as e:
            log.error("%s: %s", "screenshot.failed", e)
            self.notify(f"Screenshot failed: {e}", severity="error")

    def action_toggle_fullscreen(self) -> None:
        """Toggle fullscreen chat mode by hiding/showing the sidebar."""
        self.chat_fullscreen = not self.chat_fullscreen

        try:
            self.screen.set_class(self.chat_fullscreen, "chat-fullscreen")

            if self.chat_fullscreen:
                self.notify("✓ Fullscreen chat enabled", severity="information")
            else:
                self.notify("✓ Fullscreen chat disabled", severity="information")

        except Exception as e:
            self.notify(f"Fullscreen toggle failed: {e}", severity="error")

    def action_new_menu(self) -> None:
        """Open the New menu: Add Contact / Create Group / Join Group."""
        self.push_screen(NewMenuScreen())

    def _open_add_contact(self) -> None:
        if self.ctx is None:
            self.notify("No backend connected", severity="error")
            return

        def on_dismiss(result):
            if result and self.ctx:
                try:
                    address = result["address"]
                    alias = result.get("alias") or address[:12]
                    self.ctx.db.save_contact(alias, address)
                    self.refresh_contacts()

                    display = alias
                    self.notify(f"✓ Added contact: {display}", severity="information")

                except Exception as e:
                    log.error("%s: %s", "add_contact.failed", e)
                    self.notify(f"Failed to add contact: {e}", severity="error")

        self.push_screen(AddContactScreen(), callback=on_dismiss)

    def _open_create_group(self) -> None:
        if self.ctx is None:
            self.notify("No backend connected", severity="error")
            return

        def on_dismiss(result):
            if not result or not self.ctx:
                return
            try:
                # CreateGroupScreen may return str name or dict
                if isinstance(result, dict):
                    name = result.get("name") or result.get("title") or "Group"
                    desc = result.get("description") or ""
                else:
                    name = str(result)
                    desc = ""
                room_id = self.ctx.db.create_group(name, self.ctx.identity_id, desc)
                self.refresh_contacts()
                self.notify(f"✓ Group created: {name} ({room_id})", severity="information")
            except Exception as e:
                self.notify(f"Failed to create group: {e}", severity="error")

        self.push_screen(CreateGroupScreen(), callback=on_dismiss)

    def _open_join_group(self) -> None:
        if self.ctx is None:
            self.notify("No backend connected", severity="error")
            return

        def on_dismiss(result):
            if not result:
                return

            group_id = result.get("group_id", "").strip()
            alias = result.get("alias", "").strip() or "New Group"

            if not group_id:
                self.notify("Group ID is required", severity="warning")
                return

            try:
                command_result = registry.dispatch(self.ctx, f"/join_group {group_id} {alias}")

                if command_result.ok:
                    self.refresh_contacts()
                    self.notify(f"✓ Joined group: {alias}", severity="success")
                else:
                    self.notify(
                        f"Failed to join group: {command_result.message}",
                        severity="error",
                    )

            except Exception as e:
                log.error("%s: %s", "join_group.failed", e)
                self.notify(f"Failed to join group: {e}", severity="error")

        self.push_screen(JoinGroupScreen(), callback=on_dismiss)

    def action_server_settings(self) -> None:
        if self.ctx is None:
            self.notify("No backend connected", severity="error")
            return

        current_url = self.ctx.server or "http://localhost:8000"

        def on_dismiss(result):
            if result:
                try:
                    self.ctx.server = result
                    self.header_bar.status = f"Server: {result[:20]}"
                    self.notify(f"✓ Server updated: {result}", severity="information")
                except Exception as e:
                    self.notify(f"Server update failed: {e}", severity="error")

        self.push_screen(ServerSettingsScreen(current_url), callback=on_dismiss)

    def action_clear_chat(self) -> None:
        try:
            chat_messages = self.query_one("#chat_messages", Vertical)

            for child in chat_messages.children:
                child.remove()

            chat_messages.mount(Static("Chat cleared"))
            self.notify("Chat display cleared", severity="information")

        except NoMatches:
            pass

    def action_delete_contact(self) -> None:
        if self.ctx is None:
            self.notify("No backend connected", severity="error")
            return

        if not self.active_contact:
            return

        alias = (
            self.active_contact.get("name")
            or self.active_contact.get("display_name")
            or self.active_contact.get("identity_id", "")[:12]
        )

        def on_dismiss(confirm):
            if confirm:
                try:
                    identity_id = self.active_contact.get("identity_id", "")

                    if identity_id and self.ctx and self.ctx.db:
                        self.ctx.db.execute(
                            "DELETE FROM contacts WHERE identity_id = ?",
                            (identity_id,),
                        )
                        self.ctx.db.commit()

                        self.active_contact = None
                        self.action_clear_chat()
                        self.refresh_contacts()

                        self.notify(f"✓ Deleted contact: {alias}", severity="information")

                except Exception as e:
                    log.error("%s: %s", "delete_contact.failed", e)
                    self.notify(f"Failed to delete contact: {e}", severity="error")

        self.push_screen(
            ConfirmDialog(
                f"Delete contact '{alias}'?\nThis will remove all message history.",
                title="Delete Contact",
            ),
            callback=on_dismiss,
        )

    def action_quit_confirm(self) -> None:
        def on_dismiss(confirm):
            if confirm:
                self.exit()

        self.push_screen(
            ConfirmDialog("Are you sure you want to quit NYX?", title="Quit"),
            callback=on_dismiss,
        )

    def action_group_members(self) -> None:
        if self.ctx is None:
            self.notify("No backend connected", severity="error")
            return

        if not self.active_contact or "room_id" not in self.active_contact:
            self.notify("No active group selected", severity="warning")
            return

        self.push_screen(GroupMembersScreen())

    # ─────────────────────────────────────────────────────────────────────────
    # Delete Item (Ctrl+D) - Contact or Group deletion
    # ─────────────────────────────────────────────────────────────────────────

    def action_delete_item(self) -> None:
        """Delete the currently active contact or group."""
        if self.ctx is None:
            self.notify("No backend connected", severity="error")
            return

        if not self.active_contact:
            self.notify("No contact or group selected", severity="warning")
            return

        is_group = "room_id" in self.active_contact

        if is_group:
            group_name = self.active_contact.get("title", "Unnamed Group")
            room_id = self.active_contact.get("room_id", "")

            # Determine if user is the creator
            creator_id = self.active_contact.get("creator_id", "")
            is_creator = (creator_id == self.ctx.identity_id) if creator_id else False

            # Fallback: if no creator_id, check is_creator flag
            if not creator_id and self.active_contact.get("is_creator", False):
                is_creator = True

            if is_creator:
                message = f"Delete group '{group_name}'?\nThis will remove the group for ALL members."
                title = "Delete Group"
            else:
                message = f"Leave group '{group_name}'?\nThis will remove you from the group."
                title = "Leave Group"

            def on_dismiss(confirm):
                if confirm:
                    self._delete_group_data(room_id, group_name, is_creator)

            self.push_screen(ConfirmDialog(message, title=title), callback=on_dismiss)
        else:
            alias = (
                self.active_contact.get("display_name")
                or self.active_contact.get("identity_id", "")[:12]
            )
            peer_id = self.active_contact.get("identity_id", "")

            message = (
                f"Delete contact '{alias}' and all message history?\n"
                f"This will also remove you from their contact list."
            )

            def on_dismiss(confirm):
                if confirm:
                    self._delete_contact_data(peer_id, alias)

            self.push_screen(ConfirmDialog(message, title="Delete Contact"), callback=on_dismiss)

    def _delete_contact_data(self, peer_id: str, alias: str) -> None:
        """Perform local and server-side deletion of a contact."""
        if not peer_id:
            self.notify("Invalid contact", severity="error")
            return

        local_success = False

        # ── Local DB deletion ──
        try:
            if self.ctx and self.ctx.db:
                # Delete contact from contacts table
                self.ctx.db.execute(
                    "DELETE FROM contacts WHERE identity_id = ?",
                    (peer_id,),
                )

                # Delete messages for this conversation
                conv_id = _conversation_id(self.ctx.identity_id, peer_id)

                self.ctx.db.execute(
                    "DELETE FROM messages WHERE conversation_id = ?",
                    (conv_id,),
                )

                # Optionally delete conversation entry
                try:
                    self.ctx.db.execute(
                        "DELETE FROM conversations WHERE conversation_id = ?",
                        (conv_id,),
                    )
                except Exception:
                    pass

                self.ctx.db.commit()
                local_success = True

        except Exception as e:
            log.error("%s: %s", "delete_contact.local.failed", e)
            self.notify(f"Local deletion failed: {e}", severity="error")

        # ── Server communication (best-effort) ──
        try:
            result = registry.dispatch(self.ctx, f"/delete_contact {peer_id}")

            if not result.ok:
                log.warning("%s: %s", "delete_contact.server.failed", result.message)

        except Exception as e:
            log.error("%s: %s", "delete_contact.server.error", e)

        # ── Update UI ──
        self.active_contact = None
        self.refresh_contacts()
        self.refresh_chat()

        if local_success:
            self.notify(f"✓ Deleted contact: {alias}", severity="success")
        else:
            self.notify(f"⚠ Partially deleted contact: {alias}", severity="warning")

    def _delete_group_data(self, room_id: str, group_name: str, is_creator: bool) -> None:
        """Perform local and server-side deletion/leaving of a group."""
        if not room_id:
            self.notify("Invalid group", severity="error")
            return

        local_success = False

        # ── Local DB deletion ──
        try:
            if self.ctx and self.ctx.db:
                # Delete conversation entry for this group
                self.ctx.db.execute(
                    "DELETE FROM conversations WHERE room_id = ?",
                    (room_id,),
                )

                # Delete all messages associated with this group
                # Try by room_id first, then by conversation_id pattern
                try:
                    self.ctx.db.execute(
                        "DELETE FROM messages WHERE room_id = ?",
                        (room_id,),
                    )
                except Exception:
                    # Fallback: delete by conversation_id if room_id column doesn't exist
                    try:
                        self.ctx.db.execute(
                            "DELETE FROM messages WHERE conversation_id = ?",
                            (room_id,),
                        )
                    except Exception:
                        pass

                self.ctx.db.commit()
                local_success = True

        except Exception as e:
            log.error("%s: %s", "delete_group.local.failed", e)
            self.notify(f"Local deletion failed: {e}", severity="error")

        # ── Server communication (best-effort) ──
        try:
            if is_creator:
                result = registry.dispatch(self.ctx, f"/delete_group {room_id}")
            else:
                result = registry.dispatch(self.ctx, f"/leave_group {room_id}")

            if not result.ok:
                log.warning("%s: %s", "delete_group.server.failed", result.message)

        except Exception as e:
            log.error("%s: %s", "delete_group.server.error", e)

        # ── Update UI ──
        self.active_contact = None
        self.refresh_contacts()
        self.refresh_chat()

        if local_success:
            if is_creator:
                self.notify(f"✓ Group deleted for all members: {group_name}", severity="success")
            else:
                self.notify(f"✓ You left the group: {group_name}", severity="success")
        else:
            self.notify(f"⚠ Partially processed group: {group_name}", severity="warning")

    def _apply_theme(self, theme: Theme) -> None:
        """Apply the only supported theme: Midnight."""
        css_rules = """
        Screen {
            background: black;
        }

        HeaderBar {
            background: cyan;
            color: black;
        }

        #sidebar {
            background: black;
            border-right: tall cyan;
        }

        #sidebar_title,
        #sidebar_title_groups,
        #sidebar_title_menu {
            background: cyan;
            color: black;
        }

        #chat_header {
            background: black;
            border-bottom: solid cyan;
            color: cyan;
        }

        #chat_messages {
            border-bottom: solid cyan;
        }

        #input_bar {
            background: black;
            color: green;
            border: tall cyan;
        }

        Button {
            background: cyan;
            color: black;
            border: tall cyan;
        }

        ListView > ListItem {
            background: black;
            color: cyan;
        }

        ListView > ListItem:hover,
        ListView > ListItem:focus {
            background: cyan;
            color: black;
        }
        """

        try:
            self.stylesheet.add_rules(css_rules)
            self.refresh_css()

            # Direct styling for HeaderBar to ensure immediate update
            self.header_bar.styles.background = theme.accent
            self.header_bar.styles.color = theme.header_bg

        except Exception as e:
            log.debug("%s: %s", "apply_theme.failed", e)

    def on_unmount(self) -> None:
        """Cleanup background tasks on exit."""
        pass


if __name__ == "__main__":
    if "--repl" in sys.argv:
        ctx = CommandContext()
        repl = ReplUI(ctx)
        sys.exit(repl.run())
    else:
        app = NyxTUI()
        app.run()