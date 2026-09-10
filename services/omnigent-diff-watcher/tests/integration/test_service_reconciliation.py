from __future__ import annotations

from pathlib import Path

from omnigent_diff_watcher.command_source import SOURCE_NAME as COMMAND_SOURCE_NAME
from omnigent_diff_watcher.command_source import CommandSpec
from omnigent_diff_watcher.domain import (
    COMMAND_EVENT_KINDS,
    SessionSnapshot,
    SubscriptionState,
    WatcherConfig,
)
from omnigent_diff_watcher.service import DiffWatcherService
from omnigent_diff_watcher.settings import ServiceSettings
from omnigent_diff_watcher.source_models import ReviewSourceError, SourceErrorCategory
from tests.support import FakeReviewSource, fixture


class FakeOmnigentClient:
    def __init__(self) -> None:
        self.sessions: list[dict[str, object]] = []
        self.closed = False

    async def list_sessions(self) -> list[dict[str, object]]:
        return self.sessions

    async def get(self, session_id: str) -> SessionSnapshot:
        return SessionSnapshot(session_id=session_id, labels={}, reachable=True)

    async def close(self) -> None:
        self.closed = True


def _settings(tmp_path: Path) -> ServiceSettings:
    return ServiceSettings(
        server_url="http://server",
        database_path=tmp_path / "watcher.sqlite3",
        delivery_mode="log_only",
        delivery_session_allowlist=frozenset(),
        reconcile_interval_seconds=15,
        scheduler_error_retry_seconds=30,
        watcher=WatcherConfig(),
    )


async def test_reconciles_label_preferences_without_rebaselining(tmp_path: Path) -> None:
    client = FakeOmnigentClient()
    client.sessions = [
        {
            "id": "conv_test",
            "labels": {
                "omnigent.diff.number": "D90000001",
                "omnigent.diff.watch": "ci_failure,review_comment",
            },
        }
    ]
    service = DiffWatcherService(_settings(tmp_path), client=client)  # type: ignore[arg-type]
    source = FakeReviewSource(fixture("active"))
    service.watcher.source = source

    await service.reconcile_subscriptions()
    subscription = service.repository.subscription("conv_test")
    assert subscription is not None and subscription.state is SubscriptionState.ACTIVE
    assert len(source.calls) == 1

    await service.reconcile_subscriptions()
    assert len(source.calls) == 1

    client.sessions[0]["labels"] = {
        "omnigent.diff.number": "D90000001",
        "omnigent.diff.watch": "off",
    }
    await service.reconcile_subscriptions()
    subscription = service.repository.subscription("conv_test")
    assert subscription is not None and subscription.state is SubscriptionState.RETIRED
    assert subscription.retired_reason == "unsubscribed"
    await client.close()


async def test_one_session_watches_every_diff_in_its_stack(tmp_path: Path) -> None:
    """A session that submits a stack must get a subscription per diff."""
    client = FakeOmnigentClient()
    client.sessions = [
        {
            "id": "conv_stack",
            "labels": {
                "omnigent.diff.number": "D90000001,D90000002,D90000003",
                "omnigent.diff.watch": "ci_failure,review_comment",
            },
        }
    ]
    service = DiffWatcherService(_settings(tmp_path), client=client)  # type: ignore[arg-type]
    base = fixture("active")
    service.watcher.source = FakeReviewSource(
        *(base.model_copy(update={"subject": d}) for d in ("D90000001", "D90000002", "D90000003"))
    )

    await service.reconcile_subscriptions()
    subscriptions = service.repository.subscriptions_for_session("conv_stack")
    assert [row.subject for row in subscriptions] == [
        "D90000001",
        "D90000002",
        "D90000003",
    ]
    assert all(row.state is SubscriptionState.ACTIVE for row in subscriptions)
    await client.close()


async def test_landing_one_diff_retires_only_that_subscription(tmp_path: Path) -> None:
    client = FakeOmnigentClient()
    client.sessions = [
        {
            "id": "conv_stack",
            "labels": {
                "omnigent.diff.number": "D90000001,D90000002",
                "omnigent.diff.watch": "ci_failure",
            },
        }
    ]
    service = DiffWatcherService(_settings(tmp_path), client=client)  # type: ignore[arg-type]
    base = fixture("active")
    service.watcher.source = FakeReviewSource(
        *(base.model_copy(update={"subject": d}) for d in ("D90000001", "D90000002"))
    )
    await service.reconcile_subscriptions()

    # The bottom diff lands and drops out of the label; the rest keep watching.
    client.sessions[0]["labels"] = {
        "omnigent.diff.number": "D90000002",
        "omnigent.diff.watch": "ci_failure",
    }
    await service.reconcile_subscriptions()

    by_diff = {r.subject: r for r in service.repository.subscriptions_for_session("conv_stack")}
    assert by_diff["D90000001"].state is SubscriptionState.RETIRED
    assert by_diff["D90000001"].retired_reason == "preference_removed"
    assert by_diff["D90000002"].state is SubscriptionState.ACTIVE
    await client.close()


async def test_an_unusable_diff_does_not_block_the_rest_of_the_stack(tmp_path: Path) -> None:
    """A stale or terminal label entry must not starve its siblings.

    Sessions accumulate diff ids over their lifetime, so one entry going
    terminal is routine -- and a poisoned label entry is possible outright.
    """
    client = FakeOmnigentClient()
    client.sessions = [
        {
            "id": "conv_stack",
            "labels": {
                "omnigent.diff.number": "D90000004,D90000002",
                "omnigent.diff.watch": "ci_failure",
            },
        }
    ]
    service = DiffWatcherService(_settings(tmp_path), client=client)  # type: ignore[arg-type]
    service.watcher.source = FakeReviewSource(
        fixture("committed"),  # D90000004 is terminal -> SubscriptionError
        fixture("active").model_copy(update={"subject": "D90000002"}),
    )

    await service.reconcile_subscriptions()
    by_diff = {r.subject: r for r in service.repository.subscriptions_for_session("conv_stack")}
    assert "D90000004" not in by_diff
    assert by_diff["D90000002"].state is SubscriptionState.ACTIVE
    await client.close()


async def test_a_diff_the_source_cannot_read_does_not_block_the_rest(tmp_path: Path) -> None:
    """An unreadable diff must be isolated the same way a terminal one is.

    A label entry naming a diff that does not resolve fails inside the review
    source rather than in subscribe's own validation, so it arrives as a
    ReviewSourceError. Reconciliation must survive it: the whole scheduler
    iteration -- every other session included -- rides on this loop.
    """
    client = FakeOmnigentClient()
    client.sessions = [
        {
            "id": "conv_stack",
            "labels": {
                "omnigent.diff.number": "D12345678,D90000002",
                "omnigent.diff.watch": "ci_failure",
            },
        }
    ]
    service = DiffWatcherService(_settings(tmp_path), client=client)  # type: ignore[arg-type]
    service.watcher.source = FakeReviewSource(
        ReviewSourceError(SourceErrorCategory.MALFORMED),
        fixture("active").model_copy(update={"subject": "D90000002"}),
    )

    await service.reconcile_subscriptions()
    by_diff = {r.subject: r for r in service.repository.subscriptions_for_session("conv_stack")}
    assert "D12345678" not in by_diff
    assert by_diff["D90000002"].state is SubscriptionState.ACTIVE
    await client.close()


async def test_a_recorded_watch_request_becomes_a_live_subscription(tmp_path: Path) -> None:
    """The seam between the two halves of a generic watch.

    The MCP tool only records intent in ``watch_requests`` -- it never polls.
    The sidecar is what turns that row into a real subscription, so this covers
    the handoff neither side tests on its own.
    """
    value = tmp_path / "knob"
    value.write_text("false\n")
    client = FakeOmnigentClient()
    service = DiffWatcherService(_settings(tmp_path), client=client)  # type: ignore[arg-type]

    spec = CommandSpec(["cat", str(value)], interval_seconds=60.0).to_json()
    service.repository.request_watch(
        "conv_test",
        COMMAND_SOURCE_NAME,
        "jk:demo",
        COMMAND_EVENT_KINDS,
        spec=spec,
        now=1000.0,
    )

    await service.reconcile_subscriptions()

    subscription = service.repository.subscription("conv_test", "jk:demo")
    assert subscription is not None
    assert subscription.state is SubscriptionState.ACTIVE
    watch = service.repository.watch("jk:demo")
    assert watch is not None
    assert watch.source == COMMAND_SOURCE_NAME
    assert watch.spec == spec

    # Idempotent: a second cycle must not rebaseline or duplicate the watch.
    await service.reconcile_subscriptions()
    rebound = service.repository.subscription("conv_test", "jk:demo")
    assert rebound is not None and rebound.id == subscription.id


async def test_a_label_sweep_leaves_generic_watches_alone(tmp_path: Path) -> None:
    """A session's labels describe only its diffs.

    The label reconciler retires whatever the labels no longer claim, so if it
    were not scoped by source it would delete every generic watch on the very
    next cycle.
    """
    value = tmp_path / "knob"
    value.write_text("false\n")
    client = FakeOmnigentClient()
    client.sessions = [{"id": "conv_test", "labels": {}}]
    service = DiffWatcherService(_settings(tmp_path), client=client)  # type: ignore[arg-type]

    service.repository.request_watch(
        "conv_test",
        COMMAND_SOURCE_NAME,
        "jk:demo",
        COMMAND_EVENT_KINDS,
        spec=CommandSpec(["cat", str(value)], interval_seconds=60.0).to_json(),
        now=1000.0,
    )

    await service.reconcile_subscriptions()
    await service.reconcile_subscriptions()

    subscription = service.repository.subscription("conv_test", "jk:demo")
    assert subscription is not None
    assert subscription.state is SubscriptionState.ACTIVE


async def test_cancelling_a_request_stops_rebinding_it(tmp_path: Path) -> None:
    value = tmp_path / "knob"
    value.write_text("false\n")
    client = FakeOmnigentClient()
    service = DiffWatcherService(_settings(tmp_path), client=client)  # type: ignore[arg-type]

    service.repository.request_watch(
        "conv_test",
        COMMAND_SOURCE_NAME,
        "jk:demo",
        COMMAND_EVENT_KINDS,
        spec=CommandSpec(["cat", str(value)], interval_seconds=60.0).to_json(),
        now=1000.0,
    )
    await service.reconcile_subscriptions()
    assert service.repository.cancel_watch_requests("conv_test", now=1100.0) == 1

    await service.watcher.unsubscribe("conv_test")
    await service.reconcile_subscriptions()

    subscription = service.repository.subscription("conv_test", "jk:demo")
    assert subscription is not None
    assert subscription.state is SubscriptionState.RETIRED
