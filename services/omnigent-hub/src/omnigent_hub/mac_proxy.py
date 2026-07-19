from __future__ import annotations

import asyncio
import contextlib
import os
from dataclasses import dataclass
from typing import Final

_BUFFER_SIZE: Final = 64 * 1024
_HEALTH_REQUEST: Final = b"GET /health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"


@dataclass(frozen=True, slots=True)
class Candidate:
    name: str
    host: str
    port: int


class MacProxy:
    def __init__(self, candidates: tuple[Candidate, ...], *, connect_timeout: float = 3.0) -> None:
        self._candidates = candidates
        self._connect_timeout = connect_timeout
        self._last_candidate: str | None = None

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
                continue
            return candidate, reader, writer
        return None

    async def _healthy(self, candidate: Candidate) -> bool:
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
