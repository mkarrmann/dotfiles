"""`omnigent-opencode --version`, `serve`, as T3 Code runs an OpenCode binary, and `daemon`.

T3 Code reads the version off stdout, then runs `serve --hostname=H --port=P`
with `OPENCODE_PASSWORD` set and waits for `server listening on <url>` on
stdout. When this user's daemon answers on its socket, `serve` forwards to it
(`proxy`); otherwise it serves on its own, following sessions only while T3
Code has it running.

`daemon` serves the sessions on a Unix socket and follows each, T3 Code
running or not, until it has been quiet for `OMNIGENT_OPENCODE_WATCH_DAYS`
(default 7). It puts every Omnigent turn T3 Code did not start to T3 Code with
the `t3` CLI (`wakes`). launchd or systemd runs it. `daemon-stale` tells
bin/t3-ensure whether to restart it: exit 0 when the daemon's code is not what
is on disk and nothing would be lost, 2 when it is stale but busy (a turn
running, a steer pending, a turn held for T3 Code), 1 otherwise.

The environment configures the rest (T3 Code's provider instance for `serve`
alone, the service for the daemon):

- `OMNIGENT_URL`: the Omnigent server (default `http://127.0.0.1:6767`).
- `OMNIGENT_OPENCODE_AGENTS`: comma-separated Omnigent agents offered as
  providers (default `claude-native-ui,codex-native-ui`).
- `OMNIGENT_OPENCODE_HOST`: the execution host sessions run on (default `auto`,
  this machine's).
- `OMNIGENT_OPENCODE_STATE`: the SQLite state file (default under `$XDG_STATE_HOME`).
- `OMNIGENT_OPENCODE_SOCKET`: the daemon's socket (default under `$XDG_STATE_HOME`).
- `OMNIGENT_OPENCODE_T3`: the `t3` CLI the daemon runs (default `t3` on PATH, else `~/.local/bin/t3`).
- `OMNIGENT_OPENCODE_LOG`: a file to log to, rotated at `LOG_BYTES` (T3 Code
  discards the server's output); stderr then gets only what escapes logging.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import logging
import logging.handlers
import os
import shutil
import signal
import sys
from pathlib import Path

import aiohttp
from aiohttp import web

from .omnigent import OmnigentClient
from .proxy import daemon_up, default_socket, make_proxy, t3_home
from .server import STATUS_PATH, App, Config, make_app
from .store import Store, default_path
from .wakes import Waker
from .wire import VERSION

LOG = logging.getLogger(__name__)

DEFAULT_AGENTS = "claude-native-ui,codex-native-ui"
DEFAULT_WATCH_DAYS = 7.0
DAY_MS = 24 * 3600 * 1000
STALE, CURRENT, BUSY = 0, 1, 2
LOG_BYTES = 16 * 1024 * 1024
LOG_BACKUPS = 3


def parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="omnigent-opencode")
    parser.add_argument("--version", action="store_true")
    commands = parser.add_subparsers(dest="command")
    serve = commands.add_parser("serve")
    serve.add_argument("--hostname", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=0)
    daemon = commands.add_parser("daemon")
    stale = commands.add_parser("daemon-stale")
    stale.add_argument("--socket", type=Path, default=None, help="the daemon's Unix socket")
    for command in (serve, daemon):
        command.add_argument("--state", type=Path, default=None, help="SQLite state file")
        command.add_argument("--socket", type=Path, default=None, help="the daemon's Unix socket")
        command.add_argument("--log", type=Path, default=None, help="append logs here as well as to stderr")
    return parser.parse_known_args(argv)[0]


def socket_path(args: argparse.Namespace) -> Path:
    return args.socket or (
        Path(os.environ["OMNIGENT_OPENCODE_SOCKET"]) if os.environ.get("OMNIGENT_OPENCODE_SOCKET") else default_socket()
    )


def make_sessions(password: str | None, args: argparse.Namespace, watch_ms: int | None) -> tuple[Config, OmnigentClient, Store]:
    config = Config(
        password=password,
        host_spec=os.environ.get("OMNIGENT_OPENCODE_HOST", "auto"),
        agent_specs=[a.strip() for a in os.environ.get("OMNIGENT_OPENCODE_AGENTS", DEFAULT_AGENTS).split(",") if a.strip()],
        default_directory=str(Path.home()),
        watch_ms=watch_ms,
    )
    client = OmnigentClient(os.environ.get("OMNIGENT_URL", "http://127.0.0.1:6767"))
    state = args.state or (
        Path(os.environ["OMNIGENT_OPENCODE_STATE"]) if os.environ.get("OMNIGENT_OPENCODE_STATE") else default_path()
    )
    return config, client, Store(state)


async def until_stopped() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)
    await stop.wait()


async def serve(args: argparse.Namespace) -> None:
    password = os.environ.get("OPENCODE_PASSWORD", "")
    if not password:
        raise SystemExit("omnigent-opencode: OPENCODE_PASSWORD is not set")
    socket = socket_path(args)
    if await daemon_up(socket):
        home = t3_home()
        LOG.info("forwarding to the daemon on %s for the T3 Code server in %s", socket, home)
        application = make_proxy(password, socket, home)
    else:
        LOG.info("no daemon on %s; serving on my own", socket)
        application = make_app(App(*make_sessions(password, args, None)))
    runner = web.AppRunner(application, handle_signals=False)
    await runner.setup()
    site = web.TCPSite(runner, args.hostname, args.port)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    print(f"server listening on http://{args.hostname}:{port}", flush=True)
    await until_stopped()
    await runner.cleanup()


def code_hash() -> str:
    """The provider's code as it is on disk: its modules, project file and lock file."""
    package = Path(__file__).resolve().parent
    project = package.parent.parent
    digest = hashlib.sha256()
    for path in sorted([*package.glob("*.py"), project / "pyproject.toml", project / "uv.lock"]):
        if path.is_file():
            digest.update(path.name.encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


async def daemon_stale(args: argparse.Namespace) -> int:
    socket = socket_path(args)
    try:
        async with aiohttp.ClientSession(connector=aiohttp.UnixConnector(path=str(socket))) as client:
            async with client.get(f"http://daemon{STATUS_PATH}", timeout=aiohttp.ClientTimeout(total=5)) as response:
                status = await response.json() if response.status == 200 else {}
    except (aiohttp.ClientError, OSError, TimeoutError):
        print(f"no daemon answers on {socket}")
        return CURRENT
    current = code_hash()
    running = str(status.get("code") or "")
    if running == current:
        print(f"the daemon runs the code on disk ({current[:12]})")
        return CURRENT
    print(f"the daemon runs {running[:12] or 'unrecorded code'}; the code on disk is {current[:12]}")
    if status.get("busy"):
        print("it is busy; restart it once its turns are done")
        return BUSY
    return STALE


def t3_command() -> str:
    return os.environ.get("OMNIGENT_OPENCODE_T3") or shutil.which("t3") or str(Path.home() / ".local/bin/t3")


async def daemon(args: argparse.Namespace) -> None:
    code = code_hash()
    socket = socket_path(args)
    if await daemon_up(socket):
        raise SystemExit(f"omnigent-opencode: a daemon already answers on {socket}")
    days = float(os.environ.get("OMNIGENT_OPENCODE_WATCH_DAYS") or DEFAULT_WATCH_DAYS)
    config, client, store = make_sessions(None, args, int(days * DAY_MS))
    app = App(config, client, store)
    app.code = code
    app.wake = Waker(t3_command(), app.home_of)
    runner = web.AppRunner(make_app(app), handle_signals=False)
    await runner.setup()
    socket.parent.mkdir(parents=True, exist_ok=True)
    socket.unlink(missing_ok=True)
    old_umask = os.umask(0o177)
    try:
        await web.UnixSite(runner, str(socket)).start()
    finally:
        os.umask(old_umask)
    LOG.info(
        "daemon %s listening on %s, following sessions for %g days, waking threads with %s", VERSION, socket, days, t3_command()
    )
    follow = asyncio.create_task(app.follow())
    try:
        await until_stopped()
    finally:
        follow.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await follow
        await runner.cleanup()
        socket.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> None:
    args = parse(sys.argv[1:] if argv is None else argv)
    if args.version:
        print(VERSION)
        return
    if args.command == "daemon-stale":
        raise SystemExit(asyncio.run(daemon_stale(args)))
    if args.command not in ("serve", "daemon"):
        raise SystemExit("usage: omnigent-opencode --version | serve --hostname=H --port=P | daemon | daemon-stale")
    log = args.log or (Path(os.environ["OMNIGENT_OPENCODE_LOG"]) if os.environ.get("OMNIGENT_OPENCODE_LOG") else None)
    handlers: list[logging.Handler] = (
        [logging.StreamHandler(sys.stderr)]
        if log is None
        else [logging.handlers.RotatingFileHandler(log, maxBytes=LOG_BYTES, backupCount=LOG_BACKUPS)]
    )
    level = logging.DEBUG if os.environ.get("OMNIGENT_OPENCODE_DEBUG") else logging.INFO
    logging.basicConfig(level=level, handlers=handlers, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpcore", "aiohttp.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    asyncio.run(serve(args) if args.command == "serve" else daemon(args))
