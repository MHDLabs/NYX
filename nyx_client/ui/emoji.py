"""
Common emoji sets for TUI compose helpers (Unicode — no external packs required).
"""

from __future__ import annotations

QUICK_EMOJI = [
    "😀", "😂", "🥰", "😎", "🤔", "😢", "😡", "👍", "👎", "❤️",
    "🔥", "✨", "🎉", "🚀", "🌙", "⚡", "🔒", "📎", "🖼️", "✅",
    "❌", "⚠️", "💬", "📢", "🛒", "💰",
]


def list_quick() -> list:
    return list(QUICK_EMOJI)


def strip_for_ascii_fallback(text: str) -> str:
    """Optional: terminals without emoji still show text."""
    return text
