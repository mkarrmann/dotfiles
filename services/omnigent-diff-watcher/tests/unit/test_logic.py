from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

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
    summary = render_batch_summary("dwb_test", "D90000001", 2, 1)
    assert summary.startswith("[Diff watcher dwb_test] D90000001")
    assert "2 unresolved review comments" in summary
    assert "1 current-version CI failure" in summary
    assert "http" not in summary
    assert len(summary) < 300
