from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest

from omnigent_watcher.phabricator_source import (
    PhabricatorReviewSource,
    ReviewSourceError,
    bounded_source_environment,
)
from omnigent_watcher.source_command import (
    SourceCommandError,
    SourceCommandErrorCategory,
)
from omnigent_watcher.source_models import (
    CIAggregateState,
    DiffLifecycle,
    SourceErrorCategory,
)


def properties(*, status: str = "Needs Review") -> dict[str, object]:
    return {
        "number": 90000001,
        "status": status,
        "is_closed": status == "Closed",
        "created_time": 1768478400,
        "author": {"id": "author-synthetic", "unixname": "author"},
        "latest_phabricator_version": {"id": "version-7", "number": 7},
        "latest_draft_phabricator_version": None,
    }


def comments() -> list[dict[str, object]]:
    common = {
        "version_id": "version-7",
        "updated_at": "2026-01-15T12:02:00Z",
        "author": {"id": "reviewer-synthetic"},
    }
    return [
        {**common, "id": "comment-good", "content": "  Please fix this.  "},
        {
            "id": "comment-unknown-author",
            "version_id": "version-7",
            "updated_at": "2026-01-15T12:02:00Z",
            "content": "identity is required",
        },
        {**common, "id": "comment-resolved", "content": "old", "resolved": True},
        {
            **common,
            "id": "comment-author",
            "content": "self note",
            "author": {"id": "author-synthetic"},
        },
        {**common, "id": "comment-signal", "content": "automated", "is_signal": True},
        {**common, "id": "comment-draft", "content": "draft", "is_draft": True},
        {**common, "id": "comment-deleted", "content": "gone", "deleted": True},
        {
            **common,
            "id": "comment-old-version",
            "content": "old version",
            "version_id": "version-6",
        },
    ]


def review_group(*names: str, status: str = "WARNING") -> dict[str, object]:
    return {
        "group_data": {"functional_type": "REVIEW_INSIGHTS"},
        "signals": {
            "nodes": [
                {"name": name, "status": status, "slp_functional_type": "REVIEW_INSIGHTS"}
                for name in names
            ]
        },
    }


def noise_group(*names: str) -> dict[str, object]:
    return {
        "group_data": {"functional_type": "TEST"},
        "signals": {
            "nodes": [
                {"name": name, "status": "WARNING", "slp_functional_type": "TEST"} for name in names
            ]
        },
    }


def ci(
    *,
    pending: int = 0,
    failed: int = 1,
    review_groups: Sequence[dict[str, object]] = (),
) -> dict[str, object]:
    nodes = (
        [
            {
                "name": "synthetic-unit-test",
                "status": "FAILED",
                "slp_functional_type": "TEST",
            }
        ]
        if failed
        else []
    )
    return {
        "signalview_signals": {
            "all": {"count": max(1, pending + failed)},
            "failed": {"count": failed, "nodes": nodes},
            "pending": {"count": pending},
            "reviews": {"nodes": list(review_groups)},
        }
    }


def arctic(
    *,
    title: str = "unset optional dereference",
    resolution: str = "unresolved",
) -> list[dict[str, object]]:
    return [
        {
            "insight_type": "spotlight",
            "title": title,
            "severity": "warning",
            "file": "src/thing.cpp",
            "lines": "10-12",
            "resolution": resolution,
            "details": "detail text",
        }
    ]


class RecordingRunner:
    def __init__(
        self,
        comments_result: object,
        ci_result: object,
        arctic_result: object = None,
    ) -> None:
        self.comments_result = comments_result
        self.ci_result = ci_result
        self.arctic_result: object = [] if arctic_result is None else arctic_result
        self.calls: list[tuple[str, ...]] = []

    def call_for(self, *prefix: str) -> tuple[str, ...]:
        return next(call for call in self.calls if call[: len(prefix)] == prefix)

    async def __call__(self, argv: Sequence[str]) -> object:
        call = tuple(argv)
        self.calls.append(call)
        if call[:2] == ("jf", "diff-properties"):
            return properties()
        if call[:3] == ("meta", "phabricator.diff", "comments"):
            if isinstance(self.comments_result, Exception):
                raise self.comments_result
            return self.comments_result
        if call[:3] == ("meta", "phabricator.diff", "arctic"):
            if isinstance(self.arctic_result, Exception):
                raise self.arctic_result
            return self.arctic_result
        if call[:2] == ("jf", "graphql"):
            if isinstance(self.ci_result, Exception):
                raise self.ci_result
            return self.ci_result
        raise AssertionError(f"unexpected argv: {call}")


@pytest.mark.asyncio
async def test_adapter_uses_fixed_read_only_commands_and_filters_comments() -> None:
    runner = RecordingRunner(comments(), ci())
    snapshot = await PhabricatorReviewSource(runner=runner).snapshot("D90000001", None)

    assert snapshot.lifecycle is DiffLifecycle.ACTIVE
    assert snapshot.author_id == "author-synthetic"
    assert snapshot.latest_version_id == "version-7"
    assert [item.external_id for item in snapshot.comments.items] == ["comment-good"]
    assert snapshot.comments.items[0].content_fingerprint.startswith("sha256:")
    assert "Please fix" not in repr(snapshot.comments.items[0])
    assert snapshot.ci.aggregate is CIAggregateState.FAILING
    assert len(snapshot.ci.failures) == 1

    comments_call = next(call for call in runner.calls if call[0] == "meta")
    assert comments_call == (
        "meta",
        "phabricator.diff",
        "comments",
        "--number=D90000001",
        "--output=json",
        "--no-color",
        "--latest-version",
        "--skip-author",
        "--unresolved-only",
        "--no-suggestions",
    )
    graphql_call = next(call for call in runner.calls if call[:2] == ("jf", "graphql"))
    assert "expensive_signal_details" not in graphql_call[3]
    assert "detail" not in graphql_call[3]


@pytest.mark.asyncio
async def test_adapter_accepts_current_meta_comments_shape() -> None:
    current_shape = [
        {
            "id": "comment-current-cli",
            "author": "reviewer-synthetic",
            "content": "Current CLI comment",
            "created": "2026-07-19T12:02:00-07:00",
            "phabricator_version": "123456789",
            "resolved": "",
            "source": "user",
        }
    ]
    snapshot = await PhabricatorReviewSource(
        runner=RecordingRunner(current_shape, ci(failed=0))
    ).snapshot("D90000001", None)

    assert snapshot.comments.status == "ok"
    assert [item.external_id for item in snapshot.comments.items] == ["comment-current-cli"]
    assert snapshot.comments.items[0].updated_at.isoformat() == "2026-07-19T12:02:00-07:00"


@pytest.mark.asyncio
async def test_comment_and_ci_fail_independently() -> None:
    failure = SourceCommandError(SourceCommandErrorCategory.TIMEOUT, "safe timeout")
    comments_failed = await PhabricatorReviewSource(
        runner=RecordingRunner(failure, ci(failed=0))
    ).snapshot("D90000001", None)
    assert comments_failed.comments.status == "error"
    assert comments_failed.comments.error.category is SourceErrorCategory.TIMEOUT  # type: ignore[union-attr]
    assert comments_failed.ci.status == "ok"

    ci_failed = await PhabricatorReviewSource(runner=RecordingRunner(comments(), failure)).snapshot(
        "D90000001", None
    )
    assert ci_failed.comments.status == "ok"
    assert ci_failed.ci.status == "error"
    assert ci_failed.ci.error.category is SourceErrorCategory.TIMEOUT  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_failures_surface_while_the_rest_of_the_run_is_still_pending() -> None:
    """A wide test selection almost always has something pending.

    Holding failures until the whole run settles would therefore hide them for
    most of the run, which is exactly when they are worth knowing about.
    """
    snapshot = await PhabricatorReviewSource(
        runner=RecordingRunner([], ci(pending=2, failed=1))
    ).snapshot("D90000001", None)
    assert snapshot.ci.aggregate is CIAggregateState.PENDING
    assert len(snapshot.ci.failures) == 1
    assert not snapshot.ci.green


async def test_a_pending_run_reports_no_failures_when_none_have_landed() -> None:
    snapshot = await PhabricatorReviewSource(
        runner=RecordingRunner([], ci(pending=2, failed=0))
    ).snapshot("D90000001", None)
    assert snapshot.ci.aggregate is CIAggregateState.PENDING
    assert snapshot.ci.failures == ()
    assert not snapshot.ci.green


async def test_only_a_clean_finished_run_counts_as_green() -> None:
    """A red run already reports itself through failures.

    Treating it as a completion event as well would wake a session twice for
    one outcome.
    """
    passed = await PhabricatorReviewSource(
        runner=RecordingRunner([], ci(pending=0, failed=0))
    ).snapshot("D90000001", None)
    assert passed.ci.aggregate is CIAggregateState.PASSED
    assert passed.ci.green

    failing = await PhabricatorReviewSource(
        runner=RecordingRunner([], ci(pending=0, failed=1))
    ).snapshot("D90000001", None)
    assert failing.ci.aggregate is CIAggregateState.FAILING
    assert not failing.ci.green


@pytest.mark.asyncio
async def test_automated_reviewer_findings_are_collected_from_both_feeds() -> None:
    """RADAR reports at WARNING and Arctic never reaches signalview at all.

    Neither reaches the human comment stream, which filters automated authors,
    so both have to be read explicitly.
    """
    runner = RecordingRunner(
        [],
        ci(failed=0, review_groups=[review_group("RADAR Reviewer")]),
        arctic(),
    )
    snapshot = await PhabricatorReviewSource(runner=runner).snapshot("D90000001", None)

    assert snapshot.ai_reviews.status == "ok"
    prefixes = sorted(item.external_id.split(":")[0] for item in snapshot.ai_reviews.items)
    assert prefixes == ["arctic", "review"]
    assert runner.call_for("meta", "phabricator.diff", "arctic") == (
        "meta",
        "phabricator.diff",
        "arctic",
        "--number=D90000001",
        "--insight-type=spotlight",
        "--output=json",
    )


@pytest.mark.asyncio
async def test_coverage_warnings_are_not_mistaken_for_automated_review() -> None:
    """The signal list is mostly warning-level noise; only the group matters."""
    snapshot = await PhabricatorReviewSource(
        runner=RecordingRunner(
            [],
            ci(failed=0, review_groups=[noise_group("pkg:thing_needed_coverage")]),
        )
    ).snapshot("D90000001", None)
    assert snapshot.ai_reviews.items == ()


@pytest.mark.asyncio
async def test_arctic_findings_the_author_already_handled_are_dropped() -> None:
    snapshot = await PhabricatorReviewSource(
        runner=RecordingRunner([], ci(failed=0), arctic(resolution="confirmed_addressed"))
    ).snapshot("D90000001", None)
    assert snapshot.ai_reviews.items == ()


@pytest.mark.asyncio
async def test_a_reviewer_finding_changes_identity_when_its_verdict_changes() -> None:
    async def fingerprints(status: str) -> str:
        snapshot = await PhabricatorReviewSource(
            runner=RecordingRunner(
                [],
                ci(failed=0, review_groups=[review_group("RADAR Reviewer", status=status)]),
            )
        ).snapshot("D90000001", None)
        return snapshot.ai_reviews.items[0].fingerprint

    assert await fingerprints("WARNING") != await fingerprints("FAILED")


@pytest.mark.asyncio
async def test_one_unreadable_reviewer_feed_holds_the_whole_component() -> None:
    """Advancing on a partial read would mark the unread feed's findings gone."""
    snapshot = await PhabricatorReviewSource(
        runner=RecordingRunner(
            [],
            ci(failed=0, review_groups=[review_group("RADAR Reviewer")]),
            SourceCommandError(SourceCommandErrorCategory.TIMEOUT, "safe timeout"),
        )
    ).snapshot("D90000001", None)
    assert snapshot.ai_reviews.status == "error"
    assert snapshot.ai_reviews.items == ()
    assert snapshot.ci.status == "ok"


@pytest.mark.asyncio
async def test_top_level_malformed_and_missing_are_typed() -> None:
    async def malformed(_argv: Sequence[str]) -> object:
        return {}

    with pytest.raises(ReviewSourceError) as exc_info:
        await PhabricatorReviewSource(runner=malformed).snapshot("D90000001", None)
    assert exc_info.value.category is SourceErrorCategory.MALFORMED

    async def missing(_argv: Sequence[str]) -> object:
        return {"not_found": True, "diff": None}

    snapshot = await PhabricatorReviewSource(runner=missing).snapshot("D90000001", None)
    assert snapshot.lifecycle is DiffLifecycle.MISSING
    assert snapshot.comments.error.category is SourceErrorCategory.MISSING  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_terminal_diff_skips_comment_and_ci_queries() -> None:
    calls: list[tuple[str, ...]] = []

    async def terminal(argv: Sequence[str]) -> object:
        call = tuple(argv)
        calls.append(call)
        assert call[:2] == ("jf", "diff-properties")
        return properties(status="Committed")

    snapshot = await PhabricatorReviewSource(runner=terminal).snapshot("D90000001", None)

    assert snapshot.lifecycle is DiffLifecycle.COMMITTED
    assert snapshot.comments.items == ()
    assert snapshot.ci.aggregate is CIAggregateState.SKIPPED
    assert snapshot.ai_reviews.items == ()
    # A landed diff is not a run that reached a verdict, so it must not wake a
    # session with "CI settled".
    assert not snapshot.ci.green
    assert calls == [("jf", "diff-properties", "D90000001")]


@pytest.mark.asyncio
async def test_invalid_diff_id_is_rejected_before_any_command() -> None:
    called = False

    async def runner(_argv: Sequence[str]) -> object:
        nonlocal called
        called = True
        return {}

    with pytest.raises(ValueError, match="D<number>"):
        await PhabricatorReviewSource(runner=runner).snapshot("D1; rm -rf /", None)
    assert called is False


def test_bounded_environment_is_an_allowlist() -> None:
    source = {
        "PATH": "/bin",
        "HOME": "/synthetic/home",
        "USER": "synthetic",
        "USERNAME": "synthetic",
        "SECRET_TOKEN": "must-not-leak",
    }
    assert bounded_source_environment(source) == {
        "PATH": "/bin",
        "HOME": "/synthetic/home",
        "USER": "synthetic",
        "USERNAME": "synthetic",
    }


def test_bounded_environment_keeps_the_meta_x509_identity() -> None:
    # `meta` reads the caller's identity only from these two. Dropping them
    # costs the two meta-backed kinds their baseline while the jf-backed CI
    # kinds still bind, which reads as a diff problem rather than an auth one.
    source = {
        "PATH": "/bin",
        "THRIFT_TLS_CL_CERT_PATH": "/synthetic/identity.pem",
        "THRIFT_TLS_CL_KEY_PATH": "/synthetic/identity.pem",
    }
    assert bounded_source_environment(source) == source


def test_metadata_created_time_does_not_make_old_diff_look_new() -> None:
    from omnigent_watcher.phabricator_source import (
        _parse_metadata,
    )

    observed = datetime(2026, 7, 19, tzinfo=UTC)
    metadata = _parse_metadata("D90000001", properties(), observed)
    assert metadata.last_activity_at < observed - timedelta(days=100)


class StatusRunner(RecordingRunner):
    """A diff in a given review status, at a given latest version."""

    def __init__(self, ci_result: object, *, status: str = "Needs Review", version: str = "version-7") -> None:
        super().__init__([], ci_result)
        self.status = status
        self.version = version

    async def __call__(self, argv: Sequence[str]) -> object:
        if tuple(argv[:2]) == ("jf", "diff-properties"):
            self.calls.append(tuple(argv))
            return {
                **properties(status=self.status),
                "latest_phabricator_version": {"id": self.version, "number": 7},
            }
        return await super().__call__(argv)


def no_signals() -> dict[str, object]:
    return {
        "signalview_signals": {
            "all": {"count": 0},
            "failed": {"count": 0, "nodes": []},
            "pending": {"count": 0},
            "reviews": {"nodes": []},
        }
    }


def kinds_with_events(result) -> set[str]:
    return {kind.value for kind, events in result.events.items() if events}


def aged(cursor: str, minutes: int) -> str:
    """The same cursor, as if its version had been first seen MINUTES earlier."""
    import json

    doc = json.loads(cursor)
    seen = datetime.fromisoformat(doc["version_seen_at"])
    doc["version_seen_at"] = (seen - timedelta(minutes=minutes)).isoformat()
    return json.dumps(doc)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("Accepted", {"accepted"}),
        ("Needs Revision", {"changes_requested"}),
        ("Changes Planned", set()),
        ("Needs Review", set()),
    ],
)
async def test_a_reviewers_verdict_is_an_event_but_the_authors_plan_is_not(status, expected) -> None:
    result = await PhabricatorReviewSource(runner=StatusRunner(ci(failed=0), status=status)).poll("D90000001", None)
    assert kinds_with_events(result) & {"accepted", "changes_requested"} == expected
    # Known on every read, so a quiet status is "nothing new", not "unreadable".
    assert {"accepted", "changes_requested"} <= {kind.value for kind in result.ok_kinds}


@pytest.mark.asyncio
async def test_ci_that_never_starts_is_reported_once_its_version_is_half_an_hour_old() -> None:
    source = PhabricatorReviewSource(runner=StatusRunner(no_signals()))
    first = await source.poll("D90000001", None)
    assert "ci_stalled" not in kinds_with_events(first)
    early = await source.poll("D90000001", aged(first.cursor, 29))
    assert "ci_stalled" not in kinds_with_events(early)
    # Polls of the same version keep the time it was first seen.
    import json

    def seen(cursor: str) -> datetime:
        return datetime.fromisoformat(json.loads(cursor)["version_seen_at"].replace("Z", "+00:00"))

    assert seen(early.cursor) == seen(aged(first.cursor, 29))
    late = await source.poll("D90000001", aged(first.cursor, 31))
    assert "ci_stalled" in kinds_with_events(late)


@pytest.mark.asyncio
async def test_a_new_version_restarts_the_stall_clock_and_any_signal_clears_it() -> None:
    old = await PhabricatorReviewSource(runner=StatusRunner(no_signals())).poll("D90000001", None)
    stale_cursor = aged(old.cursor, 60)
    amended = await PhabricatorReviewSource(runner=StatusRunner(no_signals(), version="version-8")).poll(
        "D90000001", stale_cursor
    )
    assert "ci_stalled" not in kinds_with_events(amended)
    started = await PhabricatorReviewSource(runner=StatusRunner(ci(pending=3, failed=0))).poll(
        "D90000001", stale_cursor
    )
    assert "ci_stalled" not in kinds_with_events(started)
