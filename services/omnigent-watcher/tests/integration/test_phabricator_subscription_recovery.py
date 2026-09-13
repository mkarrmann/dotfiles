"""One stack subscription owns subsequent feedback across polls and restarts."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from omnigent_watcher import http_api
from omnigent_watcher.domain import (
    DIFF_EVENT_KINDS,
    EventDeliveryResult,
    EventDeliveryStatus,
    EventKind,
    SessionSnapshot,
    SubscriptionState,
    WatcherConfig,
)
from omnigent_watcher.repository import WatcherRepository
from omnigent_watcher.source_models import (
    AIReviewFinding,
    CIAggregateState,
    CIFailure,
    DiffLifecycle,
    DiffSnapshot,
    ReviewComment,
    ReviewSourceError,
    SourceCursor,
    SourceErrorCategory,
    fingerprint,
)
from omnigent_watcher.watcher import Watcher
from tests.support import (
    DiffSourceMixin,
    FakeClock,
    FakeSessionService,
    RecordingDeliveryService,
    fixture,
)

SESSION = "stack-owner"
DIFFS = ("D90000001", "D90000002")
FEEDBACK_KINDS = (EventKind.REVIEW_COMMENT, EventKind.AI_REVIEW, EventKind.CI_FAILURE)
FEEDBACK_TEXT = {
    EventKind.REVIEW_COMMENT: "unresolved review comment",
    EventKind.AI_REVIEW: "unresolved automated-review finding",
    EventKind.CI_FAILURE: "current-version CI failure",
}


class StackSource(DiffSourceMixin):
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        base = fixture("active")
        self.snapshots = {
            subject: base.model_copy(
                update={
                    "subject": subject,
                    "latest_version_id": f"{subject}:v1",
                    "comments": base.comments.model_copy(update={"items": ()}),
                }
            )
            for subject in DIFFS
        }

    async def snapshot(self, subject: str, previous: SourceCursor | None) -> DiffSnapshot:
        return self.snapshots[subject].model_copy(update={"observed_at": self.clock.now()})

    def ci(self, subject: str, state: CIAggregateState) -> None:
        base = self.snapshots[subject]
        failure = CIFailure(
            external_id="unit-tests",
            fingerprint=fingerprint(f"{subject}:{base.latest_version_id}:unit-tests:failed"),
        )
        self.snapshots[subject] = base.model_copy(
            update={
                "last_activity_at": self.clock.now(),
                "ci": base.ci.model_copy(
                    update={
                        "cursor": f"{state}:{self.clock.now().timestamp()}",
                        "aggregate": state,
                        "failures": (failure,) if state is CIAggregateState.FAILING else (),
                    }
                ),
            }
        )

    def feedback(
        self, subject: str, kind: EventKind, *, present: bool, content: str = "same feedback"
    ) -> None:
        if kind is EventKind.CI_FAILURE:
            self.ci(subject, CIAggregateState.FAILING if present else CIAggregateState.PENDING)
            return
        base = self.snapshots[subject]
        if kind is EventKind.REVIEW_COMMENT:
            comment = ReviewComment(
                external_id="human-comment",
                version_id=base.latest_version_id or "",
                updated_at=self.clock.now(),
                content_fingerprint=fingerprint(content),
            )
            component: dict[str, object] = {
                "comments": base.comments.model_copy(
                    update={"items": (comment,) if present else ()}
                )
            }
        else:
            assert kind is EventKind.AI_REVIEW
            finding = AIReviewFinding(
                external_id="automated-finding", fingerprint=fingerprint(content)
            )
            component = {
                "ai_reviews": base.ai_reviews.model_copy(
                    update={"items": (finding,) if present else ()}
                )
            }
        self.snapshots[subject] = base.model_copy(
            update={**component, "last_activity_at": self.clock.now()}
        )

    def revision(self, subject: str, number: int) -> None:
        self.snapshots[subject] = self.snapshots[subject].model_copy(
            update={"latest_version_id": f"{subject}:v{number}"}
        )
        self.ci(subject, CIAggregateState.PENDING)

    def retire(self, subject: str) -> None:
        self.snapshots[subject] = self.snapshots[subject].model_copy(
            update={"lifecycle": DiffLifecycle.COMMITTED}
        )


class ReceiptDelivery(RecordingDeliveryService):
    def __init__(self, clock: FakeClock, *outcomes: EventDeliveryStatus) -> None:
        super().__init__(*outcomes)
        self.clock = clock
        self.receipts: dict[str, EventDeliveryResult] = {}
        self.receipt_error: Exception | None = None

    async def delivery_receipt(
        self, session_id: str, delivery_id: str
    ) -> EventDeliveryResult | None:
        if self.receipt_error is not None:
            raise self.receipt_error
        return self.receipts.get(delivery_id)

    def acknowledge(self, delivery_id: str, accepted_at: float) -> None:
        self.receipts[delivery_id] = EventDeliveryResult(
            EventDeliveryStatus.ALREADY_ACCEPTED, accepted_at
        )

    async def deliver_message(
        self, session_id: str, delivery_id: str, content: str
    ) -> EventDeliveryResult:
        result = await super().deliver_message(session_id, delivery_id, content)
        if result.status in {EventDeliveryStatus.ACCEPTED, EventDeliveryStatus.ALREADY_ACCEPTED}:
            accepted_at = self.clock.now().timestamp()
            self.acknowledge(delivery_id, accepted_at)
            return EventDeliveryResult(result.status, accepted_at)
        return result


class StackHarness:
    def __init__(self, path: Path, *outcomes: EventDeliveryStatus) -> None:
        self.path = path
        self.clock = FakeClock()
        self.source = StackSource(self.clock)
        self.sessions = FakeSessionService(SessionSnapshot(SESSION, {}))
        self.delivery = ReceiptDelivery(self.clock, *outcomes)
        self.restart()

    def restart(self) -> None:
        self.watcher = Watcher(
            WatcherRepository(self.path),
            (self.source,),
            self.sessions,
            self.delivery,
            clock=self.clock,
            config=WatcherConfig(
                poll_interval_override_seconds=60,
                batch_window_seconds=10,
                minimum_delivery_interval_seconds=20,
                delivery_retry_seconds=300,
            ),
        )

    def busy(self, busy: bool) -> None:
        self.sessions.snapshots[SESSION] = SessionSnapshot(SESSION, {}, can_accept_input=not busy)

    async def tick(self, seconds: float = 61) -> None:
        self.clock.advance(seconds)
        await self.watcher.run_iteration()

    async def flush(self) -> None:
        await self.tick(11)

    def bindings(self) -> dict[str, tuple[int, float]]:
        return {
            row.subject: (row.id, row.baseline_at)
            for row in self.watcher.repository.subscriptions_for_session(SESSION)
        }

    async def subscribe_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(http_api, "_repository", lambda: self.watcher.repository)
        monkeypatch.setattr(http_api, "_engine", lambda repository: self.watcher)
        app = FastAPI()
        app.include_router(http_api.router)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://isolated-test"
        ) as client:
            response = await client.post(
                "/v1/watches",
                json={
                    "session_id": SESSION,
                    "source": "phabricator",
                    "subjects": list(DIFFS),
                    "events": sorted(DIFF_EVENT_KINDS),
                },
            )
        assert response.status_code == 200, response.text
        assert response.json() == {"bound": list(DIFFS), "failures": []}
        assert self.delivery.calls == []


async def test_one_stack_subscription_survives_ten_days_of_feedback_and_restarts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = StackHarness(tmp_path / "watcher.sqlite3")
    await stack.subscribe_once(monkeypatch)
    initial = stack.bindings()
    rounds = [
        (DIFFS[index % 2], CIAggregateState.FAILING if index % 4 < 2 else CIAggregateState.PASSED)
        for index in range(10)
    ]
    for number, (subject, state) in enumerate(rounds, start=1):
        stack.clock.advance(24 * 60 * 60)
        stack.source.ci(subject, state)
        stack.restart()
        await stack.tick(0)
        await stack.flush()
        assert len(stack.delivery.calls) == number
        summary = stack.delivery.calls[-1][2]
        assert subject in summary
        assert ("CI green" if state is CIAggregateState.PASSED else "CI failure") in summary
        await stack.tick()
        assert len(stack.delivery.calls) == number
        assert stack.bindings() == initial
        assert all(
            row.state is SubscriptionState.ACTIVE
            for row in stack.watcher.repository.subscriptions_for_session(SESSION)
        )
        assert len(stack.watcher.repository.active_watch_requests(SESSION)) == 2
    assert len({delivery_id for _, delivery_id, _ in stack.delivery.calls}) == len(rounds)


@pytest.mark.parametrize("kind", FEEDBACK_KINDS)
async def test_identical_feedback_reopened_after_delivery_wakes_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: EventKind
) -> None:
    stack = StackHarness(tmp_path / "watcher.sqlite3")
    await stack.subscribe_once(monkeypatch)
    initial = stack.bindings()
    stack.source.feedback(DIFFS[0], kind, present=True)
    await stack.tick()
    await stack.flush()
    assert len(stack.delivery.calls) == 1
    stack.source.feedback(DIFFS[0], kind, present=False)
    await stack.tick()
    await stack.flush()
    assert len(stack.delivery.calls) == 1
    stack.restart()
    stack.source.feedback(DIFFS[0], kind, present=True)
    await stack.tick()
    await stack.flush()
    assert len(stack.delivery.calls) == 2
    assert stack.delivery.calls[0][1] != stack.delivery.calls[1][1]
    assert all(FEEDBACK_TEXT[kind] in summary for _, _, summary in stack.delivery.calls)
    await stack.tick(400)
    assert len(stack.delivery.calls) == 2
    assert stack.bindings() == initial


@pytest.mark.parametrize("kind", [EventKind.REVIEW_COMMENT, EventKind.AI_REVIEW])
async def test_feedback_edited_back_to_earlier_content_is_a_new_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: EventKind
) -> None:
    stack = StackHarness(tmp_path / "watcher.sqlite3")
    await stack.subscribe_once(monkeypatch)
    initial = stack.bindings()
    for expected_count, content in enumerate(("A", "B", "A"), start=1):
        stack.source.feedback(DIFFS[0], kind, present=True, content=content)
        stack.restart()
        await stack.tick()
        await stack.flush()
        assert len(stack.delivery.calls) == expected_count
        assert FEEDBACK_TEXT[kind] in stack.delivery.calls[-1][2]
        await stack.tick()
        assert len(stack.delivery.calls) == expected_count
    assert len({call[1] for call in stack.delivery.calls}) == 3
    assert stack.bindings() == initial
    assert all(
        row.state is SubscriptionState.ACTIVE
        for row in stack.watcher.repository.subscriptions_for_session(SESSION)
    )


@pytest.mark.parametrize("kind", FEEDBACK_KINDS)
@pytest.mark.parametrize("reopen_before_ack", [False, True], ids=["after-ack", "before-ack"])
async def test_a_delayed_acknowledgment_does_not_consume_reopened_feedback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: EventKind,
    reopen_before_ack: bool,
) -> None:
    stack = StackHarness(
        tmp_path / "watcher.sqlite3", EventDeliveryStatus.DEFERRED, EventDeliveryStatus.ACCEPTED
    )
    await stack.subscribe_once(monkeypatch)
    initial = stack.bindings()
    stack.source.feedback(DIFFS[0], kind, present=True)
    await stack.tick()
    await stack.flush()
    (first_attempt,) = stack.delivery.calls
    accepted_at = stack.clock.now().timestamp()

    stack.source.feedback(DIFFS[0], kind, present=False)
    await stack.tick()
    if reopen_before_ack:
        stack.source.feedback(DIFFS[0], kind, present=True)
        await stack.tick()
    stack.delivery.acknowledge(first_attempt[1], accepted_at)
    stack.restart()
    await stack.tick(601)
    assert len(stack.delivery.calls) == (2 if reopen_before_ack else 1)

    if not reopen_before_ack:
        stack.source.feedback(DIFFS[0], kind, present=True)
    await stack.tick()
    await stack.flush()
    assert len(stack.delivery.calls) == 2
    assert stack.delivery.calls[-1][1] != first_attempt[1]
    assert FEEDBACK_TEXT[kind] in stack.delivery.calls[-1][2]
    await stack.tick(400)
    assert len(stack.delivery.calls) == 2
    assert stack.bindings() == initial


@pytest.mark.parametrize("kind", FEEDBACK_KINDS)
async def test_resolved_feedback_without_a_receipt_is_not_resent_and_can_reopen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: EventKind
) -> None:
    stack = StackHarness(tmp_path / "watcher.sqlite3", EventDeliveryStatus.DEFERRED)
    await stack.subscribe_once(monkeypatch)
    stack.source.feedback(DIFFS[0], kind, present=True)
    await stack.tick()
    await stack.flush()
    (first_attempt,) = stack.delivery.calls
    stack.source.feedback(DIFFS[0], kind, present=False)
    await stack.tick()
    stack.restart()
    await stack.tick(301)
    assert stack.delivery.calls == [first_attempt]

    stack.source.feedback(DIFFS[0], kind, present=True)
    await stack.tick()
    await stack.flush()
    assert len(stack.delivery.calls) == 2
    assert stack.delivery.calls[-1][1] != first_attempt[1]
    assert FEEDBACK_TEXT[kind] in stack.delivery.calls[-1][2]
    await stack.tick(400)
    assert len(stack.delivery.calls) == 2


async def test_a_new_revision_discards_unsent_old_feedback_and_reports_new_feedback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = StackHarness(tmp_path / "watcher.sqlite3")
    await stack.subscribe_once(monkeypatch)
    initial = stack.bindings()
    stack.source.feedback(DIFFS[0], EventKind.REVIEW_COMMENT, present=True)
    stack.source.ci(DIFFS[0], CIAggregateState.FAILING)
    await stack.tick()

    stack.source.revision(DIFFS[0], 2)
    await stack.tick()
    await stack.flush()
    assert stack.delivery.calls == []
    stack.source.feedback(DIFFS[0], EventKind.REVIEW_COMMENT, present=True)
    stack.source.ci(DIFFS[0], CIAggregateState.FAILING)
    await stack.tick()
    await stack.flush()
    assert len(stack.delivery.calls) == 1
    summary = stack.delivery.calls[0][2]
    assert "1 unresolved review comment" in summary
    assert "1 current-version CI failure" in summary
    await stack.tick(400)
    assert len(stack.delivery.calls) == 1
    assert stack.bindings() == initial


@pytest.mark.parametrize("busy", [True, False], ids=["busy", "definitely-not-sent"])
async def test_feedback_is_refreshed_when_no_delivery_has_been_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, busy: bool
) -> None:
    stack = StackHarness(
        tmp_path / "watcher.sqlite3",
        EventDeliveryStatus.ACCEPTED if busy else EventDeliveryStatus.NOT_SENT,
    )
    await stack.subscribe_once(monkeypatch)
    stack.source.ci(DIFFS[0], CIAggregateState.FAILING)
    await stack.tick()
    stack.busy(busy)
    await stack.flush()
    assert len(stack.delivery.calls) == (0 if busy else 1)

    stack.source.ci(DIFFS[0], CIAggregateState.PENDING)
    stack.source.feedback(DIFFS[0], EventKind.REVIEW_COMMENT, present=True)
    await stack.tick()
    stack.busy(False)
    stack.restart()
    await stack.tick(301)
    await stack.flush()
    assert len(stack.delivery.calls) == (1 if busy else 2)
    summary = stack.delivery.calls[-1][2]
    assert "unresolved review comment" in summary
    assert "CI failure" not in summary
    await stack.tick(400)
    assert len(stack.delivery.calls) == (1 if busy else 2)


async def test_an_unconfirmed_obsolete_attempt_is_replaced_with_current_feedback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = StackHarness(
        tmp_path / "watcher.sqlite3",
        EventDeliveryStatus.DEFERRED,
        EventDeliveryStatus.ACCEPTED,
    )
    await stack.subscribe_once(monkeypatch)
    stack.source.ci(DIFFS[0], CIAggregateState.FAILING)
    await stack.tick()
    await stack.flush()
    (first_attempt,) = stack.delivery.calls
    stack.source.ci(DIFFS[0], CIAggregateState.PENDING)
    stack.source.feedback(DIFFS[0], EventKind.REVIEW_COMMENT, present=True)
    await stack.tick(301)
    assert len(stack.delivery.calls) == 2
    assert stack.delivery.calls[-1][1] != first_attempt[1]
    assert "unresolved review comment" in stack.delivery.calls[-1][2]
    assert "CI failure" not in stack.delivery.calls[-1][2]
    await stack.tick(400)
    assert len(stack.delivery.calls) == 2


async def test_an_unavailable_receipt_defers_delivery_until_acceptance_is_known(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = StackHarness(tmp_path / "watcher.sqlite3", EventDeliveryStatus.DEFERRED)
    await stack.subscribe_once(monkeypatch)
    stack.source.ci(DIFFS[0], CIAggregateState.FAILING)
    await stack.tick()
    await stack.flush()
    (first_attempt,) = stack.delivery.calls
    accepted_at = stack.clock.now().timestamp()
    stack.source.ci(DIFFS[0], CIAggregateState.PENDING)
    stack.source.feedback(DIFFS[0], EventKind.REVIEW_COMMENT, present=True)
    stack.delivery.receipt_error = RuntimeError("receipt endpoint unavailable")
    await stack.tick(301)
    assert stack.delivery.calls == [first_attempt]

    stack.delivery.receipt_error = None
    stack.delivery.acknowledge(first_attempt[1], accepted_at)
    stack.restart()
    await stack.tick(301)
    assert len(stack.delivery.calls) == 2
    assert stack.delivery.calls[-1][1] != first_attempt[1]
    assert "unresolved review comment" in stack.delivery.calls[-1][2]
    assert "CI failure" not in stack.delivery.calls[-1][2]


@pytest.mark.parametrize("reachable", [True, False], ids=["busy", "unreachable"])
async def test_a_receipt_is_acknowledged_even_when_the_session_cannot_take_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reachable: bool
) -> None:
    stack = StackHarness(tmp_path / "watcher.sqlite3", EventDeliveryStatus.DEFERRED)
    await stack.subscribe_once(monkeypatch)
    stack.source.ci(DIFFS[0], CIAggregateState.FAILING)
    await stack.tick()
    await stack.flush()
    (first_attempt,) = stack.delivery.calls
    accepted_at = stack.clock.now().timestamp()
    stack.delivery.acknowledge(first_attempt[1], accepted_at)
    stack.sessions.snapshots[SESSION] = SessionSnapshot(
        SESSION, {}, reachable=reachable, can_accept_input=False
    )
    await stack.tick(601)
    assert stack.delivery.calls == [first_attempt]
    subscription = stack.watcher.repository.subscription(SESSION, DIFFS[0])
    assert subscription is not None and subscription.last_delivery_at == accepted_at


@pytest.mark.parametrize("attempted", [False, True], ids=["unsent", "ambiguous-attempt"])
async def test_retiring_one_diff_preserves_its_siblings_pending_and_future_feedback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, attempted: bool
) -> None:
    stack = StackHarness(
        tmp_path / "watcher.sqlite3",
        EventDeliveryStatus.DEFERRED if attempted else EventDeliveryStatus.ACCEPTED,
    )
    await stack.subscribe_once(monkeypatch)
    initial = stack.bindings()
    for subject in DIFFS:
        stack.source.ci(subject, CIAggregateState.FAILING)
    await stack.tick()
    stack.busy(not attempted)
    await stack.flush()
    assert len(stack.delivery.calls) == int(attempted)
    stack.source.retire(DIFFS[0])
    await stack.tick()
    stack.busy(False)
    stack.restart()
    await stack.tick(301)
    await stack.flush()

    if attempted:
        assert len(stack.delivery.calls) == 2
        assert stack.delivery.calls[1][1] != stack.delivery.calls[0][1]
        assert DIFFS[0] not in stack.delivery.calls[1][2]
        assert DIFFS[1] in stack.delivery.calls[1][2]
    else:
        assert len(stack.delivery.calls) == 1
        assert DIFFS[0] not in stack.delivery.calls[0][2]
        assert DIFFS[1] in stack.delivery.calls[0][2]

    rows = stack.watcher.repository.subscriptions_for_session(SESSION)
    assert {row.subject: row.state for row in rows} == {
        DIFFS[0]: SubscriptionState.RETIRED,
        DIFFS[1]: SubscriptionState.ACTIVE,
    }
    previous_count = len(stack.delivery.calls)
    stack.source.ci(DIFFS[1], CIAggregateState.PENDING)
    await stack.tick()
    stack.source.ci(DIFFS[1], CIAggregateState.FAILING)
    await stack.tick()
    await stack.flush()
    assert len(stack.delivery.calls) == previous_count + 1
    assert DIFFS[0] not in stack.delivery.calls[-1][2]
    assert DIFFS[1] in stack.delivery.calls[-1][2]
    assert stack.bindings() == initial


async def test_partial_retirement_checks_the_old_receipt_before_replacing_a_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = StackHarness(tmp_path / "watcher.sqlite3", EventDeliveryStatus.DEFERRED)
    await stack.subscribe_once(monkeypatch)
    for subject in DIFFS:
        stack.source.ci(subject, CIAggregateState.FAILING)
    await stack.tick()
    await stack.flush()
    (first_attempt,) = stack.delivery.calls
    stack.delivery.acknowledge(first_attempt[1], stack.clock.now().timestamp())

    stack.source.retire(DIFFS[0])
    await stack.tick()
    stack.restart()
    await stack.tick(301)
    assert stack.delivery.calls == [first_attempt]
    assert stack.watcher.repository.delivering_batch_for_session(SESSION) is None

    stack.source.ci(DIFFS[1], CIAggregateState.PENDING)
    await stack.tick()
    stack.source.ci(DIFFS[1], CIAggregateState.FAILING)
    await stack.tick()
    await stack.flush()
    assert len(stack.delivery.calls) == 2
    assert stack.delivery.calls[-1][1] != first_attempt[1]
    assert DIFFS[0] not in stack.delivery.calls[-1][2]
    assert DIFFS[1] in stack.delivery.calls[-1][2]


async def test_an_unavailable_retired_diff_does_not_block_its_siblings_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = StackHarness(tmp_path / "watcher.sqlite3", EventDeliveryStatus.DEFERRED)
    await stack.subscribe_once(monkeypatch)
    for subject in DIFFS:
        stack.source.ci(subject, CIAggregateState.FAILING)
    await stack.tick()
    await stack.flush()
    (first_attempt,) = stack.delivery.calls
    stack.source.retire(DIFFS[0])
    await stack.tick()
    original_read = stack.source.snapshot
    retired_reads = 0

    async def read_remaining(subject: str, previous: SourceCursor | None) -> DiffSnapshot:
        nonlocal retired_reads
        if subject == DIFFS[0]:
            retired_reads += 1
            raise ReviewSourceError(SourceErrorCategory.UNAVAILABLE)
        return await original_read(subject, previous)

    monkeypatch.setattr(stack.source, "snapshot", read_remaining)
    stack.restart()
    await stack.tick(301)
    assert retired_reads == 0
    assert len(stack.delivery.calls) == 2
    assert stack.delivery.calls[-1][1] != first_attempt[1]
    assert DIFFS[0] not in stack.delivery.calls[-1][2]
    assert DIFFS[1] in stack.delivery.calls[-1][2]
    await stack.tick(400)
    assert len(stack.delivery.calls) == 2
