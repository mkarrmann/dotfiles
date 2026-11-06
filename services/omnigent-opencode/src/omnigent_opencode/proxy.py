"""The server T3 Code runs, when this host's daemon is up: it forwards every request to the daemon.

T3 Code stops the OpenCode server it ran 30 s after it last released its
session, and that is when an Omnigent turn T3 Code did not start most needs
someone listening. So the sessions live in one daemon per user
(`omnigent-opencode daemon`, on a Unix socket only the user can open), and
the server T3 Code runs checks T3 Code's password, names the T3 Code server it
serves by its home (`T3CODE_HOME`, else `~/.t3`, as the `t3` CLI resolves it),
and passes requests and responses through, the event stream included.
"""

from __future__ import annotations

import base64
import binascii
import logging
import os
from pathlib import Path
from typing import Final

import aiohttp
from aiohttp import web

from .server import T3_HOME_HEADER, USERNAME, ApiError

LOG = logging.getLogger(__name__)

HOP_HEADERS: Final = frozenset(
    {"connection", "keep-alive", "transfer-encoding", "content-length", "authorization", "upgrade", "te", "trailer"}
)
PROBE_TIMEOUT_S: Final = 2.0


def default_socket() -> Path:
    state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
    return Path(state) / "omnigent-opencode" / "daemon.sock"


def t3_home() -> str:
    return str(Path(os.environ.get("T3CODE_HOME") or Path.home() / ".t3").expanduser().absolute())


async def daemon_up(socket: Path) -> bool:
    if not socket.exists():
        return False
    try:
        async with aiohttp.ClientSession(connector=aiohttp.UnixConnector(path=str(socket))) as client:
            async with client.get("http://daemon/api/info", timeout=aiohttp.ClientTimeout(total=PROBE_TIMEOUT_S)) as response:
                return response.status == 200
    except (aiohttp.ClientError, OSError, TimeoutError):
        return False


def _authorized(request: web.Request, password: str) -> bool:
    header = request.headers.get("Authorization", "")
    try:
        user, _, given = base64.b64decode(header.removeprefix("Basic "), validate=True).decode().partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return False
    return header.startswith("Basic ") and user == USERNAME and given == password


def make_proxy(password: str, socket: Path, home: str) -> web.Application:
    client_key: Final = web.AppKey("client", aiohttp.ClientSession)

    async def forward(request: web.Request) -> web.StreamResponse:
        if not _authorized(request, password):
            return ApiError("UnauthorizedError", "unauthorized").response()
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
        headers[T3_HOME_HEADER] = home
        body = await request.read() if request.can_read_body else None
        client = request.app[client_key]
        try:
            upstream = await client.request(
                request.method, f"http://daemon{request.rel_url}", headers=headers, data=body, allow_redirects=False
            )
        except (aiohttp.ClientError, OSError) as exc:
            LOG.warning("the daemon did not answer %s %s: %s", request.method, request.rel_url, exc)
            return web.json_response({"message": f"omnigent-opencode daemon unreachable: {exc}"}, status=502)
        async with upstream:
            response = web.StreamResponse(
                status=upstream.status,
                headers={k: v for k, v in upstream.headers.items() if k.lower() not in HOP_HEADERS},
            )
            await response.prepare(request)
            try:
                async for chunk in upstream.content.iter_any():
                    await response.write(chunk)
            except (aiohttp.ClientError, ConnectionResetError) as exc:
                LOG.info("forwarding %s %s ended: %s", request.method, request.rel_url, exc)
            return response

    async def open_client(app: web.Application) -> None:
        app[client_key] = aiohttp.ClientSession(
            connector=aiohttp.UnixConnector(path=str(socket)),
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=10),
            auto_decompress=False,
        )

    async def close_client(app: web.Application) -> None:
        await app[client_key].close()

    application = web.Application()
    application.router.add_route("*", "/{tail:.*}", forward)
    application.on_startup.append(open_client)
    application.on_cleanup.append(close_client)
    return application
