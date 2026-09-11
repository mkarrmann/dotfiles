"""v4 -> v5: no schema-level assumption that a subject is a diff."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from omnigent_diff_watcher.repository import SCHEMA_VERSION, WatcherRepository


def _v4_database(path: Path) -> None:
    WatcherRepository(path)
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        INSERT INTO watched_subjects
            (subject, lifecycle, last_activity_at, next_poll_at, source_status,
             source, cursor, spec, failure_count, missing_count, last_success_at)
        VALUES ('D90000001', 'active', 1000.0, 1060.0, 'passed', 'phabricator',
                '{"comments": "c-1"}', NULL, 3, 1, 990.0);
        PRAGMA user_version=4;
        """
    )
    connection.commit()
    connection.close()


def test_the_source_column_no_longer_defaults_to_a_diff(tmp_path: Path) -> None:
    """A row that forgets to say what it is must fail, not become a diff."""
    path = tmp_path / "watcher.sqlite3"
    _v4_database(path)

    repository = WatcherRepository(path)
    assert repository.schema_version() == SCHEMA_VERSION

    connection = sqlite3.connect(path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO watched_subjects (subject, lifecycle, last_activity_at, "
                "next_poll_at, source_status) VALUES ('x:1', 'active', 1.0, 2.0, 'ok')"
            )
    finally:
        connection.close()


def test_polling_state_survives_the_rebuild(tmp_path: Path) -> None:
    path = tmp_path / "watcher.sqlite3"
    _v4_database(path)

    WatcherRepository(path)

    connection = sqlite3.connect(path)
    try:
        assert connection.execute(
            "SELECT subject, source, lifecycle, cursor, failure_count, missing_count, "
            "last_success_at FROM watched_subjects"
        ).fetchall() == [("D90000001", "phabricator", "active", '{"comments": "c-1"}', 3, 1, 990.0)]
    finally:
        connection.close()


def test_the_legacy_diff_shaped_cursor_columns_are_gone(tmp_path: Path) -> None:
    """v3 stopped reading them and left them in place; they are diff-specific
    names on a table that must not know what a diff is."""
    path = tmp_path / "watcher.sqlite3"
    _v4_database(path)

    WatcherRepository(path)

    connection = sqlite3.connect(path)
    try:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(watched_subjects)").fetchall()
        }
    finally:
        connection.close()
    assert "comments_cursor" not in columns
    assert "ci_cursor" not in columns
    assert "cursor" in columns


def test_a_preexisting_orphan_does_not_block_the_migration(tmp_path: Path) -> None:
    """Failing on an inherited violation would crash-loop the sidecar forever."""
    path = tmp_path / "watcher.sqlite3"
    _v4_database(path)
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        INSERT INTO subscriptions
            (session_id, subject, event_types, state, baseline_at, last_liveness_at,
             created_at, updated_at)
        VALUES ('conv_a', 'D-orphan', '["ci_failure"]', 'active', 1.0, 1.0, 1.0, 1.0);
        PRAGMA user_version=4;
        """
    )
    connection.commit()
    connection.close()

    assert WatcherRepository(path).schema_version() == SCHEMA_VERSION
