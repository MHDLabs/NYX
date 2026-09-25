"""Background sync helper: pull inbox + optional media reconciliation."""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Optional


class SyncEngine:
    def __init__(self, interval_sec: float = 5.0) -> None:
        self.interval = interval_sec
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    async def run(self, tick: Callable[[], Awaitable[Any]]) -> None:
        self._stop.clear()
        while not self._stop.is_set():
            try:
                await tick()
            except Exception:
                pass
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                continue

    def start(self, tick: Callable[[], Awaitable[Any]]) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self.run(tick))

    def stop(self) -> None:
        self._stop.set()
