"""Server launcher contracts and HTTP smoke check using the installed packages."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "bin/omnigent-server-run"
TOOL_PYTHON = Path.home() / ".local/share/uv/tools/omnigent/bin/python"


class OmnigentServerRunTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.console = self.home / ".local/bin/omnigent"
        self.console.parent.mkdir(parents=True)
        self.env = {
            **os.environ,
            "HOME": str(self.home),
            "OMNIGENT_PY": sys.executable,
            "PYTHONPATH": str(self.home),
            "PYTHONDONTWRITEBYTECODE": "1",
            "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        }

    def launch(self, *args):
        return subprocess.run(
            [str(LAUNCHER), *args],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=20,
        )

    def test_preload_argv_process_and_exit_status(self):
        (self.home / "litellm.py").write_text("import os\nready = True\npid = os.getpid()\n")
        self.console.write_text(
            "import json, os, sys\n"
            "assert 'litellm' in sys.modules\n"
            "import litellm\n"
            "assert __name__ == '__main__'\n"
            "assert litellm.ready\n"
            "assert litellm.pid == os.getpid()\n"
            "print(json.dumps(sys.argv))\n"
            "raise SystemExit(17)\n"
        )
        args = ("--config", "/path with spaces/config.yaml", "--label", "", "literal$(`value`)")
        result = self.launch(*args)
        self.assertEqual(result.returncode, 17, result.stderr)
        self.assertEqual(json.loads(result.stdout), [str(self.console), "server", *args])

    def test_initialization_failure_prevents_console_entry(self):
        (self.home / "litellm.py").write_text("raise RuntimeError('preload failed')\n")
        self.console.write_text("print('console entered')\n")
        result = self.launch()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("RuntimeError: preload failed", result.stderr)

    @unittest.skipIf(
        importlib.util.find_spec("litellm"), "test interpreter has LiteLLM installed"
    )
    def test_absent_litellm_still_enters_console(self):
        self.console.write_text(
            "import sys\nassert 'litellm' not in sys.modules\nprint('console entered')\n"
        )
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "console entered\n")

    @unittest.skipUnless(TOOL_PYTHON.is_file(), "installed Omnigent interpreter unavailable")
    def test_concurrent_http_responses_with_installed_litellm(self):
        self.env["OMNIGENT_PY"] = str(TOOL_PYTHON)
        self.env.pop("PYTHONPATH")
        self.console.write_text('''
import asyncio
import socket
import sys

assert "litellm" in sys.modules, "LiteLLM must finish loading before console entry"

import httpx
import uvicorn

async def app(scope, receive, send):
    await asyncio.to_thread(__import__, "litellm")
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-length", b"2")]})
    await send({"type": "http.response.body", "body": b"ok"})

async def check():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.listen()
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", access_log=True))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=3) as client:
            responses = await asyncio.gather(*(
                client.get(f"http://127.0.0.1:{port}/health?api_key=test-launcher-secret-0123456789")
                for _ in range(25)
            ))
        assert all(response.status_code == 200 and response.text == "ok"
                   for response in responses)
        assert "litellm" in sys.modules
        print("25 concurrent HTTP responses passed")
    finally:
        server.should_exit = True
        await task
        sock.close()

asyncio.run(check())
''')
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("25 concurrent HTTP responses passed", result.stdout)
        self.assertIn("GET /health", result.stdout + result.stderr)
        self.assertNotIn("test-launcher-secret-0123456789", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
