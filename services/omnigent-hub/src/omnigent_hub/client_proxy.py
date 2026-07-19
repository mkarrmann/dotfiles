"""Resilient loopback proxy for non-hub Linux devservers.

Every Omnigent consumer on a client box (the execution host daemon, its runners,
CodeCompanion, Orchest) dials a single stable endpoint: ``127.0.0.1:<port>``.
On the active hub that endpoint is the server itself; on every other Linux host
it is forwarded to the hub over SSH.

The naive design -- ``ssh -N -L 127.0.0.1:<port>:127.0.0.1:<port> hub`` owning
the loopback socket directly -- makes the endpoint's *existence* depend on a
single cross-datacenter TCP connection. When that SSH connection drops, the
listening socket vanishes with it: consumers get ``ECONNREFUSED`` and any
in-flight agent turn dies with ``runner_disconnected``. Worse, a half-open SSH
transport can keep the socket bound while forwarding is already dead, and
systemd's process-liveness supervision cannot see the difference.

This module decouples the two concerns:

* A persistent asyncio forwarder owns ``127.0.0.1:<port>`` for the process's
  whole life and relays to a private ``127.0.0.1:<backend>`` port. The socket
  never disappears while the service runs, so an SSH flap becomes a sub-second
  connect retry instead of a connection-refused gap.
* A supervisor owns the SSH child that provides ``<backend>``. It restarts the
  tunnel promptly on exit, and -- critically -- actively probes the backend's
  ``/health`` and kills a tunnel whose transport is alive but whose forwarding
  is dead, forcing a fresh connection instead of waiting out SSH keepalives.

Stdlib only, mirroring :mod:`omnigent_hub.mac_proxy`.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import signal
from collections.abc import Callable
from typing import Final

_BUFFER_SIZE: Final = 64 * 1024
_HEALTH_REQUEST: Final = b"GET /health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"

# Absorb a brief backend gap (an SSH restart) instead of dropping the client:
# retry the upstream connect for roughly this long before giving up.
_UPSTREAM_CONNECT_ATTEMPTS: Final = 20
_UPSTREAM_CONNECT_DELAY: Final = 0.1

# Tunnel supervision cadence.
_SSH_RESTART_DELAY: Final = 1.0
_HEALTH_INTERVAL: Final = 5.0
_HEALTH_GRACE: Final = 10.0
_HEALTH_FAILURE_THRESHOLD: Final = 3

Logger = Callable[[str], None]


def _log(message: str) -> None:
    print(f"[client-proxy] {message}", flush=True)


def build_ssh_command(
    *,
    hub: str,
    backend_port: int,
    server_port: int,
    ssh_bin: str,
) -> list[str]:
    """SSH argv that forwards a private local backend port to the hub server.

    Hardened relative to a plain forward: OS-level keepalives plus a tighter
    ``ServerAlive`` budget (~15s to notice a dead peer instead of ~45s), and
    ``ExitOnForwardFailure`` so a failed bind exits promptly for the supervisor
    to restart rather than lingering as a half-configured tunnel.
    """
    return [
        ssh_bin,
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ConnectionAttempts=3",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ServerAliveInterval=5",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "TCPKeepAlive=yes",
        "-N",
        "-L",
        f"127.0.0.1:{backend_port}:127.0.0.1:{server_port}",
        hub,
    ]


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


async def _health_ok(port: int, *, timeout: float = 3.0) -> bool:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port), timeout=timeout
        )
    except (TimeoutError, OSError):
        return False
    try:
        writer.write(_HEALTH_REQUEST)
        await writer.drain()
        response = await asyncio.wait_for(reader.readuntil(b"\r\n"), timeout=timeout)
        return response.startswith((b"HTTP/1.0 200", b"HTTP/1.1 200"))
    except (TimeoutError, OSError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        return False
    finally:
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()


class LoopbackForwarder:
    """Relays each client connection to the private backend port."""

    def __init__(
        self,
        backend_port: int,
        *,
        attempts: int = _UPSTREAM_CONNECT_ATTEMPTS,
        delay: float = _UPSTREAM_CONNECT_DELAY,
    ) -> None:
        self._backend_port = backend_port
        self._attempts = attempts
        self._delay = delay

    async def handle(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        upstream = await self._open_backend()
        if upstream is None:
            client_writer.close()
            with contextlib.suppress(OSError):
                await client_writer.wait_closed()
            return
        upstream_reader, upstream_writer = upstream
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

    async def _open_backend(
        self,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter] | None:
        for attempt in range(self._attempts):
            try:
                return await asyncio.open_connection("127.0.0.1", self._backend_port)
            except OSError:
                if attempt + 1 < self._attempts:
                    await asyncio.sleep(self._delay)
        return None


async def serve_forwarder(
    *,
    host: str,
    port: int,
    backend_port: int,
    log: Logger = _log,
) -> None:
    forwarder = LoopbackForwarder(backend_port)
    while True:
        try:
            server = await asyncio.start_server(forwarder.handle, host, port)
            break
        except OSError as exc:
            log(f"cannot bind {host}:{port}: {exc}; retrying in 5s")
            await asyncio.sleep(5)
    log(f"serving {host}:{port} -> backend 127.0.0.1:{backend_port}")
    async with server:
        await server.serve_forever()


async def _reap_when_unhealthy(
    process: asyncio.subprocess.Process,
    backend_port: int,
    *,
    interval: float,
    grace: float,
    threshold: int,
    log: Logger,
) -> None:
    """Terminate a tunnel whose transport is alive but whose forwarding is dead."""
    await asyncio.sleep(grace)
    failures = 0
    while process.returncode is None:
        if await _health_ok(backend_port):
            failures = 0
        else:
            failures += 1
            if failures >= threshold:
                log(
                    f"backend 127.0.0.1:{backend_port} unhealthy for "
                    f"{threshold} checks; recycling tunnel"
                )
                with contextlib.suppress(ProcessLookupError):
                    process.terminate()
                return
        await asyncio.sleep(interval)


async def supervise_ssh(
    *,
    hub: str,
    backend_port: int,
    server_port: int,
    ssh_bin: str,
    stop: asyncio.Event,
    restart_delay: float = _SSH_RESTART_DELAY,
    log: Logger = _log,
) -> None:
    command = build_ssh_command(
        hub=hub, backend_port=backend_port, server_port=server_port, ssh_bin=ssh_bin
    )
    while not stop.is_set():
        log(f"starting tunnel to {hub} (127.0.0.1:{backend_port} -> {hub}:{server_port})")
        process = await asyncio.create_subprocess_exec(*command)
        monitor = asyncio.create_task(
            _reap_when_unhealthy(
                process,
                backend_port,
                interval=_HEALTH_INTERVAL,
                grace=_HEALTH_GRACE,
                threshold=_HEALTH_FAILURE_THRESHOLD,
                log=log,
            )
        )
        try:
            returncode = await process.wait()
        finally:
            monitor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await monitor
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.terminate()
                with contextlib.suppress(Exception):
                    await process.wait()
        if stop.is_set():
            break
        log(f"tunnel exited (rc={returncode}); restarting in {restart_delay}s")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=restart_delay)


async def run(
    *,
    hub: str,
    server_port: int,
    backend_port: int,
    ssh_bin: str,
    log: Logger = _log,
) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signum, stop.set)

    tasks: list[asyncio.Task[None]] = [
        asyncio.create_task(
            serve_forwarder(host="127.0.0.1", port=server_port, backend_port=backend_port, log=log)
        ),
        asyncio.create_task(
            supervise_ssh(
                hub=hub,
                backend_port=backend_port,
                server_port=server_port,
                ssh_bin=ssh_bin,
                stop=stop,
                log=log,
            )
        ),
    ]
    done, pending = await asyncio.wait(
        [*tasks, asyncio.create_task(_wait_and_cancel(stop, tasks))],
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()
    for task in done | pending:
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def _wait_and_cancel(stop: asyncio.Event, tasks: list[asyncio.Task[None]]) -> None:
    await stop.wait()
    for task in tasks:
        task.cancel()


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hub", required=True, help="active hub FQDN to forward to")
    parser.add_argument(
        "--server-port",
        type=int,
        default=_int_env("OMNIGENT_PORT", 6767),
        help="stable loopback port every consumer dials",
    )
    parser.add_argument(
        "--backend-port",
        type=int,
        default=_int_env("OMNIGENT_CLIENT_BACKEND_PORT", 6768),
        help="private local port the SSH tunnel provides",
    )
    parser.add_argument(
        "--ssh-bin",
        default=os.environ.get("OMNIGENT_SSH_BIN", "/usr/bin/ssh"),
        help="ssh executable",
    )
    args = parser.parse_args(argv)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(
            run(
                hub=args.hub,
                server_port=args.server_port,
                backend_port=args.backend_port,
                ssh_bin=args.ssh_bin,
            )
        )


if __name__ == "__main__":
    main()
