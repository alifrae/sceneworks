"""In-process event bus.

V1 uses a single-process bus plus durable database events. The interface is
kept tiny so a broker-backed implementation could replace it later without
touching producers or consumers.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator


class EventBus:
    def __init__(self, max_queue: int = 1000):
        self._queues: set[asyncio.Queue] = set()
        self._max_queue = max_queue
        self._lock = asyncio.Lock()

    async def publish(self, event: dict) -> None:
        async with self._lock:
            for queue in list(self._queues):
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    # Slow consumer: drop the event rather than blocking agents.
                    pass

    async def register(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue)
        async with self._lock:
            self._queues.add(queue)
        return queue

    async def unregister(self, queue: asyncio.Queue) -> None:
        async with self._lock:
            self._queues.discard(queue)

    async def subscribe(self) -> AsyncIterator[dict]:
        """Convenience iterator; consumer must close it to unregister."""
        queue = await self.register()
        try:
            while True:
                yield await queue.get()
        finally:
            await self.unregister(queue)
