from __future__ import annotations

import asyncio
from dataclasses import dataclass

from omnigent_hub.mac_proxy import Candidate, MacProxy


def test_proxy_prefers_healthy_primary() -> None:
    assert asyncio.run(_proxy_request(primary_healthy=True)) == b"CCO"


def test_proxy_falls_back_to_healthy_standby() -> None:
    assert asyncio.run(_proxy_request(primary_healthy=False)) == b"FTW"


def test_health_verdict_is_reused_within_the_ttl() -> None:
    assert asyncio.run(_sequential_probe_count(advance=0.0)) == 1


def test_health_verdict_refreshes_after_the_ttl() -> None:
    assert asyncio.run(_sequential_probe_count(advance=5.0)) == 2


def test_concurrent_opens_share_one_health_probe() -> None:
    # The regression this guards: without single-flight every stream open in a
    # burst probes, which is what made probes the larger half of the tunnel
    # connects once the desktop app moved to HTTP/2.
    assert asyncio.run(_concurrent_probe_count(clients=8)) == 1


def test_failed_connect_drops_the_cached_verdict() -> None:
    # A cached "healthy" that then refuses a real connection must not be
    # trusted for the rest of the window, or every caller inside it pays a
    # failed connect before falling through to the standby.
    assert asyncio.run(_stale_verdict_is_dropped()) is True


@dataclass
class _Stats:
    health: int = 0
    proxied: int = 0
    health_delay: float = 0.0


class _Clock:
    """Manual monotonic clock so TTL expiry is exercised without sleeping."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


async def _proxy_request(*, primary_healthy: bool) -> bytes:
    primary, primary_port, _ = await _upstream("CCO", healthy=primary_healthy)
    standby, standby_port, _ = await _upstream("FTW", healthy=True)
    proxy = MacProxy(
        (
            Candidate("CCO", "127.0.0.1", primary_port),
            Candidate("FTW", "127.0.0.1", standby_port),
        ),
        connect_timeout=1,
    )
    async with _serving(proxy) as proxy_port:
        try:
            return await _get(proxy_port)
        finally:
            await _close_all(primary, standby)


async def _sequential_probe_count(*, advance: float) -> int:
    upstream, port, stats = await _upstream("CCO", healthy=True)
    clock = _Clock()
    proxy = MacProxy(
        (Candidate("CCO", "127.0.0.1", port),),
        connect_timeout=1,
        health_ttl=1.0,
        clock=clock,
    )
    async with _serving(proxy) as proxy_port:
        try:
            await _get(proxy_port)
            clock.now += advance
            await _get(proxy_port)
            return stats.health
        finally:
            await _close_all(upstream)


async def _concurrent_probe_count(*, clients: int) -> int:
    upstream, port, stats = await _upstream("CCO", healthy=True)
    # Hold the probe open long enough that every client is genuinely waiting on
    # it, rather than the first finishing before the rest have started.
    stats.health_delay = 0.05
    proxy = MacProxy(
        (Candidate("CCO", "127.0.0.1", port),),
        connect_timeout=1,
        health_ttl=1.0,
        clock=_Clock(),
    )
    async with _serving(proxy) as proxy_port:
        try:
            await asyncio.gather(*(_get(proxy_port) for _ in range(clients)))
            return stats.health
        finally:
            await _close_all(upstream)


async def _stale_verdict_is_dropped() -> bool:
    upstream, port, _ = await _upstream("CCO", healthy=True)
    candidate = Candidate("CCO", "127.0.0.1", port)
    # A TTL far longer than the test so expiry cannot be what clears the entry.
    proxy = MacProxy((candidate,), connect_timeout=1, health_ttl=60.0, clock=_Clock())

    async with _serving(proxy) as proxy_port:
        await _get(proxy_port)
    assert proxy._cached_health(candidate) is True  # noqa: SLF001

    await _close_all(upstream)
    assert await proxy._open_upstream() is None  # noqa: SLF001
    return proxy._cached_health(candidate) is None  # noqa: SLF001


class _serving:
    """Run `proxy.handle` on an ephemeral port for the duration of the block."""

    def __init__(self, proxy: MacProxy) -> None:
        self._proxy = proxy
        self._server: asyncio.Server | None = None

    async def __aenter__(self) -> int:
        self._server = await asyncio.start_server(self._proxy.handle, "127.0.0.1", 0)
        return int(self._server.sockets[0].getsockname()[1])

    async def __aexit__(self, *_: object) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()


async def _get(port: int) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"GET /value HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
    await writer.drain()
    response = await asyncio.wait_for(reader.read(), timeout=2)
    writer.close()
    await writer.wait_closed()
    return response.split(b"\r\n\r\n", 1)[1]


async def _close_all(*servers: asyncio.Server) -> None:
    for server in servers:
        server.close()
    await asyncio.gather(*(server.wait_closed() for server in servers))


async def _upstream(value: str, *, healthy: bool) -> tuple[asyncio.Server, int, _Stats]:
    stats = _Stats()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request = await reader.readuntil(b"\r\n\r\n")
        is_health = request.startswith(b"GET /health ")
        if is_health:
            stats.health += 1
            if stats.health_delay:
                await asyncio.sleep(stats.health_delay)
        else:
            stats.proxied += 1
        status = b"200 OK" if (healthy or not is_health) else b"503 Service Unavailable"
        body = b"ok" if is_health else value.encode()
        writer.write(
            b"HTTP/1.1 "
            + status
            + b"\r\nContent-Length: "
            + str(len(body)).encode()
            + b"\r\nConnection: close\r\n\r\n"
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, int(server.sockets[0].getsockname()[1]), stats
