from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path

import yaml

DOTFILES = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(DOTFILES))

from omnigent_config.policy_modules.capture_diff import (  # type: ignore[import-not-found]  # noqa: E402
    approve_diff_watch_subscription,
    capture_labels_policy,
    diff_watch_preference_policy,
)

SKILL = DOTFILES / "agent_config/skills/phabricator-diff-watch/SKILL.md"


def _skill_parts() -> tuple[dict[str, object], str]:
    text = SKILL.read_text(encoding="utf-8")
    match = re.fullmatch(r"---\n(.*?)\n---\n(.*)", text, re.DOTALL)
    assert match is not None
    metadata = yaml.safe_load(match.group(1))
    assert isinstance(metadata, dict)
    return metadata, match.group(2)


def _tool_event(name: str, arguments: object | None = None) -> dict[str, object]:
    return {
        "type": "tool_result",
        "target": f"diff_watch__{name}",
        "data": {"result": "intent recorded"},
        "request_data": {"name": f"diff_watch__{name}", "arguments": arguments or {}},
        "context": {
            "labels": {
                "omnigent.diff.number": "D90000001",
                "omnigent.diff.watch": "ci_failure,review_comment",
            }
        },
    }


def test_skill_is_bounded_and_references_the_real_tools() -> None:
    metadata, body = _skill_parts()
    assert metadata["name"] == "phabricator-diff-watch"
    assert isinstance(metadata["description"], str)
    assert len(metadata["description"]) <= 1024
    for tool in (
        "diff_watch__diff_watch_subscribe",
        "mcp__diff_watch__diff_watch_subscribe",
        "diff_watch_status",
        "diff_watch_unsubscribe",
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
    assert by_name["created_and_owned"]["expected_calls"] == ["diff_watch__diff_watch_subscribe"]
    assert by_name["read_only_review"]["expected_calls"] == []
    assert "diff_watch__diff_watch_subscribe" in by_name["watcher_wake"]["forbidden_calls"]
    assert by_name["handoff"]["expected_calls"] == ["diff_watch__diff_watch_unsubscribe"]


def test_personal_agent_specs_use_supported_stdio_mcp_tools() -> None:
    for name in ("claude", "codex"):
        raw = yaml.safe_load((DOTFILES / f"omnigent_config/agents/{name}/config.yaml").read_text())
        tools = raw["tools"]
        assert "plugins" not in tools
        assert tools["diff_watch"] == {
            "type": "mcp",
            "command": "omnigent-diff-watch-mcp",
            "tools": [
                "diff_watch_subscribe",
                "diff_watch_unsubscribe",
                "diff_watch_status",
            ],
        }


def test_native_codex_config_registers_the_diff_watch_mcp() -> None:
    config = tomllib.loads((DOTFILES / "codex_config/config.template.toml").read_text())
    assert config["mcp_servers"]["diff_watch"] == {
        "command": "omnigent-diff-watch-mcp",
        "args": ["--native-codex"],
        "env_vars": ["CODEX_HOME"],
    }


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
    Targeting it left the whole bundle -- diff_watch included -- silently
    inert on every Claude session, which is the failure this pins.
    """
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude.json").write_text(json.dumps({"projects": {"keep": 1}}))
    (tmp_path / ".claude" / "settings.json").write_text(
        json.dumps({"model": "keep-me", "mcpServers": {"scuba": {"command": "stale"}}})
    )

    _run_sync_mcps("claude", tmp_path)

    user_scope = json.loads((tmp_path / ".claude.json").read_text())
    assert "scuba" in user_scope["mcpServers"]
    assert user_scope["projects"] == {"keep": 1}, "unrelated state must survive"

    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert "mcpServers" not in settings, "the ignored key must be retracted"
    assert settings["model"] == "keep-me"


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


def test_server_config_uses_only_existing_policy_extension_surface() -> None:
    config = yaml.safe_load((DOTFILES / "omnigent_config/server.yaml").read_text())
    assert "server_plugins" not in config
    assert config["policies"]["approve_diff_watch_subscription"]["function"] == (
        "capture_diff.approve_diff_watch_subscription"
    )
    preference = config["policies"]["diff_watch_preferences"]
    assert preference["function"] == "capture_diff.diff_watch_preference_policy"
    assert preference["set_labels"] == ["omnigent.diff.watch"]


def test_subscription_policy_asks_for_namespaced_subscribe_only() -> None:
    subscribe = approve_diff_watch_subscription(
        {
            "type": "tool_call",
            "target": "diff_watch__diff_watch_subscribe",
            "data": {"name": "diff_watch__diff_watch_subscribe", "arguments": {}},
        }
    )
    assert subscribe is not None and subscribe["result"] == "ASK"
    assert (
        approve_diff_watch_subscription(
            {
                "type": "tool_call",
                "target": "diff_watch__diff_watch_status",
                "data": {"name": "diff_watch__diff_watch_status", "arguments": {}},
            }
        )
        is None
    )
    native_subscribe = approve_diff_watch_subscription(
        {
            "type": "tool_call",
            "target": "",
            "data": {
                "name": "mcp__diff_watch__diff_watch_subscribe",
                "arguments": {},
            },
        }
    )
    assert native_subscribe is not None and native_subscribe["result"] == "ASK"


def test_preference_policy_binds_subscribe_status_and_unsubscribe_to_labels() -> None:
    subscribe = diff_watch_preference_policy(
        _tool_event("diff_watch_subscribe", {"events": ["review_comment"]})
    )
    assert subscribe is not None
    assert subscribe["set_labels"] == {"omnigent.diff.watch": "review_comment"}
    assert "D90000001" in subscribe["data"]

    status = diff_watch_preference_policy(_tool_event("diff_watch_status"))
    assert status is not None
    assert "ci_failure,review_comment" in status["data"]

    unsubscribe = diff_watch_preference_policy(_tool_event("diff_watch_unsubscribe"))
    assert unsubscribe is not None
    assert unsubscribe["set_labels"] == {"omnigent.diff.watch": "off"}


def test_subscribe_without_a_captured_diff_does_not_write_preference() -> None:
    event = _tool_event("diff_watch_subscribe")
    event["context"] = {"labels": {}}
    result = diff_watch_preference_policy(event)
    assert result is not None
    assert "set_labels" not in result
    assert "no associated" in result["data"]


def test_service_and_mcp_runtime_are_source_control_wired() -> None:
    unit = (DOTFILES / "systemd/omnigent-diff-watcher.service").read_text()
    assert "omnigent-hub gate" in unit
    assert "delivery_mode" not in unit
    wrapper = DOTFILES / "bin/omnigent-diff-watch-mcp"
    assert wrapper.stat().st_mode & 0o111
    assert "services/omnigent-diff-watcher/.venv" in wrapper.read_text()


def test_agent_ensure_reconciles_existing_bundle_content() -> None:
    script = (DOTFILES / "bin/omnigent-agents-ensure").read_text()
    assert 'managed_dirs+=("$spec_dir")' in script
    assert 'for d in "${managed_dirs[@]}"' in script
    assert 'has_agent "$live_url"' not in script
    assert 'has_current_agent "$live_url"' in script
    assert 'server.get("name") == "diff_watch"' in script
    assert 'server.get("command") == "omnigent-diff-watch-mcp"' in script
    assert "quiesce-check --json" in script


def test_init_restarts_only_an_active_watcher_after_sync() -> None:
    script = (DOTFILES / "init.sh").read_text()
    assert "systemctl --user try-restart omnigent-diff-watcher.service" in script


def test_sync_leaves_hub_owned_watcher_to_reconciliation() -> None:
    script = (DOTFILES / "sync.sh").read_text()
    generic_enable_case = script.split('case "$unit_name" in', maxsplit=1)[1].split(
        "esac", maxsplit=1
    )[0]
    assert "omnigent-diff-watcher.service" in generic_enable_case


def test_preference_policy_reports_every_diff_in_a_stack() -> None:
    event = _tool_event("diff_watch_subscribe")
    event["context"] = {
        "labels": {
            "omnigent.diff.number": "D90000001,D90000002,D90000003",
            "omnigent.diff.watch": "ci_failure,review_comment",
        }
    }
    subscribe = diff_watch_preference_policy(event)
    assert subscribe is not None
    assert subscribe["set_labels"] == {"omnigent.diff.watch": "ci_failure,review_comment"}
    for diff_id in ("D90000001", "D90000002", "D90000003"):
        assert diff_id in subscribe["data"]


def test_preference_policy_ignores_malformed_entries_in_the_diff_label() -> None:
    event = _tool_event("diff_watch_status")
    event["context"] = {
        "labels": {
            "omnigent.diff.number": "D90000001,,notadiff,D0,D90000001,D90000002",
            "omnigent.diff.watch": "ci_failure",
        }
    }
    status = diff_watch_preference_policy(event)
    assert status is not None
    # Deduped, D0 and the non-diff token dropped, first-seen order preserved.
    assert "D90000001, D90000002" in status["data"]
    assert "notadiff" not in status["data"]


def _capture_event(text: str, labels: dict[str, str] | None = None) -> dict[str, object]:
    return {
        "type": "tool_result",
        "target": "Bash",
        "data": {"result": text},
        "context": {"labels": labels or {}},
    }


def _diff_capture() -> Callable[[dict[str, object]], dict[str, object] | None]:
    """Build the evaluator from the real server config, not a local copy."""
    config = yaml.safe_load((DOTFILES / "omnigent_config/server.yaml").read_text())
    arguments = config["policies"]["capture_diff"]["function"]["arguments"]
    evaluator: Callable[[dict[str, object]], dict[str, object] | None] = capture_labels_policy(
        **arguments
    )
    return evaluator


def _captured_diffs(result: dict[str, object] | None) -> str:
    """Extract the diff label a capture evaluator wrote."""
    assert result is not None
    labels = result["set_labels"]
    assert isinstance(labels, dict)
    value = labels["omnigent.diff.number"]
    assert isinstance(value, str)
    return value


def _revision(diff_id: str) -> str:
    return f"Differential Revision: https://phabricator.intern.facebook.com/{diff_id}"


def test_capture_accumulates_every_diff_a_single_submit_prints() -> None:
    evaluate = _diff_capture()
    result = evaluate(
        _capture_event("\n".join(_revision(d) for d in ("D115903821", "D115903820", "D115903819")))
    )
    assert _captured_diffs(result) == "D115903821,D115903820,D115903819"


def test_capture_accepts_current_jf_submit_result_lines() -> None:
    evaluate = _diff_capture()
    result = evaluate(
        _capture_event(
            "\n".join(
                (
                    "      - created: https://www.internalfb.com/diff/D116563979 "
                    "with draft version 416630625",
                    "      - updated: https://www.internalfb.com/diff/D116338876",
                    "      - skipped: https://www.internalfb.com/diff/D115903819",
                )
            )
        )
    )
    assert _captured_diffs(result) == "D116563979,D116338876,D115903819"


def test_capture_appends_to_an_existing_stack_without_duplicating() -> None:
    evaluate = _diff_capture()
    result = evaluate(
        _capture_event(
            "\n".join(_revision(d) for d in ("D115903821", "D116338876")),
            {"omnigent.diff.number": "D115903821,D115903820"},
        )
    )
    assert _captured_diffs(result) == "D115903821,D115903820,D116338876"


def test_capture_abstains_when_the_stack_is_already_complete() -> None:
    evaluate = _diff_capture()
    event = _capture_event(_revision("D115903821"), {"omnigent.diff.number": "D115903821"})
    assert evaluate(event) is None


def test_capture_ignores_bare_diff_numbers_in_documentation() -> None:
    """Regression: a bare ``D\\d+`` pattern bound sessions to doc placeholders.

    The capture policy inspects every tool's output, so example diff numbers in
    skill and CLI documentation latched onto the label permanently under the old
    first-match-wins rule.
    """
    evaluate = _diff_capture()
    assert evaluate(_capture_event('  "diff_num": "D12345678",')) is None
    assert evaluate(_capture_event("Differential Revision: D12345678")) is None
    assert evaluate(_capture_event("see Differential Revision: D12345 for an example")) is None


def test_capture_drops_oldest_entries_at_the_label_cap() -> None:
    evaluate = _diff_capture()
    existing = ",".join(f"D9000{index:04d}" for index in range(30))
    assert len(existing) > 256
    result = evaluate(_capture_event(_revision("D115903821"), {"omnigent.diff.number": existing}))
    value = _captured_diffs(result)
    assert len(value) <= 256
    assert value.endswith("D115903821")
    assert not value.startswith("D90000000")
