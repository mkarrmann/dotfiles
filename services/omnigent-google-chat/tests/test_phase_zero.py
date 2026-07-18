from __future__ import annotations

from pathlib import Path

import pytest

import omnigent_google_chat.phase_zero as phase_zero
from omnigent_google_chat.config import PhaseZeroSettings
from omnigent_google_chat.models import GoogleChatMessage, SentMessage


class FakeMetaClient:
    def __init__(self, **kwargs: object) -> None:
        self.calls: list[dict[str, object]] = []

    async def send_message(self, **kwargs: object) -> SentMessage:
        self.calls.append(kwargs)
        is_root = kwargs.get("thread_name") is None
        return SentMessage(
            name="messages/root" if is_root else f"messages/{len(self.calls)}",
            thread_name="threads/probe",
            actor_id="users/bot",
            actor_type="BOT",
        )

    async def list_all_messages(self, *, created_after: str) -> list[GoogleChatMessage]:
        return [
            GoogleChatMessage(
                name="messages/bot",
                space_name="spaces/s",
                thread_name="threads/probe",
                actor_id="users/bot",
                actor_type="BOT",
                create_time="2026-01-01T00:00:00Z",
                text="probe",
                has_attachments=False,
                raw={},
            ),
            GoogleChatMessage(
                name="messages/human",
                space_name="spaces/s",
                thread_name="threads/probe",
                actor_id="users/human",
                actor_type="HUMAN",
                create_time="2026-01-01T00:00:01Z",
                text="phone reply",
                has_attachments=False,
                raw={},
            ),
        ]


def settings() -> PhaseZeroSettings:
    return PhaseZeroSettings.model_validate(
        {
            "OMNIGENT_GCHAT_SPACE": "spaces/s",
            "OMNIGENT_GCHAT_MENTION_UNIXNAME": "owner",
            "META_CLI": "/fake/meta",
        }
    )


async def test_phase_zero_checks_notifications_identity_list_and_idempotency(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    instance = FakeMetaClient()
    monkeypatch.setattr(phase_zero, "MetaGoogleChatClient", lambda **kwargs: instance)
    monkeypatch.setattr(phase_zero, "_resolve_meta_executable", lambda path: Path(path))
    answers = iter(["yes", "", "no", "", "yes", ""])
    await phase_zero.run_phase_zero(settings(), input_function=lambda prompt: next(answers))
    output = capsys.readouterr().out
    assert "Phase 0 passed" in output
    assert "users/human" in output
    assert "users/bot" in output
    assert "Unmentioned thread push: no" in output
    assert instance.calls[0]["request_id"] == instance.calls[-1]["request_id"]
    assert instance.calls[2]["mention_unixname"] == "owner"


async def test_phase_zero_fails_when_real_mention_does_not_notify(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = FakeMetaClient()
    monkeypatch.setattr(phase_zero, "MetaGoogleChatClient", lambda **kwargs: instance)
    monkeypatch.setattr(phase_zero, "_resolve_meta_executable", lambda path: Path(path))
    answers = iter(["yes", "", "no", "", "no"])
    with pytest.raises(RuntimeError, match="did not notify"):
        await phase_zero.run_phase_zero(settings(), input_function=lambda prompt: next(answers))
