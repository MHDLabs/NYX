"""
commands.py — Command implementations for the NYX Messenger REPL.

Each function performs one user action (register, send, sync, etc.) and
returns a result dict.  The REPL dispatcher in main.py handles the
user-facing output.  All errors are printed locally and never raise.

No sys.exit() calls — the REPL must keep running.
"""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from typing import Any, Dict, Optional

import requests
from rich.console import Console

import config
import crypto
import db

# ── Console for plain text output (no colors, no ANSI codes) ──────────────
console = Console(color_system=None, no_color=True, force_terminal=False)


# ── Error helper ───────────────────────────────────────────────────────────

def _error(msg: str) -> None:
    """Print a plain error message."""
    print(f"[ERROR] {msg}")


def _info(msg: str) -> None:
    """Print a plain info message."""
    print(f"[INFO] {msg}")


def _success(msg: str) -> None:
    """Print a plain success message."""
    print(f"[OK] {msg}")


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
    print()
    print("=== NYX Commands ===")
    print()
    print("  help                          Show this help message")
    print("  register                      Register your identity with the relay server")
    print("  myid                          Show your device ID and public key")
    print("  sync                          Pull new messages from the server")
    print("  send <contact> <message>      Send an encrypted message")
    print("  contacts                      List known contacts (device IDs)")
    print("  import <public_key>           Import a contact's public key")
    print("  decrypt <ciphertext> <nonce>  Decrypt a message manually")
    print("  config [key] [value]          View or set configuration")
    print("  server [url]                  View or set the relay server URL")
    print("  clear                         Clear the terminal screen")
    print("  debug                         Show debug information")
    print("  quit / exit                   Exit NYX")
    print()


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

    print()
    print("Device Identity")
    print(f"  Device ID:    {device_id}")
    print(f"  Public Key:   {public_key}")
    print(f"  Key length:   {len(public_key)} chars (base64, 64 raw bytes)")
    print()
    print("Copy the full public key above to share with other NYX users.")


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

            # Attempt decryption with sender_device_id for AAD verification
            plaintext = crypto_engine.decrypt(ciphertext_b64, nonce_b64, msg.get('sender_id', ''))

            if plaintext:
                print(f"  [OK] [{created}] from {sender}... : {plaintext}")
            else:
                print(f"  [--] [{created}] from {sender}... : [encrypted, cannot decrypt]")

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

    # ── Fetch recipient's public key bundle from local DB ─────────
    recipient_pubkey_bundle = local_db.get_contact(recipient_id)
    if not recipient_pubkey_bundle:
        _error(f"No public key found for {recipient_id}")
        _info("Use 'sync' to discover contacts, or 'import' to add manually.")
        return None

    # ── Parse the bundle to extract X25519 public key ─────────────
    try:
        _, recipient_x25519_pub = crypto.parse_public_key_bundle(recipient_pubkey_bundle)
    except ValueError as e:
        _error(f"Invalid public key format: {e}")
        return None

    # ── Encrypt with recipient's X25519 public key (NOT device_id!)
    ciphertext_b64, nonce_b64 = crypto_engine.encrypt(plaintext, recipient_x25519_pub)

    # ── Generate message ID ────────────────────────────────────────
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

    print()
    print("=== Known Contacts ===")
    print()
    print(f"  {'Device ID':<20} {'Public Key (truncated)'}")
    print(f"  {'-'*20} {'-'*40}")
    for cid, ckey in contacts:
        print(f"  {cid:<20} {ckey[:48]}...")
    print()


def import_contact(local_db: db.NYXDatabase, public_key_b64: str) -> None:
    """
    Import a contact by their public key bundle.

    Accepts the 88-character base64 bundle (the exact output of 'myid').
    The device_id is deterministically derived from the Ed25519 public key:
      device_id = hashlib.sha256(ed25519_public).hexdigest()[:16]
    """
    try:
        ed_pub, x_pub = crypto.parse_public_key_bundle(public_key_b64.strip())
    except ValueError:
        _error("Invalid public key format.")
        return
    except Exception:
        _error("Invalid public key format.")
        return

    device_id = hashlib.sha256(ed_pub).hexdigest()[:16]

    local_db.save_contact(device_id, public_key_b64.strip())
    _success(f"Contact imported — device_id: {device_id}")
    print(f"  Ed25519: {ed_pub.hex()[:32]}...")
    print(f"  X25519:  {x_pub.hex()[:32]}...")


def decrypt_message(crypto_engine: crypto.NYXCrypto,
                    ciphertext_b64: str, nonce_b64: str,
                    sender_device_id: str = '') -> None:
    """Manually decrypt a base64 ciphertext + nonce."""
    plaintext = crypto_engine.decrypt(ciphertext_b64, nonce_b64, sender_device_id)

    if plaintext:
        print(f"Decrypted: {plaintext}")
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
    print()
    print("Debug Information")
    print(f"  Config path:    {cfg.config_path}")
    print(f"  Server URL:     {cfg.server_url}")
    print(f"  DB path:        {cfg.db_path}")
    print(f"  Device ID:      {crypto_engine.device_id}")
    print(f"  Has identity:   {crypto_engine.has_identity()}")
    print(f"  Registered:     {local_db.is_registered()}")
    print(f"  Contacts:       {len(local_db.get_contacts())}")