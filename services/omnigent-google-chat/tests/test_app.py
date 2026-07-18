from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

import omnigent_google_chat.app as app
from omnigent_google_chat.config import Settings
from omnigent_google_chat.meta_chat import MetaChatError
from omnigent_google_chat.models import SentMessage, SessionSummary
from omnigent_google_chat.store import SQLiteStore


def settings(tmp_path: Path) -> Settings:
    meta = tmp_path / "meta"
    meta.write_text("#!/bin/sh\n")
    meta.chmod(0o700)
    return Settings.model_validate(
        {
            "OMNIGENT_GCHAT_SPACE": "spaces/s",
            "OMNIGENT_GCHAT_ALLOWED_ACTOR_ID": "users/human",
            "OMNIGENT_GCHAT_META_BOT_ACTOR_ID": "users/bot",
            "OMNIGENT_GCHAT_MENTION_UNIXNAME": "owner",
            "OMNIGENT_GCHAT_HOST_ID": "host_1",
            "OMNIGENT_GCHAT_DATABASE": str(tmp_path / "bridge.sqlite3"),
            "OMNIGENT_GCHAT_PHASE0_VALIDATED": True,
            "META_CLI": str(meta),
        }
    )


class FakeStore:
    instance: FakeStore | None = None

    def __init__(self, path: Path) -> None:
        self.path = path
        self.initialized = False
        self.bound_space: str | None = None
        self.closed = False
        FakeStore.instance = self

    async def initialize(self) -> None:
        self.initialized = True

    async def bind_space(self, space: str) -> None:
        self.bound_space = space

    async def list_restart_ambiguous(self) -> list[tuple[str, str]]:
        return []

    def close(self) -> None:
        self.closed = True


class FakeOmnigent:
    instance: FakeOmnigent | None = None

    def __init__(self, **kwargs: object) -> None:
        self.closed = False
        self.host_validated = False
        FakeOmnigent.instance = self

    async def validate_host(self) -> dict[str, object]:
        self.host_validated = True
        return {"host_id": "host_1"}

    async def list_sessions(self) -> list[SessionSummary]:
        return [SessionSummary(id="readonly", title="Read only", status="idle", permission_level=1)]

    async def aclose(self) -> None:
        self.closed = True


class FakeChat:
    member_ids = {"users/human"}

    def __init__(self, **kwargs: object) -> None:
        pass

    async def list_member_actor_ids(self) -> set[str]:
        return set(self.member_ids)


class FakeSender:
    def __init__(self, **kwargs: object) -> None:
        self.trigger: object | None = None
        self.calls: list[dict[str, object]] = []

    def set_poll_trigger(self, trigger: object) -> None:
        self.trigger = trigger

    async def send(self, **kwargs: object) -> list[SentMessage]:
        self.calls.append(kwargs)
        return [SentMessage(name="messages/notice", thread_name=str(kwargs["thread_name"]))]


class FakeInbound:
    def __init__(self, **kwargs: object) -> None:
        pass


class FakePoller:
    instance: FakePoller | None = None

    def __init__(self, **kwargs: object) -> None:
        self.polled = False
        FakePoller.instance = self

    def trigger(self) -> None:
        pass

    async def poll_once(self) -> int:
        self.polled = True
        return 0

    async def run(self, stop: object) -> None:
        stop.set()  # type: ignore[attr-defined]
        return None


class FakeReconciler:
    def __init__(self, **kwargs: object) -> None:
        pass

    def has_active_sessions(self) -> bool:
        return False

    async def run(self, stop: object) -> None:
        return None


def install_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app, "SQLiteStore", FakeStore)
    monkeypatch.setattr(app, "OmnigentClient", FakeOmnigent)
    monkeypatch.setattr(app, "MetaGoogleChatClient", FakeChat)
    monkeypatch.setattr(app, "GoogleChatSender", FakeSender)
    monkeypatch.setattr(app, "InboundProcessor", FakeInbound)
    monkeypatch.setattr(app, "GoogleChatPoller", FakePoller)
    monkeypatch.setattr(app, "SessionReconciler", FakeReconciler)
    monkeypatch.setattr(app, "_resolve_meta_executable", lambda value: Path(value))
    monkeypatch.setattr(app, "_install_signal_handlers", lambda stop: None)


async def test_app_wires_components_validates_member_and_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fakes(monkeypatch)
    FakeChat.member_ids = {"users/human"}
    await app.run(settings(tmp_path))
    assert FakeStore.instance is not None
    assert FakeStore.instance.initialized
    assert FakeStore.instance.bound_space == "spaces/s"
    assert FakeStore.instance.closed
    assert FakeOmnigent.instance is not None
    assert FakeOmnigent.instance.host_validated
    assert FakeOmnigent.instance.closed
    assert FakePoller.instance is not None and FakePoller.instance.polled


async def test_app_rejects_nonmember_and_still_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fakes(monkeypatch)
    FakeChat.member_ids = {"users/other"}
    with pytest.raises(ValueError, match="not a human member"):
        await app.run(settings(tmp_path))
    assert FakeStore.instance is not None and FakeStore.instance.closed
    assert FakeOmnigent.instance is not None and FakeOmnigent.instance.closed


async def test_app_fails_when_component_exits_before_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fakes(monkeypatch)
    FakeChat.member_ids = {"users/human"}

    class EarlyExitPoller(FakePoller):
        async def run(self, stop: object) -> None:
            return None

    monkeypatch.setattr(app, "GoogleChatPoller", EarlyExitPoller)
    with pytest.raises(RuntimeError, match="exited unexpectedly"):
        await app.run(settings(tmp_path))
    assert FakeStore.instance is not None and FakeStore.instance.closed


def test_resolve_meta_executable_and_missing_path(tmp_path: Path) -> None:
    executable = tmp_path / "meta"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o700)
    assert app._resolve_meta_executable(str(executable)) == executable.resolve()
    with pytest.raises(ValueError, match="not executable"):
        app._resolve_meta_executable(str(tmp_path / "missing"))


async def test_member_validation_retries_transient_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    class FlakyChat:
        async def list_member_actor_ids(self) -> set[str]:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise MetaChatError("temporary")
            return {"users/human"}

    async def no_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    assert await app._list_member_actor_ids_with_retry(FlakyChat()) == {"users/human"}  # type: ignore[arg-type]
    assert attempts == 3


async def test_restart_ambiguity_posts_once_and_marks_notified(tmp_path: Path) -> None:
    path = tmp_path / "bridge.sqlite3"
    first = SQLiteStore(path)
    await first.initialize()
    await first.create_thread("conv", "spaces/s", "threads/t", "messages/root", "Session")
    await first.claim_inbound(
        message_name="messages/phone",
        thread_name="threads/t",
        actor_id="users/human",
        created_at_google="2026-01-01T00:00:00Z",
        text_sha256="hash",
    )
    from omnigent_google_chat.models import InboundState

    await first.set_inbound_state("messages/phone", InboundState.DISPATCHING)
    first.close()

    second = SQLiteStore(path)
    await second.initialize()
    sender = FakeSender()
    try:
        await app._notify_restart_ambiguities(
            second,
            sender,  # type: ignore[arg-type]
            logging.getLogger("test"),
        )
        assert len(sender.calls) == 1
        assert "uncertain delivery" in str(sender.calls[0]["text"])
        assert await second.list_restart_ambiguous() == []
    finally:
        second.close()
