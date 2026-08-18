from __future__ import annotations

from pathlib import Path

from omnigent_diff_watcher.domain import SessionSnapshot, SubscriptionState, WatcherConfig
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
        *(base.model_copy(update={"diff_id": d}) for d in ("D90000001", "D90000002", "D90000003"))
    )

    await service.reconcile_subscriptions()
    subscriptions = service.repository.subscriptions_for_session("conv_stack")
    assert [row.diff_id for row in subscriptions] == [
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
        *(base.model_copy(update={"diff_id": d}) for d in ("D90000001", "D90000002"))
    )
    await service.reconcile_subscriptions()

    # The bottom diff lands and drops out of the label; the rest keep watching.
    client.sessions[0]["labels"] = {
        "omnigent.diff.number": "D90000002",
        "omnigent.diff.watch": "ci_failure",
    }
    await service.reconcile_subscriptions()

    by_diff = {r.diff_id: r for r in service.repository.subscriptions_for_session("conv_stack")}
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
        fixture("active").model_copy(update={"diff_id": "D90000002"}),
    )

    await service.reconcile_subscriptions()
    by_diff = {r.diff_id: r for r in service.repository.subscriptions_for_session("conv_stack")}
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
        fixture("active").model_copy(update={"diff_id": "D90000002"}),
    )

    await service.reconcile_subscriptions()
    by_diff = {r.diff_id: r for r in service.repository.subscriptions_for_session("conv_stack")}
    assert "D12345678" not in by_diff
    assert by_diff["D90000002"].state is SubscriptionState.ACTIVE
    await client.close()
