"""v2 -> v3: the watch key becomes a source-owned subject.

The v2 tests already run the whole 1 -> 3 chain, but they only inspect batches.
These pin the part v3 actually changes: that renaming the key preserved every
row in the tables that carry it, and that existing watches are attributed to
the source that has always produced them.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from omnigent_diff_watcher.repository import V1_SCHEMA, WatcherRepository


def _populated_v1_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(V1_SCHEMA)
        connection.executescript(
            """
            BEGIN IMMEDIATE;
            INSERT INTO watched_diffs
                (diff_id, lifecycle, latest_version_id, last_activity_at,
                 next_poll_at, ci_state, comments_cursor, failure_count)
            VALUES ('D90000001', 'active', 'v7', 100.0, 160.0, 'failing',
                    'cursor-abc', 2);
            INSERT INTO subscriptions
                (id, session_id, diff_id, event_types, state, baseline_at,
                 last_liveness_at, created_at, updated_at)
            VALUES (1, 'conv_a', 'D90000001', '["ci_failure"]', 'active',
                    100.0, 100.0, 100.0, 100.0);
            INSERT INTO source_events
                (diff_id, kind, external_id, version_id, fingerprint,
                 actionable, first_seen_at, last_changed_at, last_seen_at)
            VALUES ('D90000001', 'ci_failure', 'sig-1', 'v7', 'fp-1', 1,
                    100.0, 100.0, 100.0);
            COMMIT;
            """
        )
    finally:
        connection.close()


def test_v3_renames_the_key_without_losing_rows(tmp_path: Path) -> None:
    path = tmp_path / "watcher.sqlite3"
    _populated_v1_database(path)

    repository = WatcherRepository(path)
    assert repository.schema_version() == 3

    connection = sqlite3.connect(path)
    try:
        watch = connection.execute(
            "SELECT subject, source, lifecycle, latest_version_id, "
            "comments_cursor, failure_count FROM watched_subjects"
        ).fetchall()
        # Every pre-existing watch is a Phabricator diff by construction, and
        # its polling state must survive verbatim -- a migration should not
        # reset a backoff streak or re-fetch from a lost cursor.
        assert watch == [("D90000001", "phabricator", "active", "v7", "cursor-abc", 2)]

        assert connection.execute("SELECT session_id, subject FROM subscriptions").fetchall() == [
            ("conv_a", "D90000001")
        ]
        assert connection.execute(
            "SELECT subject, kind, external_id, fingerprint FROM source_events"
        ).fetchall() == [("D90000001", "ci_failure", "sig-1", "fp-1")]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def test_v3_leaves_no_diff_named_columns_behind(tmp_path: Path) -> None:
    """A stale ``diff_id`` column would mean the rename half-applied, which the
    row-level assertions above cannot see."""
    path = tmp_path / "watcher.sqlite3"
    _populated_v1_database(path)
    WatcherRepository(path)

    connection = sqlite3.connect(path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert "watched_subjects" in tables
        assert "watched_diffs" not in tables
        for table in ("watched_subjects", "subscriptions", "source_events", "batch_events"):
            columns = {
                row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            assert "diff_id" not in columns, table
            assert "subject" in columns, table
    finally:
        connection.close()


def test_v3_is_idempotent_across_reopens(tmp_path: Path) -> None:
    path = tmp_path / "watcher.sqlite3"
    _populated_v1_database(path)

    WatcherRepository(path)
    reopened = WatcherRepository(path)

    assert reopened.schema_version() == 3
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("SELECT subject, source FROM watched_subjects").fetchall() == [
            ("D90000001", "phabricator")
        ]
    finally:
        connection.close()


def test_a_non_owner_refuses_to_migrate_a_stale_database(tmp_path: Path) -> None:
    """The MCP tool must not migrate the database.

    The sidecar is a long-running process with its old code already in memory.
    A migration run by anything else renames tables out from under it, and it
    then fails every poll until someone restarts it. Refusing is recoverable.
    """
    from omnigent_diff_watcher.repository import StaleSchemaError

    path = tmp_path / "watcher.sqlite3"
    _populated_v1_database(path)

    with pytest.raises(StaleSchemaError, match="Restart the diff-watcher service"):
        WatcherRepository(path, migrate=False)

    # Untouched: still v1, so the owner can still migrate it correctly later.
    connection = sqlite3.connect(path)
    try:
        assert int(connection.execute("PRAGMA user_version").fetchone()[0]) == 1
    finally:
        connection.close()

    WatcherRepository(path)
    assert WatcherRepository(path, migrate=False).schema_version() == 3
