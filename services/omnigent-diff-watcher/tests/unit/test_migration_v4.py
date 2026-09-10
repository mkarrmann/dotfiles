"""v3 -> v4: every live subscription gains the request row that declares it.

Watches used to be declared two ways: generic ones through ``watch_requests``,
diff ones through session labels the sidecar swept. The label sweep is gone, so
a subscription with no request row has nothing to re-bind it and would stop at
the next restart -- silently, which is the one failure a notifier must not have.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from omnigent_diff_watcher.repository import SCHEMA_VERSION, WatcherRepository


def _v3_database(path: Path) -> None:
    """A v3 database holding one label-declared diff watch and one generic one."""
    repository = WatcherRepository(path)
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        DELETE FROM watch_requests;
        INSERT INTO watched_subjects
            (subject, lifecycle, last_activity_at, next_poll_at, source_status, source, spec)
        VALUES
            ('D90000001', 'active', 1000.0, 1060.0, 'passed', 'phabricator', NULL),
            ('jk:demo', 'active', 1000.0, 1060.0, 'ok', 'command', '{"argv": ["true"]}');
        INSERT INTO subscriptions
            (session_id, subject, event_types, state, baseline_at, last_liveness_at,
             created_at, updated_at)
        VALUES
            ('conv_a', 'D90000001', '["ci_failure"]', 'active', 900.0, 900.0, 900.0, 950.0),
            ('conv_b', 'D90000001', '["ci_green"]', 'active', 900.0, 900.0, 900.0, 950.0),
            ('conv_a', 'jk:demo', '["changed"]', 'active', 900.0, 900.0, 900.0, 950.0),
            ('conv_c', 'D90000002', '["ci_failure"]', 'retired', 900.0, 900.0, 900.0, 950.0);
        INSERT INTO watch_requests
            (session_id, source, subject, spec, event_types, state, created_at, updated_at)
        VALUES ('conv_a', 'command', 'jk:demo', '{"argv": ["true"]}', '["changed"]',
                'active', 900.0, 950.0);
        PRAGMA user_version=3;
        """
    )
    connection.commit()
    connection.close()
    del repository


def test_a_label_declared_diff_watch_gains_a_request_row(tmp_path: Path) -> None:
    path = tmp_path / "watcher.sqlite3"
    _v3_database(path)

    repository = WatcherRepository(path)

    assert repository.schema_version() == SCHEMA_VERSION
    rows = {
        (session, subject): (source, spec, kinds)
        for session, source, subject, spec, kinds in repository.active_watch_requests()
    }
    # Both subscribers of the diff get their own row; event types are preserved.
    assert rows[("conv_a", "D90000001")][0] == "phabricator"
    assert {k.value for k in rows[("conv_a", "D90000001")][2]} == {"ci_failure"}
    assert {k.value for k in rows[("conv_b", "D90000001")][2]} == {"ci_green"}
    # A retired subscription is not resurrected.
    assert ("conv_c", "D90000002") not in rows


def test_the_generic_watch_that_already_had_a_row_is_untouched(tmp_path: Path) -> None:
    """Backfilling must not duplicate or rewrite an existing declaration."""
    path = tmp_path / "watcher.sqlite3"
    _v3_database(path)

    WatcherRepository(path)

    connection = sqlite3.connect(path)
    rows = connection.execute(
        "SELECT spec, created_at FROM watch_requests WHERE session_id='conv_a' "
        "AND subject='jk:demo'"
    ).fetchall()
    connection.close()
    assert len(rows) == 1
    assert json.loads(rows[0][0]) == {"argv": ["true"]}
    assert rows[0][1] == 900.0


def test_migrating_twice_changes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "watcher.sqlite3"
    _v3_database(path)

    WatcherRepository(path)
    first = sorted(WatcherRepository(path).active_watch_requests())
    WatcherRepository(path)
    assert sorted(WatcherRepository(path).active_watch_requests()) == first


def test_a_backfilled_watch_actually_rebinds(tmp_path: Path) -> None:
    """The point of the backfill: reconciliation must find it.

    A row that exists but is not picked up by ``active_watch_requests`` would
    leave the watch just as dead as no row at all.
    """
    path = tmp_path / "watcher.sqlite3"
    _v3_database(path)

    repository = WatcherRepository(path)
    subjects = {subject for _s, _src, subject, _spec, _k in repository.active_watch_requests()}
    assert subjects == {"D90000001", "jk:demo"}


def test_a_cancelled_request_is_not_revived(tmp_path: Path) -> None:
    """A watch the session already stopped must stay stopped.

    The orphan state -- cancelled request, subscription that outlived it -- was
    a real bug in the unsubscribe tool. The backfill deliberately matches on the
    request row regardless of its state, so an orphan gets no new declaration
    and lapses at the next restart. That is the same outcome the user asked for
    when they unsubscribed; reviving it would resurrect a watch they stopped.
    """
    path = tmp_path / "watcher.sqlite3"
    _v3_database(path)
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        UPDATE watch_requests SET state='cancelled' WHERE subject='jk:demo';
        PRAGMA user_version=3;
        """
    )
    connection.commit()
    connection.close()

    repository = WatcherRepository(path)

    subjects = {subject for _s, _src, subject, _spec, _k in repository.active_watch_requests()}
    assert "jk:demo" not in subjects
    assert "D90000001" in subjects
