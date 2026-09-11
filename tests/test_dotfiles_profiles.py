"""Isolated bootstrap checks: python3 -m unittest discover -s tests -p test_dotfiles_profiles.py."""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = Path("lua/codecompanion/adapters/omnigent/init.lua")
GITHUB_FORK = "https://github.com/mkarrmann/codecompanion.nvim.git"


class ProfileFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.home = self.base / "home"
        self.bin = self.base / "bin"
        self.dotfiles = self.base / "dotfiles"
        for directory in (self.home, self.bin, self.dotfiles / "bin"):
            directory.mkdir(parents=True)
        self.log = self.base / "calls.log"
        self.env = {
            "HOME": str(self.home),
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "DOTFILES_DIR": str(self.dotfiles),
            "TEST_CALLS": str(self.log),
            "TEST_PLATFORM": "Linux",
            "TEST_HOST": "desktop.example.net",
            "LC_ALL": "C",
        }
        self.stub(self.bin / "hostname", 'printf "%s\\n" "$TEST_HOST"')
        self.stub(self.bin / "uname", 'printf "%s\\n" "$TEST_PLATFORM"')
        shutil.copy2(ROOT / "bin/dotfiles-profile", self.dotfiles / "bin/dotfiles-profile")

    def stub(self, path, body):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/bash\nset -eu\n" + body + "\n")
        path.chmod(0o755)

    def run_script(self, path):
        return subprocess.run(
            ["/bin/bash", str(path)],
            env=self.env,
            cwd=self.dotfiles,
            capture_output=True,
            text=True,
            timeout=15,
        )

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []


class DotfilesProfileTest(ProfileFixture):
    def profile(self):
        result = self.run_script(self.dotfiles / "bin/dotfiles-profile")
        self.assert_success(result)
        return result.stdout.strip()

    def test_personal_linux_and_mac_default_to_desktop(self):
        for platform in ("Linux", "Darwin"):
            with self.subTest(platform=platform):
                self.env["TEST_PLATFORM"] = platform
                self.assertEqual(self.profile(), "desktop")

    def test_internal_fqdn_detects_work(self):
        self.env["TEST_HOST"] = "devvm123.facebook.com"
        self.assertEqual(self.profile(), "work")

    def test_similar_external_domain_is_not_work(self):
        self.env["TEST_HOST"] = "devvm.facebook.com.example.net"
        self.assertEqual(self.profile(), "desktop")

    def test_work_mac_detected_by_x2ssh(self):
        self.env["TEST_PLATFORM"] = "Darwin"
        self.stub(self.bin / "x2ssh", "exit 99")
        self.assertEqual(self.profile(), "work")

    def test_explicit_profile_overrides_detection(self):
        self.env["TEST_HOST"] = "devvm123.facebook.com"
        self.env["DOTFILES_PROFILE"] = "desktop"
        self.assertEqual(self.profile(), "desktop")
        self.env["TEST_HOST"] = "desktop.example.net"
        self.env["DOTFILES_PROFILE"] = "work"
        self.assertEqual(self.profile(), "work")

    def test_profile_file_and_environment_precedence(self):
        self.env["XDG_CONFIG_HOME"] = str(self.base / "custom-config")
        profile = Path(self.env["XDG_CONFIG_HOME"]) / "dotfiles/profile"
        profile.parent.mkdir(parents=True)
        profile.write_text("work\n")
        self.assertEqual(self.profile(), "work")
        self.env["DOTFILES_PROFILE"] = "desktop"
        self.assertEqual(self.profile(), "desktop")
        self.env["DOTFILES_PROFILE"] = "auto"
        self.assertEqual(self.profile(), "desktop")

    def test_profile_file_defaults_to_home_config(self):
        del self.env["XDG_CONFIG_HOME"]
        profile = self.home / ".config/dotfiles/profile"
        profile.parent.mkdir(parents=True)
        profile.write_text("work\n")
        self.assertEqual(self.profile(), "work")

    def test_invalid_override_and_file_fail_before_setup(self):
        for value in ("linux", "WORK", "work\ndesktop"):
            with self.subTest(value=value):
                self.env["DOTFILES_PROFILE"] = value
                result = self.run_script(self.dotfiles / "bin/dotfiles-profile")
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(result.stderr.strip())
        del self.env["DOTFILES_PROFILE"]
        profile = self.home / ".config/dotfiles/profile"
        profile.parent.mkdir(parents=True)
        profile.write_text("unknown\n")
        result = self.run_script(self.dotfiles / "bin/dotfiles-profile")
        self.assertNotEqual(result.returncode, 0)


class CodeCompanionProfileTest(ProfileFixture):
    def setUp(self):
        super().setUp()
        self.script = self.dotfiles / "bin/codecompanion-fork-ensure"
        shutil.copy2(ROOT / "bin/codecompanion-fork-ensure", self.script)
        self.repo = self.home / "repos/codecompanion.nvim"
        self.env["CODECOMPANION_NVIM_REPO"] = str(self.repo)
        self.stub(
            self.bin / "git",
            'printf "git %s\\n" "$*" >> "$TEST_CALLS"\n'
            'if [[ "$1" == clone ]]; then\n'
            '  destination="${!#}"\n'
            '  mkdir -p "$destination/.git" '
            '"$destination/lua/codecompanion/adapters/omnigent"\n'
            '  touch "$destination/lua/codecompanion/adapters/omnigent/init.lua"\n'
            "fi",
        )
        resolver = (
            'printf "route %s\\n" "$*" >> "$TEST_CALLS"\n'
            'case "$1" in\n'
            '  --primary) echo primary.facebook.com ;;\n'
            '  --standby) echo standby.facebook.com ;;\n'
            "  *) exit 1 ;;\n"
            "esac"
        )
        self.stub(self.bin / "omnigent-server-url", resolver)
        self.stub(self.dotfiles / "bin/omnigent-server-url", resolver)

    def test_desktop_bootstraps_from_github_without_hub_queries(self):
        self.env["DOTFILES_PROFILE"] = "desktop"
        self.assert_success(self.run_script(self.script))
        clones = [line for line in self.calls() if line.startswith("git clone ")]
        self.assertEqual(len(clones), 1, self.calls())
        self.assertIn(GITHUB_FORK, clones[0])
        self.assertFalse(any(line.startswith("route ") for line in self.calls()))
        self.assertTrue((self.repo / ADAPTER).is_file())

    def test_work_linux_keeps_internal_clone_route(self):
        self.env["DOTFILES_PROFILE"] = "work"
        self.assert_success(self.run_script(self.script))
        clones = [line for line in self.calls() if line.startswith("git clone ")]
        self.assertEqual(len(clones), 1, self.calls())
        self.assertIn(f"primary.facebook.com:{self.repo}", clones[0])
        self.assertTrue((self.repo / ADAPTER).is_file())

    def test_existing_fork_is_preserved_without_git_operations(self):
        (self.repo / ".git").mkdir(parents=True)
        adapter = self.repo / ADAPTER
        adapter.parent.mkdir(parents=True)
        adapter.write_text("local edits\n")
        self.env["DOTFILES_PROFILE"] = "desktop"
        self.assert_success(self.run_script(self.script))
        self.assertEqual(adapter.read_text(), "local edits\n")
        self.assertEqual(self.calls(), [])

    def test_invalid_existing_checkout_is_not_replaced(self):
        self.repo.mkdir(parents=True)
        sentinel = self.repo / "keep.txt"
        sentinel.write_text("precious\n")
        self.env["DOTFILES_PROFILE"] = "desktop"
        result = self.run_script(self.script)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sentinel.read_text(), "precious\n")
        self.assertEqual(self.calls(), [])


class InitProfileTest(ProfileFixture):
    def setUp(self):
        super().setUp()
        del self.env["DOTFILES_DIR"]
        self.script = self.dotfiles / "init.sh"
        shutil.copy2(ROOT / "init.sh", self.script)
        recorder = 'printf "%s %s profile=%s dotfiles=%s\\n" "${0##*/}" "$*" "${DOTFILES_PROFILE:-}" "${DOTFILES_DIR:-}" >> "$TEST_CALLS"'
        self.recorder = recorder
        helpers = (
            "sync.sh", "agent_config/bootstrap-plugins",
            "bin/codecompanion-fork-ensure", "bin/omnigent-version-ensure",
            "bin/omnigent-desktop-ensure",
            "bin/omnigent-desktop-app-ensure",
            "bin/omnigent-dvsc-ensure", "bin/omnigent-config-ensure",
            "bin/omnigent-codex-login-ensure", "bin/omnigent-agents-ensure",
            "bin/omnigent-google-chat-ensure", "bin/omnigent-retire-legacy-standby",
            "bin/omnigent-onboard-check", "bin/stylua-ensure", "bin/marksman-ensure",
            "bin/install-or-upgrade-nori", "bin-macos/omnigent-tls-ensure",
            "bin/gh-ensure", "bin/aws-agent-toolkit-ensure",
        )
        for helper in helpers:
            self.stub(self.dotfiles / helper, recorder)
        self.stub(self.dotfiles / "bin/nvs-sessions-file", "exit 1")
        self.stub(self.dotfiles / "bin/omnigent-server-url", "exit 1")
        self.stub(
            self.dotfiles / "bin/omnigent-hub",
            recorder + '\nif [[ "$1" == discover ]]; then exit "${TEST_DISCOVER_EXIT:-0}"; fi',
        )
        for project in ("omnigent-hub", "omnigent-watcher"):
            project_path = self.dotfiles / "services" / project
            project_path.mkdir(parents=True)
            (project_path / "uv.lock").touch()
            self.stub(project_path / ".venv/bin" / project, recorder)
        for tool in ("systemctl", "launchctl", "uv", "cargo", "bob", "nori", "sleep"):
            self.stub(self.bin / tool, recorder)
        for tool in ("curl", "git", "ssh", "x2ssh", "brew", "pip", "pip3"):
            self.stub(self.bin / tool, recorder + "\nexit 91")
        (self.home / ".oh-my-zsh").mkdir()
        (self.home / ".tmux/plugins/tpm").mkdir(parents=True)

    def test_desktop_keeps_common_setup_and_skips_work_bootstrap(self):
        for platform in ("Linux", "Darwin"):
            with self.subTest(platform=platform):
                self.env["TEST_PLATFORM"] = platform
                self.env["DOTFILES_PROFILE"] = "desktop"
                self.log.write_text("")
                result = self.run_script(self.script)
                self.assert_success(result)
                calls = self.calls()
                for helper in ("sync.sh", "codecompanion-fork-ensure", "omnigent-desktop-ensure", "stylua-ensure", "marksman-ensure", "gh-ensure", "aws-agent-toolkit-ensure"):
                    self.assertTrue(any(line.startswith(helper + " ") for line in calls), calls)
                forbidden = ("omnigent-", "bootstrap-plugins ", "systemctl ", "launchctl ", "uv ", "curl ", "git ")
                self.assertFalse(any(line.startswith(forbidden) for line in calls if not line.startswith(("omnigent-desktop-ensure ", "omnigent-desktop-app-ensure "))), calls)
                self.assertEqual(
                    any(line.startswith("omnigent-desktop-app-ensure ") for line in calls),
                    platform == "Linux",
                )
                self.assertTrue(all("profile=desktop" in line for line in calls), calls)
                self.assertTrue(all(line.endswith(f"dotfiles={self.dotfiles}") for line in calls), calls)

    def test_work_discovers_routing_before_dependents(self):
        self.env["DOTFILES_PROFILE"] = "work"
        self.assert_success(self.run_script(self.script))
        calls = self.calls()
        discovery = next(index for index, line in enumerate(calls) if line.startswith("omnigent-hub discover "))
        for helper in ("omnigent-dvsc-ensure", "omnigent-agents-ensure", "omnigent-google-chat-ensure", "omnigent-onboard-check"):
            index = next(index for index, line in enumerate(calls) if line.startswith(helper + " "))
            self.assertGreater(index, discovery, calls)
        self.assertTrue(any(line.startswith("bootstrap-plugins ") for line in calls), calls)
        self.assertTrue(any(line.startswith("systemctl ") for line in calls), calls)
        self.assertTrue(any(line.startswith("gh-ensure ") for line in calls), calls)
        self.assertFalse(any(line.startswith("aws-agent-toolkit-ensure ") for line in calls), calls)

    def test_failed_discovery_skips_routing_dependent_operations(self):
        self.env["DOTFILES_PROFILE"] = "work"
        self.env["TEST_DISCOVER_EXIT"] = "1"
        self.run_script(self.script)
        calls = self.calls()
        self.assertTrue(any(line.startswith("omnigent-hub discover ") for line in calls), calls)
        forbidden = ("omnigent-dvsc-ensure ", "omnigent-agents-ensure ", "omnigent-google-chat-ensure ", "omnigent-onboard-check ", "omnigent-hub reconcile-services ")
        self.assertFalse(any(line.startswith(forbidden) for line in calls), calls)

    def test_failed_discovery_still_provisions_declared_editor_sessions(self):
        self.env["DOTFILES_PROFILE"] = "work"
        self.env["TEST_DISCOVER_EXIT"] = "1"
        sessions = self.home / ".config/nvs/sessions"
        sessions.parent.mkdir(parents=True)
        sessions.write_text("# Local editor sessions\ncheckout1 /work/checkout1\n")
        self.stub(self.dotfiles / "bin/nvs-sessions-file", 'printf "%s\\n" "$HOME/.config/nvs/sessions"')
        self.stub(self.home / "bin/nvs-setup", self.recorder)
        self.assert_success(self.run_script(self.script))
        calls = self.calls()
        self.assertTrue(any(line.startswith("omnigent-hub discover ") for line in calls), calls)
        self.assertTrue(any(line.startswith("nvs-setup checkout1 /work/checkout1 ") for line in calls), calls)
        self.assertFalse(any(line.startswith("omnigent-onboard-check ") for line in calls), calls)

    def test_work_mac_keeps_client_setup_without_linux_discovery(self):
        self.env["DOTFILES_PROFILE"] = "work"
        self.env["TEST_PLATFORM"] = "Darwin"
        self.assert_success(self.run_script(self.script))
        calls = self.calls()
        for helper in (
            "bootstrap-plugins", "omnigent-version-ensure", "omnigent-dvsc-ensure",
            "omnigent-config-ensure", "omnigent-codex-login-ensure",
            "omnigent-agents-ensure", "omnigent-tls-ensure",
        ):
            self.assertTrue(any(line.startswith(helper + " ") for line in calls), calls)
        forbidden = ("systemctl ", "omnigent-hub discover ", "omnigent-hub cache-routing ", "omnigent-onboard-check ", "aws-agent-toolkit-ensure ")
        self.assertFalse(any(line.startswith(forbidden) for line in calls), calls)
        self.assertTrue(any(line.startswith("gh-ensure ") for line in calls), calls)
        self.assertTrue(all(line.endswith(f"dotfiles={self.dotfiles}") for line in calls), calls)


class SyncProfileTest(ProfileFixture):
    def setUp(self):
        super().setUp()
        if not shutil.which("jq", path=self.env["PATH"]):
            self.skipTest("sync.sh needs jq")
        self.script = self.dotfiles / "sync.sh"
        shutil.copy2(ROOT / "sync.sh", self.script)
        sources = (
            ".shell_env", ".shellrc", ".shell_aliases", ".shell_functions",
            ".bashrc", ".bash_profile", ".zshrc", ".zprofile", ".zshenv",
            ".screenrc", ".inputrc", ".tmux.conf", ".git-prompt.sh",
            "nvim_init.lua", "wofi_config", "sway_config", "ghostty_config",
            "claude_config/CLAUDE.md", "claude_config/meta-config.toml",
            "claude_config/statusline.sh", "claude_config/obsidian-vault.conf",
            "agent_config/global-development-preferences.md",
            "nori_config/config.toml", "hammerspoon.lua", "aerospace.toml",
            "sketchybar/sketchybarrc", "orchest_plugins.json",
            "systemd/omnigent-host.service", "systemd/omnigent-hub-reconcile.timer",
            "systemd/desktop/omnigent-host.service",
            "omnigent_config/omnigent-desktop-electron.desktop",
            "launchd/com.mkarrmann.omnigent-host.plist",
            "launchd/com.mkarrmann.omnigent-tls.plist",
            "launchd/com.mkarrmann.omnigent-tunnel.plist",
        )
        for source in sources:
            path = self.dotfiles / source
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        shutil.copy2(ROOT / "bin/omnigent-desktop-ensure", self.dotfiles / "bin/omnigent-desktop-ensure")
        recorder = 'printf "%s %s profile=%s\\n" "${0##*/}" "$*" "${DOTFILES_PROFILE:-}" >> "$TEST_CALLS"'
        for helper in (
            "agent_config/sync-mcps", "bin/omnigent-config-ensure",
            "bin/omnigent-codex-login-ensure", "bin/omnigent-codex-tmp-ensure",
        ):
            self.stub(self.dotfiles / helper, recorder)
        self.stub(self.dotfiles / "bin/omnigent-server-config-stale", recorder + "\nexit 1")
        for tool in ("systemctl", "launchctl"):
            self.stub(self.bin / tool, recorder)
        self.settings = self.home / ".claude/settings.json"
        self.settings.parent.mkdir()
        self.settings.write_text(json.dumps({
            "enabledPlugins": {"meta-lsp@claude-templates": True, "personal@example": True},
            "env": {"PERSONAL_SETTING": "keep"},
        }))

    def test_desktop_sync_skips_work_state_and_preserves_personal_settings(self):
        for platform in ("Linux", "Darwin"):
            with self.subTest(platform=platform):
                self.env["TEST_PLATFORM"] = platform
                self.env["DOTFILES_PROFILE"] = "desktop"
                self.log.write_text("")
                result = self.run_script(self.script)
                self.assert_success(result)
                calls = self.calls()
                self.assertFalse(any(line.startswith(("omnigent-", "systemctl ", "launchctl ")) for line in calls), calls)
                self.assertTrue(any(line.startswith("sync-mcps ") for line in calls), calls)
                self.assertTrue(all("profile=desktop" in line for line in calls), calls)
                self.assertTrue((self.home / ".zshrc").is_symlink())
                self.assertTrue((self.home / ".config/nvim/init.lua").is_symlink())
                if platform == "Linux":
                    self.assertEqual(
                        (self.home / ".config/systemd/user/omnigent-host.service").resolve(),
                        self.dotfiles / "systemd/desktop/omnigent-host.service",
                    )
                self.assertFalse((self.home / "Library/LaunchAgents").exists())
                self.assertFalse((self.home / ".config/environment.d/omnigent.conf").exists())
                self.assertFalse((self.home / ".hgrc").exists())
                settings = json.loads(self.settings.read_text())
                self.assertNotIn("meta-lsp@claude-templates", settings["enabledPlugins"])
                self.assertTrue(settings["enabledPlugins"]["personal@example"])
                self.assertEqual(settings["env"]["PERSONAL_SETTING"], "keep")
                self.assertEqual(settings["statusLine"]["command"], "~/.claude/statusline.sh")

    def test_work_sync_restores_host_unit_after_desktop_profile(self):
        self.env["DOTFILES_PROFILE"] = "desktop"
        self.assert_success(self.run_script(self.script))
        host_unit = self.home / ".config/systemd/user/omnigent-host.service"
        self.assertEqual(host_unit.resolve(), self.dotfiles / "systemd/desktop/omnigent-host.service")
        self.env["DOTFILES_PROFILE"] = "work"
        result = self.run_script(self.script)
        self.assert_success(result)
        self.assertEqual(host_unit.resolve(), self.dotfiles / "systemd/omnigent-host.service")
        self.assertNotIn("SHADOWED", result.stdout)

    def test_work_sync_keeps_internal_services_and_settings(self):
        self.env["DOTFILES_PROFILE"] = "work"
        self.assert_success(self.run_script(self.script))
        calls = self.calls()
        self.assertTrue(any(line.startswith("omnigent-config-ensure ") for line in calls), calls)
        self.assertTrue(any(line.startswith("systemctl --user enable --now omnigent-host.service ") for line in calls), calls)
        self.assertTrue((self.home / ".config/environment.d/omnigent.conf").is_file())
        settings = json.loads(self.settings.read_text())
        self.assertTrue(settings["enabledPlugins"]["meta-lsp@claude-templates"])
        self.assertTrue(settings["enabledPlugins"]["personal@example"])
        # The capture-diff hook repopulated tool_result payloads for the
        # capture_diff policy. That policy is gone and no tool_result policy
        # replaced it, so re-registering the hook would spawn a subprocess per
        # Bash call for no consumer.
        self.assertNotIn("omnigent-capture-diff", json.dumps(settings["hooks"]))

    def test_work_mac_sync_keeps_launchd_and_internal_helpers(self):
        self.env["DOTFILES_PROFILE"] = "work"
        self.env["TEST_PLATFORM"] = "Darwin"
        self.assert_success(self.run_script(self.script))
        calls = self.calls()
        for helper in ("omnigent-config-ensure", "omnigent-codex-login-ensure"):
            self.assertTrue(any(line.startswith(helper + " ") for line in calls), calls)
        for service in ("omnigent-host", "omnigent-tls", "omnigent-tunnel"):
            plist = self.home / "Library/LaunchAgents" / f"com.mkarrmann.{service}.plist"
            self.assertTrue(plist.is_file())
            self.assertTrue(any(line.startswith("launchctl bootstrap ") and str(plist) in line for line in calls), calls)
        self.assertFalse(any(line.startswith("systemctl ") for line in calls), calls)
        self.assertFalse((self.home / ".config/environment.d/omnigent.conf").exists())
        settings = json.loads(self.settings.read_text())
        self.assertTrue(settings["enabledPlugins"]["meta-lsp@claude-templates"])


if __name__ == "__main__":
    unittest.main()
