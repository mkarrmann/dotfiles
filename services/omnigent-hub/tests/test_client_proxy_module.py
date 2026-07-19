from __future__ import annotations

import asyncio

from omnigent_hub.client_proxy import LoopbackForwarder, _health_ok, build_ssh_command


def test_build_ssh_command_is_hardened() -> None:
    cmd = build_ssh_command(
        hub="hub.example.com",
        backend_port=6768,
        server_port=6767,
        ssh_bin="/usr/bin/ssh",
    )
    assert cmd[0] == "/usr/bin/ssh"
    assert cmd[-1] == "hub.example.com"
    assert "-N" in cmd
    assert "ExitOnForwardFailure=yes" in cmd
    assert "TCPKeepAlive=yes" in cmd
    assert "ServerAliveInterval=5" in cmd
    assert "ServerAliveCountMax=3" in cmd
    # The tunnel feeds the private backend, not the public loopback port.
    assert "127.0.0.1:6768:127.0.0.1:6767" in cmd
    assert "127.0.0.1:6767:127.0.0.1:6767" not in cmd


def test_forwarder_relays_client_to_backend() -> None:
    assert asyncio.run(_relay_roundtrip()) == b"UP:ping"


def test_forwarder_closes_client_when_backend_absent() -> None:
    assert asyncio.run(_forward_to_dead_backend()) == b""


def test_health_ok_distinguishes_200_from_503() -> None:
    healthy, unhealthy = asyncio.run(_probe_both())
    assert healthy is True
    assert unhealthy is False


async def _relay_roundtrip() -> bytes:
    upstream, backend_port = await _echo_backend()
    forwarder = LoopbackForwarder(backend_port, attempts=3, delay=0.05)
    front = await asyncio.start_server(forwarder.handle, "127.0.0.1", 0)
    front_port = int(front.sockets[0].getsockname()[1])
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", front_port)
        writer.write(b"ping")
        await writer.drain()
        writer.write_eof()
        response = await asyncio.wait_for(reader.read(), timeout=2)
        writer.close()
        await writer.wait_closed()
        return response
    finally:
        front.close()
        upstream.close()
        await asyncio.gather(front.wait_closed(), upstream.wait_closed())


async def _forward_to_dead_backend() -> bytes:
    dead_port = await _closed_port()
    forwarder = LoopbackForwarder(dead_port, attempts=2, delay=0.01)
    front = await asyncio.start_server(forwarder.handle, "127.0.0.1", 0)
    front_port = int(front.sockets[0].getsockname()[1])
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", front_port)
        response = await asyncio.wait_for(reader.read(), timeout=2)
        writer.close()
        await writer.wait_closed()
        return response
    finally:
        front.close()
        await front.wait_closed()


async def _probe_both() -> tuple[bool, bool]:
    healthy_server, healthy_port = await _health_backend(healthy=True)
    unhealthy_server, unhealthy_port = await _health_backend(healthy=False)
    try:
        return (await _health_ok(healthy_port), await _health_ok(unhealthy_port))
    finally:
        healthy_server.close()
        unhealthy_server.close()
        await asyncio.gather(healthy_server.wait_closed(), unhealthy_server.wait_closed())


async def _echo_backend() -> tuple[asyncio.Server, int]:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        data = await reader.read(1024)
        writer.write(b"UP:" + data)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, int(server.sockets[0].getsockname()[1])


async def _health_backend(*, healthy: bool) -> tuple[asyncio.Server, int]:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        status = b"200 OK" if healthy else b"503 Service Unavailable"
        writer.write(b"HTTP/1.1 " + status + b"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, int(server.sockets[0].getsockname()[1])


async def _closed_port() -> int:
    server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port = int(server.sockets[0].getsockname()[1])
    server.close()
    await server.wait_closed()
    return port
