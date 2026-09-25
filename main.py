#!/usr/bin/env python3
import sys
import argparse
from pathlib import Path

# Ensure client directory is on sys.path for direct script execution and language servers
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from config import load_settings
    from db import NYXDatabase
    from crypto import Identity
    from commands import CommandContext
    from ui import ReplUI, NyxTUI
except ImportError:
    from .config import load_settings
    from .db import NYXDatabase
    from .crypto import Identity
    from .commands import CommandContext
    from .ui import ReplUI, NyxTUI

VERSION = "0.0.6"


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description=f'NYX - Secure Messaging Client v{VERSION}')
    parser.add_argument('--version', action='version', version=f'NYX v{VERSION}')
    parser.add_argument('--config', help='Config file path')
    parser.add_argument('--repl', action='store_true', help='Use REPL interface')
    parser.add_argument('--tui', action='store_true', help='Use TUI interface (default)')
    args = parser.parse_args()

    settings = load_settings(args.config)

    db_path = settings.storage.database_path()
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    db = NYXDatabase(db_path)

    identity_data = db.load_identity()
    if identity_data:
        identity = Identity.load(identity_data['private_key'])
        identity_id = identity_data['id']
    else:
        identity = Identity.create()
        identity_id = identity.id
        db.save_identity(identity_id, identity.private_key_bytes, identity.public_key_bytes)

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
