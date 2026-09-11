from __future__ import annotations

from pathlib import Path

import pytest

from omnigent_watcher.command_source import SOURCE_NAME as COMMAND_SOURCE_NAME
from omnigent_watcher.command_source import CommandSpec
from omnigent_watcher.domain import (
    COMMAND_EVENT_KINDS,
    DIFF_EVENT_KINDS,
    SessionSnapshot,
    SubscriptionState,
    WatcherConfig,
)
from omnigent_watcher.phabricator_source import SOURCE_NAME as PHABRICATOR_SOURCE_NAME
from omnigent_watcher.service import WatcherService
from omnigent_watcher.settings import ServiceSettings
from tests.support import FakeReviewSource, fixture


class FakeOmnigentClient:
    def __init__(self) -> None:
        self.closed = False

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


async def test_a_recorded_watch_request_becomes_a_live_subscription(tmp_path: Path) -> None:
    """The seam between the two halves of a generic watch.

    The MCP tool only records intent in ``watch_requests`` -- it never polls.
    The sidecar is what turns that row into a real subscription, so this covers
    the handoff neither side tests on its own.
    """
    value = tmp_path / "knob"
    value.write_text("false\n")
    client = FakeOmnigentClient()
    service = WatcherService(_settings(tmp_path), client=client)  # type: ignore[arg-type]

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


async def test_cancelling_a_request_stops_rebinding_it(tmp_path: Path) -> None:
    value = tmp_path / "knob"
    value.write_text("false\n")
    client = FakeOmnigentClient()
    service = WatcherService(_settings(tmp_path), client=client)  # type: ignore[arg-type]

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


class FakeTime:
    """Stand-in for the ``time`` module, so a backoff can be waited out."""

    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def time(self) -> float:
        return self.now


def _diff_service(tmp_path: Path, client: object, outcomes: int) -> WatcherService:
    """A service whose Phabricator source is a fake that is always terminal.

    Registered by name, like any source: a watch request names its source, so
    reconciliation resolves through the map and would otherwise reach the real
    ``meta phabricator.diff``.
    """
    service = WatcherService(_settings(tmp_path), client=client)  # type: ignore[arg-type]
    fake = FakeReviewSource(*(fixture("committed") for _ in range(outcomes)))
    service.watcher.sources[fake.name] = fake
    service.repository.request_watch(
        "conv_stack",
        PHABRICATOR_SOURCE_NAME,
        "D90000004",
        DIFF_EVENT_KINDS,
        spec=None,
        now=1000.0,
    )
    return service


async def test_an_unusable_diff_is_not_retried_every_cycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A binding that cannot succeed must decay, not hammer.

    Reconciliation is desired-state, so a failure used to leave no trace and
    the next cycle simply tried again -- every 15s, forever, at one HTTP call
    and one `jf` subprocess each. The live log had 58k of these, 31k of them
    for a single placeholder diff. The proof is that repeated cycles do not
    reach the source again, and that the retry still happens once the delay
    has elapsed.
    """
    from omnigent_watcher import service as service_module

    clock = FakeTime()
    monkeypatch.setattr(service_module, "time", clock)

    client = FakeOmnigentClient()
    service = _diff_service(tmp_path, client, outcomes=20)
    source = service.watcher.sources[PHABRICATOR_SOURCE_NAME]
    assert isinstance(source, FakeReviewSource)

    await service.reconcile_subscriptions()
    assert len(source.calls) == 1

    # Three cycles at the real 15s interval, staying inside the first delay.
    for _ in range(3):
        clock.now += 15.0
        await service.reconcile_subscriptions()
    assert len(source.calls) == 1, "a failed binding must not be retried every cycle"

    # It is a deferral, not a blacklist: a diff that becomes readable later
    # still binds.
    clock.now += service_module.RECONCILE_BACKOFF_BASE_SECONDS
    await service.reconcile_subscriptions()
    assert len(source.calls) == 2
    await client.close()


async def test_the_backoff_table_forgets_bindings_nobody_wants(tmp_path: Path) -> None:
    """Otherwise it grows for the life of the process, and a session that
    re-registers later inherits a stale deferral."""
    client = FakeOmnigentClient()
    service = _diff_service(tmp_path, client, outcomes=5)

    await service.reconcile_subscriptions()
    assert ("conv_stack", "D90000004") in service._deferred_bindings

    service.repository.cancel_watch_requests("conv_stack", now=2000.0)
    await service.reconcile_subscriptions()
    assert service._deferred_bindings == {}
    await client.close()
