"""Command system for the NYX client. Whitepaper Section 09."""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from nyx_client.config.logging import get_logger

log = get_logger(__name__)


@dataclass
class CommandContext:
    identity_id: Optional[str] = None
    server: Optional[str] = None
    connected: bool = False
    services: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CommandResult:
    ok: bool
    message: str
    data: Optional[Any] = None


CommandHandler = Callable[[CommandContext, List[str]], CommandResult]


@dataclass
class CommandSpec:
    name: str
    handler: CommandHandler
    help: str
    usage: str = ""


class CommandRegistry:
    def __init__(self) -> None:
        self._commands: Dict[str, CommandSpec] = {}

    def register(self, name: str, help: str, usage: str = ""):
        def decorator(fn: CommandHandler) -> CommandHandler:
            self._commands[name] = CommandSpec(
                name=name, handler=fn, help=help, usage=usage or ("/" + name)
            )
            return fn
        return decorator

    def get(self, name: str) -> Optional[CommandSpec]:
        return self._commands.get(name)

    def list_commands(self) -> List[CommandSpec]:
        return sorted(self._commands.values(), key=lambda c: c.name)

    def dispatch(self, ctx: CommandContext, line: str) -> CommandResult:
        line = line.strip()
        if not line:
            return CommandResult(ok=True, message="")
        if not line.startswith("/"):
            return CommandResult(
                ok=False,
                message="Commands start with /. Type /help for a list.",
            )
        try:
            parts = shlex.split(line[1:])
        except ValueError as exc:
            return CommandResult(ok=False, message="parse error: " + str(exc))
        if not parts:
            return CommandResult(ok=False, message="empty command")
        name = parts[0].lower()
        args = parts[1:]
        if name in ("help", "?"):
            return self._help(args)
        spec = self._commands.get(name)
        if spec is None:
            return CommandResult(ok=False, message="unknown command: /" + name)
        if args and args[0] in ("--help", "-h"):
            return CommandResult(
                ok=True,
                message="/{0} - {1}\nUsage: {2}".format(spec.name, spec.help, spec.usage),
            )
        try:
            return spec.handler(ctx, args)
        except Exception as e:
            log.exception("command.error", command=name)
            return CommandResult(ok=False, message="error: " + str(e))

    def _help(self, args: List[str]) -> CommandResult:
        if args:
            key = args[0].lstrip("/").lower()
            spec = self._commands.get(key)
            if spec is None:
                return CommandResult(ok=False, message="unknown: " + args[0])
            return CommandResult(
                ok=True,
                message="/{0} - {1}\nUsage: {2}".format(spec.name, spec.help, spec.usage),
            )
        lines = ["Available commands:", ""]
        for spec in self.list_commands():
            lines.append("  /{0:<12} {1}".format(spec.name, spec.help))
        lines.append("")
        lines.append("Type /help <command> for details.")
        return CommandResult(ok=True, message="\n".join(lines))


registry = CommandRegistry()


@registry.register("status", "Connection, sync, identity status", "/status")
def cmd_status(ctx: CommandContext, args: List[str]) -> CommandResult:
    lines = [
        "Identity : " + (ctx.identity_id or "(none)"),
        "Server   : " + (ctx.server or "(none)"),
        "Connected: " + ("yes" if ctx.connected else "no"),
    ]
    return CommandResult(ok=True, message="\n".join(lines))


@registry.register("identity", "Show local identity", "/identity [show]")
def cmd_identity(ctx: CommandContext, args: List[str]) -> CommandResult:
    if not ctx.identity_id:
        return CommandResult(ok=False, message="no identity loaded")
    return CommandResult(ok=True, message="Identity: " + ctx.identity_id)


@registry.register("dm", "Open or send a direct message", "/dm <identity> [message...]")
def cmd_dm(ctx: CommandContext, args: List[str]) -> CommandResult:
    if not args:
        return CommandResult(ok=False, message="Usage: /dm <identity> [message]")
    peer = args[0]
    messaging = ctx.services.get("messaging")
    if messaging is None:
        return CommandResult(ok=False, message="messaging service not available")
    if len(args) == 1:
        hist = messaging.history(peer, limit=20)
        if not hist:
            return CommandResult(ok=True, message="(no messages with " + peer[:24] + ")")
        lines = []
        for m in hist:
            arrow = "->" if m.direction.value == "out" else "<-"
            text = m.plaintext.decode("utf-8", errors="replace")
            lines.append("  {0} [{1}] {2}".format(arrow, m.sequence, text))
        return CommandResult(ok=True, message="\n".join(lines))
    text = " ".join(args[1:])
    try:
        env = messaging.send_dm(peer, text.encode("utf-8"))
        return CommandResult(
            ok=True,
            message="sent seq={0} id={1}...".format(env.sequence, env.message_id[:20]),
            data=env,
        )
    except Exception as exc:
        return CommandResult(ok=False, message=str(exc))


@registry.register("contacts", "List contacts", "/contacts")
def cmd_contacts(ctx: CommandContext, args: List[str]) -> CommandResult:
    store = ctx.services.get("contacts")
    if store is None:
        return CommandResult(ok=False, message="contact store not available")
    contacts = store.list_all()
    if not contacts:
        return CommandResult(ok=True, message="(no contacts)")
    lines = []
    for c in contacts:
        name = c.display_name or "(no name)"
        trust = "trusted" if c.trusted else ""
        lines.append("  {0}...  {1}  {2}".format(c.identity_id[:28], name, trust))
    return CommandResult(ok=True, message="\n".join(lines))


@registry.register("addcontact", "Add or update a contact", "/addcontact <identity> [name]")
def cmd_addcontact(ctx: CommandContext, args: List[str]) -> CommandResult:
    if not args:
        return CommandResult(ok=False, message="Usage: /addcontact <identity> [name]")
    store = ctx.services.get("contacts")
    messaging = ctx.services.get("messaging")
    if store is None:
        return CommandResult(ok=False, message="contact store not available")
    peer = args[0]
    name = " ".join(args[1:]) if len(args) > 1 else None
    if messaging is not None:
        c = messaging.ensure_contact(peer, display_name=name)
    else:
        c = store.upsert(peer, display_name=name)
    return CommandResult(ok=True, message="contact saved: " + c.identity_id[:28] + "...")


@registry.register("exit", "Safe exit", "/exit [--force]")
def cmd_exit(ctx: CommandContext, args: List[str]) -> CommandResult:
    return CommandResult(ok=True, message="__EXIT__", data={"force": "--force" in args})


@registry.register("quit", "Alias for /exit", "/quit")
def cmd_quit(ctx: CommandContext, args: List[str]) -> CommandResult:
    return cmd_exit(ctx, args)



@registry.register("servers", "List known relays ranked by score", "/servers [refresh]")
def cmd_servers(ctx: CommandContext, args: List[str]) -> CommandResult:
    app = ctx.services.get("app")
    if app is None or getattr(app, "directory", None) is None:
        return CommandResult(ok=False, message="server directory not available")
    if args and args[0] == "refresh":
        ranked = app.refresh_servers(probe=True)
    else:
        ranked = app.directory.ranked()
    if not ranked:
        return CommandResult(ok=True, message="(no servers)")
    lines = ["  SCORE   LAT(ms)  TRUST  ENDPOINT"]
    for s in ranked[:20]:
        lines.append(
            "  {0:5.2f}  {1:7.0f}  {2:5d}  {3}".format(
                s.score, s.latency_ms, s.trust_level, s.endpoint
            )
        )
    best = app.select_best_server()
    lines.append("")
    lines.append("  preferred: " + str(best))
    return CommandResult(ok=True, message=chr(10).join(lines))


@registry.register("update", "Check or install client updates", "/update [check|install]")
def cmd_update(ctx: CommandContext, args: List[str]) -> CommandResult:
    app = ctx.services.get("app")
    if app is None or getattr(app, "updater", None) is None:
        return CommandResult(ok=False, message="update client not available")
    action = (args[0] if args else "check").lower()
    if action == "install":
        try:
            ver = app.apply_update()
            return CommandResult(ok=True, message="update result: " + ver)
        except Exception as exc:
            return CommandResult(ok=False, message="install failed: " + str(exc))
    result = app.check_updates()
    if result.error:
        return CommandResult(ok=False, message=result.error)
    if not result.update_available or result.candidate is None:
        return CommandResult(
            ok=True,
            message="up to date (current {0})".format(result.current_version),
        )
    c = result.candidate
    parts = [
        "update available: {0} (from {1})".format(c.version, result.source),
        "  current : {0}".format(result.current_version),
        "  artifact: {0}".format(c.artifact),
        "  channel : {0}".format(c.release_channel),
        "Run /update install to download+verify+stage.",
    ]
    return CommandResult(ok=True, message=chr(10).join(parts), data=result)


@registry.register("connect", "Connect to best or given relay", "/connect [endpoint]")
def cmd_connect(ctx: CommandContext, args: List[str]) -> CommandResult:
    app = ctx.services.get("app")
    if app is None:
        return CommandResult(ok=False, message="app not available")
    endpoint = args[0] if args else None
    try:
        session = app.connect_sync(endpoint=endpoint, use_http=True)
        ctx.connected = True
        ctx.server = session.server
        tok = (session.session_token or "")[:16]
        return CommandResult(
            ok=True,
            message="connected to " + session.server + " token=" + tok,
        )
    except Exception as exc:
        return CommandResult(ok=False, message="connect failed: " + str(exc))


@registry.register("newgroup", "Create a private group", "/newgroup <title>")
def cmd_newgroup(ctx: CommandContext, args: List[str]) -> CommandResult:
    app = ctx.services.get("app")
    if app is None:
        return CommandResult(ok=False, message="app not available")
    if not args:
        return CommandResult(ok=False, message="Usage: /newgroup <title>")
    title = " ".join(args)
    try:
        room = app.create_group(title)
        return CommandResult(ok=True, message="group created: " + room.title + " (" + room.room_id[:20] + "...)")
    except Exception as exc:
        return CommandResult(ok=False, message=str(exc))


@registry.register("newchannel", "Create a channel", "/newchannel <title>")
def cmd_newchannel(ctx: CommandContext, args: List[str]) -> CommandResult:
    app = ctx.services.get("app")
    if app is None:
        return CommandResult(ok=False, message="app not available")
    if not args:
        return CommandResult(ok=False, message="Usage: /newchannel <title>")
    title = " ".join(args)
    try:
        room = app.create_channel(title, public=True)
        return CommandResult(ok=True, message="channel created: " + room.title + " (" + room.room_id[:20] + "...)")
    except Exception as exc:
        return CommandResult(ok=False, message=str(exc))


@registry.register("search", "Search users, groups, channels", "/search <query>")
def cmd_search(ctx: CommandContext, args: List[str]) -> CommandResult:
    app = ctx.services.get("app")
    if app is None:
        return CommandResult(ok=False, message="app not available")
    q = " ".join(args)
    hits = app.search_directory(q)
    if not hits:
        return CommandResult(ok=True, message="(no results)")
    lines = []
    for h in hits[:30]:
        lines.append("  [{0}] {1}  {2}".format(h.kind, h.title, h.subtitle))
    return CommandResult(ok=True, message=chr(10).join(lines))


@registry.register("register", "Show identity / key registration info", "/register")
def cmd_register(ctx: CommandContext, args: List[str]) -> CommandResult:
    app = ctx.services.get("app")
    if app is None or app.identity is None:
        return CommandResult(ok=False, message="no identity")
    ident = app.identity
    pub = ident.public_key_bytes.hex()
    lines = [
        "Identity (auto-registered):",
        "  id         : " + ident.id,
        "  public_key : " + pub[:32] + "..." + pub[-16:],
        "  private_key: held encrypted in local profile (never displayed)",
    ]
    if getattr(app, "last_mnemonic", None):
        lines.append("  recovery   : (shown once at creation — check startup log)")
    return CommandResult(ok=True, message=chr(10).join(lines))


@registry.register("whois", "Show a user profile", "/whois <identity>")
def cmd_whois(ctx: CommandContext, args: List[str]) -> CommandResult:
    app = ctx.services.get("app")
    if app is None:
        return CommandResult(ok=False, message="app not available")
    if not args:
        return CommandResult(ok=False, message="Usage: /whois <identity>")
    try:
        prof = app.get_user_profile(args[0])
    except Exception as exc:
        return CommandResult(ok=False, message=str(exc))
    lines = [
        "Name    : " + (prof.display_name or "(unknown)"),
        "Identity: " + prof.identity_id,
        "Bio     : " + (prof.bio or "(empty)"),
        "Trusted : " + ("yes" if prof.trusted else "no"),
    ]
    return CommandResult(ok=True, message=chr(10).join(lines))


@registry.register("setname", "Set local display name for a contact", "/setname <identity> <name>")
def cmd_setname(ctx: CommandContext, args: List[str]) -> CommandResult:
    app = ctx.services.get("app")
    if app is None or len(args) < 2:
        return CommandResult(ok=False, message="Usage: /setname <identity> <name>")
    peer, name = args[0], " ".join(args[1:])
    app.set_contact_profile(peer, display_name=name)
    return CommandResult(ok=True, message="updated name for " + peer[:24])


@registry.register("setbio", "Set local bio note for a contact", "/setbio <identity> <bio...>")
def cmd_setbio(ctx: CommandContext, args: List[str]) -> CommandResult:
    app = ctx.services.get("app")
    if app is None or len(args) < 2:
        return CommandResult(ok=False, message="Usage: /setbio <identity> <text>")
    peer, bio = args[0], " ".join(args[1:])
    app.set_contact_profile(peer, bio=bio)
    return CommandResult(ok=True, message="updated bio for " + peer[:24])
