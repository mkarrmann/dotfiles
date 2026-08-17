"""v1 -> v2 migration: batches move from subscription-scoped to session-scoped."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from omnigent_diff_watcher.repository import V1_SCHEMA, WatcherRepository


def _v1_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(V1_SCHEMA)
        connection.executescript(
            """
            BEGIN IMMEDIATE;
            INSERT INTO watched_diffs
                (diff_id, lifecycle, latest_version_id, last_activity_at,
                 next_poll_at, ci_state)
            VALUES ('D90000001', 'active', 'v1', 100.0, 160.0, 'failing');
            INSERT INTO subscriptions
                (id, session_id, diff_id, event_types, state, baseline_at,
                 last_liveness_at, created_at, updated_at)
            VALUES (1, 'conv_a', 'D90000001', '["ci_failure"]', 'active',
                    100.0, 100.0, 100.0, 100.0);
            INSERT INTO batches
                (batch_id, subscription_id, diff_id, state, first_event_at,
                 flush_at, next_attempt_at, created_at, updated_at)
            VALUES ('dwb_old', 1, 'D90000001', 'open', 110.0, 410.0, 410.0,
                    110.0, 110.0);
            INSERT INTO batch_events
                (batch_id, diff_id, kind, external_id, fingerprint)
            VALUES ('dwb_old', 'D90000001', 'ci_failure', 'sig-1', 'fp-1');
            COMMIT;
            """
        )
    finally:
        connection.close()


def test_v1_batches_are_rekeyed_to_their_session(tmp_path: Path) -> None:
    path = tmp_path / "watcher.sqlite3"
    _v1_database(path)

    repository = WatcherRepository(path)

    assert repository.schema_version() == 2
    batch = repository.open_batch_for_session("conv_a")
    assert batch is not None
    assert batch.batch_id == "dwb_old"
    assert batch.session_id == "conv_a"
    assert batch.diff_ids == ("D90000001",)
    # Scheduling state must survive verbatim; a migration should not re-time a
    # pending wake.
    assert batch.flush_at == 410.0
    assert batch.first_event_at == 110.0


def test_v1_batch_events_survive_the_primary_key_change(tmp_path: Path) -> None:
    path = tmp_path / "watcher.sqlite3"
    _v1_database(path)
    WatcherRepository(path)

    connection = sqlite3.connect(path)
    try:
        rows = connection.execute(
            "SELECT batch_id, diff_id, kind, external_id, fingerprint FROM batch_events"
        ).fetchall()
        assert rows == [("dwb_old", "D90000001", "ci_failure", "sig-1", "fp-1")]
        columns = {row[1] for row in connection.execute("PRAGMA table_info(batches)").fetchall()}
        assert "session_id" in columns
        assert "subscription_id" not in columns
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def test_migration_is_idempotent_across_reopens(tmp_path: Path) -> None:
    path = tmp_path / "watcher.sqlite3"
    _v1_database(path)

    WatcherRepository(path)
    reopened = WatcherRepository(path)

    assert reopened.schema_version() == 2
    batch = reopened.open_batch_for_session("conv_a")
    assert batch is not None and batch.diff_ids == ("D90000001",)


def test_fresh_database_lands_on_v2_directly(tmp_path: Path) -> None:
    repository = WatcherRepository(tmp_path / "fresh.sqlite3")
    assert repository.schema_version() == 2
    assert repository.open_batch_for_session("conv_missing") is None
