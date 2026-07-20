from __future__ import annotations

from pathlib import Path

from omnigent_diff_watcher.domain import SessionSnapshot, SubscriptionState, WatcherConfig
from omnigent_diff_watcher.service import DiffWatcherService
from omnigent_diff_watcher.settings import ServiceSettings
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
