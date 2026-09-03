from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

DOTFILES = Path(__file__).resolve().parents[4]
OMNIGENT_PYTHON = Path.home() / ".local/share/uv/tools/omnigent/bin/python"


@pytest.mark.skipif(not OMNIGENT_PYTHON.exists(), reason="published Omnigent is not installed")
def test_published_omnigent_parses_the_mcp_agent_bundles(tmp_path: Path) -> None:
    script = """
import json
import sys
from pathlib import Path
from omnigent.spec.parser import parse

result = {}
for raw in sys.argv[1:]:
    spec = parse(Path(raw))
    result[Path(raw).name] = [
        {
            "name": server.name,
            "transport": server.transport,
            "command": server.command,
            "tools": server.tools,
        }
        for server in spec.mcp_servers
    ]
print(json.dumps(result))
"""
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
    paths = [
        DOTFILES / "omnigent_config/agents/claude",
        DOTFILES / "omnigent_config/agents/codex",
        DOTFILES / "omnigent_config/agents/dvsc",
        tmp_path / "polly",
        tmp_path / "debby",
    ]
    result = subprocess.run(
        [str(OMNIGENT_PYTHON), "-c", script, *(str(path) for path in paths)],
        check=True,
        text=True,
        capture_output=True,
    )
    parsed = json.loads(result.stdout)
    expected = [
        {
            "name": "diff_watch",
            "transport": "stdio",
            "command": "omnigent-diff-watch-mcp",
            "tools": [
                "diff_watch_subscribe",
                "diff_watch_unsubscribe",
                "diff_watch_status",
            ],
        }
    ]
    assert parsed == {
        "claude": expected,
        "codex": expected,
        "dvsc": expected,
        "polly": expected,
        "debby": expected,
    }


@pytest.mark.skipif(not OMNIGENT_PYTHON.exists(), reason="published Omnigent is not installed")
def test_native_codex_policy_hook_forwards_diff_watch_tools() -> None:
    script = """
import json
from omnigent.native_policy_hook import hook_payload_to_evaluation_request

events = {}
for hook_event in ("PreToolUse", "PostToolUse"):
    events[hook_event] = hook_payload_to_evaluation_request(
        hook_event,
        {
            "hook_event_name": hook_event,
            "tool_name": "mcp__diff_watch__diff_watch_subscribe",
            "tool_input": {},
            "tool_output": "Diff-watch preference requested for: ci_failure,review_comment",
        },
    )
print(json.dumps(events))
"""
    result = subprocess.run(
        [str(OMNIGENT_PYTHON), "-c", script],
        check=True,
        text=True,
        capture_output=True,
    )
    events = json.loads(result.stdout)
    assert events["PreToolUse"]["event"]["data"]["name"] == (
        "mcp__diff_watch__diff_watch_subscribe"
    )
    assert events["PostToolUse"]["event"]["request_data"]["name"] == (
        "mcp__diff_watch__diff_watch_subscribe"
    )
