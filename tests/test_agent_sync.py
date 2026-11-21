"""Tests for cross-agent MCP sync (Pi, Muse, Metacode, Claude, Codex) and
shared Omnigent agent bundle overlays.

Run with: python3 -m unittest discover -s tests -p test_agent_sync.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent_config"))
sys.path.insert(0, str(ROOT / "omnigent_config"))

import mcp_registry  # noqa: E402


class McpRegistryTest(unittest.TestCase):
    def test_portable_command_strips_home_bin_prefixes(self):
        for raw, expected in (
            ("~/dotfiles/bin/omnigent-watch-mcp", "omnigent-watch-mcp"),
            ("~/bin/scuba-mcp-launcher", "scuba-mcp-launcher"),
            ("/usr/local/bin/claude_code/bin/fast_mux", "/usr/local/bin/claude_code/bin/fast_mux"),
            ("~/custom/dir/tool", "~/custom/dir/tool"),
        ):
            spec = mcp_registry.McpServerSpec(name="demo", raw_command=raw, command=raw)
            self.assertEqual(spec.portable_command, expected)

    def test_resolve_pi_agent_dir_maps_ephemeral_omnigent_dirs_to_canonical_home(self):
        home = Path("/fake/home")
        for ephemeral in (
            "/tmp/omnigent_pi_abc123",
            "/fake/home/.pi/omnigent_pi_xyz",
            "/fake/home/.omnigent/pi-native/sess1/agent",
        ):
            self.assertEqual(
                mcp_registry.resolve_pi_agent_dir(home, {"PI_CODING_AGENT_DIR": ephemeral}),
                home / ".pi" / "agent",
            )
        self.assertEqual(
            mcp_registry.resolve_pi_agent_dir(home, {"PI_CODING_AGENT_DIR": "/custom/pi-dir"}),
            Path("/custom/pi-dir"),
        )


class SyncMcpsMultiAgentTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.home = self.base / "home"
        self.home.mkdir()
        self.env = {
            "HOME": str(self.home),
            "PATH": os.environ["PATH"],
            "AGENT_CONFIG_DIR": str(ROOT / "agent_config"),
            "DOTFILES_PROFILE": "work",
            "LC_ALL": "C",
        }

    def run_sync(self, *args: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(ROOT / "agent_config/sync-mcps"), *args],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_pi_sync_writes_mcps_preserves_overrides_and_retracts_on_desktop(self):
        pi_dir = self.home / ".pi/agent"
        pi_dir.mkdir(parents=True)
        mcp_path = pi_dir / "mcp.json"
        mcp_path.write_text(json.dumps({
            "mcpServers": {
                "personal": {"command": "/usr/bin/personal"},
                "diff_watch": {"command": "/stale/retired"},
                "watch": {"command": "/old/watch", "exposure": "direct", "timeout": 30},
            }
        }))
        # Ephemeral Omnigent session dir must still update canonical ~/.pi/agent/mcp.json.
        self.env["PI_CODING_AGENT_DIR"] = "/tmp/omnigent_pi_session42"

        self.run_sync("pi")
        servers = json.loads(mcp_path.read_text())["mcpServers"]
        self.assertIn("personal", servers)
        self.assertNotIn("diff_watch", servers)
        self.assertIn("scuba", servers)
        self.assertIn("chronos", servers)
        self.assertEqual(
            servers["watch"]["command"],
            str(self.home / "dotfiles/bin/omnigent-watch-mcp"),
        )
        self.assertEqual(servers["watch"]["exposure"], "direct")
        self.assertEqual(servers["watch"]["timeout"], 30)
        self.assertTrue(servers["watch"].get("description"))

        # Desktop retracts work-only servers and retains watch + personal.
        self.env["DOTFILES_PROFILE"] = "desktop"
        self.run_sync("pi")
        desktop_servers = json.loads(mcp_path.read_text())["mcpServers"]
        self.assertEqual(set(desktop_servers), {"personal", "watch"})

    def test_muse_sync_merges_mcps_preserves_meta_core_and_retracts_on_desktop(self):
        muse_settings = self.home / ".config/muse/settings.json"
        muse_settings.parent.mkdir(parents=True)
        muse_settings.write_text(json.dumps({
            "schema_version": 1,
            "mcpServers": {
                "meta_core": {
                    "command": "/usr/local/bin/muse_code/bin/fast_mux",
                    "enabled": False,
                    "framing": "line_delimited_json",
                },
                "diff_watch": {"command": "/retired"},
                "scuba": {"command": "/old", "enabled": False},
            },
        }))

        self.run_sync("muse")
        data = json.loads(muse_settings.read_text())
        self.assertEqual(data["schema_version"], 1)
        servers = data["mcpServers"]
        self.assertEqual(servers["meta_core"]["enabled"], False)
        self.assertEqual(servers["meta_core"]["framing"], "line_delimited_json")
        self.assertNotIn("diff_watch", servers)
        self.assertEqual(servers["scuba"]["command"], str(self.home / "bin/scuba-mcp-launcher"))
        self.assertEqual(servers["scuba"]["enabled"], False)
        self.assertIn("watch", servers)
        self.assertIn("wiki", servers)

        self.env["DOTFILES_PROFILE"] = "desktop"
        self.run_sync("muse")
        desktop_servers = json.loads(muse_settings.read_text())["mcpServers"]
        self.assertEqual(set(desktop_servers), {"meta_core", "watch"})

    def test_muse_sync_skips_when_settings_absent(self):
        result = self.run_sync("muse")
        self.assertIn("skip muse", result.stdout)
        self.assertFalse((self.home / ".config/muse/settings.json").exists())

    def test_metacode_work_includes_both_custom_and_vendored_skills_paths(self):
        opencode = self.home / ".config/opencode/opencode.json"
        opencode.parent.mkdir(parents=True)
        opencode.write_text(json.dumps({"skills": {"paths": ["/personal/skills"]}}))

        self.run_sync("metacode")
        work_paths = json.loads(opencode.read_text())["skills"]["paths"]
        self.assertEqual(
            work_paths,
            [
                "/personal/skills",
                str(ROOT / "agent_config/skills"),
                str(ROOT / "agent_config/skills/meta-powertools-vendored"),
            ],
        )

        self.env["DOTFILES_PROFILE"] = "desktop"
        self.run_sync("metacode")
        desktop_paths = json.loads(opencode.read_text())["skills"]["paths"]
        self.assertEqual(desktop_paths, ["/personal/skills"])


def _omnigent_python() -> str | None:
    try:
        tools_dir = subprocess.check_output(["uv", "tool", "dir"], text=True).strip()
    except (OSError, subprocess.SubprocessError):
        tools_dir = str(Path.home() / ".local/share/uv/tools")
    candidate = Path(tools_dir) / "omnigent/bin/python3"
    return str(candidate) if candidate.is_file() else None


class MaterializeAgentOverlaysTest(unittest.TestCase):
    def setUp(self):
        self.omnigent_py = _omnigent_python()
        if not self.omnigent_py:
            self.skipTest("omnigent python (pyyaml) not installed")
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()

    def materialize(self, *args: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [self.omnigent_py, str(ROOT / "omnigent_config/materialize_agent_overlays.py"), *args],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def load_yaml(self, path: Path) -> dict:
        code = "import json, sys, yaml; print(json.dumps(yaml.safe_load(open(sys.argv[1]))))"
        out = subprocess.check_output([self.omnigent_py, "-c", code, str(path)], text=True)
        return json.loads(out)

    def test_tracked_work_bundles_get_shared_overlays_and_gemini_gets_work_mcps(self):
        out = self.base / "out-work"
        self.materialize(
            str(out),
            "--profile", "work",
            "--tracked-dir", str(ROOT / "omnigent_config/agents"),
            "--tracked", "claude", "codex", "dvsc", "muse", "gemini",
        )
        gemini = self.load_yaml(out / "gemini/config.yaml")
        self.assertEqual(gemini["os_env"]["sandbox"]["write_paths"], ["/"])
        self.assertIn("blast_radius", gemini["guardrails"]["policies"])
        self.assertEqual(gemini["tools"]["watch"]["command"], "omnigent-watch-mcp")
        self.assertEqual(gemini["tools"]["scuba"]["command"], "scuba-mcp-launcher")
        for expected_mcp in (
            "watch", "chronos", "google_chat", "get_phabricator_review_queue",
            "graphql_query", "scuba", "unidash", "wiki",
        ):
            self.assertIn(expected_mcp, gemini["tools"], expected_mcp)

        # Harnesses that load native MCP configs keep watch without duplicating work MCPs.
        for other in ("claude", "codex", "dvsc", "muse"):
            cfg = self.load_yaml(out / other / "config.yaml")
            self.assertEqual(cfg["tools"]["watch"]["command"], "omnigent-watch-mcp")
            self.assertNotIn("scuba", cfg["tools"])

        # Validate the materialized gemini bundle with Omnigent's real spec parser.
        parse_check = subprocess.run(
            [
                self.omnigent_py, "-c",
                "from pathlib import Path; import sys; "
                "from omnigent.spec.parser import parse; "
                "spec = parse(Path(sys.argv[1])); "
                "names = sorted(s.name for s in spec.mcp_servers); "
                "assert 'watch' in names and 'scuba' in names and 'wiki' in names, names; "
                "print(','.join(names))",
                str(out / "gemini"),
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(parse_check.returncode, 0, parse_check.stdout + parse_check.stderr)

    def test_minimal_new_agent_spec_inherits_watch_and_blast_radius(self):
        tracked_dir = self.base / "agents"
        spec_dir = tracked_dir / "new-agent"
        spec_dir.mkdir(parents=True)
        (spec_dir / "config.yaml").write_text(
            "spec_version: 1\n"
            "name: new-agent\n"
            "executor:\n"
            "  type: omnigent\n"
            "  config:\n"
            "    harness: pi\n"
            "os_env:\n"
            "  type: caller_process\n"
            "  cwd: .\n"
        )
        out = self.base / "out-desktop"
        self.materialize(
            str(out),
            "--profile", "desktop",
            "--tracked-dir", str(tracked_dir),
            "--tracked", "new-agent",
        )
        cfg = self.load_yaml(out / "new-agent/config.yaml")
        self.assertEqual(cfg["tools"]["watch"]["command"], "omnigent-watch-mcp")
        self.assertIn("blast_radius", cfg["guardrails"]["policies"])
        self.assertNotIn("scuba", cfg["tools"])


if __name__ == "__main__":
    unittest.main()
