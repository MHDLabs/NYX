"""
main.py — NYX Messenger interactive REPL.

Runs a persistent session with:
  • prompt_toolkit for beautiful line editing
  • background sync thread that auto-fetches and displays new messages
  • plain text output (no ANSI escape codes)

Usage:
    python main.py               # Start the REPL
    python main.py --server URL  # Override the server URL

Inside the REPL, type 'help' for available commands.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import traceback
from typing import Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console

# ── Add parent directory to path so we can import sibling modules ──────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import db
import crypto
import commands

# ── Console for plain text output (no colors, no ANSI codes) ──────────────
console = Console(color_system=None, no_color=True, force_terminal=False)

# ── Global state ───────────────────────────────────────────────────────────
running = True
sync_thread: Optional[threading.Thread] = None
sync_event = threading.Event()


def print_banner():
    """Print the NYX startup banner."""
    print()
    print("+====================================+")
    print("|         NYX Messenger v1.0         |")
    print("|   End-to-End Encrypted Messaging   |")
    print("+====================================+")
    print()


def background_sync(cfg: config.NYXConfig, local_db: db.NYXDatabase, crypto_engine: crypto.NYXCrypto):
    """
    Background thread: pulls new messages periodically and announces them.
    """
    global running
    interval = 3  # seconds between sync cycles

    while running:
        try:
            # Fetch from server
            since = local_db.get_last_sync_time()
            result = commands.sync_messages(cfg, local_db, crypto_engine, since=since)

            if result and result.get('messages'):
                # Update local sync timestamp
                last_times = [m.get('created_at', '') for m in result['messages'] if m.get('created_at')]
                if last_times:
                    newest = max(last_times)
                    local_db.set_last_sync_time(newest)

                # Announce new messages (they've already been printed by sync_messages)
                pass

        except Exception:
            # Silently continue on sync errors — don't crash the REPL
            pass

        # Wait for the interval, but check `running` flag frequently
        for _ in range(interval * 10):
            if not running:
                return
            time.sleep(0.1)


def process_command(line: str, cfg: config.NYXConfig, local_db: db.NYXDatabase,
                    crypto_engine: crypto.NYXCrypto) -> bool:
    """
    Parse and execute a single command line.
    Returns False if the user wants to quit.
    """
    global running

    stripped = line.strip()
    if not stripped:
        return True

    parts = stripped.split(maxsplit=1)
    cmd = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    try:
        if cmd == 'quit' or cmd == 'exit':
            print("Goodbye. Stay encrypted.")
            running = False
            return False

        elif cmd == 'help':
            commands.show_help()

        elif cmd == 'register':
            commands.register(cfg, local_db, crypto_engine)

        elif cmd == 'myid':
            commands.show_my_id(crypto_engine)

        elif cmd == 'sync':
            since = local_db.get_last_sync_time()
            result = commands.sync_messages(cfg, local_db, crypto_engine, since=since)
            if result and result.get('messages'):
                last_times = [m.get('created_at', '') for m in result['messages'] if m.get('created_at')]
                if last_times:
                    local_db.set_last_sync_time(max(last_times))

        elif cmd == 'send':
            if not args:
                print("Usage: send <contact> <message>")
            else:
                # Split args into contact name and message
                send_parts = args.split(maxsplit=1)
                if len(send_parts) < 2:
                    print("Usage: send <contact> <message>")
                else:
                    contact_name = send_parts[0]
                    message = send_parts[1]
                    commands.send_message(cfg, local_db, crypto_engine, contact_name, message)

        elif cmd == 'contacts':
            commands.list_contacts(local_db)

        elif cmd == 'import':
            if not args:
                print("Usage: import <public_key>")
            else:
                commands.import_contact(local_db, args)

        elif cmd == 'decrypt':
            if not args:
                print("Usage: decrypt <base64_ciphertext> <base64_nonce>")
            else:
                dec_parts = args.split()
                if len(dec_parts) < 2:
                    print("Usage: decrypt <base64_ciphertext> <base64_nonce>")
                else:
                    commands.decrypt_message(crypto_engine, dec_parts[0], dec_parts[1])

        elif cmd == 'config':
            if not args:
                print(f"Server: {cfg.server_url}")
                print(f"DB path: {cfg.db_path}")
            else:
                cfg_parts = args.split(maxsplit=1)
                if len(cfg_parts) == 2:
                    commands.set_config(cfg, cfg_parts[0], cfg_parts[1])
                else:
                    print("Usage: config <key> <value>")
                    print("Available keys: server_url")

        elif cmd == 'server':
            if not args:
                print(f"Server URL: {cfg.server_url}")
            else:
                cfg.set('server_url', args.strip())
                print(f"Server URL set to: {args.strip()}")

        elif cmd == 'clear':
            os.system('clear' if os.name != 'nt' else 'cls')

        elif cmd == 'debug':
            commands.show_debug_info(crypto_engine, local_db, cfg)

        else:
            print(f"Unknown command: {cmd}")
            print("Type 'help' for available commands.")

    except KeyboardInterrupt:
        print("\nCommand interrupted.")
    except Exception as e:
        print(f"Error: {e}")
        if os.environ.get('NYX_DEBUG'):
            traceback.print_exc()

    return True


def run_repl(cfg: config.NYXConfig, local_db: db.NYXDatabase,
             crypto_engine: crypto.NYXCrypto, no_sync: bool = False):
    """
    Main REPL loop using prompt_toolkit with patch_stdout for background sync.
    """
    global running, sync_thread

    # Build the prompt string
    prompt_session = PromptSession(
        history=FileHistory(str(config.NYX_HOME / '.nyx_history')),
    )

    # Start background sync thread (unless disabled)
    if not no_sync:
        sync_thread = threading.Thread(
            target=background_sync,
            args=(cfg, local_db, crypto_engine),
            daemon=True,
        )
        sync_thread.start()

    # Use patch_stdout so background messages don't collide with input
    with patch_stdout():
        while running:
            try:
                line = prompt_session.prompt("nyx> ")
                if not process_command(line, cfg, local_db, crypto_engine):
                    break
            except (KeyboardInterrupt, EOFError):
                print("\nGoodbye. Stay encrypted.")
                break

    # Signal the sync thread to stop
    running = False
    if sync_thread and sync_thread.is_alive():
        sync_thread.join(timeout=2)


def main():
    """Entry point for the NYX Messenger REPL."""
    global running

    parser = argparse.ArgumentParser(
        description="NYX Messenger — End-to-end encrypted messaging client",
    )
    parser.add_argument(
        "--server", "-s",
        help="Server URL (default: from config or http://localhost:8000)",
    )
    parser.add_argument(
        "--no-sync",
        action="store_true",
        help="Disable background auto-sync",
    )
    args = parser.parse_args()

    # ── Initialize configuration ───────────────────────────────────────
    cfg = config.NYXConfig()
    cfg.load()

    if args.server:
        cfg.set("server_url", args.server)

    # ── Initialize database ────────────────────────────────────────────
    local_db = db.NYXDatabase(cfg.db_path)

    # ── Initialize cryptographic identity ──────────────────────────────
    crypto_engine = crypto.NYXCrypto(
        device_id_path=str(config.NYX_HOME / 'device_id'),
        keys_path=str(config.NYX_HOME / 'keys'),
    )

    # ── Print banner ───────────────────────────────────────────────────
    print_banner()

    # ── Auto-register if needed ────────────────────────────────────────
    if not local_db.is_registered():
        print("No local identity found. Generating new identity...")
        crypto_engine.generate_identity()
        commands.register(cfg, local_db, crypto_engine)
        print()

    # ── Check connectivity ─────────────────────────────────────────────
    print(f"Server: {cfg.server_url}")
    print(f"Identity: {crypto_engine.device_id[:16]}...")
    print()

    # ── Run the REPL ───────────────────────────────────────────────────
    run_repl(cfg, local_db, crypto_engine, no_sync=args.no_sync)


if __name__ == '__main__':
    main()