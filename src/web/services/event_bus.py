"""In-memory async pub/sub for streaming pipeline progress to SSE clients.

Not durable, not multi-process. This is intentional:
  - A full page reload rejoins from the persisted DB state and picks
    up NEW events from here.
  - Multi-process durability (Redis pub/sub, etc.) is future work if
    the web UI ever runs behind multiple workers. For a local dev
    server the in-process bus is simpler and faster.

Contract:
  publish(run_id, event)     -- non-blocking; fan out to every current
                                subscriber of that run
  subscribe(run_id)          -- async iterator; each subscriber gets a
                                fresh queue and receives every event
                                published AFTER the subscription
  Terminating event          -- publishing {"type": "done", ...} tells
                                subscribers to stop iterating; downstream
                                cleanup happens in the ``finally`` block
                                so a broken client connection also frees
                                the queue.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import AsyncIterator, Dict, List


class EventBus:
    """Fan-out pub/sub keyed by run_id.

    Each subscriber holds its own asyncio.Queue, so a slow consumer only
    backs up ITS queue -- other subscribers keep flowing. Publish is
    fire-and-forget (put_nowait); if a consumer stops draining, the queue
    grows unboundedly. For a local dev server running one pipeline at a
    time this is fine; add a bounded queue + drop-on-overflow later if
    needed.
    """

    def __init__(self) -> None:
        self._queues: Dict[int, List[asyncio.Queue]] = defaultdict(list)
        self._lock = asyncio.Lock()

    def publish(self, run_id: int, event: dict) -> None:
        """Non-blocking fan-out. Missing subscribers are a no-op."""
        # Snapshot the subscriber list so a subscribe/unsubscribe
        # racing us doesn't mutate our iteration target.
        for q in list(self._queues.get(run_id, [])):
            q.put_nowait(event)

    async def subscribe(self, run_id: int) -> AsyncIterator[dict]:
        """Async iterator over events for run_id.

        Stops iterating when it sees an event with type == "done". The
        finally block always removes the queue from the registry so a
        client disconnect (which raises through the iterator) frees the
        resource just as cleanly.
        """
        q: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            self._queues[run_id].append(q)
        try:
            while True:
                event = await q.get()
                yield event
                if event.get("type") == "done":
                    return
        finally:
            async with self._lock:
                # Guard against double-removal if the queue was already
                # cleaned up (defensive; happens under some cancel paths).
                if q in self._queues.get(run_id, []):
                    self._queues[run_id].remove(q)
                if not self._queues[run_id]:
                    self._queues.pop(run_id, None)


# Module-level singleton. FastAPI routes and the subprocess runner
# both import this by name so they share fan-out state.
event_bus = EventBus()
