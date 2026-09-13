#!/usr/bin/env python3
"""Stand-in for Chromium's remote-debugging endpoint, for the resolver e2e test.

Serves the two things bin-linux/nvim-show-resolver uses:

  GET /json                        the page list, re-read from SPEC on every
                                   request so a test can rewrite it
  ws://.../devtools/page/<id>      Runtime.evaluate for `document.title` reads
                                   and `document.title = "..."` writes

Each page in SPEC is {"id", "url", "nvim"}: "nvim" is the --listen address of
the Neovim that plays that window, and its 'titlestring' IS the page title --
reads come from it and writes go to it, so a title change reaches sway the same
way a real window's does (terminal OSC title -> ghostty -> sway).

Usage: nvim-show-fake-devtools.py DATA_DIR SPEC
Binds 127.0.0.1:0 and writes DATA_DIR/DevToolsActivePort like Chromium would.
"""

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
DATA_DIR, SPEC = sys.argv[1], sys.argv[2]


def pages():
    with open(SPEC) as handle:
        return json.load(handle)


def nvim_expr(address, expression):
    return subprocess.run(
        ["nvim", "--server", address, "--remote-expr", expression],
        capture_output=True, text=True, timeout=5, check=True,
    ).stdout


def read_title(address):
    return nvim_expr(address, "&titlestring")


def write_title(address, title):
    fd, path = tempfile.mkstemp(prefix="fake-devtools-title.")
    with os.fdopen(fd, "w") as handle:
        handle.write(title + "\n")
    nvim_expr(address, f"execute('let &titlestring = readfile(\"{path}\")[0] | redraw')")
    os.unlink(path)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path == "/json":
            body = json.dumps([
                {
                    "type": "page",
                    "id": page["id"],
                    "url": page["url"],
                    "title": read_title(page["nvim"]),
                    "webSocketDebuggerUrl": f"ws://127.0.0.1:{self.server.server_port}/devtools/page/{page['id']}",
                }
                for page in pages()
            ]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        match = re.fullmatch(r"/devtools/page/([A-Za-z0-9]+)", self.path)
        page = next((p for p in pages() if match and p["id"] == match.group(1)), None)
        if page is None or self.headers.get("Upgrade", "").lower() != "websocket":
            self.send_error(404)
            return
        accept = base64.b64encode(hashlib.sha1((self.headers["Sec-WebSocket-Key"] + GUID).encode()).digest()).decode()
        self.wfile.write(
            f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n\r\n".encode()
        )
        self.wfile.flush()
        self.close_connection = True
        self.serve_websocket(page)

    def read_exact(self, size):
        data = b""
        while len(data) < size:
            chunk = self.connection.recv(size - len(data))
            if not chunk:
                raise ConnectionError
            data += chunk
        return data

    def serve_websocket(self, page):
        while True:
            try:
                first, second = self.read_exact(2)
            except ConnectionError:
                return
            opcode, length = first & 0x0F, second & 0x7F
            if length == 126:
                length = int.from_bytes(self.read_exact(2), "big")
            elif length == 127:
                length = int.from_bytes(self.read_exact(8), "big")
            mask = self.read_exact(4) if second & 0x80 else b""
            payload = self.read_exact(length)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:
                return
            if opcode != 0x1:
                continue
            message = json.loads(payload)
            self.send_text(json.dumps({"id": message.get("id"), "result": self.evaluate(page, message)}))

    def evaluate(self, page, message):
        if message.get("method") != "Runtime.evaluate":
            return {}
        expression = message["params"]["expression"]
        assignment = re.fullmatch(r'document\.title = (".*"); document\.title', expression)
        if assignment:
            write_title(page["nvim"], json.loads(assignment.group(1)))
        elif expression != "document.title":
            return {"result": {"type": "undefined"}}
        return {"result": {"type": "string", "value": read_title(page["nvim"])}}

    def send_text(self, text):
        body = text.encode()
        header = bytes([0x81])
        if len(body) < 126:
            header += bytes([len(body)])
        elif len(body) < 65536:
            header += bytes([126]) + len(body).to_bytes(2, "big")
        else:
            header += bytes([127]) + len(body).to_bytes(8, "big")
        self.connection.sendall(header + body)


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
with open(os.path.join(DATA_DIR, "DevToolsActivePort"), "w") as handle:
    handle.write(f"{server.server_port}\n/devtools/browser/fake\n")
print(server.server_port, flush=True)
server.serve_forever()
