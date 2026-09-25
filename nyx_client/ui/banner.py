"""
NYX ASCII banners for the professional TUI.
"""

from __future__ import annotations

from typing import List

# Compact banner (fits ~72 cols)
BANNER_COMPACT = r"""
╔══════════════════════════════════════════════════════════════════════╗
║   ███╗   ██╗██╗   ██╗██╗  ██╗                                        ║
║   ████╗  ██║╚██╗ ██╔╝╚██╗██╔╝                                        ║
║   ██╔██╗ ██║ ╚████╔╝  ╚███╔╝                                         ║
║   ██║╚██╗██║  ╚██╔╝   ██╔██╗                                         ║
║   ██║ ╚████║   ██║   ██╔╝ ██╗                                        ║
║   ╚═╝  ╚═══╝   ╚═╝   ╚═╝  ╚═╝                                        ║
╠══════════════════════════════════════════════════════════════════════╣
║          Terminal-Native Secure Messaging  ·  Whitepaper v3.0        ║
╚══════════════════════════════════════════════════════════════════════╝
""".strip("\n")

BANNER_MINI = r"""
┌─────────────────────────────────────────┐
│  N Y X  ·  secure terminal messenger    │
└─────────────────────────────────────────┘
""".strip("\n")

SUBTITLE = "Born from the night — quiet, distributed, hard to censor"


def banner_lines(wide: bool = True) -> List[str]:
    text = BANNER_COMPACT if wide else BANNER_MINI
    return text.splitlines()
