from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib import request

PROXY = Path(__file__).parents[3] / "bin/omnigent-prodnet-proxy"


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def log_message(self, format: str, *args: object) -> None:
        del format, args


def unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_ipv4_client_proxy_relays_http() -> None:
    target = ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
    target_thread = Thread(target=target.serve_forever, daemon=True)
    target_thread.start()
    listen_port = unused_port()
    env = os.environ.copy()
    env.update(
        {
            "OMNIGENT_PRODNET_HOST": "127.0.0.1",
            "OMNIGENT_PORT": str(listen_port),
            "OMNIGENT_TARGET_HOST": "127.0.0.1",
            "OMNIGENT_TARGET_PORT": str(target.server_port),
        }
    )
    proxy = subprocess.Popen(
        [sys.executable, str(PROXY)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while True:
            try:
                with request.urlopen(
                    f"http://127.0.0.1:{listen_port}/health", timeout=0.5
                ) as response:
                    assert response.read() == b'{"status":"ok"}'
                    break
            except OSError as exc:
                if proxy.poll() is not None:
                    assert proxy.stderr is not None
                    raise AssertionError(proxy.stderr.read()) from exc
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
    finally:
        proxy.terminate()
        proxy.wait(timeout=5)
        target.shutdown()
        target.server_close()
        target_thread.join(timeout=5)
