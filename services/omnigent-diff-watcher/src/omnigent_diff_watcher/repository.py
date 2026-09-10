"""Plugin-owned SQLite persistence for watches, events, and wake batches."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path

from .domain import (
    Batch,
    BatchState,
    EventKind,
    Lifecycle,
    NormalizedEvent,
    PollResult,
    Subscription,
    SubscriptionState,
    WatchedSubject,
)

SCHEMA_VERSION = 3

# The v1 DDL, kept as a constant so the v1 -> v2 migration test exercises the
# real historical schema instead of a copy that can drift from it.
V1_SCHEMA = """
    BEGIN IMMEDIATE;
    CREATE TABLE watched_diffs (
        diff_id TEXT PRIMARY KEY,
        lifecycle TEXT NOT NULL,
        latest_version_id TEXT,
        last_activity_at REAL NOT NULL,
        next_poll_at REAL NOT NULL,
        comments_cursor TEXT,
        ci_cursor TEXT,
        ci_state TEXT NOT NULL,
        failure_count INTEGER NOT NULL DEFAULT 0,
        lease_owner TEXT,
        lease_until REAL,
        last_success_at REAL,
        missing_count INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        diff_id TEXT NOT NULL,
        event_types TEXT NOT NULL,
        state TEXT NOT NULL,
        baseline_at REAL NOT NULL,
        last_liveness_at REAL NOT NULL,
        unavailable_since REAL,
        last_delivery_at REAL,
        retired_reason TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        UNIQUE(session_id, diff_id),
        FOREIGN KEY(diff_id) REFERENCES watched_diffs(diff_id)
    );
    CREATE INDEX subscriptions_state_diff
        ON subscriptions(state, diff_id);
    CREATE TABLE source_events (
        diff_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        external_id TEXT NOT NULL,
        version_id TEXT NOT NULL,
        fingerprint TEXT NOT NULL,
        actionable INTEGER NOT NULL,
        first_seen_at REAL NOT NULL,
        last_changed_at REAL NOT NULL,
        last_seen_at REAL NOT NULL,
        PRIMARY KEY(diff_id, kind, external_id),
        FOREIGN KEY(diff_id) REFERENCES watched_diffs(diff_id)
    );
    CREATE TABLE batches (
        batch_id TEXT PRIMARY KEY,
        subscription_id INTEGER NOT NULL,
        diff_id TEXT NOT NULL,
        state TEXT NOT NULL,
        first_event_at REAL NOT NULL,
        flush_at REAL NOT NULL,
        retry_count INTEGER NOT NULL DEFAULT 0,
        next_attempt_at REAL NOT NULL,
        summary TEXT,
        delivered_at REAL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        FOREIGN KEY(subscription_id) REFERENCES subscriptions(id)
            ON DELETE CASCADE
    );
    CREATE UNIQUE INDEX one_open_batch_per_subscription
        ON batches(subscription_id)
        WHERE state IN ('open', 'delivering');
    CREATE INDEX batches_due
        ON batches(state, flush_at, next_attempt_at);
    CREATE TABLE subscription_events (
        subscription_id INTEGER NOT NULL,
        kind TEXT NOT NULL,
        external_id TEXT NOT NULL,
        fingerprint TEXT NOT NULL,
        handled_at REAL NOT NULL,
        PRIMARY KEY(subscription_id, kind, external_id, fingerprint),
        FOREIGN KEY(subscription_id) REFERENCES subscriptions(id)
            ON DELETE CASCADE
    );
    CREATE TABLE batch_events (
        batch_id TEXT NOT NULL,
        diff_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        external_id TEXT NOT NULL,
        fingerprint TEXT NOT NULL,
        PRIMARY KEY(batch_id, kind, external_id, fingerprint),
        FOREIGN KEY(batch_id) REFERENCES batches(batch_id)
            ON DELETE CASCADE
    );
    PRAGMA user_version=1;
    COMMIT;
"""


class NewerSchemaError(RuntimeError):
    """The database belongs to a newer plugin version."""


class StaleSchemaError(RuntimeError):
    """The database predates this build, and this caller may not migrate it."""


class SubscriptionConstraintError(RuntimeError):
    """A subscription would violate a watcher resource invariant."""


class WatcherRepository:
    """Short-transaction repository; external calls never run under its lock."""

    def __init__(self, path: Path, *, migrate: bool = True) -> None:
        """Open the watcher database.

        :param migrate: Whether this caller owns the schema. The sidecar does
            and passes the default; the MCP tool does not and passes ``False``.
            A migration run by anything other than the sidecar would rename
            tables out from under the *already running* sidecar process, whose
            old code is loaded in memory and would then fail every poll until
            restarted. Refusing is recoverable and legible; migrating is not.
        """
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if migrate:
            self._migrate()
        else:
            self._require_current_schema()
        with suppress(OSError):
            self.path.chmod(0o600)

    def _require_current_schema(self) -> None:
        with self._connect() as connection:
            current = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if current == SCHEMA_VERSION:
            return
        if current > SCHEMA_VERSION:
            raise NewerSchemaError(
                f"watcher schema {current} is newer than supported {SCHEMA_VERSION}"
            )
        raise StaleSchemaError(
            f"the watcher database is at schema {current}, but this build expects "
            f"{SCHEMA_VERSION}. Restart the diff-watcher service so it can migrate: "
            "systemctl --user restart omnigent-diff-watcher"
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _migrate(self) -> None:
        with self._connect() as connection:
            current = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current > SCHEMA_VERSION:
                raise NewerSchemaError(
                    f"watcher schema {current} is newer than supported {SCHEMA_VERSION}"
                )
            if current == 0:
                connection.executescript(V1_SCHEMA)
                current = 1
            if current < 2:
                self._migrate_to_session_batches()
                current = 2
            if current < 3:
                self._migrate_to_generic_subjects()

    def _migrate_to_session_batches(self) -> None:
        """v1 -> v2: re-key batches from one subscription to one session.

        A session may now watch a whole stack, and one wake should cover all of
        it rather than firing once per diff. ``batch_events.diff_id`` already
        carries the per-diff attribution, so the events survive untouched; only
        the owning key changes. ``diff_id`` also joins the batch_events primary
        key, since two diffs in one batch can carry the same external id.

        Foreign keys are disabled for the table rebuild (SQLite's documented
        procedure) and cannot be toggled inside a transaction, so this uses its
        own connection rather than ``_connect``.
        """
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=10000")
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE batches_v2 (
                    batch_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    first_event_at REAL NOT NULL,
                    flush_at REAL NOT NULL,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at REAL NOT NULL,
                    summary TEXT,
                    delivered_at REAL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                INSERT INTO batches_v2
                    (batch_id, session_id, state, first_event_at, flush_at,
                     retry_count, next_attempt_at, summary, delivered_at,
                     created_at, updated_at)
                SELECT b.batch_id, s.session_id, b.state, b.first_event_at,
                       b.flush_at, b.retry_count, b.next_attempt_at, b.summary,
                       b.delivered_at, b.created_at, b.updated_at
                FROM batches b JOIN subscriptions s ON s.id = b.subscription_id;
                CREATE TABLE batch_events_v2 (
                    batch_id TEXT NOT NULL,
                    diff_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    PRIMARY KEY(batch_id, diff_id, kind, external_id, fingerprint),
                    FOREIGN KEY(batch_id) REFERENCES batches(batch_id)
                        ON DELETE CASCADE
                );
                INSERT INTO batch_events_v2
                    (batch_id, diff_id, kind, external_id, fingerprint)
                SELECT be.batch_id, be.diff_id, be.kind, be.external_id,
                       be.fingerprint
                FROM batch_events be
                WHERE be.batch_id IN (SELECT batch_id FROM batches_v2);
                DROP TABLE batch_events;
                DROP TABLE batches;
                ALTER TABLE batches_v2 RENAME TO batches;
                ALTER TABLE batch_events_v2 RENAME TO batch_events;
                CREATE UNIQUE INDEX one_open_batch_per_session
                    ON batches(session_id)
                    WHERE state IN ('open', 'delivering');
                CREATE INDEX batches_due
                    ON batches(state, flush_at, next_attempt_at);
                PRAGMA user_version=2;
                COMMIT;
                """
            )
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(
                    f"diff-watcher v2 migration left {len(violations)} FK violations"
                )
        finally:
            connection.close()

    def _migrate_to_generic_subjects(self) -> None:
        """v2 -> v3: re-key watches from a diff id to a source-owned subject.

        The engine below this line — leasing, fingerprint diffing, batching,
        delivery — never depended on the subject being a diff. Only the naming
        did. This renames the key so a subject may be any watchable thing, and
        adds the ``source`` column that says who knows how to poll it. Existing
        rows are all Phabricator diffs by construction.

        The three diff-shaped state columns collapse into two source-owned
        ones: ``cursor`` holds whatever the source needs to resume (Phabricator
        keeps its per-section cursors there as JSON, backfilled here so no
        watch refetches from scratch), and ``spec`` holds how to poll a subject
        the source cannot derive from the subject alone -- the argv of a
        command watch. ``ci_state`` becomes ``source_status``: it was only ever
        written, never read, and is kept because it is NOT NULL and cheap to
        carry as a source-owned status string.

        ``watch_requests`` is the desired state for non-diff watches. Diff
        watches are reconciled from session labels, but a label value is capped
        at 256 characters, which an arbitrary argv overruns; a generic watch is
        recorded here by the MCP tool and reconciled from the table instead.

        The primary key stays ``subject`` alone rather than becoming
        ``(source, subject)``: three tables carry a foreign key to it, so a
        composite key would mean rebuilding four tables on a live database.
        Global uniqueness is preserved by construction instead — every source
        other than Phabricator namespaces its subjects as ``<source>:<id>``,
        which cannot collide with a bare ``D123``. See ``SUBJECT_PATTERN``.
        """
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=10000")
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                ALTER TABLE watched_diffs RENAME TO watched_subjects;
                ALTER TABLE watched_subjects RENAME COLUMN diff_id TO subject;
                ALTER TABLE watched_subjects RENAME COLUMN ci_state TO source_status;
                ALTER TABLE watched_subjects
                    ADD COLUMN source TEXT NOT NULL DEFAULT 'phabricator';
                ALTER TABLE watched_subjects ADD COLUMN cursor TEXT;
                ALTER TABLE watched_subjects ADD COLUMN spec TEXT;
                UPDATE watched_subjects SET cursor = json_object(
                    'latest_version_id', latest_version_id,
                    'comments', comments_cursor,
                    'ci', ci_cursor);
                ALTER TABLE subscriptions RENAME COLUMN diff_id TO subject;
                ALTER TABLE source_events RENAME COLUMN diff_id TO subject;
                ALTER TABLE batch_events RENAME COLUMN diff_id TO subject;
                DROP INDEX IF EXISTS subscriptions_state_diff;
                CREATE INDEX IF NOT EXISTS subscriptions_state_subject
                    ON subscriptions(state, subject);
                CREATE TABLE IF NOT EXISTS watch_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    spec TEXT,
                    event_types TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'active',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(session_id, subject)
                );
                CREATE INDEX IF NOT EXISTS watch_requests_state
                    ON watch_requests(state);
                PRAGMA user_version=3;
                COMMIT;
                """
            )
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(
                    f"diff-watcher v3 migration left {len(violations)} FK violations"
                )
        finally:
            connection.close()

    def schema_version(self) -> int:
        with self._connect() as connection:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])

    def active_subject_count(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(DISTINCT subject) FROM subscriptions WHERE state != 'retired'"
            ).fetchone()
            return int(row[0])

    def watch(self, subject: str) -> WatchedSubject | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM watched_subjects WHERE subject = ?", (subject,)
            ).fetchone()
            return self._watch(row) if row is not None else None

    def subscribe(
        self,
        session_id: str,
        subject: str,
        event_types: frozenset[EventKind],
        result: PollResult,
        *,
        now: float,
        next_poll_at: float,
        max_active_subjects: int | None = None,
        spec: str | None = None,
    ) -> tuple[Subscription, bool]:
        """Baseline source state and idempotently activate one subscription."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM subscriptions WHERE session_id = ? AND subject = ?",
                (session_id, subject),
            ).fetchone()
            diff_is_active = connection.execute(
                "SELECT 1 FROM subscriptions WHERE subject = ? AND state != 'retired' LIMIT 1",
                (subject,),
            ).fetchone()
            if max_active_subjects is not None and diff_is_active is None:
                active_count = int(
                    connection.execute(
                        "SELECT COUNT(DISTINCT subject) FROM subscriptions WHERE state != 'retired'"
                    ).fetchone()[0]
                )
                if active_count >= max_active_subjects:
                    raise SubscriptionConstraintError("diff watcher active-diff limit reached")
            self._upsert_watch(connection, result, now=now, next_poll_at=next_poll_at, spec=spec)
            self._replace_source_components(connection, result, now=now)
            encoded_types = json.dumps(sorted(kind.value for kind in event_types))
            created = existing is None
            reset_baseline = existing is None or existing["state"] == "retired"
            previous_types = set() if reset_baseline else set(json.loads(existing["event_types"]))
            if existing is None:
                cursor = connection.execute(
                    "INSERT INTO subscriptions "
                    "(session_id, subject, event_types, state, baseline_at, "
                    "last_liveness_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, 'active', ?, ?, ?, ?)",
                    (session_id, subject, encoded_types, now, now, now, now),
                )
                if cursor.lastrowid is None:
                    raise RuntimeError("subscription insert returned no row id")
                subscription_id = int(cursor.lastrowid)
            elif existing["state"] in {"active", "suspended"}:
                subscription_id = int(existing["id"])
                connection.execute(
                    "UPDATE subscriptions SET event_types = ?, state = 'active', "
                    "updated_at = ? WHERE id = ?",
                    (encoded_types, now, subscription_id),
                )
            else:
                subscription_id = int(existing["id"])
                connection.execute(
                    "UPDATE subscriptions SET event_types = ?, state = 'active', "
                    "baseline_at = ?, last_liveness_at = ?, unavailable_since = NULL, "
                    "last_delivery_at = NULL, retired_reason = NULL, updated_at = ? "
                    "WHERE id = ?",
                    (encoded_types, now, now, now, subscription_id),
                )
            if reset_baseline:
                connection.execute(
                    "DELETE FROM subscription_events WHERE subscription_id = ?",
                    (subscription_id,),
                )
            baseline_types = (
                {kind.value for kind in event_types}
                if reset_baseline
                else {kind.value for kind in event_types} - previous_types
            )
            for event_type in baseline_types:
                connection.execute(
                    "INSERT OR IGNORE INTO subscription_events "
                    "(subscription_id, kind, external_id, fingerprint, handled_at) "
                    "SELECT ?, kind, external_id, fingerprint, ? FROM source_events "
                    "WHERE subject = ? AND kind = ? AND actionable = 1",
                    (subscription_id, now, subject, event_type),
                )
            connection.commit()
        created_subscription = self.subscription(session_id, subject)
        assert created_subscription is not None
        return created_subscription, created

    def request_watch(
        self,
        session_id: str,
        source: str,
        subject: str,
        event_types: frozenset[EventKind],
        *,
        spec: str | None,
        now: float,
    ) -> None:
        """Record a session's desire to watch a subject, for the sidecar to apply."""
        encoded = json.dumps(sorted(kind.value for kind in event_types))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO watch_requests "
                "(session_id, source, subject, spec, event_types, state, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'active', ?, ?) "
                "ON CONFLICT(session_id, subject) DO UPDATE SET "
                "source = excluded.source, spec = excluded.spec, "
                "event_types = excluded.event_types, state = 'active', "
                "updated_at = excluded.updated_at",
                (session_id, source, subject, spec, encoded, now, now),
            )
            connection.commit()

    def active_watch_requests(
        self, session_id: str | None = None
    ) -> list[tuple[str, str, str, str | None, frozenset[EventKind]]]:
        """Return ``(session_id, source, subject, spec, event_types)`` rows."""
        query = (
            "SELECT session_id, source, subject, spec, event_types FROM watch_requests "
            "WHERE state = 'active'"
        )
        parameters: tuple[object, ...] = ()
        if session_id is not None:
            query += " AND session_id = ?"
            parameters = (session_id,)
        with self._connect() as connection:
            rows = connection.execute(query + " ORDER BY id", parameters).fetchall()
        results = []
        for row in rows:
            try:
                kinds = frozenset(EventKind(value) for value in json.loads(row["event_types"]))
            except ValueError:
                # A kind written by a newer build is skipped rather than fatal,
                # so a downgrade cannot wedge reconciliation.
                continue
            results.append(
                (
                    str(row["session_id"]),
                    str(row["source"]),
                    str(row["subject"]),
                    row["spec"],
                    kinds,
                )
            )
        return results

    def cancel_watch_requests(
        self,
        session_id: str,
        *,
        now: float,
        subject: str | None = None,
        sources: Iterable[str] | None = None,
    ) -> int:
        """Cancel a session's watch requests, optionally scoped by source.

        ``sources`` is what stops one surface's unsubscribe from cancelling the
        other's requests: without it a bare ``diff_watch_unsubscribe`` would
        retire only the diff subscriptions but cancel every request the session
        owns, and the generic watches would silently stop being re-bound.
        """
        query = "UPDATE watch_requests SET state = 'cancelled', updated_at = ? WHERE session_id = ?"
        parameters: tuple[object, ...] = (now, session_id)
        if subject is not None:
            query += " AND subject = ?"
            parameters += (subject,)
        if sources is not None:
            names = tuple(sources)
            placeholders = ",".join("?" for _ in names)
            query += f" AND source IN ({placeholders})"
            parameters += names
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(query + " AND state = 'active'", parameters)
            connection.commit()
            return max(cursor.rowcount, 0)

    def subscription(self, session_id: str, subject: str | None = None) -> Subscription | None:
        sql = "SELECT * FROM subscriptions WHERE session_id = ?"
        params: tuple[object, ...] = (session_id,)
        if subject is not None:
            sql += " AND subject = ?"
            params = (session_id, subject)
        sql += " ORDER BY id DESC LIMIT 1"
        with self._connect() as connection:
            row = connection.execute(sql, params).fetchone()
            return self._subscription(row) if row is not None else None

    def subscriptions_for_session(
        self,
        session_id: str,
        *,
        states: Iterable[SubscriptionState] | None = None,
        sources: Iterable[str] | None = None,
    ) -> list[Subscription]:
        """Every subscription a session owns; one session may watch a stack.

        ``sources`` scopes the result to subjects owned by particular sources.
        Reconciliation needs this: the diff reconciler retires whatever a
        session's labels no longer claim, and must not sweep away a generic
        watch the labels never described in the first place.
        """
        sql = "SELECT s.* FROM subscriptions s WHERE s.session_id = ?"
        params: tuple[object, ...] = (session_id,)
        if states is not None:
            values = tuple(state.value for state in states)
            placeholders = ",".join("?" for _ in values)
            sql += f" AND s.state IN ({placeholders})"
            params += values
        if sources is not None:
            names = tuple(sources)
            placeholders = ",".join("?" for _ in names)
            sql += (
                " AND COALESCE((SELECT ws.source FROM watched_subjects ws "
                f"WHERE ws.subject = s.subject), 'phabricator') IN ({placeholders})"
            )
            params += names
        sql += " ORDER BY s.id"
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
            return [self._subscription(row) for row in rows]

    def subscriptions_for_diff(
        self,
        subject: str,
        *,
        states: Iterable[SubscriptionState] = (SubscriptionState.ACTIVE,),
    ) -> list[Subscription]:
        values = tuple(state.value for state in states)
        placeholders = ",".join("?" for _ in values)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM subscriptions WHERE subject = ? "
                f"AND state IN ({placeholders}) ORDER BY id",
                (subject, *values),
            ).fetchall()
            return [self._subscription(row) for row in rows]

    def live_subscriptions(self) -> list[Subscription]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM subscriptions WHERE state != 'retired' ORDER BY id"
            ).fetchall()
            return [self._subscription(row) for row in rows]

    def unsubscribe(self, session_id: str, *, now: float) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT id FROM subscriptions WHERE session_id = ? AND state != 'retired'",
                (session_id,),
            ).fetchall()
            if not rows:
                connection.commit()
                return False
            ids = [int(row[0]) for row in rows]
            connection.executemany(
                "UPDATE subscriptions SET state = 'retired', retired_reason = 'unsubscribed', "
                "updated_at = ? WHERE id = ?",
                [(now, subscription_id) for subscription_id in ids],
            )
            connection.execute(
                "UPDATE batches SET state = 'cancelled', updated_at = ? "
                "WHERE session_id = ? AND state IN ('open', 'delivering')",
                (now, session_id),
            )
            connection.commit()
            return True

    def retire_subscription(self, subscription_id: int, reason: str, *, now: float) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE subscriptions SET state = 'retired', retired_reason = ?, "
                "updated_at = ? WHERE id = ?",
                (reason, now, subscription_id),
            )
            self._detach_subscription_from_batches(connection, subscription_id, now)
            connection.commit()

    def apply_poll(
        self,
        result: PollResult,
        *,
        now: float,
        next_poll_at: float,
        batch_window_seconds: float,
    ) -> int:
        """Update source state and merge newly qualifying events into batches."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            prior = connection.execute(
                "SELECT latest_version_id, missing_count FROM watched_subjects WHERE subject = ?",
                (result.subject,),
            ).fetchone()
            previous_version = prior["latest_version_id"] if prior is not None else None
            missing_count = int(prior["missing_count"] or 0) if prior is not None else 0
            if result.lifecycle is Lifecycle.MISSING:
                missing_count += 1
            else:
                missing_count = 0
            self._upsert_watch(
                connection,
                result,
                now=now,
                next_poll_at=next_poll_at,
                missing_count=missing_count,
                reset_failure_count=not result.failed_kinds,
            )
            self._replace_source_components(connection, result, now=now)
            if (
                previous_version
                and previous_version != result.latest_version_id
                and result.version_scoped_kinds
            ):
                placeholders = ",".join("?" for _ in result.version_scoped_kinds)
                connection.execute(
                    "UPDATE source_events SET actionable = 0, last_seen_at = ? "
                    f"WHERE subject = ? AND kind IN ({placeholders}) AND version_id != ?",
                    (
                        now,
                        result.subject,
                        *sorted(kind.value for kind in result.version_scoped_kinds),
                        result.latest_version_id or "",
                    ),
                )

            terminal_reason: str | None = None
            if result.lifecycle is Lifecycle.TERMINAL:
                terminal_reason = result.state_label
            elif result.lifecycle is Lifecycle.MISSING and missing_count >= 2:
                terminal_reason = "missing"
            if terminal_reason is not None:
                self._retire_diff_locked(connection, result.subject, terminal_reason, now)
                connection.commit()
                return 0

            added = 0
            subscriptions = connection.execute(
                "SELECT * FROM subscriptions WHERE subject = ? AND state = 'active'",
                (result.subject,),
            ).fetchall()
            for subscription in subscriptions:
                selected = set(json.loads(subscription["event_types"]))
                events = connection.execute(
                    "SELECT * FROM source_events WHERE subject = ? AND actionable = 1",
                    (result.subject,),
                ).fetchall()
                qualifying = [
                    event
                    for event in events
                    if event["kind"] in selected
                    and (
                        float(event["first_seen_at"]) > float(subscription["baseline_at"])
                        or float(event["last_changed_at"]) > float(subscription["baseline_at"])
                    )
                    and not self._fingerprint_seen(
                        connection,
                        int(subscription["id"]),
                        event["kind"],
                        event["external_id"],
                        event["fingerprint"],
                    )
                ]
                if not qualifying:
                    continue
                # Batches are session-scoped: a second diff in the same stack
                # joins the session's open batch instead of opening its own.
                batch_id = self._open_batch_id(connection, str(subscription["session_id"]))
                if batch_id is None:
                    batch_id = f"dwb_{uuid.uuid4().hex}"
                    connection.execute(
                        "INSERT INTO batches "
                        "(batch_id, session_id, state, first_event_at, "
                        "flush_at, next_attempt_at, created_at, updated_at) "
                        "VALUES (?, ?, 'open', ?, ?, ?, ?, ?)",
                        (
                            batch_id,
                            str(subscription["session_id"]),
                            now,
                            now + batch_window_seconds,
                            now + batch_window_seconds,
                            now,
                            now,
                        ),
                    )
                for event in qualifying:
                    connection.execute(
                        "DELETE FROM batch_events WHERE batch_id = ? AND subject = ? "
                        "AND kind = ? AND external_id = ?",
                        (batch_id, result.subject, event["kind"], event["external_id"]),
                    )
                    cursor = connection.execute(
                        "INSERT OR IGNORE INTO batch_events "
                        "(batch_id, subject, kind, external_id, fingerprint) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            batch_id,
                            result.subject,
                            event["kind"],
                            event["external_id"],
                            event["fingerprint"],
                        ),
                    )
                    added += max(cursor.rowcount, 0)
            connection.commit()
            return added

    def _replace_source_components(
        self,
        connection: sqlite3.Connection,
        result: PollResult,
        *,
        now: float,
    ) -> None:
        # Only kinds the source read authoritatively are replaced. A kind that
        # failed this poll keeps its last known events rather than being
        # cleared, so a transient source error cannot look like a resolution.
        for kind in result.ok_kinds:
            events = result.events.get(kind, ())
            connection.execute(
                "UPDATE source_events SET actionable = 0, last_seen_at = ? "
                "WHERE subject = ? AND kind = ?",
                (now, result.subject, kind.value),
            )
            for event in events:
                self._upsert_source_event(connection, event, now=now)
            connection.execute(
                "DELETE FROM subscription_events WHERE kind = ? "
                "AND subscription_id IN (SELECT id FROM subscriptions WHERE subject = ?) "
                "AND external_id IN (SELECT external_id FROM source_events "
                "WHERE subject = ? AND kind = ? AND actionable = 0)",
                (kind.value, result.subject, result.subject, kind.value),
            )

    @staticmethod
    def _upsert_source_event(
        connection: sqlite3.Connection,
        event: NormalizedEvent,
        *,
        now: float,
    ) -> None:
        existing = connection.execute(
            "SELECT fingerprint, first_seen_at, last_changed_at FROM source_events "
            "WHERE subject = ? AND kind = ? AND external_id = ?",
            (event.subject, event.kind.value, event.external_id),
        ).fetchone()
        # Discovery time is authoritative for watcher ordering. External
        # timestamps may be skewed or rounded, so a newly observed fingerprint
        # must still compare newer than a subscription baseline.
        changed_at = max(event.changed_at.timestamp(), now)
        if existing is not None and existing["fingerprint"] == event.fingerprint:
            changed_at = float(existing["last_changed_at"])
        first_seen_at = float(existing["first_seen_at"]) if existing is not None else now
        connection.execute(
            "INSERT INTO source_events "
            "(subject, kind, external_id, version_id, fingerprint, actionable, "
            "first_seen_at, last_changed_at, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?) "
            "ON CONFLICT(subject, kind, external_id) DO UPDATE SET "
            "version_id = excluded.version_id, fingerprint = excluded.fingerprint, "
            "actionable = 1, last_changed_at = excluded.last_changed_at, "
            "last_seen_at = excluded.last_seen_at",
            (
                event.subject,
                event.kind.value,
                event.external_id,
                event.version_id,
                event.fingerprint,
                first_seen_at,
                changed_at,
                now,
            ),
        )

    @staticmethod
    def _fingerprint_seen(
        connection: sqlite3.Connection,
        subscription_id: int,
        kind: str,
        external_id: str,
        fingerprint: str,
    ) -> bool:
        row = connection.execute(
            # The second arm asks "is this already pending in my session's open
            # batch?". Batches are session-scoped, so it must also match the
            # event's diff -- otherwise a sibling diff's event would suppress
            # this one.
            "SELECT 1 FROM subscription_events WHERE subscription_id = ? AND kind = ? "
            "AND external_id = ? AND fingerprint = ? UNION ALL "
            "SELECT 1 FROM batch_events be JOIN batches b ON b.batch_id = be.batch_id "
            "JOIN subscriptions s ON s.session_id = b.session_id "
            "WHERE s.id = ? AND be.subject = s.subject "
            "AND b.state IN ('open', 'delivering') "
            "AND be.kind = ? "
            "AND be.external_id = ? AND be.fingerprint = ? LIMIT 1",
            (
                subscription_id,
                kind,
                external_id,
                fingerprint,
                subscription_id,
                kind,
                external_id,
                fingerprint,
            ),
        ).fetchone()
        return row is not None

    @staticmethod
    def _open_batch_id(connection: sqlite3.Connection, session_id: str) -> str | None:
        row = connection.execute(
            "SELECT batch_id FROM batches WHERE session_id = ? "
            "AND state IN ('open', 'delivering') LIMIT 1",
            (session_id,),
        ).fetchone()
        return str(row[0]) if row is not None else None

    @staticmethod
    def _batch_diff_ids(connection: sqlite3.Connection, batch_id: str) -> tuple[str, ...]:
        rows = connection.execute(
            "SELECT DISTINCT subject FROM batch_events WHERE batch_id = ? ORDER BY subject",
            (batch_id,),
        ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def batch(self, batch_id: str) -> Batch | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
            if row is None:
                return None
            return self._batch(row, self._batch_diff_ids(connection, batch_id))

    def open_batch_for_session(self, session_id: str) -> Batch | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM batches WHERE session_id = ? "
                "AND state IN ('open', 'delivering') LIMIT 1",
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            return self._batch(row, self._batch_diff_ids(connection, str(row["batch_id"])))

    def open_batch_for(self, subscription_id: int) -> Batch | None:
        """The open batch that would carry this subscription's events.

        Batches are session-scoped, so this is the owning session's batch --
        it may also carry events for sibling diffs in the same stack.
        """
        with self._connect() as connection:
            row = connection.execute(
                "SELECT b.* FROM batches b JOIN subscriptions s "
                "ON s.session_id = b.session_id WHERE s.id = ? "
                "AND b.state IN ('open', 'delivering') LIMIT 1",
                (subscription_id,),
            ).fetchone()
            if row is None:
                return None
            return self._batch(row, self._batch_diff_ids(connection, str(row["batch_id"])))

    def due_batches(self, now: float) -> list[Batch]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM batches WHERE state IN ('open', 'delivering') "
                "AND flush_at <= ? AND next_attempt_at <= ? ORDER BY flush_at",
                (now, now),
            ).fetchall()
            return [
                self._batch(row, self._batch_diff_ids(connection, str(row["batch_id"])))
                for row in rows
            ]

    def prepare_batch(self, batch_id: str, *, now: float) -> dict[EventKind, int] | None:
        """Prune stale members, freeze a summary, and mark delivering."""
        from .logic import render_batch_summary

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM batch_events WHERE batch_id = ? AND NOT EXISTS ("
                "SELECT 1 FROM source_events se WHERE se.subject = batch_events.subject "
                "AND se.kind = batch_events.kind AND se.external_id = batch_events.external_id "
                "AND se.fingerprint = batch_events.fingerprint AND se.actionable = 1)",
                (batch_id,),
            )
            row = connection.execute(
                "SELECT session_id FROM batches WHERE batch_id = ? "
                "AND state IN ('open', 'delivering')",
                (batch_id,),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            per_diff: dict[str, dict[EventKind, int]] = {}
            sources: dict[str, str] = {}
            for event_row in connection.execute(
                "SELECT be.subject AS subject, be.kind AS kind, COUNT(*) AS count, "
                "COALESCE(ws.source, 'phabricator') AS source FROM batch_events be "
                "LEFT JOIN watched_subjects ws ON ws.subject = be.subject "
                "WHERE be.batch_id = ? GROUP BY be.subject, be.kind ORDER BY be.subject",
                (batch_id,),
            ).fetchall():
                # A kind written by a newer build is ignored rather than fatal,
                # so a downgrade cannot wedge batch delivery.
                try:
                    kind = EventKind(str(event_row["kind"]))
                except ValueError:
                    continue
                subject = str(event_row["subject"])
                sources[subject] = str(event_row["source"])
                bucket = per_diff.setdefault(subject, {})
                bucket[kind] = int(event_row["count"])
            totals: dict[EventKind, int] = {}
            for bucket in per_diff.values():
                for kind, count in bucket.items():
                    totals[kind] = totals.get(kind, 0) + count
            if not any(totals.values()):
                connection.execute(
                    "UPDATE batches SET state = 'cancelled', updated_at = ? WHERE batch_id = ?",
                    (now, batch_id),
                )
                connection.commit()
                return None
            summary = render_batch_summary(
                batch_id,
                [
                    (sources.get(subject, "phabricator"), subject, bucket)
                    for subject, bucket in per_diff.items()
                ],
            )
            connection.execute(
                "UPDATE batches SET state = 'delivering', summary = ?, updated_at = ? "
                "WHERE batch_id = ?",
                (summary, now, batch_id),
            )
            connection.commit()
            return totals

    def defer_batch(self, batch_id: str, *, now: float, retry_at: float) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE batches SET state = 'open', retry_count = retry_count + 1, "
                "next_attempt_at = ?, updated_at = ? WHERE batch_id = ? "
                "AND state IN ('open', 'delivering')",
                (retry_at, now, batch_id),
            )

    def deliver_batch(self, batch_id: str, *, now: float) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT session_id FROM batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
            if row is not None:
                session_id = str(row["session_id"])
                # Each event is handled by the subscription owning its diff, so
                # a re-observed finding is suppressed per diff, not per session.
                connection.execute(
                    "INSERT OR IGNORE INTO subscription_events "
                    "(subscription_id, kind, external_id, fingerprint, handled_at) "
                    "SELECT s.id, be.kind, be.external_id, be.fingerprint, ? "
                    "FROM batch_events be JOIN subscriptions s "
                    "ON s.session_id = ? AND s.subject = be.subject "
                    "WHERE be.batch_id = ?",
                    (now, session_id, batch_id),
                )
                connection.execute(
                    "UPDATE batches SET state = 'delivered', delivered_at = ?, updated_at = ? "
                    "WHERE batch_id = ?",
                    (now, now, batch_id),
                )
                # One wake covers the session, so the minimum-interval throttle
                # advances for every diff it watches.
                connection.execute(
                    "UPDATE subscriptions SET last_delivery_at = ?, updated_at = ? "
                    "WHERE session_id = ? AND state != 'retired'",
                    (now, now, session_id),
                )
            connection.commit()

    def claim_due_watches(
        self,
        *,
        now: float,
        owner: str,
        lease_seconds: float,
        limit: int,
    ) -> list[WatchedSubject]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT wd.* FROM watched_subjects wd WHERE wd.next_poll_at <= ? "
                "AND (wd.lease_until IS NULL OR wd.lease_until <= ?) AND EXISTS ("
                "SELECT 1 FROM subscriptions s WHERE s.subject = wd.subject "
                "AND s.state = 'active') ORDER BY wd.next_poll_at LIMIT ?",
                (now, now, limit),
            ).fetchall()
            claimed: list[WatchedSubject] = []
            for row in rows:
                cursor = connection.execute(
                    "UPDATE watched_subjects SET lease_owner = ?, lease_until = ? "
                    "WHERE subject = ? AND (lease_until IS NULL OR lease_until <= ?)",
                    (owner, now + lease_seconds, row["subject"], now),
                )
                if cursor.rowcount == 1:
                    claimed.append(self._watch(row))
            connection.commit()
            return claimed

    def claim_watch(
        self,
        subject: str,
        *,
        now: float,
        owner: str,
        lease_seconds: float,
    ) -> WatchedSubject | None:
        """Claim a specific diff for flush-time revalidation."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM watched_subjects WHERE subject = ? AND "
                "(lease_until IS NULL OR lease_until <= ?)",
                (subject, now),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            cursor = connection.execute(
                "UPDATE watched_subjects SET lease_owner = ?, lease_until = ? "
                "WHERE subject = ? AND (lease_until IS NULL OR lease_until <= ?)",
                (owner, now + lease_seconds, subject, now),
            )
            connection.commit()
            return self._watch(row) if cursor.rowcount == 1 else None

    def poll_failed(self, subject: str, owner: str, *, next_poll_at: float) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE watched_subjects SET failure_count = failure_count + 1, "
                "next_poll_at = ?, lease_owner = NULL, lease_until = NULL "
                "WHERE subject = ? AND lease_owner = ?",
                (next_poll_at, subject, owner),
            )

    def partial_poll_failed(self, subject: str, *, next_poll_at: float) -> None:
        """Back off after persisting only the source components that succeeded."""

        with self._connect() as connection:
            connection.execute(
                "UPDATE watched_subjects SET failure_count = failure_count + 1, "
                "next_poll_at = ? WHERE subject = ?",
                (next_poll_at, subject),
            )

    def release_lease(self, subject: str, owner: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE watched_subjects SET lease_owner = NULL, lease_until = NULL "
                "WHERE subject = ? AND lease_owner = ?",
                (subject, owner),
            )

    def release_owner_leases(self, owner: str) -> None:
        """Release every poll lease held by one stopped scheduler instance."""

        with self._connect() as connection:
            connection.execute(
                "UPDATE watched_subjects SET lease_owner = NULL, lease_until = NULL "
                "WHERE lease_owner = ?",
                (owner,),
            )

    def next_wake_at(
        self,
        *,
        active_probe_seconds: float,
        suspended_probe_seconds: float,
    ) -> float | None:
        """Return the next external-poll, batch, or liveness deadline."""

        with self._connect() as connection:
            candidates: list[float] = []
            poll = connection.execute(
                "SELECT MIN(wd.next_poll_at) FROM watched_subjects wd WHERE EXISTS ("
                "SELECT 1 FROM subscriptions s WHERE s.subject = wd.subject "
                "AND s.state = 'active')"
            ).fetchone()[0]
            if poll is not None:
                candidates.append(float(poll))
            batch = connection.execute(
                "SELECT MIN(MAX(flush_at, next_attempt_at)) FROM batches "
                "WHERE state IN ('open', 'delivering')"
            ).fetchone()[0]
            if batch is not None:
                candidates.append(float(batch))
            liveness = connection.execute(
                "SELECT MIN(last_liveness_at + CASE state "
                "WHEN 'active' THEN ? ELSE ? END) FROM subscriptions "
                "WHERE state IN ('active', 'suspended')",
                (active_probe_seconds, suspended_probe_seconds),
            ).fetchone()[0]
            if liveness is not None:
                candidates.append(float(liveness))
            return min(candidates) if candidates else None

    def suspend_or_retire_session(
        self,
        session_id: str,
        *,
        now: float,
        terminal_reason: str | None,
        suspend_after: float,
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT * FROM subscriptions WHERE session_id = ? AND state != 'retired'",
                (session_id,),
            ).fetchall()
            for row in rows:
                if terminal_reason is not None:
                    self._retire_subscription_locked(
                        connection, int(row["id"]), terminal_reason, now
                    )
                    continue
                unavailable_since = row["unavailable_since"]
                if unavailable_since is None:
                    connection.execute(
                        "UPDATE subscriptions SET unavailable_since = ?, last_liveness_at = ?, "
                        "updated_at = ? WHERE id = ?",
                        (now, now, now, int(row["id"])),
                    )
                elif now - float(unavailable_since) >= suspend_after:
                    connection.execute(
                        "UPDATE subscriptions SET state = 'suspended', last_liveness_at = ?, "
                        "updated_at = ? WHERE id = ?",
                        (now, now, int(row["id"])),
                    )
            connection.commit()

    def mark_session_usable(self, session_id: str, *, now: float) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            recovered_diff_ids = [
                str(row[0])
                for row in connection.execute(
                    "SELECT DISTINCT subject FROM subscriptions WHERE session_id = ? "
                    "AND state = 'suspended'",
                    (session_id,),
                ).fetchall()
            ]
            cursor = connection.execute(
                "UPDATE subscriptions SET state = CASE WHEN state = 'suspended' "
                "THEN 'active' ELSE state END, unavailable_since = NULL, "
                "last_liveness_at = ?, updated_at = ? WHERE session_id = ? "
                "AND state != 'retired'",
                (now, now, session_id),
            )
            connection.executemany(
                "UPDATE watched_subjects SET next_poll_at = MIN(next_poll_at, ?) WHERE subject = ?",
                [(now, subject) for subject in recovered_diff_ids],
            )
            connection.commit()
            return cursor.rowcount > 0

    def liveness_due(
        self,
        now: float,
        active_probe_seconds: float,
        suspended_probe_seconds: float,
    ) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT session_id FROM subscriptions "
                "WHERE (state = 'active' AND last_liveness_at <= ?) "
                "OR (state = 'suspended' AND last_liveness_at <= ?)",
                (
                    now - active_probe_seconds,
                    now - suspended_probe_seconds,
                ),
            ).fetchall()
            return [str(row[0]) for row in rows]

    def prune(self, *, now: float, retention_seconds: float) -> None:
        cutoff = now - retention_seconds
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM batches WHERE state IN ('delivered', 'cancelled') AND updated_at < ?",
                (cutoff,),
            )
            connection.execute(
                "DELETE FROM source_events WHERE actionable = 0 AND last_seen_at < ? "
                "AND NOT EXISTS (SELECT 1 FROM batch_events be WHERE "
                "be.subject = source_events.subject AND be.kind = source_events.kind "
                "AND be.external_id = source_events.external_id)",
                (cutoff,),
            )
            connection.execute(
                "DELETE FROM subscriptions WHERE state = 'retired' AND updated_at < ?",
                (cutoff,),
            )
            connection.execute(
                "DELETE FROM source_events WHERE NOT EXISTS ("
                "SELECT 1 FROM subscriptions s WHERE s.subject = source_events.subject)",
            )
            connection.execute(
                "DELETE FROM watched_subjects WHERE NOT EXISTS ("
                "SELECT 1 FROM subscriptions s WHERE s.subject = watched_subjects.subject)",
            )
            connection.commit()

    def counts(self) -> dict[str, int]:
        with self._connect() as connection:
            result: dict[str, int] = {}
            for state in SubscriptionState:
                result[f"subscriptions_{state.value}"] = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM subscriptions WHERE state = ?",
                        (state.value,),
                    ).fetchone()[0]
                )
            result["watched_subjects"] = int(
                connection.execute(
                    "SELECT COUNT(DISTINCT subject) FROM subscriptions WHERE state != 'retired'"
                ).fetchone()[0]
            )
            result["open_batches"] = int(
                connection.execute(
                    "SELECT COUNT(*) FROM batches WHERE state IN ('open', 'delivering')"
                ).fetchone()[0]
            )
            result["source_failed_watches"] = int(
                connection.execute(
                    "SELECT COUNT(*) FROM watched_subjects WHERE failure_count > 0 "
                    "AND EXISTS (SELECT 1 FROM subscriptions s "
                    "WHERE s.subject = watched_subjects.subject AND s.state = 'active')"
                ).fetchone()[0]
            )
            result["source_failure_streak"] = int(
                connection.execute(
                    "SELECT COALESCE(MAX(failure_count), 0) FROM watched_subjects"
                ).fetchone()[0]
            )
            return result

    def oldest_pending_age(self, now: float) -> int:
        with self._connect() as connection:
            first = connection.execute(
                "SELECT MIN(first_event_at) FROM batches WHERE state IN ('open', 'delivering')"
            ).fetchone()[0]
            return max(0, int(now - float(first))) if first is not None else 0

    @staticmethod
    def _upsert_watch(
        connection: sqlite3.Connection,
        result: PollResult,
        *,
        now: float,
        next_poll_at: float,
        missing_count: int = 0,
        reset_failure_count: bool = True,
        spec: str | None = None,
    ) -> None:
        # ``spec`` is set once, when the subject is first watched, and is not
        # refreshed by later polls: a poll describes what the subject looks
        # like, not how to reach it.
        connection.execute(
            "INSERT INTO watched_subjects "
            "(subject, source, lifecycle, latest_version_id, last_activity_at, next_poll_at, "
            "cursor, spec, source_status, failure_count, last_success_at, "
            "missing_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?) "
            "ON CONFLICT(subject) DO UPDATE SET lifecycle = excluded.lifecycle, "
            "latest_version_id = excluded.latest_version_id, "
            "last_activity_at = excluded.last_activity_at, next_poll_at = excluded.next_poll_at, "
            "cursor = excluded.cursor, "
            "source_status = excluded.source_status, failure_count = CASE WHEN ? "
            "THEN 0 ELSE watched_subjects.failure_count END, "
            "last_success_at = excluded.last_success_at, missing_count = excluded.missing_count, "
            "lease_owner = NULL, lease_until = NULL",
            (
                result.subject,
                result.source,
                result.state_label,
                result.latest_version_id,
                result.last_activity_at.timestamp(),
                next_poll_at,
                result.cursor,
                spec,
                result.status,
                now,
                missing_count,
                int(reset_failure_count),
            ),
        )

    @staticmethod
    def _retire_diff_locked(
        connection: sqlite3.Connection,
        subject: str,
        reason: str,
        now: float,
    ) -> None:
        rows = connection.execute(
            "SELECT id FROM subscriptions WHERE subject = ? AND state != 'retired'",
            (subject,),
        ).fetchall()
        for row in rows:
            WatcherRepository._retire_subscription_locked(connection, int(row["id"]), reason, now)

    @staticmethod
    def _retire_subscription_locked(
        connection: sqlite3.Connection,
        subscription_id: int,
        reason: str,
        now: float,
    ) -> None:
        connection.execute(
            "UPDATE subscriptions SET state = 'retired', retired_reason = ?, updated_at = ? "
            "WHERE id = ?",
            (reason, now, subscription_id),
        )
        WatcherRepository._detach_subscription_from_batches(connection, subscription_id, now)

    @staticmethod
    def _detach_subscription_from_batches(
        connection: sqlite3.Connection,
        subscription_id: int,
        now: float,
    ) -> None:
        """Drop a retiring subscription's diff from its session's open batch.

        Batches are session-scoped, so retiring one diff of a stack must not
        cancel the pending wake for its siblings -- only remove its own events,
        and cancel the batch if that empties it.
        """
        row = connection.execute(
            "SELECT session_id, subject FROM subscriptions WHERE id = ?",
            (subscription_id,),
        ).fetchone()
        if row is None:
            return
        connection.execute(
            "DELETE FROM batch_events WHERE subject = ? AND batch_id IN "
            "(SELECT batch_id FROM batches WHERE session_id = ? "
            "AND state IN ('open', 'delivering'))",
            (str(row["subject"]), str(row["session_id"])),
        )
        connection.execute(
            "UPDATE batches SET state = 'cancelled', updated_at = ? "
            "WHERE session_id = ? AND state IN ('open', 'delivering') "
            "AND NOT EXISTS (SELECT 1 FROM batch_events WHERE batch_id = batches.batch_id)",
            (now, str(row["session_id"])),
        )

    @staticmethod
    def _subscription(row: sqlite3.Row) -> Subscription:
        return Subscription(
            id=int(row["id"]),
            session_id=str(row["session_id"]),
            subject=str(row["subject"]),
            event_types=frozenset(EventKind(value) for value in json.loads(row["event_types"])),
            state=SubscriptionState(row["state"]),
            baseline_at=float(row["baseline_at"]),
            last_delivery_at=(
                float(row["last_delivery_at"]) if row["last_delivery_at"] is not None else None
            ),
            unavailable_since=(
                float(row["unavailable_since"]) if row["unavailable_since"] is not None else None
            ),
            retired_reason=row["retired_reason"],
        )

    @staticmethod
    def _batch(row: sqlite3.Row, subjects: tuple[str, ...]) -> Batch:
        return Batch(
            batch_id=str(row["batch_id"]),
            session_id=str(row["session_id"]),
            subjects=subjects,
            state=BatchState(row["state"]),
            first_event_at=float(row["first_event_at"]),
            flush_at=float(row["flush_at"]),
            retry_count=int(row["retry_count"]),
            next_attempt_at=float(row["next_attempt_at"]),
            summary=row["summary"],
        )

    @staticmethod
    def _watch(row: sqlite3.Row) -> WatchedSubject:
        return WatchedSubject(
            subject=str(row["subject"]),
            source=str(row["source"]),
            lifecycle=str(row["lifecycle"]),
            latest_version_id=row["latest_version_id"],
            last_activity_at=float(row["last_activity_at"]),
            next_poll_at=float(row["next_poll_at"]),
            cursor=row["cursor"],
            spec=row["spec"],
            failure_count=int(row["failure_count"]),
            last_success_at=(
                float(row["last_success_at"]) if row["last_success_at"] is not None else None
            ),
        )
