"""A shared command subject cannot silently change its command contract."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from omnigent_watcher import http_api
from omnigent_watcher.command_source import SOURCE_NAME, CommandSource, CommandSpec
from omnigent_watcher.domain import COMMAND_EVENT_KINDS, SessionSnapshot, SubscriptionState
from omnigent_watcher.repository import WatcherRepository
from omnigent_watcher.watcher import SubscriptionError, Watcher
from tests.support import FakeClock, FakeSessionService, RecordingDeliveryService

SUBJECT = "job:export"
SPEC = CommandSpec(["printf", "ready"]).to_json()


@pytest.fixture
def command_runner(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    runner = AsyncMock(return_value="ready")
    monkeypatch.setattr("omnigent_watcher.command_source.run_text_command", runner)
    return runner


def _watcher(repository: WatcherRepository, clock: FakeClock | None = None) -> Watcher:
    return Watcher(
        repository,
        (CommandSource(env={}),),
        FakeSessionService(
            SessionSnapshot(session_id="session-1", labels={}),
            SessionSnapshot(session_id="session-2", labels={}),
        ),
        RecordingDeliveryService(),
        clock=clock,
    )


@pytest.mark.parametrize(
    "different_spec",
    [
        CommandSpec(["printf", "different"]).to_json(),
        CommandSpec(["printf", "ready"], extract=r"(ready)").to_json(),
        CommandSpec(["printf", "ready"], interval_seconds=120).to_json(),
        CommandSpec(["printf", "ready"], timeout_seconds=10).to_json(),
    ],
    ids=["argv", "extract", "interval", "timeout"],
)
@pytest.mark.parametrize("retired", [False, True], ids=["active", "retired"])
@pytest.mark.parametrize("session_id", ["session-1", "session-2"])
async def test_conflicting_specs_are_rejected_before_polling_or_mutation(
    tmp_path: Path,
    command_runner: AsyncMock,
    different_spec: str,
    retired: bool,
    session_id: str,
) -> None:
    repository = WatcherRepository(tmp_path / "watcher.sqlite3")
    watcher = _watcher(repository)
    await watcher.subscribe(
        "session-1", SUBJECT, COMMAND_EVENT_KINDS, source_name=SOURCE_NAME, spec=SPEC
    )
    if retired:
        await watcher.unsubscribe("session-1")
    original_watch = repository.watch(SUBJECT)
    original_subscription = repository.subscription("session-1", SUBJECT)

    with pytest.raises(SubscriptionError, match="different command spec.*subject"):
        await watcher.subscribe(
            session_id,
            SUBJECT,
            COMMAND_EVENT_KINDS,
            source_name=SOURCE_NAME,
            spec=different_spec,
        )

    assert command_runner.await_count == 1
    assert repository.watch(SUBJECT) == original_watch
    assert repository.subscription("session-1", SUBJECT) == original_subscription
    assert repository.subscription("session-2", SUBJECT) is None


async def test_a_different_source_is_rejected_before_polling_or_mutation(
    tmp_path: Path, command_runner: AsyncMock
) -> None:
    class OtherSource(CommandSource):
        @property
        def name(self) -> str:
            return "other"

    repository = WatcherRepository(tmp_path / "watcher.sqlite3")
    watcher = _watcher(repository)
    await watcher.subscribe(
        "session-1", SUBJECT, COMMAND_EVENT_KINDS, source_name=SOURCE_NAME, spec=SPEC
    )
    original_watch = repository.watch(SUBJECT)
    watcher.sources["other"] = OtherSource(env={})

    with pytest.raises(SubscriptionError, match="belongs to another source"):
        await watcher.subscribe(
            "session-2", SUBJECT, COMMAND_EVENT_KINDS, source_name="other", spec=SPEC
        )

    assert command_runner.await_count == 1
    assert repository.watch(SUBJECT) == original_watch
    assert repository.subscription("session-2", SUBJECT) is None


@pytest.mark.parametrize("retired", [False, True], ids=["active", "retired"])
async def test_equivalent_legacy_specs_share_one_poll_and_can_resubscribe(
    tmp_path: Path, command_runner: AsyncMock, retired: bool
) -> None:
    repository = WatcherRepository(tmp_path / "watcher.sqlite3")
    clock = FakeClock()
    watcher = _watcher(repository, clock)
    legacy_spec = json.dumps({"interval_seconds": 60, "argv": ["printf", "ready"]})
    baseline = await watcher.source_for(SOURCE_NAME).poll(SUBJECT, None, legacy_spec)
    repository.subscribe(
        "session-1",
        SUBJECT,
        COMMAND_EVENT_KINDS,
        baseline,
        now=clock.now().timestamp(),
        next_poll_at=clock.now().timestamp() + 60,
        spec=legacy_spec,
    )
    if retired:
        await watcher.unsubscribe("session-1")
    subscription, created = await watcher.subscribe(
        "session-1" if retired else "session-2",
        SUBJECT,
        COMMAND_EVENT_KINDS,
        source_name=SOURCE_NAME,
        spec=SPEC,
    )

    assert subscription.state is SubscriptionState.ACTIVE
    assert created is not retired
    watch = repository.watch(SUBJECT)
    assert watch is not None and watch.spec == legacy_spec
    assert repository.active_subject_count() == 1
    clock.advance(120)
    await watcher.run_iteration()
    assert command_runner.await_count == 3
    assert repository.open_batch_for_session("session-1") is None
    assert repository.open_batch_for_session("session-2") is None


async def test_concurrent_first_subscriptions_cannot_overwrite_the_winning_spec(
    tmp_path: Path, command_runner: AsyncMock
) -> None:
    repository = WatcherRepository(tmp_path / "watcher.sqlite3")
    first = _watcher(repository)
    second = _watcher(repository)
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_read(*args: object, **kwargs: object) -> str:
        started.set()
        await release.wait()
        return "losing-value"

    command_runner.side_effect = slow_read
    async with asyncio.timeout(5):
        pending = asyncio.create_task(
            first.subscribe(
                "session-1", SUBJECT, COMMAND_EVENT_KINDS, source_name=SOURCE_NAME, spec=SPEC
            )
        )
        try:
            await started.wait()
            command_runner.side_effect = None
            winning_spec = CommandSpec(["printf", "winner"]).to_json()
            await second.subscribe(
                "session-2",
                SUBJECT,
                COMMAND_EVENT_KINDS,
                source_name=SOURCE_NAME,
                spec=winning_spec,
            )
            winning_watch = repository.watch(SUBJECT)
            release.set()
            with pytest.raises(SubscriptionError, match="different command spec"):
                await pending
        finally:
            release.set()
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)

    assert repository.watch(SUBJECT) == winning_watch
    assert repository.subscription("session-1", SUBJECT) is None
    winner = repository.subscription("session-2", SUBJECT)
    assert winner is not None and winner.state is SubscriptionState.ACTIVE
    assert repository.open_batch_for_session("session-2") is None


async def test_http_rejection_preserves_the_original_durable_request(
    tmp_path: Path, command_runner: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = WatcherRepository(tmp_path / "watcher.sqlite3")
    monkeypatch.setattr(http_api, "_repository", lambda: repository)
    monkeypatch.setattr(http_api, "_settings", lambda: None)
    app = FastAPI()
    app.include_router(http_api.router)
    request = {
        "session_id": "session-1",
        "source": SOURCE_NAME,
        "subjects": [SUBJECT],
        "events": ["changed"],
        "spec": SPEC,
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://isolated-test"
    ) as client:
        response = await client.post("/v1/watches", json=request)
        assert response.status_code == 200
        assert response.json() == {"bound": [SUBJECT], "failures": []}
        original_requests = repository.active_watch_requests()
        original_watch = repository.watch(SUBJECT)

        for session_id in ("session-1", "session-2"):
            response = await client.post(
                "/v1/watches",
                json={
                    **request,
                    "session_id": session_id,
                    "spec": CommandSpec(["printf", "different"]).to_json(),
                },
            )
            assert response.status_code == 200
            assert response.json()["bound"] == []
            assert "different command spec" in response.json()["failures"][0]

    assert command_runner.await_count == 1
    assert repository.active_watch_requests() == original_requests
    assert repository.watch(SUBJECT) == original_watch
    assert repository.subscription("session-2", SUBJECT) is None
