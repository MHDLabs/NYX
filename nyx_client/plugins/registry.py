"""Simple in-process plugin registry."""
from __future__ import annotations

from typing import Any, Callable, Dict, List

_hooks: Dict[str, List[Callable[..., Any]]] = {}


def register(hook: str, fn: Callable[..., Any]) -> None:
    _hooks.setdefault(hook, []).append(fn)


def emit(hook: str, **kwargs: Any) -> List[Any]:
    return [fn(**kwargs) for fn in _hooks.get(hook, [])]


def clear() -> None:
    _hooks.clear()
