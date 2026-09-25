#!/usr/bin/env python3
import sys
import argparse
import json
import getpass
from pathlib import Path

# Ensure client directory is on sys.path for direct script execution and language servers
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from config import load_settings
    from db import NYXDatabase
    from crypto import NYXIdentity
    from commands import CommandContext
    from ui import ReplUI, NyxTUI
except ImportError:
    from .config import load_settings
    from .db import NYXDatabase
    from .crypto import NYXIdentity
    from .commands import CommandContext
    from .ui import ReplUI, NyxTUI

VERSION = "0.0.6"


def _get_password() -> Optional[str]:
    """Get encryption password from user."""
    # Check if there's an existing database with encrypted data
    # For now, always prompt for password
    print("NYX Secure Messaging Client")
    print("Enter encryption password (leave empty for no encryption):")
    password = getpass.getpass("Password: ")
    if not password:
        return None
    confirm = getpass.getpass("Confirm: ")
    if password != confirm:
        print("Passwords do not match!")
        return _get_password()
    return password


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description=f'NYX - Secure Messaging Client v{VERSION}')
    parser.add_argument('--version', action='version', version=f'NYX v{VERSION}')
    parser.add_argument('--config', help='Config file path')
    parser.add_argument('--repl', action='store_true', help='Use REPL interface')
    parser.add_argument('--tui', action='store_true', help='Use TUI interface (default)')
    parser.add_argument('--no-encrypt', action='store_true', help='Disable database encryption')
    args = parser.parse_args()

    settings = load_settings(args.config)

    db_path = settings.storage.database_path()
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    
    # Get password for encryption (unless disabled)
    password = None if args.no_encrypt else _get_password()
    db = NYXDatabase(db_path, password=password)

    identity_data = db.load_identity()
    if identity_data:
        # New format: private_key contains JSON with all keys
        try:
            key_data = json.loads(identity_data['private_key'])
            identity = NYXIdentity.from_dict(key_data)
        except (json.JSONDecodeError, KeyError):
            # Fallback: generate new identity if old format
            identity = NYXIdentity.generate()
            identity_id = identity.id
            db.save_identity(identity_id, json.dumps(identity.to_dict()).encode(), identity.public_key_bytes)
        else:
            identity_id = identity_data['id']
    else:
        identity = NYXIdentity.generate()
        identity_id = identity.id
        db.save_identity(identity_id, json.dumps(identity.to_dict()).encode(), identity.public_key_bytes)

    # Attach profile fields onto identity for header convenience
    try:
        profile = db.get_profile(identity_id)
        identity.display_name = profile.get("display_name") or identity_id[:16]
        identity.avatar = profile.get("avatar") or "👤"
    except Exception:
        identity.display_name = identity_id[:16]
        identity.avatar = "👤"

    server = settings.network.default_server
    session_token = db.get_meta("session_token")
    device_id = db.get_meta("device_id")

    ctx = CommandContext(
        identity=identity,
        identity_id=identity_id,
        server=server,
        connected=False,
        db=db,
        session_token=session_token,
        device_id=device_id,
    )

    use_repl = args.repl

    try:
        if use_repl:
            ui = ReplUI(ctx)
            ui.run()
        else:
            ui = NyxTUI(ctx)
            ui.run()
    except KeyboardInterrupt:
        print("\nExiting...")
    finally:
        db.close()


if __name__ == '__main__':
    main()
