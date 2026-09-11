"""Run with python3 -m unittest discover -s tests -p test_codex_config.py."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent_config"))
import codex_config as config


class CodexConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.dotfiles = self.home / "dotfiles"
        self.config_dir = self.dotfiles / "codex_config"
        self.config_dir.mkdir(parents=True)
        self.template = self.config_dir / "config.template.toml"
        self.template.write_text(
            'model = "shared"\nmodel_reasoning_effort = "high"\n'
            '[features]\nshared_flag = true\n'
        )
        (self.config_dir / 'config.work.toml').write_text(
            '[mcp_servers.watch]\ncommand = "watch"\nargs = ["--native"]\n'
        )
        self.mcps = self.dotfiles / "agent_config/plugins/custom-mcps/mcps"
        self.mcps.mkdir(parents=True)
        (self.mcps / "scuba.json").write_text(
            '{"mcpServers": {"scuba": {"command": "~/bin/scuba-mcp-launcher"},'
            '"quoted.server": {"command": "managed"}}}'
        )
        (self.mcps / 'diff-watch.json').write_text(
            '{"agents": ["claude"], "mcpServers": {"watch": {"command": "watch"}}}'
        )
        self.codex_home = self.home / ".codex"
        self.codex_home.mkdir()
        self.path = self.codex_home / "config.toml"
        self.local = self.codex_home / "config.local.toml"
        self.env = dict(os.environ, HOME=str(self.home), CODEX_HOME=str(self.codex_home),
                        DOTFILES_PROFILE="work",
                        AGENT_CONFIG_DIR=str(self.dotfiles / "agent_config"))

    def run_sync(self, generate=True, success=True):
        command = [sys.executable, str(ROOT / "agent_config/sync-mcps"), "codex"]
        if generate:
            command.append("--generate-config")
        result = subprocess.run(command, env=self.env, capture_output=True, text=True)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def test_local_root_nested_and_array_overrides_survive_mcp_sync(self):
        self.local.write_text(
            'model = "local"\n[features]\nshared_flag = false\nlocal_flag = true\n'
            '[mcp_servers.watch]\nargs = []\n'
            '[mcp_servers.scuba]\nenabled = false\n'
            '[mcp_servers."quoted.server"]\ncommand = "local-server"\n'
            f'[projects."{self.dotfiles}"]\ntrust_level = "untrusted"\n'
        )
        self.run_sync()
        before = self.path.read_bytes()
        self.run_sync(generate=False)
        self.assertEqual(self.path.read_bytes(), before)
        data = config.read_config(self.path)
        self.assertEqual(data["model"], "local")
        self.assertEqual(data["features"], {"shared_flag": False, "local_flag": True})
        self.assertEqual(data["projects"][str(self.dotfiles)]["trust_level"], "untrusted")
        self.assertEqual(data["mcp_servers"]["watch"]["args"], [])
        self.assertFalse(data["mcp_servers"]["scuba"]["enabled"])
        self.assertEqual(data["mcp_servers"]["quoted.server"]["command"], "local-server")

    def test_source_declared_server_drops_a_key_the_source_removed(self):
        """A recursive merge cannot express a removal.

        watch stopped taking ``--native-codex`` and CODEX_HOME, but both
        survived in the installed config and kept being passed to a server that
        had started rejecting them, so it exited before serving a single tool.
        The source table is authoritative for the servers it declares.
        """
        self.path.write_text(
            '[mcp_servers.watch]\ncommand = "watch"\n'
            'args = ["--native"]\nenv_vars = ["CODEX_HOME"]\n'
        )
        self.run_sync()
        data = config.read_config(self.path)
        self.assertEqual(
            data["mcp_servers"]["watch"],
            {"command": "watch", "args": ["--native"]},
        )

    def test_an_unmanaged_server_in_the_installed_config_is_left_alone(self):
        """Only servers the source declares are replaced; a hand-added one is
        state the generator has no opinion about and must not delete."""
        self.path.write_text('[mcp_servers.handwritten]\ncommand = "mine"\nargs = ["-x"]\n')
        self.run_sync()
        data = config.read_config(self.path)
        self.assertEqual(
            data["mcp_servers"]["handwritten"],
            {"command": "mine", "args": ["-x"]},
        )

    def test_legacy_migration_preserves_settings_and_original(self):
        legacy = self.config_dir / "config.toml"
        original = (
            'model = "personal"\napprovals_reviewer = "user"\n'
            'model_reasoning_effort = "high"\n'
            '[projects."/work"]\ntrust_level = "trusted"\n'
            '[tui.model_availability_nux]\n"model.name" = 2\n'
            '[mcp_servers.scuba]\ncommand = "bash"\nargs = ["obsolete"]\n'
            '[mcp_servers.custom]\ncommand = "custom"\n'
        )
        legacy.write_text(original)
        self.path.symlink_to(legacy)
        self.run_sync()
        self.assertFalse(self.path.is_symlink())
        self.assertEqual(legacy.read_text(), original)
        backups = list(self.codex_home.glob("config.toml.backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), original)
        local = config.read_config(self.local)
        self.assertEqual(local["model"], "personal")
        self.assertEqual(local["approvals_reviewer"], "user")
        self.assertNotIn("model_reasoning_effort", local)
        self.assertNotIn("scuba", local["mcp_servers"])
        self.assertNotIn("tui", local)
        data = config.read_config(self.path)
        self.assertEqual(data["tui"]["model_availability_nux"]["model.name"], 2)
        self.assertEqual(data["mcp_servers"]["scuba"],
                         {"command": str(self.home / "bin/scuba-mcp-launcher")})
        self.assertEqual(data["mcp_servers"]["custom"]["command"], "custom")
        first = self.path.stat()
        self.run_sync()
        self.run_sync(generate=False)
        self.assertEqual(self.path.stat().st_mtime_ns, first.st_mtime_ns)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(list(self.codex_home.glob("*.backup-*"))), 1)

    def test_existing_local_wins_during_migration_and_is_backed_up(self):
        legacy = self.config_dir / "config.toml"
        legacy.write_text('model = "legacy"\napprovals_reviewer = "user"\n')
        self.path.symlink_to(legacy)
        self.local.write_text('model = "explicit"\n')
        self.run_sync()
        self.assertEqual(config.read_config(self.path)["model"], "explicit")
        self.assertEqual(config.read_config(self.local)["approvals_reviewer"], "user")
        backup = next(self.codex_home.glob("config.local.toml.backup-*"))
        self.assertEqual(backup.read_text(), 'model = "explicit"\n')

    def test_invalid_toml_leaves_both_files_and_symlink_untouched(self):
        legacy = self.config_dir / "config.toml"
        legacy.write_text('model = "original"\n')
        self.path.symlink_to(legacy)
        self.local.write_text('model = "one"\nmodel = "duplicate"\n')
        original = self.path.read_bytes(), self.local.read_bytes()
        self.run_sync(success=False)
        self.assertTrue(self.path.is_symlink())
        self.assertEqual((self.path.read_bytes(), self.local.read_bytes()), original)
        self.assertEqual(list(self.codex_home.glob("*.backup-*")), [])

    def test_regeneration_keeps_runtime_state_and_updates_shared_defaults(self):
        self.run_sync()
        data = config.read_config(self.path)
        data["projects"]["/newly-trusted"] = {"trust_level": "trusted"}
        data["tui"] = {"model_availability_nux": {"example": 3}}
        data["plugins"] = {"plugin@example": {"enabled": True}}
        self.path.write_text(config.dumps(data))
        self.template.write_text(self.template.read_text().replace('"shared"', '"new-shared"'))
        self.run_sync()
        result = config.read_config(self.path)
        self.assertEqual(result["model"], "new-shared")
        for key in ("projects", "tui", "plugins"):
            self.assertEqual(result[key], data[key])

    def test_relocated_and_native_session_homes(self):
        relocated = self.home / "relocated"
        self.env["CODEX_HOME"] = str(relocated)
        self.run_sync()
        self.assertTrue((relocated / "config.toml").exists())
        self.assertFalse(self.path.exists())
        self.run_sync(generate=False)
        self.env["CODEX_HOME"] = str(self.home / ".omnigent/codex-native/session/codex-home")
        self.run_sync()
        self.run_sync(generate=False)
        self.assertTrue(self.path.exists())
        self.assertFalse(Path(self.env["CODEX_HOME"]).exists())

    def test_toml_values_roundtrip(self):
        data = config.tomllib.loads('''
title = "Snowman ☃ and \\"quotes\\""
multiline = """first
second"""
date = 2026-09-07
time = 14:30:00
timestamp = 2026-09-07T14:30:00Z
number = 1.25
empty = {}
[[hooks.events]]
name = "first"
args = ["a", "b"]
[[hooks.events]]
name = "second"
''')
        data["control\x7f"] = "\x7f"
        self.assertEqual(config.tomllib.loads(config.dumps(data)), data)

    def test_desktop_retracts_work_mcps_and_preserves_personal_config(self):
        self.local.write_text('model = "personal"\n[mcp_servers.personal]\ncommand = "personal"\n')
        self.run_sync()
        claude = self.home / '.claude.json'
        claude.write_text(json.dumps({'mcpServers': {'scuba': {'command': 'old'},
                                                   'watch': {'command': 'watch'},
                                                   'personal': {'command': 'personal'}},
                                     'other': 42}))
        stale = self.home / '.claude/settings.json'
        stale.parent.mkdir()
        stale.write_text(json.dumps({'mcpServers': {'scuba': {'command': 'ignored'}}}))
        metacode = self.home / '.config/opencode/opencode.json'
        metacode.parent.mkdir(parents=True)
        vendored = str(self.dotfiles / 'agent_config/skills/meta-powertools-vendored')
        metacode.write_text(json.dumps({'mcp': {'scuba': {}, 'personal': {'type': 'local'}},
                                       'skills': {'paths': [vendored, '/personal/skills']}}))
        self.env['DOTFILES_PROFILE'] = 'desktop'
        self.run_sync()
        result = subprocess.run([sys.executable, str(ROOT / 'agent_config/sync-mcps'), 'all'],
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        codex = config.read_config(self.path)
        self.assertEqual(codex['mcp_servers'], {'personal': {'command': 'personal'}})
        self.assertEqual(codex['model'], 'personal')
        self.assertEqual(json.loads(claude.read_text()),
                         {'mcpServers': {'personal': {'command': 'personal'}}, 'other': 42})
        self.assertNotIn('mcpServers', json.loads(stale.read_text()))
        meta = json.loads(metacode.read_text())
        self.assertEqual(meta['mcp'], {'personal': {'type': 'local'}})
        self.assertEqual(meta['skills']['paths'], ['/personal/skills'])
        self.env['DOTFILES_PROFILE'] = 'work'
        self.run_sync()
        servers = config.read_config(self.path)['mcp_servers']
        self.assertIn('watch', servers)
        self.assertIn('scuba', servers)
        self.assertIn('personal', servers)


if __name__ == "__main__":
    unittest.main()
