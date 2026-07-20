from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

DOTFILES = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(DOTFILES))

from omnigent_config.policy_modules.capture_diff import (  # type: ignore[import-not-found]  # noqa: E402
    approve_diff_watch_subscription,
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
        "diff_watch__diff_watch_status",
        "diff_watch__diff_watch_unsubscribe",
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
