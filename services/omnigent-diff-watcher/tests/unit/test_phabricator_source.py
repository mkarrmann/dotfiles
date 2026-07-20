from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest

from omnigent_diff_watcher.phabricator_source import (
    PhabricatorReviewSource,
    ReviewSourceError,
    bounded_source_environment,
)
from omnigent_diff_watcher.source_command import (
    SourceCommandError,
    SourceCommandErrorCategory,
)
from omnigent_diff_watcher.source_models import (
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


def ci(*, pending: int = 0, failed: int = 1) -> dict[str, object]:
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
        }
    }


class RecordingRunner:
    def __init__(self, comments_result: object, ci_result: object) -> None:
        self.comments_result = comments_result
        self.ci_result = ci_result
        self.calls: list[tuple[str, ...]] = []

    async def __call__(self, argv: Sequence[str]) -> object:
        call = tuple(argv)
        self.calls.append(call)
        if call[:2] == ("jf", "diff-properties"):
            return properties()
        if call[:3] == ("meta", "phabricator.diff", "comments"):
            if isinstance(self.comments_result, Exception):
                raise self.comments_result
            return self.comments_result
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
async def test_pending_precedes_failure_until_ci_is_terminal() -> None:
    snapshot = await PhabricatorReviewSource(
        runner=RecordingRunner([], ci(pending=2, failed=1))
    ).snapshot("D90000001", None)
    assert snapshot.ci.aggregate is CIAggregateState.PENDING
    assert snapshot.ci.failures == ()


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


def test_metadata_created_time_does_not_make_old_diff_look_new() -> None:
    from omnigent_diff_watcher.phabricator_source import (
        _parse_metadata,
    )

    observed = datetime(2026, 7, 19, tzinfo=UTC)
    metadata = _parse_metadata("D90000001", properties(), observed)
    assert metadata.last_activity_at < observed - timedelta(days=100)
