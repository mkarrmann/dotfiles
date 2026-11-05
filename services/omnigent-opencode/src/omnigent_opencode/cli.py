"""`omnigent-opencode --version` and `omnigent-opencode serve`, as T3 Code runs an OpenCode binary.

T3 Code reads the version off stdout, then runs `serve --hostname=H --port=P`
with `OPENCODE_PASSWORD` set and waits for `server listening on <url>` on
stdout. Its provider instance's environment configures the rest:

- `OMNIGENT_URL`: the Omnigent server (default `http://127.0.0.1:6767`).
- `OMNIGENT_OPENCODE_AGENTS`: comma-separated Omnigent agents offered as
  providers (default `claude-native-ui,codex-native-ui`).
- `OMNIGENT_OPENCODE_HOST`: the execution host sessions run on (default `auto`,
  this machine's).
- `OMNIGENT_OPENCODE_STATE`: the SQLite state file (default under `$XDG_STATE_HOME`).
- `OMNIGENT_OPENCODE_LOG`: a file to append logs to; T3 Code discards the server's output.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from aiohttp import web

from .omnigent import OmnigentClient
from .server import App, Config, make_app
from .store import Store, default_path
from .wire import VERSION

DEFAULT_AGENTS = "claude-native-ui,codex-native-ui"


def parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="omnigent-opencode")
    parser.add_argument("--version", action="store_true")
    commands = parser.add_subparsers(dest="command")
    serve = commands.add_parser("serve")
    serve.add_argument("--hostname", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=0)
    serve.add_argument("--state", type=Path, default=None, help="SQLite state file")
    serve.add_argument("--log", type=Path, default=None, help="append logs here as well as to stderr")
    return parser.parse_known_args(argv)[0]


async def serve(args: argparse.Namespace) -> None:
    password = os.environ.get("OPENCODE_PASSWORD", "")
    if not password:
        raise SystemExit("omnigent-opencode: OPENCODE_PASSWORD is not set")
    config = Config(
        password=password,
        host_spec=os.environ.get("OMNIGENT_OPENCODE_HOST", "auto"),
        agent_specs=[a.strip() for a in os.environ.get("OMNIGENT_OPENCODE_AGENTS", DEFAULT_AGENTS).split(",") if a.strip()],
        default_directory=str(Path.home()),
    )
    client = OmnigentClient(os.environ.get("OMNIGENT_URL", "http://127.0.0.1:6767"))
    state = args.state or (
        Path(os.environ["OMNIGENT_OPENCODE_STATE"]) if os.environ.get("OMNIGENT_OPENCODE_STATE") else default_path()
    )
    app = App(config, client, Store(state))
    runner = web.AppRunner(make_app(app), handle_signals=False)
    await runner.setup()
    site = web.TCPSite(runner, args.hostname, args.port)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    print(f"server listening on http://{args.hostname}:{port}", flush=True)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)
    await stop.wait()
    await runner.cleanup()


def main(argv: list[str] | None = None) -> None:
    args = parse(sys.argv[1:] if argv is None else argv)
    if args.version:
        print(VERSION)
        return
    if args.command != "serve":
        raise SystemExit("usage: omnigent-opencode --version | serve --hostname=H --port=P")
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    log = args.log or (Path(os.environ["OMNIGENT_OPENCODE_LOG"]) if os.environ.get("OMNIGENT_OPENCODE_LOG") else None)
    if log is not None:
        handlers.append(logging.FileHandler(log))
    level = logging.DEBUG if os.environ.get("OMNIGENT_OPENCODE_DEBUG") else logging.INFO
    logging.basicConfig(level=level, handlers=handlers, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpcore", "aiohttp.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    asyncio.run(serve(args))
