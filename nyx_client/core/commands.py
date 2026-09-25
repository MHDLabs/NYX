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
        "Server: " + (ctx.server or "-"),
    ]
    return CommandResult(ok=True, message="\n".join(lines))


@registry.register("identity", "Show local identity", "/identity [show]")
def cmd_identity(ctx: CommandContext, args: List[str]) -> CommandResult:
    if not ctx.identity_id:
        return CommandResult(ok=False, message="no identity loaded")
    return CommandResult(ok=True, message="Identity: " + ctx.identity_id)


@registry.register("dm", "Open or send a direct message", "/dm <identity|@handle> [message...]")
def cmd_dm(ctx: CommandContext, args: List[str]) -> CommandResult:
    if not args:
        return CommandResult(ok=False, message="Usage: /dm <@handle|nyx1...> [message]")
    peer = args[0]
    app = ctx.services.get("app")
    if app is not None and hasattr(app, "resolve_handle"):
        try:
            peer = app.resolve_handle(peer)
        except Exception as exc:
            return CommandResult(ok=False, message="resolve: " + str(exc))
    if not str(peer).startswith("nyx1"):
        return CommandResult(
            ok=False,
            message="could not resolve to nyx1 id. Other user must: /connect then /id amir",
        )
    # require connection
    if app is not None and (
        app.connection is None
        or app.connection.session is None
        or not app.connection.session.is_authenticated()
    ):
        return CommandResult(ok=False, message="not connected — run /connect <relay-url> first")
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
        # confirm relay accepted
        if getattr(env, "status", None) and str(getattr(env.status, "value", env.status)) == "failed":
            return CommandResult(ok=False, message="send failed — check /connect and session")
        return CommandResult(
            ok=True,
            message="sent to {0} seq={1} id={2}...".format(peer[:20], env.sequence, env.message_id[:18]),
            data=env,
        )
    except Exception as exc:
        return CommandResult(ok=False, message="send error: " + str(exc))


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
    app = ctx.services.get("app")
    if app is not None and hasattr(app, "resolve_handle"):
        peer = app.resolve_handle(peer.lstrip("@"))
    if messaging is not None:
        c = messaging.ensure_contact(peer, display_name=name)
    else:
        c = store.upsert(peer, display_name=name)
    return CommandResult(ok=True, message="contact saved: " + c.identity_id[:28] + "...")


@registry.register("delcontact", "Remove a contact", "/delcontact <identity>")
def cmd_delcontact(ctx: CommandContext, args: List[str]) -> CommandResult:
    if not args:
        return CommandResult(ok=False, message="Usage: /delcontact <identity>")
    store = ctx.services.get("contacts")
    if store is None:
        return CommandResult(ok=False, message="contact store not available")
    peer = args[0]
    app = ctx.services.get("app")
    if app is not None and hasattr(app, "resolve_handle"):
        peer = app.resolve_handle(peer)
    ok = store.delete(peer)
    return CommandResult(ok=ok, message="deleted" if ok else "not found")


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
        meta = getattr(session, "sync_meta", {}) or {}
        lines = [
            "connected to " + session.server,
            "  registered : " + ("yes" if getattr(session, "registered", False) or meta.get("registered") else "session-only"),
            "  token      : " + ((session.session_token or "")[:20] + "..."),
            "  servers    : " + str(meta.get("servers_fetched", 0)) + " fetched",
            "  profile    : " + ("pushed" if meta.get("profile_pushed") else "skipped"),
            "  inbox      : " + str(meta.get("messages_synced", 0)) + " new msg(s)",
        ]
        if getattr(session, "server_identity", None):
            lines.append("  server_id  : " + str(session.server_identity)[:40])
        return CommandResult(ok=True, message=chr(10).join(lines))
    except Exception as exc:
        return CommandResult(ok=False, message="connect failed: " + str(exc))


@registry.register("newgroup", "Create a group (add public for discoverable)", "/newgroup [public] <title>")
def cmd_newgroup(ctx: CommandContext, args: List[str]) -> CommandResult:
    app = ctx.services.get("app")
    if app is None:
        return CommandResult(ok=False, message="app not available")
    if not args:
        return CommandResult(ok=False, message="Usage: /newgroup [public] <title>")
    public = False
    if args[0].lower() == "public":
        public = True
        args = args[1:]
    if not args:
        return CommandResult(ok=False, message="Usage: /newgroup [public] <title>")
    title = " ".join(args)
    try:
        room = app.create_group(title, public=public)
        vis = "public" if public else "private"
        return CommandResult(
            ok=True,
            message="group created ({0}): {1}\nid: {2}\nShare id with friends or use public search".format(
                vis, room.title, room.room_id
            ),
        )
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



@registry.register("wallet", "Show NYX UTXO wallet address, balance, coins", "/wallet")
def cmd_wallet(ctx: CommandContext, args: List[str]) -> CommandResult:
    app = ctx.services.get("app")
    if app is None or app.wallet is None:
        return CommandResult(ok=False, message="wallet not available")
    info = app.wallet.info()
    lines = [
        "Address : " + info.address,
        "Balance : " + app.wallet.format_balance() + "  (spendable UTXO)",
        "UTXOs   : " + str(len(app.wallet.list_utxos())),
        "Recent:",
    ]
    for u in app.wallet.list_utxos()[:8]:
        lines.append(f"  coin {u.outpoint[:20]}…  {u.amount_micro/1_000_000:.6f} NYX")
    for tx in app.wallet.history(8):
        sign = "+" if tx.kind in ("credit", "sale", "transfer_in", "import", "mint") else "-"
        nyx = tx.amount_micro / 1_000_000
        lines.append(f"  {sign}{nyx:.6f}  {tx.kind:10}  {tx.memo[:40]}")
    lines.append("Commands: /claimmint <voucher.json> · /claimrelay · /pay <id> <nyx>")
    return CommandResult(ok=True, message=chr(10).join(lines))



@registry.register("market", "List/search marketplace", "/market [category|search <q>]")
def cmd_market(ctx: CommandContext, args: List[str]) -> CommandResult:
    app = ctx.services.get("app")
    if app is None or app.marketplace is None:
        return CommandResult(ok=False, message="marketplace not available")
    if args and args[0].lower() == "cats":
        cats = app.marketplace.categories()
        if not cats:
            return CommandResult(ok=True, message="categories: source asset service digital other")
        lines = [f"  {c} ({n})" for c, n in cats]
        return CommandResult(ok=True, message=chr(10).join(lines))
    if args and args[0].lower() == "search":
        q = " ".join(args[1:])
        items = app.marketplace.search(q)
    elif args:
        items = app.marketplace.list_active(category=args[0])
    else:
        items = app.marketplace.list_active()
    if not items:
        return CommandResult(ok=True, message="(no listings)")
    lines = []
    for L in items:
        lines.append(
            f"  {L.listing_id[:12]}  {L.price_nyx:.4f} NYX  [{L.category}]  {L.title}"
        )
    return CommandResult(ok=True, message=chr(10).join(lines))


@registry.register("sell", "List item for sale (NYX)", "/sell <price> [category] <title> [| /path/file]")
def cmd_sell(ctx: CommandContext, args: List[str]) -> CommandResult:
    app = ctx.services.get("app")
    if app is None or app.marketplace is None:
        return CommandResult(ok=False, message="marketplace not available")
    if len(args) < 2:
        return CommandResult(ok=False, message="Usage: /sell <price> [category] <title> [| filepath]")
    raw = " ".join(args)
    product = ""
    if "|" in raw:
        raw, product = raw.split("|", 1)
        product = product.strip().strip('"')
        args = raw.split()
    try:
        price = float(args[0])
    except ValueError:
        return CommandResult(ok=False, message="invalid price")
    cats = set(getattr(app.marketplace, "CATEGORIES", ()))
    if len(args) >= 3 and args[1].lower() in cats:
        category = args[1].lower()
        title = " ".join(args[2:])
    else:
        category = "digital" if product else "other"
        title = " ".join(args[1:])
    try:
        L = app.marketplace.create_listing(
            title, price_nyx=price, category=category, product_path=product
        )
        extra = " +file" if L.product_path else ""
        return CommandResult(
            ok=True,
            message=f"listed {L.listing_id} [{L.category}] {L.price_nyx:.6f} NYX — {L.title}{extra}",
        )
    except Exception as exc:
        return CommandResult(ok=False, message=str(exc))



@registry.register("buy", "Buy listing; receive product file if attached", "/buy <listing_id>")
def cmd_buy(ctx: CommandContext, args: List[str]) -> CommandResult:
    app = ctx.services.get("app")
    if app is None or app.marketplace is None:
        return CommandResult(ok=False, message="marketplace not available")
    if not args:
        return CommandResult(ok=False, message="Usage: /buy <listing_id>")
    try:
        order = app.buy_listing(args[0]) if hasattr(app, "buy_listing") else app.marketplace.buy(args[0])
        bal = app.wallet.format_balance() if app.wallet else "?"
        msg = f"order {order.order_id} · paid · balance {bal}"
        if order.delivery_path:
            msg += f" · file → {order.delivery_path}"
        else:
            msg += " · (no digital file on listing)"
        return CommandResult(ok=True, message=msg)
    except Exception as exc:
        return CommandResult(ok=False, message=str(exc))



@registry.register("setemail", "Set recovery email (synced to relay on connect)", "/setemail <email>")
def cmd_setemail(ctx: CommandContext, args: List[str]) -> CommandResult:
    app = ctx.services.get("app")
    if app is None or app.prefs is None:
        return CommandResult(ok=False, message="prefs not available")
    if not args:
        return CommandResult(ok=False, message="Usage: /setemail you@example.com")
    try:
        app.prefs.set_recovery_email(args[0])
        return CommandResult(ok=True, message="recovery email saved (pushed on next /connect)")
    except Exception as exc:
        return CommandResult(ok=False, message=str(exc))


@registry.register("roomrole", "Set member role in a room", "/roomrole <room_id> <identity> <owner|admin|poster|member>")
def cmd_roomrole(ctx: CommandContext, args: List[str]) -> CommandResult:
    app = ctx.services.get("app")
    if app is None or app.room_roles is None or app.identity is None:
        return CommandResult(ok=False, message="not available")
    if len(args) < 3:
        return CommandResult(ok=False, message="Usage: /roomrole <room_id> <identity> <role>")
    room_id, ident, role = args[0], args[1], args[2]
    my = app.room_roles.get_role(room_id, app.identity.id)
    if my != "owner":
        return CommandResult(ok=False, message="only owner can assign roles")
    try:
        app.room_roles.upsert_member(room_id, ident, role)
        return CommandResult(ok=True, message=f"{ident[:20]}… → {role}")
    except Exception as exc:
        return CommandResult(ok=False, message=str(exc))


@registry.register("roompolicy", "Set who can post", "/roompolicy <room_id> <owner_only|posters|members>")
def cmd_roompolicy(ctx: CommandContext, args: List[str]) -> CommandResult:
    app = ctx.services.get("app")
    if app is None or app.room_roles is None or app.identity is None:
        return CommandResult(ok=False, message="not available")
    if len(args) < 2:
        return CommandResult(ok=False, message="Usage: /roompolicy <room_id> <policy>")
    room_id, policy = args[0], args[1]
    if app.room_roles.get_role(room_id, app.identity.id) != "owner":
        return CommandResult(ok=False, message="only owner can set policy")
    try:
        app.room_roles.set_post_policy(room_id, policy)
        return CommandResult(ok=True, message="post policy = " + policy)
    except Exception as exc:
        return CommandResult(ok=False, message=str(exc))


@registry.register("attach", "Send a file to a peer or room", "/attach <peer|@handle|room_id> <file_path>")
def cmd_attach(ctx: CommandContext, args: List[str]) -> CommandResult:
    app = ctx.services.get("app")
    if app is None or app.attachments is None or app.messaging is None:
        return CommandResult(ok=False, message="not available")
    if len(args) < 2:
        return CommandResult(ok=False, message="Usage: /attach <peer|room_id> <path>")
    from pathlib import Path as P
    target = args[0]
    path = P(" ".join(args[1:]).strip().strip('"'))
    try:
        if hasattr(app, "resolve_handle"):
            target = app.resolve_handle(target.lstrip("@"))
        note = f"[attachment] {path.name}"
        if target.startswith("grp_") or target.startswith("chn_") or "group" in target or "channel" in target:
            env = app.messaging.send_room_message(target, note.encode("utf-8"))
        else:
            env = app.messaging.send_dm(target, note.encode("utf-8"))
            try:
                app.messaging.ensure_contact(target)
            except Exception:
                pass
        att = app.attachments.store_file(path, env.conversation_id, env.message_id)
        return CommandResult(
            ok=True,
            message=f"file sent {att.filename} ({att.size} B) → {att.local_path}",
        )
    except Exception as exc:
        return CommandResult(ok=False, message=str(exc))


@registry.register("files", "List received/sent files for a conversation", "/files <conversation_id>")
def cmd_files(ctx: CommandContext, args: List[str]) -> CommandResult:
    app = ctx.services.get("app")
    if app is None or app.attachments is None:
        return CommandResult(ok=False, message="not available")
    if not args:
        return CommandResult(ok=False, message="Usage: /files <conversation_id>")
    items = app.attachments.list_for_conversation(args[0])
    if not items:
        return CommandResult(ok=True, message="(no files)")
    lines = [f"  {a.attachment_id[:12]}  {a.size:8} B  {a.filename}" for a in items]
    return CommandResult(ok=True, message="\n".join(lines))


@registry.register("emoji", "List quick emoji for compose", "/emoji")
def cmd_emoji(ctx: CommandContext, args: List[str]) -> CommandResult:
    from nyx_client.ui.emoji import list_quick
    return CommandResult(ok=True, message=" ".join(list_quick()))


@registry.register("setbg", "Set custom background tag/path for UI", "/setbg <value>")
def cmd_setbg(ctx: CommandContext, args: List[str]) -> CommandResult:
    app = ctx.services.get("app")
    if app is None or app.prefs is None:
        return CommandResult(ok=False, message="not available")
    val = " ".join(args).strip()
    app.prefs.set_custom_bg(val)
    return CommandResult(ok=True, message="background preference saved: " + (val or "(cleared)"))



@registry.register("walletexport", "Export encrypted wallet keystore to a folder", "/walletexport <dir> <passphrase>")
def cmd_walletexport(ctx: CommandContext, args: List[str]) -> CommandResult:
    app = ctx.services.get("app")
    if app is None or app.wallet is None:
        return CommandResult(ok=False, message="wallet not available")
    if len(args) < 2:
        return CommandResult(ok=False, message="Usage: /walletexport <directory> <passphrase>")
    from pathlib import Path as P
    directory, passphrase = P(args[0]), args[1]
    try:
        path = app.wallet.export_keystore(directory, passphrase)
        return CommandResult(ok=True, message="exported keystore → " + str(path))
    except Exception as exc:
        return CommandResult(ok=False, message=str(exc))


@registry.register("walletimport", "Import encrypted keystore", "/walletimport <file.nks> <passphrase> [--replace]")
def cmd_walletimport(ctx: CommandContext, args: List[str]) -> CommandResult:
    app = ctx.services.get("app")
    if app is None or app.wallet is None:
        return CommandResult(ok=False, message="wallet not available")
    if len(args) < 2:
        return CommandResult(ok=False, message="Usage: /walletimport <file.nks> <passphrase> [--replace]")
    from pathlib import Path as P
    path, passphrase = P(args[0]), args[1]
    replace = "--replace" in args[2:]
    try:
        info = app.wallet.import_keystore(path, passphrase, replace_ledger=replace)
        return CommandResult(
            ok=True,
            message=f"imported · address {info.address} · balance {info.balance_nyx:.6f} NYX",
        )
    except Exception as exc:
        return CommandResult(ok=False, message=str(exc))


@registry.register("fund", "Disabled — coins only via server-signed vouchers", "/fund")
def cmd_fund(ctx: CommandContext, args: List[str]) -> CommandResult:
    return CommandResult(
        ok=False,
        message="no free credit: use /claimmint <voucher.json> or /claimrelay after /connect",
    )


@registry.register(
    "claimmint",
    "Claim a signed mint voucher (mined coins) into wallet",
    "/claimmint <voucher.json>",
)
def cmd_claimmint(ctx: CommandContext, args: List[str]) -> CommandResult:
    app = ctx.services.get("app")
    if app is None:
        return CommandResult(ok=False, message="app not available")
    if not args:
        return CommandResult(ok=False, message="Usage: /claimmint <path-to-voucher.json>")
    try:
        r = app.claim_mint_voucher(args[0])
        return CommandResult(
            ok=True,
            message=f"claimed {r['amount_nyx']:.6f} NYX · txid {r['txid'][:16]}… · {r['balance']}",
        )
    except Exception as e:
        return CommandResult(ok=False, message=str(e))


@registry.register(
    "claimrelay",
    "Pull server-mined vouchers for this identity and claim them",
    "/claimrelay",
)
def cmd_claimrelay(ctx: CommandContext, args: List[str]) -> CommandResult:
    app = ctx.services.get("app")
    if app is None:
        return CommandResult(ok=False, message="app not available")
    try:
        r = app.fetch_mint_from_relay()
        return CommandResult(
            ok=True,
            message=f"claimed {r['claimed']} voucher(s) · balance {r['balance']}",
        )
    except Exception as e:
        return CommandResult(ok=False, message=str(e))


@registry.register("pay", "Pay NYX via signed UTXO transaction", "/pay <nyx1_identity> <amount_nyx> [memo...]")
def cmd_pay(ctx: CommandContext, args: List[str]) -> CommandResult:
    app = ctx.services.get("app")
    if app is None:
        return CommandResult(ok=False, message="app not available")
    if len(args) < 2:
        return CommandResult(ok=False, message="Usage: /pay <identity> <amount_nyx> [memo]")
    try:
        amount = float(args[1])
        memo = " ".join(args[2:])
        dest = app.resolve_handle(args[0])
        result = app.transfer_nyx(dest, amount, memo=memo)
        return CommandResult(
            ok=True,
            message=f"txid {result['txid']}  broadcast={result['broadcast']}  bal={app.wallet.format_balance()}",
        )
    except Exception as exc:
        return CommandResult(ok=False, message=str(exc))


@registry.register("mute", "Mute a user in a room", "/mute <room_id> <identity> [seconds] [reason...]")
def cmd_mute(ctx: CommandContext, args: List[str]) -> CommandResult:
    app = ctx.services.get("app")
    if app is None or app.room_roles is None or app.identity is None:
        return CommandResult(ok=False, message="not available")
    if len(args) < 2:
        return CommandResult(ok=False, message="Usage: /mute <room_id> <identity> [seconds] [reason]")
    room_id, target = args[0], args[1]
    duration = 0
    reason = ""
    if len(args) >= 3 and args[2].isdigit():
        duration = int(args[2])
        reason = " ".join(args[3:])
    else:
        reason = " ".join(args[2:])
    try:
        app.room_roles.mute(room_id, app.identity.id, target, duration_sec=duration, reason=reason)
        return CommandResult(ok=True, message="muted " + target[:24])
    except Exception as exc:
        return CommandResult(ok=False, message=str(exc))


@registry.register("unmute", "Unmute a user", "/unmute <room_id> <identity>")
def cmd_unmute(ctx: CommandContext, args: List[str]) -> CommandResult:
    app = ctx.services.get("app")
    if app is None or app.room_roles is None or app.identity is None:
        return CommandResult(ok=False, message="not available")
    if len(args) < 2:
        return CommandResult(ok=False, message="Usage: /unmute <room_id> <identity>")
    try:
        app.room_roles.unmute(args[0], app.identity.id, args[1])
        return CommandResult(ok=True, message="unmuted")
    except Exception as exc:
        return CommandResult(ok=False, message=str(exc))


@registry.register("kick", "Remove a user from a room", "/kick <room_id> <identity> [reason...]")
def cmd_kick(ctx: CommandContext, args: List[str]) -> CommandResult:
    app = ctx.services.get("app")
    if app is None or app.room_roles is None or app.identity is None:
        return CommandResult(ok=False, message="not available")
    if len(args) < 2:
        return CommandResult(ok=False, message="Usage: /kick <room_id> <identity> [reason]")
    reason = " ".join(args[2:])
    try:
        app.room_roles.kick(args[0], app.identity.id, args[1], reason=reason)
        return CommandResult(ok=True, message="kicked " + args[1][:24])
    except Exception as exc:
        return CommandResult(ok=False, message=str(exc))


@registry.register("voice", "Send a voice note (audio file) to a room or peer", "/voice <target_id> <audio_path> [duration_sec]")
def cmd_voice(ctx: CommandContext, args: List[str]) -> CommandResult:
    app = ctx.services.get("app")
    if app is None or app.attachments is None or app.messaging is None:
        return CommandResult(ok=False, message="not available")
    if len(args) < 2:
        return CommandResult(ok=False, message="Usage: /voice <room_or_peer> <audio_file> [duration]")
    from pathlib import Path as P
    target, path = args[0], P(args[1])
    duration = float(args[2]) if len(args) > 2 else 0.0
    try:
        note = f"[voice] {path.name} ({duration:.1f}s)"
        if target.startswith("grp_") or target.startswith("chn_"):
            env = app.messaging.send_room_message(target, note.encode("utf-8"))
        else:
            env = app.messaging.send_dm(target, note.encode("utf-8"))
        att = app.attachments.store_file(path, env.conversation_id, env.message_id, mime="audio/ogg")
        if app.media_sessions:
            app.media_sessions.register_voice_note(env.message_id, att.attachment_id, duration)
        return CommandResult(ok=True, message=f"voice note sent · {att.attachment_id}")
    except Exception as exc:
        return CommandResult(ok=False, message=str(exc))



@registry.register("handle", "Set or check unique username", "/handle [check] <name> | /handle set <name> | /handle room <room_id> <name>")
def cmd_handle(ctx: CommandContext, args: List[str]) -> CommandResult:
    app = ctx.services.get("app")
    if app is None:
        return CommandResult(ok=False, message="app not available")
    if not args:
        rec = app.handles.get_by_target(app.identity.id) if app.handles and app.identity else None
        if rec:
            return CommandResult(ok=True, message="your handle: @" + rec.handle)
        return CommandResult(ok=False, message="Usage: /handle set <name> | /handle check <name> | /handle room <room_id> <name>")
    op = args[0].lower()
    try:
        if op == "check" and len(args) >= 2:
            r = app.check_handle_available(args[1])
            return CommandResult(ok=r["status"] in ("available", "yours"), message=f"{r['status']}: {r['message']}")
        if op == "set" and len(args) >= 2:
            h = app.set_user_handle(args[1])
            return CommandResult(ok=True, message="handle set: @" + h)
        if op == "room" and len(args) >= 3:
            h = app.set_room_handle(args[1], args[2])
            return CommandResult(ok=True, message="room handle: @" + h)
        # shorthand /handle myname
        if op not in ("check", "set", "room"):
            h = app.set_user_handle(op)
            return CommandResult(ok=True, message="handle set: @" + h)
        return CommandResult(ok=False, message="bad usage")
    except Exception as e:
        return CommandResult(ok=False, message=str(e))


@registry.register("sync", "Pull inbox from relay (also runs automatically on connect)", "/sync")
def cmd_sync(ctx: CommandContext, args: List[str]) -> CommandResult:
    app = ctx.services.get("app")
    if app is None or app.messaging is None:
        return CommandResult(ok=False, message="not available")
    if app.connection is None:
        return CommandResult(ok=False, message="not connected — use /connect first")
    app.messaging._connection = app.connection
    full = bool(args and args[0].lower() in ("full", "all", "--full", "-f"))
    res = app.messaging.sync_inbox(full=full)
    if res.get("error"):
        return CommandResult(ok=False, message=str(res["error"]))
    ctx.connected = True
    return CommandResult(
        ok=True,
        message=f"synced pulled={res.get('pulled', 0)} ingested={res.get('ingested', 0)} full={full}",
    )


@registry.register("orders", "Your marketplace purchases", "/orders")
def cmd_orders(ctx: CommandContext, args: List[str]) -> CommandResult:
    app = ctx.services.get("app")
    if app is None or app.marketplace is None:
        return CommandResult(ok=False, message="not available")
    orders = app.marketplace.my_orders()
    if not orders:
        return CommandResult(ok=True, message="(no orders)")
    lines = []
    for o in orders:
        deliv = "file" if o.delivery_path else "no-file"
        lines.append(f"  {o.order_id[:14]}  {o.price_micro/1e6:.4f} NYX  [{deliv}]  {o.title[:40]}")
        if o.delivery_path:
            lines.append(f"      → {o.delivery_path}")
    return CommandResult(ok=True, message=chr(10).join(lines))


@registry.register("rate", "Rate a purchase 1-5", "/rate <order_id> <1-5> [comment]")
def cmd_rate(ctx: CommandContext, args: List[str]) -> CommandResult:
    app = ctx.services.get("app")
    if app is None or app.marketplace is None:
        return CommandResult(ok=False, message="not available")
    if len(args) < 2:
        return CommandResult(ok=False, message="Usage: /rate <order_id> <1-5> [comment]")
    try:
        score = int(args[1])
        comment = " ".join(args[2:])
        app.marketplace.rate(args[0], score, comment)
        return CommandResult(ok=True, message=f"rated {score}/5")
    except Exception as e:
        return CommandResult(ok=False, message=str(e))


@registry.register("joinroom", "Join a group/channel by room id", "/joinroom <room_id>")
def cmd_joinroom(ctx: CommandContext, args: List[str]) -> CommandResult:
    if not args:
        return CommandResult(ok=False, message="Usage: /joinroom <room_id>")
    app = ctx.services.get("app")
    if app is None:
        return CommandResult(ok=False, message="app not available")
    try:
        room = app.join_room(args[0])
        if room is None:
            return CommandResult(ok=False, message="join failed")
        return CommandResult(ok=True, message=f"joined {room.room_id} · {room.title}")
    except Exception as exc:
        return CommandResult(ok=False, message=str(exc))

@registry.register("whoami", "Show own identity", "/whoami")
def cmd_whoami(ctx: CommandContext, args: List[str]) -> CommandResult:
    app = ctx.services.get("app")
    if app is None or app.identity is None:
        return CommandResult(ok=False, message="not started")
    return CommandResult(ok=True, message=app.identity.id)

@registry.register("lookup", "Lookup user profile on relay", "/lookup <nyx1...>")
def cmd_lookup(ctx: CommandContext, args: List[str]) -> CommandResult:
    if not args:
        return CommandResult(ok=False, message="Usage: /lookup <identity>")
    app = ctx.services.get("app")
    if app is None:
        return CommandResult(ok=False, message="app not available")
    data = app.fetch_profile(args[0])
    if not data:
        return CommandResult(ok=False, message="not found or offline")
    name = data.get("display_name") or "(no name)"
    return CommandResult(ok=True, message=f"{name}\n{data.get('identity_id', args[0])}\ndm_key={'yes' if data.get('dm_public_key') else 'no'}")


@registry.register("mine", "Mine NYX tokens on connected relay", "/mine [nonce]")
def cmd_mine(ctx: CommandContext, args: List[str]) -> CommandResult:
    app = ctx.services.get("app")
    if app is None or not app.connection or not app.connection.session:
        return CommandResult(ok=False, message="not connected")
    import hashlib, os, time, asyncio
    cfg = {}
    try:
        async def _cfg():
            return await app.connection.transport.request("GET", "/api/v3/token/mining/config", timeout=10)
        cfg = asyncio.run(_cfg()) or {}
    except Exception as exc:
        return CommandResult(ok=False, message="config: " + str(exc))
    conf = (cfg.get("config") if isinstance(cfg, dict) else None) or {}
    diff = int(conf.get("difficulty") or 2)
    if not conf.get("enabled", True):
        return CommandResult(ok=False, message="mining disabled on this relay")
    identity = app.identity.id
    device = ""
    day = time.strftime("%Y-%m-%d", time.gmtime())
    need = "0" * diff
    nonce = args[0] if args else None
    if not nonce:
        for i in range(5_000_000):
            nonce = format(i, "x") + os.urandom(4).hex()
            h = hashlib.sha256(f"{identity}|{nonce}|{device}|{day}".encode()).hexdigest()
            if h.startswith(need):
                break
        else:
            return CommandResult(ok=False, message="could not find proof (raise attempts)")
    try:
        async def _m():
            return await app.connection.transport.request(
                "POST", "/api/v3/token/mine", body={"nonce": nonce, "device_id": device}, timeout=30
            )
        res = asyncio.run(_m())
    except Exception as exc:
        return CommandResult(ok=False, message=str(exc))
    if not isinstance(res, dict) or res.get("status") != "ok":
        return CommandResult(ok=False, message=str(res))
    return CommandResult(
        ok=True,
        message="mined {0} micro · tx {1}".format(res.get("amount_micro"), res.get("txid", "")[:20]),
    )


@registry.register("roomrole", "Set member role in room", "/roomrole <room_id> <identity> <admin|moderator|member|readonly>")
def cmd_roomrole(ctx: CommandContext, args: List[str]) -> CommandResult:
    if len(args) < 3:
        return CommandResult(ok=False, message="Usage: /roomrole <room> <nyx1> <role>")
    app = ctx.services.get("app")
    if app is None or not app.connection:
        return CommandResult(ok=False, message="not connected")
    import asyncio
    from urllib.parse import quote
    room, ident, role = args[0], args[1], args[2]
    try:
        async def _r():
            return await app.connection.transport.request(
                "POST",
                f"/api/v3/rooms/{quote(room, safe='')}/role",
                body={"identity_id": ident, "role": role},
                timeout=15,
            )
        res = asyncio.run(_r())
        return CommandResult(ok=True, message=str(res))
    except Exception as exc:
        return CommandResult(ok=False, message=str(exc))


@registry.register("roomkick", "Kick member from room", "/roomkick <room_id> <identity>")
def cmd_roomkick(ctx: CommandContext, args: List[str]) -> CommandResult:
    if len(args) < 2:
        return CommandResult(ok=False, message="Usage: /roomkick <room> <nyx1>")
    app = ctx.services.get("app")
    if app is None or not app.connection:
        return CommandResult(ok=False, message="not connected")
    import asyncio
    from urllib.parse import quote
    try:
        async def _r():
            return await app.connection.transport.request(
                "POST",
                f"/api/v3/rooms/{quote(args[0], safe='')}/kick",
                body={"identity_id": args[1]},
                timeout=15,
            )
        res = asyncio.run(_r())
        return CommandResult(ok=True, message=str(res))
    except Exception as exc:
        return CommandResult(ok=False, message=str(exc))

# Explicit /id and /setid (always registered)
@registry.register("id", "Set or show your @username", "/id [name]")
@registry.register("setid", "Alias of /id", "/setid [name]")
@registry.register("username", "Alias of /id", "/username [name]")
def cmd_id(ctx: CommandContext, args: List[str]) -> CommandResult:
    """Set public @handle on the connected relay, or show current."""
    app = ctx.services.get("app")
    if app is None:
        return CommandResult(ok=False, message="app not ready")
    if not args:
        if app.handles and app.identity:
            rec = None
            try:
                rec = app.handles.get_by_target(app.identity.id)
            except Exception:
                rec = None
            if rec is not None:
                h = getattr(rec, "handle", None) or (rec.get("handle") if isinstance(rec, dict) else None)
                if h:
                    return CommandResult(ok=True, message="@" + str(h) + " → " + app.identity.id)
        return CommandResult(ok=True, message="no @id yet. Connect first, then: /id myname")
    name = args[0].lstrip("@").strip()
    if len(name) < 3:
        return CommandResult(ok=False, message="name too short (min 3)")
    try:
        h = app.set_user_handle(name)
        return CommandResult(ok=True, message="OK — your id is @" + h)
    except Exception as exc:
        return CommandResult(ok=False, message=str(exc))
