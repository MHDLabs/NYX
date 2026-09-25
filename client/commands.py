"""
commands.py — Command implementations for the NYX Messenger REPL.

Each function performs one user action (register, send, sync, etc.) and
returns a result dict.  The REPL dispatcher in main.py handles the
user-facing output.  All errors are printed locally and never raise.

No sys.exit() calls — the REPL must keep running.
"""

from __future__ import annotations

import base64
import getpass
import json
from typing import Any, Dict, Optional

import requests
from rich.console import Console
from rich.table import Table

import config
import crypto
import db

console = Console()


# ── Error helper ───────────────────────────────────────────────────────────

def _error(msg: str) -> None:
    """Print a styled error message."""
    console.print(f"[red][ERROR] {msg}[/]")


def _info(msg: str) -> None:
    """Print a styled info message."""
    console.print(f"[cyan][INFO] {msg}[/]")


def _success(msg: str) -> None:
    """Print a styled success message."""
    console.print(f"[green][OK] {msg}[/]")


# ── Network helper ─────────────────────────────────────────────────────────

def _post(cfg: config.NYXConfig, endpoint: str, payload: dict,
          timeout: int = 10) -> Optional[dict]:
    """
    POST JSON to the relay server.  Returns the parsed response on
    success, or None on failure.
    """
    url = f"{cfg.server_url}/{endpoint}"
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        data = resp.json()
        return data
    except requests.exceptions.ConnectionError:
        _error(f"Cannot reach server at {url}")
        return None
    except requests.exceptions.Timeout:
        _error(f"Request timed out after {timeout}s")
        return None
    except Exception as e:
        _error(f"Network error: {e}")
        return None


# ── Commands ───────────────────────────────────────────────────────────────

def show_help() -> None:
    """Print the help screen."""
    table = Table(title="NYX Commands", show_header=True,
                  header_style="bold cyan")
    table.add_column("Command", style="bold")
    table.add_column("Description")

    table.add_row("help", "Show this help message")
    table.add_row("register", "Register your identity with the relay server")
    table.add_row("myid", "Show your device ID and public key")
    table.add_row("sync", "Pull new messages from the server")
    table.add_row("send <contact> <message>", "Send an encrypted message")
    table.add_row("contacts", "List known contacts (device IDs)")
    table.add_row("import <public_key>", "Import a contact's public key")
    table.add_row("decrypt <ciphertext> <nonce>", "Decrypt a message manually")
    table.add_row("config [key] [value]", "View or set configuration")
    table.add_row("server [url]", "View or set the relay server URL")
    table.add_row("clear", "Clear the terminal screen")
    table.add_row("debug", "Show debug information")
    table.add_row("quit / exit", "Exit NYX")

    console.print(table)


def register(cfg: config.NYXConfig, local_db: db.NYXDatabase,
             crypto_engine: crypto.NYXCrypto) -> Optional[Dict[str, Any]]:
    """
    Register this device's public key with the relay server.
    Generates a new identity if one doesn't exist yet.
    """
    if not crypto_engine.has_identity():
        crypto_engine.generate_identity()

    device_id = crypto_engine.device_id
    public_key = crypto_engine.get_public_key_b64()

    _info(f"Registering device {device_id[:16]}...")

    resp = _post(cfg, 'register.php', {
        'device_id': device_id,
        'public_key': public_key,
    })

    if resp is None:
        _error("Registration failed — server unreachable.")
        return None

    if resp.get('status') == 'ok':
        _success("Registered successfully.")
        local_db.save_identity(device_id, public_key)
    else:
        _error(f"Registration rejected: {resp.get('error', 'unknown error')}")

    return resp


def show_my_id(crypto_engine: crypto.NYXCrypto) -> None:
    """Display the local device identity."""
    device_id = crypto_engine.device_id
    public_key = crypto_engine.get_public_key_b64()

    console.print("[bold]Device Identity[/]")
    console.print(f"  [cyan]Device ID:[/]    {device_id}")
    console.print(f"  [cyan]Public Key:[/]   {public_key[:64]}...")
    console.print(f"  [dim]Key length: {len(public_key)} chars (base64)[/]")


def sync_messages(cfg: config.NYXConfig, local_db: db.NYXDatabase,
                  crypto_engine: crypto.NYXCrypto,
                  since: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Pull pending messages from the relay server.
    Decrypts and displays them, then stores them locally.
    """
    device_id = crypto_engine.device_id

    payload: Dict[str, Any] = {'user_id': device_id}
    if since:
        payload['since'] = since

    resp = _post(cfg, 'sync.php', payload)

    if resp is None:
        _error("Sync failed — server unreachable.")
        return None

    # ── Process received messages ──────────────────────────────────
    messages = resp.get('messages', [])
    if messages:
        _info(f"Received {len(messages)} new message(s).")
        for msg in messages:
            sender = msg.get('sender_id', '???')[:16]
            ciphertext_b64 = msg.get('ciphertext', '')
            nonce_b64 = msg.get('nonce', '')
            created = msg.get('created_at', '???')

            # Attempt decryption
            plaintext = crypto_engine.decrypt(ciphertext_b64, nonce_b64)

            if plaintext:
                console.print(f"  [green]✓[/] [{created}] from {sender}... : {plaintext}")
            else:
                console.print(f"  [yellow]✗[/] [{created}] from {sender}... : [encrypted, cannot decrypt]")

            # Store in local DB
            local_db.save_message(
                message_id=msg.get('message_id', ''),
                sender_id=msg.get('sender_id', ''),
                ciphertext=ciphertext_b64,
                nonce=nonce_b64,
                plaintext=plaintext or '',
                created_at=created,
            )
    else:
        _info("No new messages.")

    # ── Store discovered public keys ───────────────────────────────
    keys = resp.get('keys', {})
    count = 0
    for kid, kpub in keys.items():
        if kid != device_id:
            local_db.save_contact(kid, kpub)
            count += 1
    if count:
        _info(f"Discovered {count} new contact(s).")

    return resp


def send_message(cfg: config.NYXConfig, local_db: db.NYXDatabase,
                 crypto_engine: crypto.NYXCrypto,
                 contact_name: str, plaintext: str) -> Optional[Dict[str, Any]]:
    """
    Send an encrypted message to a contact.
    contact_name can be a full device_id or a prefix/alias.
    """
    # ── Resolve contact ────────────────────────────────────────────
    recipient_id = local_db.resolve_contact(contact_name)

    if not recipient_id:
        _error(f"Unknown contact: {contact_name}")
        _info("Use 'contacts' to list known contacts, or 'import' to add one.")
        return None

    # ── Encrypt the message ────────────────────────────────────────
    ciphertext_b64, nonce_b64 = crypto_engine.encrypt(plaintext, recipient_id)

    # ── Generate message ID ────────────────────────────────────────
    import uuid
    message_id = str(uuid.uuid4())

    # ── Send to server ─────────────────────────────────────────────
    sender_id = crypto_engine.device_id
    _info(f"Encrypting and sending to {recipient_id[:16]}...")

    resp = _post(cfg, 'send.php', {
        'message_id': message_id,
        'sender_id': sender_id,
        'recipient_id': recipient_id,
        'ciphertext': ciphertext_b64,
        'nonce': nonce_b64,
    })

    if resp is None:
        _error("Send failed — server unreachable.")
        return None

    if resp.get('status') == 'ok':
        _success(f"Message sent to {recipient_id[:16]}...")

        # Store locally as sent
        local_db.save_message(
            message_id=message_id,
            sender_id=sender_id,
            ciphertext=ciphertext_b64,
            nonce=nonce_b64,
            plaintext=plaintext,
            created_at='',
            is_sent=True,
            recipient_id=recipient_id,
        )
    else:
        _error(f"Send rejected: {resp.get('error', 'unknown error')}")

    return resp


def list_contacts(local_db: db.NYXDatabase) -> None:
    """Display all known contacts."""
    contacts = local_db.get_contacts()

    if not contacts:
        _info("No contacts yet. Use 'sync' or 'import' to add contacts.")
        return

    table = Table(title="Known Contacts", show_header=True,
                  header_style="bold cyan")
    table.add_column("Device ID", style="bold")
    table.add_column("Public Key (truncated)")

    for cid, ckey in contacts:
        table.add_row(cid, ckey[:48] + "...")

    console.print(table)


def import_contact(local_db: db.NYXDatabase, public_key_b64: str) -> None:
    """
    Import a contact by their public key.
    The device_id is derived from the key (or shown for manual entry).
    """
    # We can't derive device_id from public key in Ed25519 without
    # the convention. For now, ask the user for the device_id, or
    # display the key and let them add it manually.
    _info("To import a contact, provide both device_id and public_key.")
    _info("Use: 'import' then enter the details when prompted.")

    device_id = input("  Device ID: ").strip()
    if not device_id:
        _error("Cannot import without a device ID.")
        return

    local_db.save_contact(device_id, public_key_b64)
    _success(f"Contact {device_id[:16]}... imported.")


def decrypt_message(crypto_engine: crypto.NYXCrypto,
                    ciphertext_b64: str, nonce_b64: str) -> None:
    """Manually decrypt a base64 ciphertext + nonce."""
    plaintext = crypto_engine.decrypt(ciphertext_b64, nonce_b64)

    if plaintext:
        console.print(f"[green]Decrypted:[/] {plaintext}")
    else:
        _error("Decryption failed. Do you have the right key?")


def set_config(cfg: config.NYXConfig, key: str, value: str) -> None:
    """Set a configuration value."""
    allowed_keys = {'server_url'}
    if key not in allowed_keys:
        _error(f"Unknown config key: {key}")
        _info(f"Allowed keys: {', '.join(sorted(allowed_keys))}")
        return

    cfg.set(key, value)
    _success(f"{key} = {value}")


def show_debug_info(crypto_engine: crypto.NYXCrypto,
                    local_db: db.NYXDatabase,
                    cfg: config.NYXConfig) -> None:
    """Show debug information about the current state."""
    console.print("[bold]Debug Information[/]")

    console.print(f"  [cyan]Config path:[/]    {cfg.config_path}")
    console.print(f"  [cyan]Server URL:[/]     {cfg.server_url}")
    console.print(f"  [cyan]DB path:[/]        {cfg.db_path}")
    console.print(f"  [cyan]Device ID:[/]      {crypto_engine.device_id}")
    console.print(f"  [cyan]Has identity:[/]   {crypto_engine.has_identity()}")
    console.print(f"  [cyan]Registered:[/]     {local_db.is_registered()}")
    console.print(f"  [cyan]Contacts:[/]       {len(local_db.get_contacts())}")