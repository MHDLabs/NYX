"""
Terminal REPL UI for NYX (MVP).

Whitepaper Section 07: Terminal-first, keyboard-driven.
Textual is preferred when available; this module provides a clean
stdin/stdout REPL that works with zero extra dependencies and shares
the same CommandRegistry so switching to Textual later is seamless.
"""

from __future__ import annotations

import sys
from typing import Optional, TextIO

from nyx_client.core.commands import CommandContext, CommandRegistry, registry
from nyx_client.config.logging import get_logger

log = get_logger(__name__)

BANNER = """
  +------------------------------------------+
  |          NYX Client  REPL                |
  |  Type /help for commands, /exit to quit  |
  +------------------------------------------+
"""


class ReplUI:
    """Simple line-oriented terminal interface."""

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
        """
        Process one input line.

        Returns False if the REPL should exit.
        """
        result = self.commands.dispatch(self.ctx, line)
        if result.message == "__EXIT__":
            self.print("Goodbye.")
            return False
        if result.message:
            prefix = "" if result.ok else "error: "
            self.print(prefix + result.message)
        return True

    def run(self) -> int:
        """Interactive loop. Returns process exit code."""
        self.print(BANNER)
        if self.ctx.identity_id:
            self.print("  Identity: " + self.ctx.identity_id)
        self.print("")
        while True:
            try:
                self.stdout.write("nyx> ")
                self.stdout.flush()
                line = self.stdin.readline()
                if line == "":
                    self.print("")
                    break
                if not self.run_line(line):
                    break
            except KeyboardInterrupt:
                self.print("\n(interrupted — type /exit to quit)")
            except EOFError:
                self.print("")
                break
        return 0
