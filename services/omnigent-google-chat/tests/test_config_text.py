from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from omnigent_google_chat.config import PhaseZeroSettings, Settings
from omnigent_google_chat.models import SessionSummary
from omnigent_google_chat.text import (
    format_root,
    item_text,
    split_message,
    stable_request_id,
)


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "OMNIGENT_GCHAT_SPACE": "spaces/abc_123",
        "OMNIGENT_GCHAT_ALLOWED_ACTOR_ID": "users/human",
        "OMNIGENT_GCHAT_META_BOT_ACTOR_ID": "users/bot",
        "OMNIGENT_GCHAT_MENTION_UNIXNAME": "mkarrmann",
        "OMNIGENT_GCHAT_HOST_ID": "host_1",
        "OMNIGENT_GCHAT_DATABASE": "/tmp/test-gchat.sqlite3",
    }
    values.update(overrides)
    return Settings.model_validate(values)


def test_settings_validate_exact_space_and_distinct_actors() -> None:
    assert settings().space_name == "spaces/abc_123"
    with pytest.raises(ValidationError, match="exact spaces"):
        settings(OMNIGENT_GCHAT_SPACE="My Space")
    with pytest.raises(ValidationError, match="must be distinct"):
        settings(OMNIGENT_GCHAT_META_BOT_ACTOR_ID="users/human")


def test_settings_require_phase_zero_for_daemon() -> None:
    with pytest.raises(ValueError, match="PHASE0_VALIDATED"):
        settings().validate_daemon_gate()
    validated = settings(OMNIGENT_GCHAT_PHASE0_VALIDATED=True)
    validated.validate_daemon_gate()


def test_phase_zero_settings_do_not_require_unknown_actor_ids() -> None:
    phase = PhaseZeroSettings.model_validate(
        {
            "OMNIGENT_GCHAT_SPACE": "spaces/abc",
            "OMNIGENT_GCHAT_MENTION_UNIXNAME": "@mkarrmann",
        }
    )
    assert phase.mention_unixname == "mkarrmann"


def test_database_path_expands() -> None:
    configured = settings(OMNIGENT_GCHAT_DATABASE="~/bridge.sqlite3")
    assert configured.database_path == Path.home() / "bridge.sqlite3"


def test_stable_request_ids_are_deterministic_and_source_specific() -> None:
    assert stable_request_id("item:s:i:0") == stable_request_id("item:s:i:0")
    assert stable_request_id("item:s:i:0") != stable_request_id("item:s:i:1")


def test_split_message_prefers_paragraphs_and_preserves_content() -> None:
    text = "first paragraph\n\nsecond paragraph with more text"
    chunks = split_message(text, 24)
    assert all(len(chunk) <= 24 for chunk in chunks)
    assert "".join(chunks) == text
    assert chunks[0].rstrip().endswith("paragraph")


def test_split_message_hard_cuts_unbroken_text() -> None:
    assert split_message("abcdefghij", 4) == ["abcd", "efgh", "ij"]
    with pytest.raises(ValueError, match="positive"):
        split_message("x", 0)


def test_item_text_only_extracts_supported_message_blocks() -> None:
    item: dict[str, object] = {
        "type": "message",
        "data": {
            "role": "assistant",
            "content": [
                {"type": "output_text", "text": "answer"},
                {"type": "reasoning", "text": "secret"},
                {"type": "tool_output", "text": "log"},
            ],
        },
    }
    assert item_text(item) == "answer"


def test_root_uses_short_workspace_and_no_sensitive_bundle_fields() -> None:
    root = format_root(
        SessionSummary(
            id="conv_1",
            title="Mobile session",
            status="running",
            workspace="/home/user/repos/project",
        )
    )
    assert "repos/project" in root
    assert "conv_1" in root
    assert "system prompt" not in root.lower()
