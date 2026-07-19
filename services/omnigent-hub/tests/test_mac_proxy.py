from __future__ import annotations

import asyncio

from omnigent_hub.mac_proxy import Candidate, MacProxy


def test_proxy_prefers_healthy_primary() -> None:
    assert asyncio.run(_proxy_request(primary_healthy=True)) == b"CCO"


def test_proxy_falls_back_to_healthy_standby() -> None:
    assert asyncio.run(_proxy_request(primary_healthy=False)) == b"FTW"


async def _proxy_request(*, primary_healthy: bool) -> bytes:
    primary, primary_port = await _upstream("CCO", healthy=primary_healthy)
    standby, standby_port = await _upstream("FTW", healthy=True)
    proxy = MacProxy(
        (
            Candidate("CCO", "127.0.0.1", primary_port),
            Candidate("FTW", "127.0.0.1", standby_port),
        ),
        connect_timeout=1,
    )
    server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
    proxy_port = int(server.sockets[0].getsockname()[1])
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
        writer.write(b"GET /value HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        await writer.drain()
        response = await asyncio.wait_for(reader.read(), timeout=2)
        writer.close()
        await writer.wait_closed()
        return response.split(b"\r\n\r\n", 1)[1]
    finally:
        server.close()
        primary.close()
        standby.close()
        await asyncio.gather(
            server.wait_closed(),
            primary.wait_closed(),
            standby.wait_closed(),
        )


async def _upstream(value: str, *, healthy: bool) -> tuple[asyncio.Server, int]:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request = await reader.readuntil(b"\r\n\r\n")
        is_health = request.startswith(b"GET /health ")
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
    return server, int(server.sockets[0].getsockname()[1])
