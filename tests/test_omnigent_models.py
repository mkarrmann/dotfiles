"""Isolated checks for bin/omnigent-models: a stub server, a temp config, no network.

Run with: python3 -m unittest discover -s tests -p test_omnigent_models.py
"""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin/omnigent-models"
HOST_ID = "fe372bd5268a451281838615ae43c26f"

CODEX_ROWS = [
    {
        "id": "gpt-6-astra",
        "isDefault": True,
        "defaultReasoningEffort": "medium",
        "supportedReasoningEfforts": [
            {"reasoningEffort": e} for e in ("low", "medium", "high", "xhigh", "max", "ultra")
        ],
    },
    {
        "id": "gpt-5.5",
        "isDefault": False,
        "defaultReasoningEffort": "medium",
        "supportedReasoningEfforts": [{"reasoningEffort": e} for e in ("low", "medium", "high")],
    },
]
CLAUDE_ROWS = [{"id": "opus"}, {"id": "opus[1m]", "isDefault": True}]
PI_LISTING = """provider   model          context  max-out  thinking  images
google     gemini-4-neon  1.0M     65.5K    yes       yes
meta       plain          128K     8192     no        no
"""


class StubHandler(BaseHTTPRequestHandler):
    requests: list[str] = []

    def do_GET(self):
        StubHandler.requests.append(self.path)
        prefix = f"/v1/hosts/{HOST_ID}/harnesses/"
        if self.path == prefix + "codex-native/model-options":
            body, status = {"models": CODEX_ROWS, "routable_models": ["gpt-6-astra"]}, 200
        elif self.path == prefix + "claude-native/model-options":
            body, status = {"models": CLAUDE_ROWS}, 200
        elif self.path in (prefix + "pi/model-options", prefix + "pi-native/model-options"):
            body, status = {"models": []}, 200
        elif self.path.startswith(prefix):
            harness = self.path[len(prefix):].split("/")[0]
            body, status = {"detail": f"model options are unsupported for harness '{harness}'"}, 502
        else:
            body, status = {"detail": "not found"}, 404
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_):
        pass


class OmnigentModelsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), StubHandler)
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        StubHandler.requests = []
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        (self.data / "config.yaml").write_text(
            f"host:\n  host_id: {HOST_ID}\n  name: box\nproviders:\n  x:\n    kind: subscription\n"
        )
        self.env = {
            "HOME": str(self.data),
            "PATH": "/usr/bin:/bin",
            "OMNIGENT_DATA_DIR": str(self.data),
            "OMNIGENT_URL": self.url,
            # Prove the proxy is bypassed: a proxy that cannot be reached.
            "http_proxy": "http://127.0.0.1:9",
            "HTTP_PROXY": "http://127.0.0.1:9",
            "DOTFILES_DIR": str(self.data / "no-dotfiles"),
        }

    def run_script(self, *args):
        return subprocess.run(
            [str(SCRIPT), *args], env=self.env, capture_output=True, text=True, timeout=30
        )

    def test_codex_is_the_default_and_maps_to_the_native_catalog(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(StubHandler.requests, [f"/v1/hosts/{HOST_ID}/harnesses/codex-native/model-options"])
        lines = result.stdout.splitlines()
        self.assertIn(f"codex-native models on host {HOST_ID} ({self.url}):", lines[0])
        self.assertRegex(lines[1], r"^  gpt-6-astra\s+default\s+efforts: low medium high xhigh max ultra \(default medium\)$")
        self.assertRegex(lines[2], r"^  gpt-5\.5\s+efforts: low medium high \(default medium\)$")
        self.assertIn("`model`", lines[-1])

    def test_sdk_harness_names_map_onto_probed_catalogs(self):
        for name, native in (("codex-sdk", "codex-native"), ("claude", "claude-native"), ("claude-sdk", "claude-native")):
            with self.subTest(name=name):
                StubHandler.requests = []
                result = self.run_script(name)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(StubHandler.requests, [f"/v1/hosts/{HOST_ID}/harnesses/{native}/model-options"])

    def test_rows_without_a_ladder_print_only_id_and_default(self):
        result = self.run_script("claude")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"(?m)^  opus\s*$")
        self.assertRegex(result.stdout, r"(?m)^  opus\[1m\]\s+default$")

    def test_json_prints_the_raw_rows(self):
        result = self.run_script("codex", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), CODEX_ROWS)

    def test_server_refusal_is_relayed_verbatim_with_exit_1(self):
        result = self.run_script("opencode-native")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("model options are unsupported for harness 'opencode-native'", result.stderr)

    def install_pi(self, listing, exit_code=0):
        bin_dir = self.data / "bin"
        bin_dir.mkdir(exist_ok=True)
        (self.data / "listing.txt").write_text(listing)
        pi = bin_dir / "pi"
        pi.write_text(
            "#!/bin/sh\n"
            '[ "$1" = --list-models ] || exit 9\n'
            f'cat "{self.data / "listing.txt"}"\n'
            f"exit {exit_code}\n"
        )
        pi.chmod(0o755)
        self.env["PATH"] = f"{bin_dir}:{self.env['PATH']}"

    def install_agent(self, name, executor):
        agent = self.data / "dotfiles/omnigent_config/agents" / name
        agent.mkdir(parents=True)
        (agent / "config.yaml").write_text(
            f"spec_version: 1\nname: {name}\nexecutor:\n{executor}"
            "os_env:\n  type: caller_process\n  cwd: .\n"
            "tools:\n  t:\n    model: not-the-executor\n"
        )
        self.env["DOTFILES_DIR"] = str(self.data / "dotfiles")

    def test_pi_falls_back_to_pi_list_models(self):
        self.install_pi(PI_LISTING)
        result = self.run_script("pi-native")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(StubHandler.requests, [f"/v1/hosts/{HOST_ID}/harnesses/pi-native/model-options"])
        self.assertIn("pi --list-models", result.stdout.splitlines()[0])
        self.assertRegex(result.stdout, r"(?m)^  google/gemini-4-neon\s+thinking$")
        self.assertRegex(result.stdout, r"(?m)^  meta/plain\s*$")
        self.assertIn("none minimal low medium high xhigh max ultra", result.stdout)

    def test_agent_name_resolves_harness_and_default_model(self):
        self.install_pi(PI_LISTING)
        self.install_agent("gem", "  type: omnigent\n  model: google/gemini-4-neon\n  config:\n    harness: pi\n")
        result = self.run_script("gem", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(StubHandler.requests, [f"/v1/hosts/{HOST_ID}/harnesses/pi/model-options"])
        defaults = [row["id"] for row in json.loads(result.stdout) if row.get("isDefault")]
        self.assertEqual(defaults, ["google/gemini-4-neon"])

    def test_agent_spec_never_overrides_the_catalogs_own_default(self):
        self.install_agent("cx", "  type: omnigent\n  model: gpt-5.5\n  config:\n    harness: codex\n")
        result = self.run_script("cx", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(StubHandler.requests, [f"/v1/hosts/{HOST_ID}/harnesses/codex-native/model-options"])
        defaults = [row["id"] for row in json.loads(result.stdout) if row.get("isDefault")]
        self.assertEqual(defaults, ["gpt-6-astra"])

    def test_pi_problems_exit_1(self):
        for listing, exit_code, message in (
            (None, 0, "pi is not on PATH"),
            ("boom", 3, "exited 3"),
            ("no table here\n", 0, "printed no models"),
        ):
            with self.subTest(message=message):
                self.setUp()
                if listing is not None:
                    self.install_pi(listing, exit_code)
                result = self.run_script("pi")
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)

    def test_missing_host_id_is_a_clear_error(self):
        (self.data / "config.yaml").write_text("providers: {}\n")
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        self.assertIn("no host_id", result.stderr)
        self.assertEqual(StubHandler.requests, [])

    def test_explicit_host_overrides_config(self):
        result = self.run_script("--host", "0000")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(StubHandler.requests, ["/v1/hosts/0000/harnesses/codex-native/model-options"])

    def test_skill_is_global_and_names_the_script(self):
        names = [
            line.strip()
            for line in (ROOT / "agent_config/skills-global.list").read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]
        self.assertIn("omnigent-models", names)
        skill = (ROOT / "agent_config/skills/omnigent-models/SKILL.md").read_text()
        self.assertIn("~/bin/omnigent-models", skill)
        self.assertIn("sys_session_create", skill)


if __name__ == "__main__":
    unittest.main()
