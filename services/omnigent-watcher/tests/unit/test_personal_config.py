from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

DOTFILES = Path(__file__).resolve().parents[4]
OMNIGENT_PYTHON = Path.home() / ".local/share/uv/tools/omnigent/bin/python"
sys.path.insert(0, str(DOTFILES))

SKILL = DOTFILES / "agent_config/skills/phabricator-diff-watch/SKILL.md"


def _skill_parts() -> tuple[dict[str, object], str]:
    text = SKILL.read_text(encoding="utf-8")
    match = re.fullmatch(r"---\n(.*?)\n---\n(.*)", text, re.DOTALL)
    assert match is not None
    metadata = yaml.safe_load(match.group(1))
    assert isinstance(metadata, dict)
    return metadata, match.group(2)


def test_skill_is_bounded_and_references_the_real_tools() -> None:
    metadata, body = _skill_parts()
    assert metadata["name"] == "phabricator-diff-watch"
    assert isinstance(metadata["description"], str)
    assert len(metadata["description"]) <= 1024
    for tool in (
        "watch__diff_subscribe",
        "mcp__watch__diff_subscribe",
        "diff_status",
        "diff_unsubscribe",
    ):
        assert tool in body
    assert "[[diff-comments]]" in body
    assert "[[ci-signals]]" in body
    assert "conv_" not in body
    assert "stale hint" in body


def test_skill_eval_cases_cover_positive_negative_wake_and_cleanup() -> None:
    payload = yaml.safe_load((SKILL.parent / "evals/cases.yaml").read_text())
    cases = payload["cases"]
    assert len(cases) == 6
    by_name = {case["name"]: case for case in cases}
    assert by_name["created_and_owned"]["expected_calls"] == ["watch__diff_subscribe"]
    assert by_name["read_only_review"]["expected_calls"] == []
    assert "watch__diff_subscribe" in by_name["watcher_wake"]["forbidden_calls"]
    assert by_name["handoff"]["expected_calls"] == ["watch__diff_unsubscribe"]


def test_personal_agent_specs_use_supported_stdio_mcp_tools() -> None:
    for name in ("claude", "codex", "dvsc"):
        raw = yaml.safe_load((DOTFILES / f"omnigent_config/agents/{name}/config.yaml").read_text())
        tools = raw["tools"]
        assert "plugins" not in tools
        # Both surfaces. Every tool takes the session to wake as an argument,
        # so nothing here depends on the harness being native.
        assert tools["watch"] == {
            "type": "mcp",
            "command": "omnigent-watch-mcp",
            "tools": [
                "diff_subscribe",
                "diff_unsubscribe",
                "diff_status",
                "subscribe",
                "unsubscribe",
                "status",
            ],
        }


def test_dvsc_uses_non_interactive_default_permissions() -> None:
    raw = yaml.safe_load((DOTFILES / "omnigent_config/agents/dvsc/config.yaml").read_text())
    assert raw["executor"]["config"]["permission_mode"] == "bypassPermissions"


def test_native_codex_config_registers_the_watch_mcp() -> None:
    """Codex gets the watcher from the work profile, not the shared template.

    ``config.work.toml`` is merged over the template only when the work profile
    is active (see ``agent_config/codex_config.py``), which is what keeps a
    Meta-internal MCP server off a personal machine. Asserting the template
    does *not* carry it is the half that keeps it that way.
    """
    template = tomllib.loads((DOTFILES / "codex_config/config.template.toml").read_text())
    assert "watch" not in template.get("mcp_servers", {})

    work = tomllib.loads((DOTFILES / "codex_config/config.work.toml").read_text())
    # No args and no env_vars: identity is a tool argument now, so the server
    # needs nothing from the harness.
    assert work["mcp_servers"]["watch"] == {"command": "omnigent-watch-mcp"}


def test_native_claude_mcp_definition_is_claude_scoped() -> None:
    """Claude gets watch; Codex must not get it from here as well.

    Codex is registered through codex_config/config.template.toml, and a
    second copy emitted into the same ~/.codex/config.toml would be a
    duplicate [mcp_servers.watch] table.
    """
    spec = json.loads((DOTFILES / "agent_config/plugins/custom-mcps/mcps/watch.json").read_text())
    assert spec["agents"] == ["claude"]
    assert "args" not in spec["mcpServers"]["watch"]
    # Absolute, because ~/dotfiles/bin is not on the PATH of an MCP server
    # spawned by Claude Code.
    assert spec["mcpServers"]["watch"]["command"].startswith("~/dotfiles/bin/")


def _run_sync_mcps(target: str, home: Path) -> str:
    result = subprocess.run(
        [sys.executable, str(DOTFILES / "agent_config/sync-mcps"), target],
        env={
            **os.environ,
            "HOME": str(home),
            "AGENT_CONFIG_DIR": str(DOTFILES / "agent_config"),
        },
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def test_sync_mcps_writes_where_claude_code_actually_reads(tmp_path: Path) -> None:
    """The registration must land in ~/.claude.json, not settings.json.

    settings.json accepts an mcpServers key and ignores it: nothing appears in
    `claude mcp list`, no tool is advertised, and no error is raised anywhere.
    Targeting it left the whole bundle -- watch included -- silently
    inert on every Claude session, which is the failure this pins.
    """
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude.json").write_text(json.dumps({"projects": {"keep": 1}}))
    (tmp_path / ".claude" / "settings.json").write_text(
        json.dumps({"model": "keep-me", "mcpServers": {"watch": {"command": "stale"}}})
    )

    _run_sync_mcps("claude", tmp_path)

    user_scope = json.loads((tmp_path / ".claude.json").read_text())
    assert "watch" in user_scope["mcpServers"]
    assert "args" not in user_scope["mcpServers"]["watch"]
    assert user_scope["projects"] == {"keep": 1}, "unrelated state must survive"

    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert "mcpServers" not in settings, "the ignored key must be retracted"
    assert settings["model"] == "keep-me"


def test_sync_mcps_keeps_watch_out_of_the_codex_config(tmp_path: Path) -> None:
    """Codex already registers watch via its template; a second copy
    would produce a duplicate [mcp_servers.watch] table."""
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "config.toml").write_text('model = "keep"\n')

    _run_sync_mcps("codex", tmp_path)

    config = tomllib.loads((tmp_path / ".codex" / "config.toml").read_text())
    assert "watch" not in config["mcp_servers"]
    assert config["mcp_servers"], "the unrestricted MCPs still sync to codex"


def test_sync_mcps_drops_unexpanded_env_refs(tmp_path: Path) -> None:
    """A literal ${VAR} value makes Claude Code report a missing env var."""
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude.json").write_text("{}")

    _run_sync_mcps("claude", tmp_path)

    servers = json.loads((tmp_path / ".claude.json").read_text())["mcpServers"]
    for name, spec in servers.items():
        for key, value in (spec.get("env") or {}).items():
            assert not value.startswith("${"), f"{name}.{key} is an unexpanded ref"


def test_sync_updates_canonical_codex_home_from_a_native_session() -> None:
    script = (DOTFILES / "sync.sh").read_text()
    assert '"$HOME"/.omnigent/codex-native/*/codex-home)' in script
    assert 'codex_home="$HOME/.codex"' in script


def test_service_and_mcp_runtime_are_source_control_wired() -> None:
    unit = (DOTFILES / "systemd/omnigent-watcher.service").read_text()
    assert "omnigent-hub gate" in unit
    assert "delivery_mode" not in unit
    wrapper = DOTFILES / "bin/omnigent-watch-mcp"
    assert wrapper.stat().st_mode & 0o111
    assert "services/omnigent-watcher/.venv" in wrapper.read_text()


def test_agent_ensure_reconciles_existing_bundle_content() -> None:
    script = (DOTFILES / "bin/omnigent-agents-ensure").read_text()
    assert "TRACKED_AGENT_NAMES=(claude codex dvsc)" in script
    assert "PACKAGED_AGENT_NAMES=(polly debby)" in script
    assert 'managed_dirs+=("$spec_dir")' in script
    assert 'for d in "${managed_dirs[@]}"' in script
    assert 'has_agent "$live_url"' not in script
    assert 'has_current_agent "$live_url"' in script
    assert 'server.get("name") == "watch"' in script
    assert 'server.get("command") == "omnigent-watch-mcp"' in script
    assert "quiesce-check --json" in script
    assert (
        '"$DOTFILES_DIR/bin/omnigent-dvsc-ensure" --config-only'
        in (DOTFILES / "init.sh").read_text()
    )


@pytest.mark.skipif(not OMNIGENT_PYTHON.exists(), reason="published Omnigent is not installed")
def test_packaged_agent_overlays_add_watch_without_losing_agent_tools(
    tmp_path: Path,
) -> None:
    subprocess.run(
        [
            str(OMNIGENT_PYTHON),
            str(DOTFILES / "omnigent_config/materialize_agent_overlays.py"),
            str(tmp_path),
            "polly",
            "debby",
        ],
        check=True,
    )

    for name in ("polly", "debby"):
        config = yaml.safe_load((tmp_path / name / "config.yaml").read_text())
        assert config["tools"]["agents"]
        assert config["tools"]["watch"] == {
            "type": "mcp",
            "command": "omnigent-watch-mcp",
            "tools": [
                "diff_subscribe",
                "diff_unsubscribe",
                "diff_status",
                "subscribe",
                "unsubscribe",
                "status",
            ],
        }


def test_init_restarts_only_an_active_watcher_after_sync() -> None:
    script = (DOTFILES / "init.sh").read_text()
    assert "systemctl --user try-restart omnigent-watcher.service" in script


def test_sync_leaves_hub_owned_watcher_to_reconciliation() -> None:
    script = (DOTFILES / "sync.sh").read_text()
    generic_enable_case = script.split('case "$unit_name" in', maxsplit=1)[1].split(
        "esac", maxsplit=1
    )[0]
    assert "omnigent-watcher.service" in generic_enable_case
