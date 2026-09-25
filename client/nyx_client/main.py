"""
NYX Client entry point.

Whitepaper sections 56 / 47: Python client architecture.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from nyx_client import __version__, __whitepaper_version__
from nyx_client.config import configure_logging, get_logger, load_settings
from nyx_client.core.app import NyxApp
from nyx_client.ui.repl import ReplUI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nyx", description="NYX Client")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--repl", action="store_true", help="interactive REPL")
    parser.add_argument("--tui", action="store_true", help="curses terminal UI")
    parser.add_argument(
        "--profile-key-file",
        type=Path,
        default=None,
        help="32-byte profile key file (dev)",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="override data directory",
    )
    args = parser.parse_args(argv)

    if args.version:
        print("nyx-client " + __version__ + " (whitepaper " + __whitepaper_version__ + ")")
        return 0

    configure_logging(level="INFO", json_logs=False)

    try:
        settings = load_settings()
    except (ValueError, OSError) as exc:
        print("error: configuration: " + str(exc), file=sys.stderr)
        return 1

    if args.data_dir is not None:
        # Rebuild storage section with override via a simple approach:
        from dataclasses import replace
        from nyx_client.config.settings import StorageSettings
        settings = replace(
            settings,
            storage=StorageSettings(
                data_dir=str(args.data_dir),
                db_filename=settings.storage.db_filename,
            ),
            data_dir=args.data_dir.resolve(),
        )

    profile_key = None
    if args.profile_key_file is not None:
        profile_key = args.profile_key_file.read_bytes()

    try:
        app = NyxApp.from_settings(settings=settings, profile_key=profile_key)
        identity = app.start()
    except Exception as exc:
        print("error: " + str(exc), file=sys.stderr)
        return 1

    if app.last_mnemonic:
        print()
        print("  *** NEW IDENTITY CREATED ***")
        print("  " + identity.id)
        print()
        print("  Recovery mnemonic (store offline, never share):")
        print("  " + app.last_mnemonic)
        print()

    if args.tui:
        try:
            from nyx_client.ui.pro_tui import ProTUI as PanelApp
        except ImportError:
            print("error: curses not available; pip install windows-curses", file=sys.stderr)
            print("or use --repl", file=sys.stderr)
            app.stop()
            return 1
        code = PanelApp(app).run()
        app.stop()
        return code

    if args.repl:
        ui = ReplUI(app.command_context())
        code = ui.run()
        app.stop()
        return code

    print()
    print("  +------------------------------------------+")
    print("  |          NYX Client  v" + __version__.ljust(18) + "|")
    print("  |  Whitepaper v" + __whitepaper_version__.ljust(27) + "|")
    print("  +------------------------------------------+")
    print()
    print("  Identity: " + identity.id)
    print("  Data    : " + str(app.settings.data_dir))
    print("  Server  : " + app.settings.network.default_server)
    print()
    print("  Run with --repl for interactive mode.")
    print()
    app.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
