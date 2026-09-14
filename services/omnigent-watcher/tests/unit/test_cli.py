"""Operator status must not perform an upgrade against a running worker."""

from __future__ import annotations

import json
import sqlite3
import stat
from contextlib import closing
from pathlib import Path

import pytest

from omnigent_watcher.cli import main
from omnigent_watcher.repository import SCHEMA_VERSION, V1_SCHEMA, WatcherRepository


class _SchemaFiveRepository(WatcherRepository):
    def _migrate(self) -> None:
        with self._connect() as connection:
            connection.executescript(V1_SCHEMA)
        self._migrate_to_session_batches()
        self._migrate_to_generic_subjects()
        self._migrate_to_request_declared_watches()
        self._migrate_to_sourceless_defaults()


def _seed(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        INSERT INTO watched_subjects
            (subject, source, lifecycle, last_activity_at, next_poll_at, source_status,
             failure_count)
        VALUES ('D90000001', 'phabricator', 'active', 1000, 1060, 'pending', 2);
        INSERT INTO subscriptions
            (session_id, subject, event_types, state, baseline_at, last_liveness_at,
             created_at, updated_at)
        VALUES ('existing-session', 'D90000001', '["ci_failure"]', 'active',
                900, 950, 900, 950);
        INSERT INTO watch_requests
            (session_id, source, subject, event_types, state, created_at, updated_at)
        VALUES ('existing-session', 'phabricator', 'D90000001', '["ci_failure"]',
                'active', 900, 950);
        """
    )


def _main_file_and_contents(path: Path) -> tuple[bytes, int, str]:
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        contents = "\n".join(connection.iterdump())
    return path.read_bytes(), stat.S_IMODE(path.stat().st_mode), contents


def _config(tmp_path: Path, database: Path) -> Path:
    config = tmp_path / "config.toml"
    config.write_text(f"database_path = {json.dumps(str(database))}\n")
    return config


def test_status_does_not_create_a_missing_database_or_parent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "absent" / "watcher.sqlite3"
    main(["--config", str(_config(tmp_path, database)), "status", "--json"])
    assert json.loads(capsys.readouterr().out) == {
        "database_path": str(database),
        "expected_schema_version": SCHEMA_VERSION,
        "schema_version": None,
        "status": "missing",
    }
    assert not database.parent.exists()


def test_status_reports_current_counts_without_changing_the_database(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "watcher ?# database.sqlite3"
    WatcherRepository(database)
    with closing(sqlite3.connect(database)) as connection:
        _seed(connection)
    database.chmod(0o640)
    before = _main_file_and_contents(database)

    main(["--config", str(_config(tmp_path, database)), "status", "--json"])
    assert json.loads(capsys.readouterr().out) == {
        "database_path": str(database),
        "expected_schema_version": SCHEMA_VERSION,
        "schema_version": SCHEMA_VERSION,
        "status": "current",
        "subscriptions_active": 1,
        "subscriptions_suspended": 0,
        "subscriptions_retired": 0,
        "watched_subjects": 1,
        "open_batches": 0,
        "source_failed_watches": 1,
        "source_failure_streak": 2,
    }
    assert _main_file_and_contents(database) == before


@pytest.mark.parametrize("legacy", [False, True], ids=["current-name", "legacy-name"])
def test_status_reports_schema_five_without_migration_or_legacy_adoption(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], legacy: bool
) -> None:
    configured = tmp_path / "watcher.sqlite3"
    database = tmp_path / "diff-watcher.sqlite3" if legacy else configured
    _SchemaFiveRepository(database)
    with closing(sqlite3.connect(database)) as connection:
        _seed(connection)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
        assert "generation" not in {
            row[1] for row in connection.execute("PRAGMA table_info(source_events)")
        }
    database.chmod(0o640)
    before = _main_file_and_contents(database)

    main(["--config", str(_config(tmp_path, configured)), "status", "--json"])
    assert json.loads(capsys.readouterr().out) == {
        "database_path": str(database),
        "expected_schema_version": SCHEMA_VERSION,
        "schema_version": 5,
        "status": "upgrade_required",
    }
    assert _main_file_and_contents(database) == before
    if legacy:
        assert not configured.exists()


def test_status_reads_committed_wal_updates_without_checkpointing_them(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "watcher.sqlite3"
    WatcherRepository(database)
    with closing(sqlite3.connect(database)) as writer:
        _seed(writer)
        assert database.with_name(database.name + "-wal").stat().st_size > 0
        before = _main_file_and_contents(database)

        main(["--config", str(_config(tmp_path, database)), "status", "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert payload["schema_version"] == SCHEMA_VERSION
        assert payload["subscriptions_active"] == 1
        assert _main_file_and_contents(database) == before


def test_plain_status_reports_the_required_upgrade_without_mutation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "watcher.sqlite3"
    _SchemaFiveRepository(database)
    before = _main_file_and_contents(database)
    main(["--config", str(_config(tmp_path, database)), "status"])
    output = capsys.readouterr().out
    assert "schema_version: 5\n" in output
    assert f"expected_schema_version: {SCHEMA_VERSION}\n" in output
    assert "status: upgrade_required\n" in output
    assert _main_file_and_contents(database) == before
