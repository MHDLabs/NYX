"""NYX client command registry — REPL/TUI command handlers + v3 API helpers."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import requests


@dataclass
class CommandResult:
    ok: bool
    message: str
    data: Optional[Any] = None


@dataclass
class CommandContext:
    identity: Any
    identity_id: str
    server: str
    connected: bool
    db: Any
    session_token: Optional[str] = None
    device_id: Optional[str] = None

    def is_connected(self) -> bool:
        return self.connected

    def get_identity_id(self) -> str:
        return self.identity_id

    def get_server_url(self) -> str:
        return self.server

    def update_connection_status(self, status: bool) -> None:
        self.connected = status

    def auth_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.session_token:
            headers["Authorization"] = f"Bearer {self.session_token}"
        return headers


class Command:
    def __init__(self, name: str, handler: Callable, help_text: str = ""):
        self.name = name
        self.handler = handler
        self.help_text = help_text
        self.parser = argparse.ArgumentParser(
            prog=name, description=help_text, add_help=False
        )

    def execute(self, ctx: CommandContext, args: list) -> str:
        try:
            parsed = self.parser.parse_args(args)
            return self.handler(ctx, parsed)
        except SystemExit:
            return f"Invalid arguments for {self.name}"
        except Exception as e:
            return f"Error: {str(e)}"


class CommandRegistry:
    def __init__(self):
        self.commands: Dict[str, Command] = {}

    def register(self, name: str, handler: Callable, help_text: str = "") -> Command:
        cmd = Command(name, handler, help_text)
        self.commands[name] = cmd
        return cmd

    def get(self, name: str) -> Optional[Command]:
        return self.commands.get(name)

    def list_commands(self) -> list:
        return list(self.commands.keys())

    def execute(self, ctx: CommandContext, command: str, args: list) -> str:
        cmd = self.get(command)
        if not cmd:
            return f"Unknown command: {command}"
        return cmd.execute(ctx, args)

    def dispatch(self, ctx: CommandContext, line: str) -> CommandResult:
        line = line.strip()
        if not line:
            return CommandResult(ok=True, message="")

        if not line.startswith("/"):
            return CommandResult(ok=False, message="Commands must start with /")

        parts = line[1:].split(None, 1)
        if not parts:
            return CommandResult(ok=False, message="Empty command")

        command = parts[0].lower()
        args = parts[1].split() if len(parts) > 1 else []

        if command in ("exit", "quit"):
            return CommandResult(ok=True, message="EXIT")

        result_message = self.execute(ctx, command, args)

        if result_message == "QUIT":
            return CommandResult(ok=True, message="EXIT")

        is_error = result_message.startswith("Error:") or result_message.startswith(
            "Unknown command"
        )
        return CommandResult(ok=not is_error, message=result_message)


registry = CommandRegistry()


# ── helpers ────────────────────────────────────────────────────────────────

def _public_key_hex(identity) -> str:
    try:
        return identity.public_key_bytes.hex()
    except Exception:
        return ""


def _device_id(ctx: CommandContext) -> str:
    if ctx.device_id:
        return ctx.device_id
    # Stable device id derived from identity
    did = "dev_" + hashlib.sha256(ctx.identity_id.encode()).hexdigest()[:24]
    ctx.device_id = did
    return did


def _v3_session_create(ctx: CommandContext) -> Optional[str]:
    """Create a v3 session; server currently accepts any signature (placeholder)."""
    device_id = _device_id(ctx)
    ts = int(time.time() * 1000)
    pub = _public_key_hex(ctx.identity)
    # Minimal signature placeholder (server validates as true for now)
    sig = hashlib.sha256(f"{ctx.identity_id}|{device_id}|{pub}|{ts}|3".encode()).hexdigest()

    body = {
        "identity": ctx.identity_id,
        "device_id": device_id,
        "device_public_key": pub or ("00" * 32),
        "timestamp": ts,
        "protocol_version": 3,
        "signature": sig,
    }
    try:
        r = requests.post(
            f"{ctx.server.rstrip('/')}/api/v3/auth/session",
            json=body,
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            token = data.get("session_token")
            if token:
                ctx.session_token = token
                if ctx.db:
                    ctx.db.set_meta("session_token", token)
                    ctx.db.set_meta("device_id", device_id)
                return token
        return None
    except Exception:
        return None


def _ensure_session(ctx: CommandContext) -> bool:
    if ctx.session_token:
        return True
    # Try restore from db
    if ctx.db:
        saved = ctx.db.get_meta("session_token")
        if saved:
            ctx.session_token = saved
            ctx.device_id = ctx.db.get_meta("device_id") or _device_id(ctx)
            return True
    return _v3_session_create(ctx) is not None


def _conversation_id_for(a: str, b: str) -> str:
    sorted_ids = sorted([a, b])
    conv_hash = hashlib.sha256(json.dumps(sorted_ids).encode()).hexdigest()[:16]
    return f"conv_{conv_hash}"


def _make_message_id() -> str:
    return "nyx_msg_" + uuid.uuid4().hex


# ── command handlers ───────────────────────────────────────────────────────

def cmd_help(ctx: CommandContext, args: argparse.Namespace) -> str:
    commands = registry.list_commands()
    return "Available commands:\n" + "\n".join(f"  /{cmd}" for cmd in sorted(commands))


def cmd_connect(ctx: CommandContext, args: argparse.Namespace) -> str:
    base = ctx.server.rstrip("/")
    try:
        # Prefer v3 health
        response = requests.get(f"{base}/api/v3/health", timeout=5)
        if response.status_code != 200:
            # fallback root
            response = requests.get(f"{base}/", timeout=5)

        if response.status_code != 200:
            ctx.update_connection_status(False)
            return f"Failed to connect: HTTP {response.status_code}"

        ctx.update_connection_status(True)

        # Establish v3 session (best-effort)
        token = _v3_session_create(ctx)
        if token:
            return f"Connected to {ctx.server} (v3 session OK)"
        return f"Connected to {ctx.server} (legacy mode, no v3 session)"
    except Exception as e:
        ctx.update_connection_status(False)
        return f"Connection failed: {str(e)}"


def cmd_register(ctx: CommandContext, args: argparse.Namespace) -> str:
    if not ctx.is_connected():
        return "Not connected. Use /connect first."

    public_key = _public_key_hex(ctx.identity)
    device_id = _device_id(ctx)
    errors = []

    # Legacy register
    try:
        r = requests.post(
            f"{ctx.server.rstrip('/')}/register.php",
            json={"identity_id": ctx.identity_id, "public_key": public_key, "device_id": device_id},
            timeout=10,
        )
        if r.status_code != 200:
            errors.append(f"legacy: {r.text[:80]}")
    except Exception as e:
        errors.append(f"legacy: {e}")

    # Ensure v3 session (also auto-registers device on server)
    if not _ensure_session(ctx):
        errors.append("v3 session failed")

    if errors and not ctx.session_token:
        return "Registration failed: " + "; ".join(errors)
    return "Registration successful"


def cmd_contacts(ctx: CommandContext, args: argparse.Namespace) -> str:
    contacts = ctx.db.list_contacts()
    if not contacts:
        return "No contacts"
    lines = ["Contacts:"]
    for c in contacts:
        lines.append(f"  {c['name']} ({c['identity_id']})")
    return "\n".join(lines)


def cmd_add_contact(ctx: CommandContext, args: argparse.Namespace) -> str:
    if not args.name or not args.identity_id:
        return "Usage: /add <name> <identity_id>"
    ctx.db.save_contact(args.name, args.identity_id, args.public_key or "")
    return f"Added contact: {args.name}"


def cmd_conversations(ctx: CommandContext, args: argparse.Namespace) -> str:
    convs = ctx.db.list_conversations()
    if not convs:
        return "No conversations"
    lines = ["Conversations:"]
    for conv in convs:
        lines.append(
            f"  {conv['conversation_id']}: {conv['participant_count']} participants, "
            f"{conv['message_count']} msgs"
        )
    return "\n".join(lines)


def cmd_send(ctx: CommandContext, args: argparse.Namespace) -> str:
    if not args.recipient or not args.message:
        return "Usage: /send <recipient_id> <message>"

    message = " ".join(args.message) if isinstance(args.message, list) else str(args.message)
    recipient = args.recipient
    message_id = _make_message_id()

    # Local persist always
    conv_id = ctx.db.ensure_conversation([ctx.identity_id, recipient])
    ctx.db.save_message(conv_id, ctx.identity_id, recipient, message, message_id=message_id)

    if not ctx.is_connected():
        return f"Message saved locally (offline) to {recipient}"

    # Try v3 envelope send
    if _ensure_session(ctx):
        envelope = {
            "message_id": message_id,
            "sender_id": ctx.identity_id,
            "device_id": _device_id(ctx),
            "conversation_id": conv_id,
            "timestamp": int(time.time() * 1000),
            "sequence": 1,
            # Blind relay — store plaintext hex as ciphertext for now (crypto upgrade pending)
            "ciphertext": message.encode("utf-8").hex(),
            "signature": hashlib.sha256(message.encode()).hexdigest(),
            "previous_hash": None,
            "protocol_version": 3,
            "participants": [ctx.identity_id, recipient],
        }
        try:
            r = requests.post(
                f"{ctx.server.rstrip('/')}/api/v3/messages/send",
                json=envelope,
                headers=ctx.auth_headers(),
                timeout=10,
            )
            if r.status_code == 200:
                return f"Message sent to {recipient} (v3)"
            # fall through to legacy
        except Exception:
            pass

    # Legacy send.php
    try:
        r = requests.post(
            f"{ctx.server.rstrip('/')}/send.php",
            json={
                "from_id": ctx.identity_id,
                "to_id": recipient,
                "content": message,
            },
            timeout=10,
        )
        if r.status_code == 200:
            return f"Message sent to {recipient}"
        return f"Saved locally; server send failed: {r.text[:120]}"
    except Exception as e:
        return f"Saved locally; server error: {e}"


def cmd_messages(ctx: CommandContext, args: argparse.Namespace) -> str:
    if not args.conversation_id:
        return "Usage: /messages <conversation_id>"
    messages = ctx.db.get_messages(args.conversation_id)
    if not messages:
        return f"No messages in conversation {args.conversation_id}"
    lines = [f"Messages in {args.conversation_id}:"]
    for msg in messages:
        lines.append(f"  [{msg['timestamp']}] {msg['from_id']}: {msg['content']}")
    return "\n".join(lines)


def cmd_sync(ctx: CommandContext, args: argparse.Namespace) -> str:
    if not ctx.is_connected():
        return "Not connected. Use /connect first."

    imported = 0

    # Prefer v3 sync with Bearer token
    if _ensure_session(ctx):
        try:
            r = requests.get(
                f"{ctx.server.rstrip('/')}/api/v3/messages/sync",
                headers=ctx.auth_headers(),
                params={"limit": 200},
                timeout=15,
            )
            if r.status_code == 200:
                data = r.json()
                for env in data.get("messages", []):
                    try:
                        mid = env.get("message_id") or _make_message_id()
                        sender = env.get("sender_id", "")
                        conv = env.get("conversation_id", "")
                        ct = env.get("ciphertext", "")
                        # ciphertext is hex of utf-8 for our interim scheme
                        try:
                            content = bytes.fromhex(ct).decode("utf-8", errors="replace")
                        except Exception:
                            content = ct
                        if not conv or not sender:
                            continue
                        # Determine peer for DM-style local storage
                        peer = sender if sender != ctx.identity_id else ""
                        participants = env.get("participants") or []
                        if not peer and isinstance(participants, list):
                            for p in participants:
                                if p != ctx.identity_id:
                                    peer = p
                                    break
                        to_id = peer or conv
                        # Ensure conversation exists
                        if conv.startswith("conv_"):
                            pass
                        elif peer:
                            conv = ctx.db.ensure_conversation([ctx.identity_id, peer])
                        ctx.db.save_message(
                            conv, sender, to_id, content, message_id=mid
                        )
                        imported += 1
                    except Exception:
                        continue
                return f"Synced {imported} messages (v3)"
        except Exception as e:
            # fall through to legacy
            pass

    # Legacy sync.php
    try:
        r = requests.post(
            f"{ctx.server.rstrip('/')}/sync.php",
            json={"identity_id": ctx.identity_id},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            for msg in data.get("messages", []):
                try:
                    sender = msg.get("from_id") or msg.get("sender_id", "")
                    recipient = msg.get("to_id") or msg.get("recipient_id", ctx.identity_id)
                    content = msg.get("content") or msg.get("ciphertext", "")
                    mid = msg.get("message_id")
                    if not sender:
                        continue
                    conv = ctx.db.ensure_conversation([ctx.identity_id, sender])
                    ctx.db.save_message(
                        conv, sender, recipient, content, message_id=mid
                    )
                    imported += 1
                except Exception:
                    continue
            return f"Synced {imported} messages"
        return f"Sync failed: {r.text[:120]}"
    except Exception as e:
        return f"Sync error: {e}"


def cmd_status(ctx: CommandContext, args: argparse.Namespace) -> str:
    status = "connected" if ctx.is_connected() else "disconnected"
    sess = "yes" if ctx.session_token else "no"
    return (
        f"Identity: {ctx.identity_id}\n"
        f"Server: {ctx.server}\n"
        f"Status: {status}\n"
        f"v3 session: {sess}"
    )


def cmd_profile(ctx: CommandContext, args: argparse.Namespace) -> str:
    """Show or set local profile. Usage: /profile  OR  /profile <name> [avatar]"""
    if not getattr(args, "name", None):
        p = ctx.db.get_profile(ctx.identity_id)
        return f"Display name: {p['display_name']}\nAvatar: {p['avatar']}"
    name = args.name
    avatar = getattr(args, "avatar", None) or "👤"
    if isinstance(avatar, list):
        avatar = " ".join(avatar) if avatar else "👤"
    ctx.db.save_profile(ctx.identity_id, name, avatar)
    # Best-effort push to server
    if ctx.is_connected() and _ensure_session(ctx):
        try:
            requests.post(
                f"{ctx.server.rstrip('/')}/api/v3/profile",
                json={
                    "device_id": _device_id(ctx),
                    "display_name": name,
                    "avatar": avatar,
                },
                headers=ctx.auth_headers(),
                timeout=8,
            )
        except Exception:
            pass
    return f"Profile updated: {name} {avatar}"


def cmd_groups(ctx: CommandContext, args: argparse.Namespace) -> str:
    groups = ctx.db.list_groups()
    if not groups:
        return "No groups"
    lines = ["Groups:"]
    for g in groups:
        lines.append(
            f"  {g['title']} ({g['room_id']}) — {g['member_count']} members"
        )
    return "\n".join(lines)


def cmd_create_group(ctx: CommandContext, args: argparse.Namespace) -> str:
    if not args.name:
        return "Usage: /create_group <name> [description...]"
    name = args.name
    desc = " ".join(args.description) if getattr(args, "description", None) else ""
    room_id = ctx.db.create_group(name, ctx.identity_id, desc)
    return f"Group created: {name} ({room_id})"


def cmd_join_group(ctx: CommandContext, args: argparse.Namespace) -> str:
    if not args.group_id:
        return "Usage: /join_group <group_id> [alias...]"
    alias = " ".join(args.alias) if getattr(args, "alias", None) else "Group"
    ctx.db.join_group(args.group_id, ctx.identity_id, title=alias)
    return f"Joined group: {alias} ({args.group_id})"


def cmd_group_send(ctx: CommandContext, args: argparse.Namespace) -> str:
    if not args.room_id or not args.message:
        return "Usage: /gsend <room_id> <message>"
    message = " ".join(args.message) if isinstance(args.message, list) else str(args.message)
    room_id = args.room_id
    group = ctx.db.get_group(room_id)
    if not group:
        return f"Unknown group: {room_id}"
    mid = _make_message_id()
    ctx.db.save_message(room_id, ctx.identity_id, room_id, message, message_id=mid)
    return f"Group message saved in {group['title']}"


def cmd_quit(ctx: CommandContext, args: argparse.Namespace) -> str:
    return "QUIT"


def cmd_delete_contact(ctx: CommandContext, args: argparse.Namespace) -> str:
    if not getattr(args, "identity_id", None):
        return "Usage: /delete_contact <identity_id>"
    if ctx.db:
        ctx.db.delete_contact(args.identity_id)
    return f"Deleted contact: {args.identity_id}"


def cmd_delete_group(ctx: CommandContext, args: argparse.Namespace) -> str:
    if not getattr(args, "room_id", None):
        return "Usage: /delete_group <room_id>"
    if ctx.db:
        ctx.db.delete_group(args.room_id)
    return f"Deleted group: {args.room_id}"


def cmd_leave_group(ctx: CommandContext, args: argparse.Namespace) -> str:
    if not getattr(args, "room_id", None):
        return "Usage: /leave_group <room_id>"
    if ctx.db:
        ctx.db.leave_group(args.room_id, ctx.identity_id)
    return f"Left group: {args.room_id}"


# ── registration ───────────────────────────────────────────────────────────

registry.register("help", cmd_help, "Show help")
registry.register("connect", cmd_connect, "Connect to server")
registry.register("register", cmd_register, "Register on server")
registry.register("contacts", cmd_contacts, "List contacts")
registry.register("conversations", cmd_conversations, "List conversations")
registry.register("status", cmd_status, "Show status")
registry.register("sync", cmd_sync, "Sync messages")
registry.register("groups", cmd_groups, "List groups")
registry.register("quit", cmd_quit, "Quit")
registry.register("exit", cmd_quit, "Exit")

cmd_add = registry.register("add", cmd_add_contact, "Add contact")
cmd_add.parser.add_argument("name", help="Contact name")
cmd_add.parser.add_argument("identity_id", help="Contact identity ID")
cmd_add.parser.add_argument("--public-key", dest="public_key", default="", help="Public key")

cmd_send = registry.register("send", cmd_send, "Send message")
cmd_send.parser.add_argument("recipient", help="Recipient identity ID")
cmd_send.parser.add_argument("message", nargs="+", help="Message to send")
cmd_dm = registry.register("dm", cmd_send.handler, "Send DM (alias for send)")
cmd_dm.parser = cmd_send.parser

cmd_messages = registry.register("messages", cmd_messages, "Show messages")
cmd_messages.parser.add_argument("conversation_id", help="Conversation ID")

cmd_profile = registry.register("profile", cmd_profile, "Show/set profile")
cmd_profile.parser.add_argument("name", nargs="?", default=None, help="Display name")
cmd_profile.parser.add_argument("avatar", nargs="*", default=None, help="Avatar emoji")

cmd_cg = registry.register("create_group", cmd_create_group, "Create a group")
cmd_cg.parser.add_argument("name", help="Group name")
cmd_cg.parser.add_argument("description", nargs="*", default=[], help="Description")

cmd_jg = registry.register("join_group", cmd_join_group, "Join a group by ID")
cmd_jg.parser.add_argument("group_id", help="Group / room ID")
cmd_jg.parser.add_argument("alias", nargs="*", default=[], help="Local alias")

cmd_gs = registry.register("gsend", cmd_group_send, "Send group message")
cmd_gs.parser.add_argument("room_id", help="Group room ID")
cmd_gs.parser.add_argument("message", nargs="+", help="Message text")

cmd_dc = registry.register("delete_contact", cmd_delete_contact, "Delete a contact")
cmd_dc.parser.add_argument("identity_id", help="Contact identity ID")

cmd_dg = registry.register("delete_group", cmd_delete_group, "Delete a group")
cmd_dg.parser.add_argument("room_id", help="Group room ID")

cmd_lg = registry.register("leave_group", cmd_leave_group, "Leave a group")
cmd_lg.parser.add_argument("room_id", help="Group room ID")
