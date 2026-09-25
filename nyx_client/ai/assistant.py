"""Local offline assistant helpers (no external LLM required)."""
from __future__ import annotations

import re
from typing import List


def summarize_text(text: str, max_sentences: int = 3) -> str:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    parts = [p for p in parts if p]
    if not parts:
        return ""
    return " ".join(parts[:max_sentences])


def extract_mentions(text: str) -> List[str]:
    return re.findall(r"@([a-zA-Z0-9_]{2,32})", text)


def draft_reply_template(peer_name: str, topic: str = "") -> str:
    if topic:
        return f"Hi {peer_name}, regarding {topic}: "
    return f"Hi {peer_name}, "
