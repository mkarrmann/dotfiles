from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

_BUFFER_SIZE: Final = 64 * 1024
_HEALTH_REQUEST: Final = b"GET /health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"

# How long a candidate's health verdict is reused before it is probed again.
#
# Every client connection used to cost two upstream connections: one to probe
# /health, then the real one. That was invisible while the desktop app could
# hold at most 6 connections, but it now negotiates HTTP/2 and opens up to 30
# conversation streams, so the probe traffic became the larger half of ~50
# tunnel connects.
#
# The window is deliberately short. A stale verdict only matters when a
# candidate dies within it AND its tunnel still accepts TCP -- a real case,
# since an ET forward accepts locally whether or not the far end is up, which
# is why the probe exists at all. One second bounds that misroute while still
# collapsing a burst of stream opens onto a single probe.
_HEALTH_TTL: Final = 1.0


@dataclass(frozen=True, slots=True)
class Candidate:
    name: str
    host: str
    port: int


class MacProxy:
    def __init__(
        self,
        candidates: tuple[Candidate, ...],
        *,
        connect_timeout: float = 3.0,
        health_ttl: float = _HEALTH_TTL,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._candidates = candidates
        self._connect_timeout = connect_timeout
        self._health_ttl = health_ttl
        self._clock = clock
        self._last_candidate: str | None = None
        self._health_cache: dict[str, tuple[float, bool]] = {}
        self._health_locks: dict[str, asyncio.Lock] = {}

    async def handle(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        selected = await self._open_upstream()
        if selected is None:
            client_writer.close()
            with contextlib.suppress(OSError):
                await client_writer.wait_closed()
            return
        candidate, upstream_reader, upstream_writer = selected
        if candidate.name != self._last_candidate:
            print(
                f"Routing Omnigent through {candidate.name} on localhost:{candidate.port}",
                flush=True,
            )
            self._last_candidate = candidate.name
        try:
            await asyncio.gather(
                _relay(client_reader, upstream_writer),
                _relay(upstream_reader, client_writer),
            )
        finally:
            upstream_writer.close()
            client_writer.close()
            await asyncio.gather(
                upstream_writer.wait_closed(),
                client_writer.wait_closed(),
                return_exceptions=True,
            )

    async def _open_upstream(
        self,
    ) -> tuple[Candidate, asyncio.StreamReader, asyncio.StreamWriter] | None:
        for candidate in self._candidates:
            if not await self._healthy(candidate):
                continue
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(candidate.host, candidate.port),
                    timeout=self._connect_timeout,
                )
            except (TimeoutError, OSError):
                # The probe said healthy but the real connection did not land.
                # Drop the verdict so the next caller re-probes instead of
                # trusting it for the rest of the window.
                self._health_cache.pop(candidate.name, None)
                continue
            return candidate, reader, writer
        return None

    async def _healthy(self, candidate: Candidate) -> bool:
        cached = self._cached_health(candidate)
        if cached is not None:
            return cached

        # Single-flight per candidate: a burst of stream opens all miss the
        # cache at once, and without this each one probes.
        lock = self._health_locks.setdefault(candidate.name, asyncio.Lock())
        async with lock:
            cached = self._cached_health(candidate)
            if cached is not None:
                return cached
            healthy = await self._probe(candidate)
            self._health_cache[candidate.name] = (self._clock(), healthy)
            return healthy

    def _cached_health(self, candidate: Candidate) -> bool | None:
        entry = self._health_cache.get(candidate.name)
        if entry is None:
            return None
        recorded_at, healthy = entry
        if self._clock() - recorded_at >= self._health_ttl:
            return None
        return healthy

    async def _probe(self, candidate: Candidate) -> bool:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(candidate.host, candidate.port),
                timeout=self._connect_timeout,
            )
        except (TimeoutError, OSError):
            return False
        try:
            writer.write(_HEALTH_REQUEST)
            await writer.drain()
            response = await asyncio.wait_for(
                reader.readuntil(b"\r\n"),
                timeout=self._connect_timeout,
            )
            return response.startswith((b"HTTP/1.0 200", b"HTTP/1.1 200"))
        except (TimeoutError, OSError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            return False
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()


async def _relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(_BUFFER_SIZE):
            writer.write(data)
            await writer.drain()
        if writer.can_write_eof():
            writer.write_eof()
            await writer.drain()
    except (ConnectionError, OSError):
        pass


def candidates_from_env() -> tuple[Candidate, ...]:
    return (
        Candidate(
            "CCO",
            "127.0.0.1",
            int(os.environ.get("OMNIGENT_PRIMARY_TUNNEL_PORT", "16767")),
        ),
        Candidate(
            "FTW",
            "127.0.0.1",
            int(os.environ.get("OMNIGENT_STANDBY_TUNNEL_PORT", "26767")),
        ),
    )


async def serve(*, host: str, port: int, candidates: tuple[Candidate, ...]) -> None:
    proxy = MacProxy(candidates)
    while True:
        try:
            server = await asyncio.start_server(proxy.handle, host, port)
            break
        except OSError as exc:
            print(f"Cannot bind {host}:{port}: {exc}; retrying in 5s", flush=True)
            await asyncio.sleep(5)
    print(
        f"Serving Omnigent on http://{host}:{port} through existing CCO/FTW ET tunnels",
        flush=True,
    )
    async with server:
        await server.serve_forever()


def main() -> None:
    port = int(os.environ.get("OMNIGENT_PORT", "6767"))
    try:
        asyncio.run(serve(host="127.0.0.1", port=port, candidates=candidates_from_env()))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
