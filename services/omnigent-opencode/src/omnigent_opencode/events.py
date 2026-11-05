"""The `GET /api/event` feed: every session's events, broadcast to every subscriber.

OpenCode's feed is volatile by contract: events published while a client is
disconnected are missed, and a client rebuilds its state from REST
(`session.active`, `message.list`, `permission.list`, `form.list`). T3 Code
fails a stream that stays silent for 45 s, so idle subscribers get an SSE
comment every `KEEPALIVE_S`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Final

from aiohttp import web

from .wire import JsonObject

LOG = logging.getLogger(__name__)

KEEPALIVE_S: Final = 10.0


class EventFeed:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[bytes]] = set()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(self, frame: JsonObject) -> None:
        encoded = f"data: {json.dumps(frame, separators=(',', ':'))}\n\n".encode()
        data = frame.get("data")
        LOG.debug("event %s %s", frame.get("type"), data.get("sessionID") if isinstance(data, dict) else "")
        for queue in self._subscribers:
            queue.put_nowait(encoded)

    async def handle(self, request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(
            headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache", "Connection": "keep-alive"}
        )
        queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._subscribers.add(queue)
        try:
            await response.prepare(request)
            # Flush the headers now: T3 Code times out a subscription that sends none.
            await response.write(b": connected\n\n")
            while True:
                try:
                    chunk = await asyncio.wait_for(queue.get(), KEEPALIVE_S)
                except TimeoutError:
                    chunk = b": ping\n\n"
                await response.write(chunk)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            self._subscribers.discard(queue)
        return response
