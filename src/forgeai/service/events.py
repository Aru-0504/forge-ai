"""Server-Sent Events (SSE) event bus supporting both in-process pub/sub and cross-process SQLite tailing."""

from __future__ import annotations

import asyncio
import threading
import time
from collections import defaultdict
from collections.abc import AsyncIterator

from forgeai.core.schemas import PipelineEvent
from forgeai.service.store import ForgeStore

_TERMINAL_EVENTS = frozenset({"run_completed", "run_failed"})
_TERMINAL_STATUSES = frozenset({"done", "failed", "budget_exhausted"})


def format_sse(event_id: int, event: PipelineEvent) -> str:
    """Serialize a PipelineEvent in W3C Server-Sent Events wire format."""
    return (
        f"id: {event_id}\n"
        f"event: {event.event_type}\n"
        f"data: {event.model_dump_json()}\n\n"
    )


class EventBus:
    """Persists events to SQLite and streams live + replayed SSE frames (works across processes)."""

    def __init__(self, store: ForgeStore) -> None:
        self.store = store
        self._lock = threading.Lock()
        self._subscribers: dict[
            str, list[tuple[asyncio.AbstractEventLoop, asyncio.Queue[tuple[int, PipelineEvent]]]]
        ] = defaultdict(list)

    def publish(self, event: PipelineEvent) -> int:
        """Persist event to SQLite and notify in-process async subscribers."""
        event_id = self.store.append_event(event)
        with self._lock:
            subs = list(self._subscribers.get(event.run_id, []))
        for loop, queue in subs:
            if not loop.is_closed():
                loop.call_soon_threadsafe(queue.put_nowait, (event_id, event))
        return event_id

    async def stream(
        self,
        run_id: str,
        *,
        last_event_id: int = 0,
        poll_interval_seconds: float = 0.5,
        heartbeat_seconds: float = 15.0,
    ) -> AsyncIterator[str]:
        """Yield historical + live SSE frames until the run reaches a terminal state."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[int, PipelineEvent]] = asyncio.Queue()

        with self._lock:
            self._subscribers[run_id].append((loop, queue))

        try:
            seen_id = last_event_id
            last_emit = time.monotonic()

            # 1. Replay stored events first so clients never miss earlier stages
            for evt_id, evt in self.store.list_events(run_id, after_id=seen_id):
                seen_id = max(seen_id, evt_id)
                last_emit = time.monotonic()
                yield format_sse(evt_id, evt)
                if evt.event_type in _TERMINAL_EVENTS:
                    return

            run = self.store.get_run(run_id)
            if run and run["status"] in _TERMINAL_STATUSES:
                return

            # 2. Stream live events (either from in-process queue or cross-process SQLite tail)
            while True:
                try:
                    evt_id, evt = await asyncio.wait_for(queue.get(), timeout=poll_interval_seconds)
                    if evt_id > seen_id:
                        seen_id = evt_id
                        last_emit = time.monotonic()
                        yield format_sse(evt_id, evt)
                        if evt.event_type in _TERMINAL_EVENTS:
                            return
                except TimeoutError:
                    # Poll SQLite for events published by an external worker process
                    new_events = self.store.list_events(run_id, after_id=seen_id)
                    for evt_id, evt in new_events:
                        seen_id = max(seen_id, evt_id)
                        last_emit = time.monotonic()
                        yield format_sse(evt_id, evt)
                        if evt.event_type in _TERMINAL_EVENTS:
                            return

                    run = self.store.get_run(run_id)
                    if run and run["status"] in _TERMINAL_STATUSES:
                        return

                    if time.monotonic() - last_emit >= heartbeat_seconds:
                        last_emit = time.monotonic()
                        yield ": keep-alive\n\n"
        finally:
            with self._lock:
                subs = self._subscribers.get(run_id, [])
                self._subscribers[run_id] = [item for item in subs if item[1] is not queue]
                if not self._subscribers[run_id]:
                    self._subscribers.pop(run_id, None)
