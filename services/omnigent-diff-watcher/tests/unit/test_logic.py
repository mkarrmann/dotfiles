from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from omnigent_diff_watcher.domain import EventKind
from omnigent_diff_watcher.logic import (
    deterministic_jitter,
    failure_poll_delay,
    render_batch_summary,
    successful_poll_delay,
)
from tests.support import fixture


@pytest.mark.parametrize(
    ("idle", "expected"),
    [
        (timedelta(minutes=59), 60),
        (timedelta(hours=1), 300),
        (timedelta(hours=6), 900),
        (timedelta(days=1), 3600),
        (timedelta(days=3), 21600),
        (timedelta(days=14), 86400),
    ],
)
def test_adaptive_interval_boundaries(idle: timedelta, expected: float) -> None:
    now = datetime(2026, 1, 20, tzinfo=UTC)
    snapshot = fixture("green").model_copy(update={"last_activity_at": now - idle})
    assert successful_poll_delay(snapshot, now) == expected


def test_pending_ci_stays_at_one_minute() -> None:
    snapshot = fixture("active")
    now = snapshot.last_activity_at + timedelta(days=30)
    assert successful_poll_delay(snapshot, now) == 60


def test_jitter_is_stable_and_bounded() -> None:
    first = deterministic_jitter(100, "D90000001", 7)
    assert first == deterministic_jitter(100, "D90000001", 7)
    assert 90 <= first <= 110
    assert first != deterministic_jitter(100, "D90000001", 8)


def test_failure_backoff_sequence_and_cap() -> None:
    bases = (60, 120, 300, 900, 1800, 1800)
    for count, base in enumerate(bases, 1):
        delay = failure_poll_delay(count, "D90000001")
        assert base * 0.9 <= delay <= base * 1.1


def test_summary_is_concise_and_contains_no_raw_detail() -> None:
    summary = render_batch_summary(
        "dwb_test",
        [("D90000001", {EventKind.REVIEW_COMMENT: 2, EventKind.CI_FAILURE: 1})],
    )
    assert summary.startswith("[Diff watcher dwb_test] D90000001")
    assert "2 unresolved review comments" in summary
    assert "1 current-version CI failure" in summary
    assert "http" not in summary
    assert len(summary) < 300


def test_summary_covers_every_affected_diff_in_one_wake() -> None:
    """A stack going red is one wake, not one per diff."""
    summary = render_batch_summary(
        "dwb_test",
        [
            ("D115903821", {EventKind.CI_FAILURE: 2}),
            ("D115903819", {EventKind.REVIEW_COMMENT: 1}),
        ],
    )
    assert "D115903821 has 2 current-version CI failures" in summary
    assert "D115903819 has 1 unresolved review comment" in summary
    assert "update each diff as needed" in summary
    assert "http" not in summary


def test_summary_names_automated_review_findings_distinctly() -> None:
    """An automated finding must not read as a human comment or a CI failure."""
    summary = render_batch_summary(
        "dwb_test",
        [("D115903819", {EventKind.AI_REVIEW: 2})],
    )
    assert "2 unresolved automated-review findings" in summary
    assert "review comment" not in summary
    assert "CI failure" not in summary


def test_summary_reports_a_green_run_without_inventing_a_count() -> None:
    summary = render_batch_summary("dwb_test", [("D115903819", {EventKind.CI_GREEN: 1})])
    assert "D115903819 has CI green" in summary
    assert "1 CI green" not in summary


def test_summary_joins_three_kinds_readably() -> None:
    summary = render_batch_summary(
        "dwb_test",
        [
            (
                "D115903819",
                {
                    EventKind.REVIEW_COMMENT: 1,
                    EventKind.AI_REVIEW: 1,
                    EventKind.CI_GREEN: 1,
                },
            )
        ],
    )
    assert (
        "1 unresolved review comment, 1 unresolved automated-review finding and CI green" in summary
    )


def test_summary_skips_diffs_with_no_findings() -> None:
    summary = render_batch_summary(
        "dwb_test",
        [("D115903821", {EventKind.CI_FAILURE: 1}), ("D115903820", {})],
    )
    assert "D115903820" not in summary
    assert "update the diff as needed" in summary


def test_summary_rejects_an_entirely_empty_batch() -> None:
    with pytest.raises(ValueError):
        render_batch_summary("dwb_test", [("D1", {EventKind.CI_FAILURE: 0})])
    with pytest.raises(ValueError):
        render_batch_summary("dwb_test", [])
