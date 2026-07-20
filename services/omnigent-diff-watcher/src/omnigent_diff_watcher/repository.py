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
    NormalizedEvent,
    Subscription,
    SubscriptionState,
    WatchedDiff,
)
from .source_models import DiffLifecycle, DiffSnapshot, SourceCursor

SCHEMA_VERSION = 1


class NewerSchemaError(RuntimeError):
    """The database belongs to a newer plugin version."""


class SubscriptionConstraintError(RuntimeError):
    """A subscription would violate a watcher resource invariant."""


class WatcherRepository:
    """Short-transaction repository; external calls never run under its lock."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._migrate()
        with suppress(OSError):
            self.path.chmod(0o600)

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
                connection.executescript(
                    """
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
                )

    def schema_version(self) -> int:
        with self._connect() as connection:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])

    def active_diff_count(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(DISTINCT diff_id) FROM subscriptions WHERE state != 'retired'"
            ).fetchone()
            return int(row[0])

    def watch(self, diff_id: str) -> WatchedDiff | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM watched_diffs WHERE diff_id = ?", (diff_id,)
            ).fetchone()
            return self._watch(row) if row is not None else None

    def subscribe(
        self,
        session_id: str,
        diff_id: str,
        event_types: frozenset[EventKind],
        snapshot: DiffSnapshot,
        *,
        now: float,
        next_poll_at: float,
        max_active_diffs: int | None = None,
    ) -> tuple[Subscription, bool]:
        """Baseline source state and idempotently activate one subscription."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM subscriptions WHERE session_id = ? AND diff_id = ?",
                (session_id, diff_id),
            ).fetchone()
            conflicting = connection.execute(
                "SELECT 1 FROM subscriptions WHERE session_id = ? AND diff_id != ? "
                "AND state != 'retired' LIMIT 1",
                (session_id, diff_id),
            ).fetchone()
            if conflicting is not None:
                raise SubscriptionConstraintError("session already watches a different diff")
            diff_is_active = connection.execute(
                "SELECT 1 FROM subscriptions WHERE diff_id = ? AND state != 'retired' LIMIT 1",
                (diff_id,),
            ).fetchone()
            if max_active_diffs is not None and diff_is_active is None:
                active_count = int(
                    connection.execute(
                        "SELECT COUNT(DISTINCT diff_id) FROM subscriptions WHERE state != 'retired'"
                    ).fetchone()[0]
                )
                if active_count >= max_active_diffs:
                    raise SubscriptionConstraintError("diff watcher active-diff limit reached")
            self._upsert_watch(connection, snapshot, now=now, next_poll_at=next_poll_at)
            self._replace_source_components(connection, snapshot, now=now)
            encoded_types = json.dumps(sorted(kind.value for kind in event_types))
            created = existing is None
            reset_baseline = existing is None or existing["state"] == "retired"
            previous_types = set() if reset_baseline else set(json.loads(existing["event_types"]))
            if existing is None:
                cursor = connection.execute(
                    "INSERT INTO subscriptions "
                    "(session_id, diff_id, event_types, state, baseline_at, "
                    "last_liveness_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, 'active', ?, ?, ?, ?)",
                    (session_id, diff_id, encoded_types, now, now, now, now),
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
                    "WHERE diff_id = ? AND kind = ? AND actionable = 1",
                    (subscription_id, now, diff_id, event_type),
                )
            connection.commit()
        result = self.subscription(session_id, diff_id)
        assert result is not None
        return result, created

    def subscription(self, session_id: str, diff_id: str | None = None) -> Subscription | None:
        sql = "SELECT * FROM subscriptions WHERE session_id = ?"
        params: tuple[object, ...] = (session_id,)
        if diff_id is not None:
            sql += " AND diff_id = ?"
            params = (session_id, diff_id)
        sql += " ORDER BY id DESC LIMIT 1"
        with self._connect() as connection:
            row = connection.execute(sql, params).fetchone()
            return self._subscription(row) if row is not None else None

    def subscriptions_for_diff(
        self,
        diff_id: str,
        *,
        states: Iterable[SubscriptionState] = (SubscriptionState.ACTIVE,),
    ) -> list[Subscription]:
        values = tuple(state.value for state in states)
        placeholders = ",".join("?" for _ in values)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM subscriptions WHERE diff_id = ? "
                f"AND state IN ({placeholders}) ORDER BY id",
                (diff_id, *values),
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
            connection.executemany(
                "UPDATE batches SET state = 'cancelled', updated_at = ? "
                "WHERE subscription_id = ? AND state IN ('open', 'delivering')",
                [(now, subscription_id) for subscription_id in ids],
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
            connection.execute(
                "UPDATE batches SET state = 'cancelled', updated_at = ? "
                "WHERE subscription_id = ? AND state IN ('open', 'delivering')",
                (now, subscription_id),
            )
            connection.commit()

    def apply_snapshot(
        self,
        snapshot: DiffSnapshot,
        *,
        now: float,
        next_poll_at: float,
        batch_window_seconds: float,
    ) -> int:
        """Update source state and merge newly qualifying events into batches."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            prior = connection.execute(
                "SELECT latest_version_id, missing_count FROM watched_diffs WHERE diff_id = ?",
                (snapshot.diff_id,),
            ).fetchone()
            previous_version = prior["latest_version_id"] if prior is not None else None
            missing_count = int(prior["missing_count"] or 0) if prior is not None else 0
            if snapshot.lifecycle is DiffLifecycle.MISSING:
                missing_count += 1
            else:
                missing_count = 0
            self._upsert_watch(
                connection,
                snapshot,
                now=now,
                next_poll_at=next_poll_at,
                missing_count=missing_count,
                reset_failure_count=(
                    snapshot.comments.status == "ok" and snapshot.ci.status == "ok"
                ),
            )
            self._replace_source_components(connection, snapshot, now=now)
            if previous_version and previous_version != snapshot.latest_version_id:
                connection.execute(
                    "UPDATE source_events SET actionable = 0, last_seen_at = ? "
                    "WHERE diff_id = ? AND kind = 'ci_failure' AND version_id != ?",
                    (now, snapshot.diff_id, snapshot.latest_version_id or ""),
                )

            terminal_reason: str | None = None
            if snapshot.lifecycle in {
                DiffLifecycle.COMMITTED,
                DiffLifecycle.ABANDONED,
                DiffLifecycle.REVERTED,
            }:
                terminal_reason = snapshot.lifecycle.value
            elif snapshot.lifecycle is DiffLifecycle.MISSING and missing_count >= 2:
                terminal_reason = "missing"
            if terminal_reason is not None:
                self._retire_diff_locked(connection, snapshot.diff_id, terminal_reason, now)
                connection.commit()
                return 0

            added = 0
            subscriptions = connection.execute(
                "SELECT * FROM subscriptions WHERE diff_id = ? AND state = 'active'",
                (snapshot.diff_id,),
            ).fetchall()
            for subscription in subscriptions:
                selected = set(json.loads(subscription["event_types"]))
                events = connection.execute(
                    "SELECT * FROM source_events WHERE diff_id = ? AND actionable = 1",
                    (snapshot.diff_id,),
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
                batch_id = self._open_batch_id(connection, int(subscription["id"]))
                if batch_id is None:
                    batch_id = f"dwb_{uuid.uuid4().hex}"
                    connection.execute(
                        "INSERT INTO batches "
                        "(batch_id, subscription_id, diff_id, state, first_event_at, "
                        "flush_at, next_attempt_at, created_at, updated_at) "
                        "VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?)",
                        (
                            batch_id,
                            int(subscription["id"]),
                            snapshot.diff_id,
                            now,
                            now + batch_window_seconds,
                            now + batch_window_seconds,
                            now,
                            now,
                        ),
                    )
                for event in qualifying:
                    connection.execute(
                        "DELETE FROM batch_events WHERE batch_id = ? AND kind = ? "
                        "AND external_id = ?",
                        (batch_id, event["kind"], event["external_id"]),
                    )
                    cursor = connection.execute(
                        "INSERT OR IGNORE INTO batch_events "
                        "(batch_id, diff_id, kind, external_id, fingerprint) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            batch_id,
                            snapshot.diff_id,
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
        snapshot: DiffSnapshot,
        *,
        now: float,
    ) -> None:
        from .logic import normalize_snapshot

        for kind, events in normalize_snapshot(snapshot).items():
            connection.execute(
                "UPDATE source_events SET actionable = 0, last_seen_at = ? "
                "WHERE diff_id = ? AND kind = ?",
                (now, snapshot.diff_id, kind.value),
            )
            for event in events:
                self._upsert_source_event(connection, event, now=now)
            connection.execute(
                "DELETE FROM subscription_events WHERE kind = ? "
                "AND subscription_id IN (SELECT id FROM subscriptions WHERE diff_id = ?) "
                "AND external_id IN (SELECT external_id FROM source_events "
                "WHERE diff_id = ? AND kind = ? AND actionable = 0)",
                (kind.value, snapshot.diff_id, snapshot.diff_id, kind.value),
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
            "WHERE diff_id = ? AND kind = ? AND external_id = ?",
            (event.diff_id, event.kind.value, event.external_id),
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
            "(diff_id, kind, external_id, version_id, fingerprint, actionable, "
            "first_seen_at, last_changed_at, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?) "
            "ON CONFLICT(diff_id, kind, external_id) DO UPDATE SET "
            "version_id = excluded.version_id, fingerprint = excluded.fingerprint, "
            "actionable = 1, last_changed_at = excluded.last_changed_at, "
            "last_seen_at = excluded.last_seen_at",
            (
                event.diff_id,
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
            "SELECT 1 FROM subscription_events WHERE subscription_id = ? AND kind = ? "
            "AND external_id = ? AND fingerprint = ? UNION ALL "
            "SELECT 1 FROM batch_events be JOIN batches b ON b.batch_id = be.batch_id "
            "WHERE b.subscription_id = ? AND b.state IN ('open', 'delivering') "
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
    def _open_batch_id(connection: sqlite3.Connection, subscription_id: int) -> str | None:
        row = connection.execute(
            "SELECT batch_id FROM batches WHERE subscription_id = ? "
            "AND state IN ('open', 'delivering') LIMIT 1",
            (subscription_id,),
        ).fetchone()
        return str(row[0]) if row is not None else None

    def batch(self, batch_id: str) -> Batch | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT b.*, s.session_id FROM batches b JOIN subscriptions s "
                "ON s.id = b.subscription_id WHERE b.batch_id = ?",
                (batch_id,),
            ).fetchone()
            return self._batch(row) if row is not None else None

    def open_batch_for(self, subscription_id: int) -> Batch | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT b.*, s.session_id FROM batches b JOIN subscriptions s "
                "ON s.id = b.subscription_id WHERE b.subscription_id = ? "
                "AND b.state IN ('open', 'delivering') LIMIT 1",
                (subscription_id,),
            ).fetchone()
            return self._batch(row) if row is not None else None

    def due_batches(self, now: float) -> list[Batch]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT b.*, s.session_id FROM batches b JOIN subscriptions s "
                "ON s.id = b.subscription_id WHERE b.state IN ('open', 'delivering') "
                "AND b.flush_at <= ? AND b.next_attempt_at <= ? ORDER BY b.flush_at",
                (now, now),
            ).fetchall()
            return [self._batch(row) for row in rows]

    def prepare_batch(self, batch_id: str, *, now: float) -> tuple[int, int] | None:
        """Prune stale members, freeze a summary, and mark delivering."""
        from .logic import render_batch_summary

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM batch_events WHERE batch_id = ? AND NOT EXISTS ("
                "SELECT 1 FROM source_events se WHERE se.diff_id = batch_events.diff_id "
                "AND se.kind = batch_events.kind AND se.external_id = batch_events.external_id "
                "AND se.fingerprint = batch_events.fingerprint AND se.actionable = 1)",
                (batch_id,),
            )
            row = connection.execute(
                "SELECT b.diff_id, b.subscription_id, s.last_delivery_at FROM batches b "
                "JOIN subscriptions s ON s.id = b.subscription_id WHERE b.batch_id = ? "
                "AND b.state IN ('open', 'delivering')",
                (batch_id,),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            counts = {
                event_row["kind"]: int(event_row["count"])
                for event_row in connection.execute(
                    "SELECT kind, COUNT(*) AS count FROM batch_events "
                    "WHERE batch_id = ? GROUP BY kind",
                    (batch_id,),
                ).fetchall()
            }
            comments = counts.get(EventKind.REVIEW_COMMENT.value, 0)
            ci_failures = counts.get(EventKind.CI_FAILURE.value, 0)
            if comments + ci_failures == 0:
                connection.execute(
                    "UPDATE batches SET state = 'cancelled', updated_at = ? WHERE batch_id = ?",
                    (now, batch_id),
                )
                connection.commit()
                return None
            summary = render_batch_summary(
                batch_id,
                str(row["diff_id"]),
                comments,
                ci_failures,
            )
            connection.execute(
                "UPDATE batches SET state = 'delivering', summary = ?, updated_at = ? "
                "WHERE batch_id = ?",
                (summary, now, batch_id),
            )
            connection.commit()
            return comments, ci_failures

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
                "SELECT subscription_id FROM batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
            if row is not None:
                connection.execute(
                    "INSERT OR IGNORE INTO subscription_events "
                    "(subscription_id, kind, external_id, fingerprint, handled_at) "
                    "SELECT ?, kind, external_id, fingerprint, ? FROM batch_events "
                    "WHERE batch_id = ?",
                    (int(row["subscription_id"]), now, batch_id),
                )
                connection.execute(
                    "UPDATE batches SET state = 'delivered', delivered_at = ?, updated_at = ? "
                    "WHERE batch_id = ?",
                    (now, now, batch_id),
                )
                connection.execute(
                    "UPDATE subscriptions SET last_delivery_at = ?, updated_at = ? WHERE id = ?",
                    (now, now, int(row["subscription_id"])),
                )
            connection.commit()

    def claim_due_watches(
        self,
        *,
        now: float,
        owner: str,
        lease_seconds: float,
        limit: int,
    ) -> list[WatchedDiff]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT wd.* FROM watched_diffs wd WHERE wd.next_poll_at <= ? "
                "AND (wd.lease_until IS NULL OR wd.lease_until <= ?) AND EXISTS ("
                "SELECT 1 FROM subscriptions s WHERE s.diff_id = wd.diff_id "
                "AND s.state = 'active') ORDER BY wd.next_poll_at LIMIT ?",
                (now, now, limit),
            ).fetchall()
            claimed: list[WatchedDiff] = []
            for row in rows:
                cursor = connection.execute(
                    "UPDATE watched_diffs SET lease_owner = ?, lease_until = ? "
                    "WHERE diff_id = ? AND (lease_until IS NULL OR lease_until <= ?)",
                    (owner, now + lease_seconds, row["diff_id"], now),
                )
                if cursor.rowcount == 1:
                    claimed.append(self._watch(row))
            connection.commit()
            return claimed

    def claim_watch(
        self,
        diff_id: str,
        *,
        now: float,
        owner: str,
        lease_seconds: float,
    ) -> WatchedDiff | None:
        """Claim a specific diff for flush-time revalidation."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM watched_diffs WHERE diff_id = ? AND "
                "(lease_until IS NULL OR lease_until <= ?)",
                (diff_id, now),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            cursor = connection.execute(
                "UPDATE watched_diffs SET lease_owner = ?, lease_until = ? "
                "WHERE diff_id = ? AND (lease_until IS NULL OR lease_until <= ?)",
                (owner, now + lease_seconds, diff_id, now),
            )
            connection.commit()
            return self._watch(row) if cursor.rowcount == 1 else None

    def poll_failed(self, diff_id: str, owner: str, *, next_poll_at: float) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE watched_diffs SET failure_count = failure_count + 1, "
                "next_poll_at = ?, lease_owner = NULL, lease_until = NULL "
                "WHERE diff_id = ? AND lease_owner = ?",
                (next_poll_at, diff_id, owner),
            )

    def partial_poll_failed(self, diff_id: str, *, next_poll_at: float) -> None:
        """Back off after persisting only the source components that succeeded."""

        with self._connect() as connection:
            connection.execute(
                "UPDATE watched_diffs SET failure_count = failure_count + 1, "
                "next_poll_at = ? WHERE diff_id = ?",
                (next_poll_at, diff_id),
            )

    def release_lease(self, diff_id: str, owner: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE watched_diffs SET lease_owner = NULL, lease_until = NULL "
                "WHERE diff_id = ? AND lease_owner = ?",
                (diff_id, owner),
            )

    def release_owner_leases(self, owner: str) -> None:
        """Release every poll lease held by one stopped scheduler instance."""

        with self._connect() as connection:
            connection.execute(
                "UPDATE watched_diffs SET lease_owner = NULL, lease_until = NULL "
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
                "SELECT MIN(wd.next_poll_at) FROM watched_diffs wd WHERE EXISTS ("
                "SELECT 1 FROM subscriptions s WHERE s.diff_id = wd.diff_id "
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
                    "SELECT DISTINCT diff_id FROM subscriptions WHERE session_id = ? "
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
                "UPDATE watched_diffs SET next_poll_at = MIN(next_poll_at, ?) WHERE diff_id = ?",
                [(now, diff_id) for diff_id in recovered_diff_ids],
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
                "be.diff_id = source_events.diff_id AND be.kind = source_events.kind "
                "AND be.external_id = source_events.external_id)",
                (cutoff,),
            )
            connection.execute(
                "DELETE FROM subscriptions WHERE state = 'retired' AND updated_at < ?",
                (cutoff,),
            )
            connection.execute(
                "DELETE FROM source_events WHERE NOT EXISTS ("
                "SELECT 1 FROM subscriptions s WHERE s.diff_id = source_events.diff_id)",
            )
            connection.execute(
                "DELETE FROM watched_diffs WHERE NOT EXISTS ("
                "SELECT 1 FROM subscriptions s WHERE s.diff_id = watched_diffs.diff_id)",
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
            result["watched_diffs"] = int(
                connection.execute(
                    "SELECT COUNT(DISTINCT diff_id) FROM subscriptions WHERE state != 'retired'"
                ).fetchone()[0]
            )
            result["open_batches"] = int(
                connection.execute(
                    "SELECT COUNT(*) FROM batches WHERE state IN ('open', 'delivering')"
                ).fetchone()[0]
            )
            result["source_failed_watches"] = int(
                connection.execute(
                    "SELECT COUNT(*) FROM watched_diffs WHERE failure_count > 0 "
                    "AND EXISTS (SELECT 1 FROM subscriptions s "
                    "WHERE s.diff_id = watched_diffs.diff_id AND s.state = 'active')"
                ).fetchone()[0]
            )
            result["source_failure_streak"] = int(
                connection.execute(
                    "SELECT COALESCE(MAX(failure_count), 0) FROM watched_diffs"
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
        snapshot: DiffSnapshot,
        *,
        now: float,
        next_poll_at: float,
        missing_count: int = 0,
        reset_failure_count: bool = True,
    ) -> None:
        previous = connection.execute(
            "SELECT latest_version_id, comments_cursor, ci_cursor "
            "FROM watched_diffs WHERE diff_id = ?",
            (snapshot.diff_id,),
        ).fetchone()
        previous_cursor = SourceCursor(
            latest_version_id=(previous["latest_version_id"] if previous is not None else None),
            comments=previous["comments_cursor"] if previous is not None else None,
            ci=previous["ci_cursor"] if previous is not None else None,
        )
        cursor = snapshot.cursor(previous_cursor)
        connection.execute(
            "INSERT INTO watched_diffs "
            "(diff_id, lifecycle, latest_version_id, last_activity_at, next_poll_at, "
            "comments_cursor, ci_cursor, ci_state, failure_count, last_success_at, "
            "missing_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?) "
            "ON CONFLICT(diff_id) DO UPDATE SET lifecycle = excluded.lifecycle, "
            "latest_version_id = excluded.latest_version_id, "
            "last_activity_at = excluded.last_activity_at, next_poll_at = excluded.next_poll_at, "
            "comments_cursor = excluded.comments_cursor, ci_cursor = excluded.ci_cursor, "
            "ci_state = excluded.ci_state, failure_count = CASE WHEN ? "
            "THEN 0 ELSE watched_diffs.failure_count END, "
            "last_success_at = excluded.last_success_at, missing_count = excluded.missing_count, "
            "lease_owner = NULL, lease_until = NULL",
            (
                snapshot.diff_id,
                snapshot.lifecycle.value,
                snapshot.latest_version_id,
                snapshot.last_activity_at.timestamp(),
                next_poll_at,
                cursor.comments,
                cursor.ci,
                snapshot.ci.aggregate.value,
                now,
                missing_count,
                int(reset_failure_count),
            ),
        )

    @staticmethod
    def _retire_diff_locked(
        connection: sqlite3.Connection,
        diff_id: str,
        reason: str,
        now: float,
    ) -> None:
        rows = connection.execute(
            "SELECT id FROM subscriptions WHERE diff_id = ? AND state != 'retired'",
            (diff_id,),
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
        connection.execute(
            "UPDATE batches SET state = 'cancelled', updated_at = ? "
            "WHERE subscription_id = ? AND state IN ('open', 'delivering')",
            (now, subscription_id),
        )

    @staticmethod
    def _subscription(row: sqlite3.Row) -> Subscription:
        return Subscription(
            id=int(row["id"]),
            session_id=str(row["session_id"]),
            diff_id=str(row["diff_id"]),
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
    def _batch(row: sqlite3.Row) -> Batch:
        return Batch(
            batch_id=str(row["batch_id"]),
            subscription_id=int(row["subscription_id"]),
            session_id=str(row["session_id"]),
            diff_id=str(row["diff_id"]),
            state=BatchState(row["state"]),
            first_event_at=float(row["first_event_at"]),
            flush_at=float(row["flush_at"]),
            retry_count=int(row["retry_count"]),
            next_attempt_at=float(row["next_attempt_at"]),
            summary=row["summary"],
        )

    @staticmethod
    def _watch(row: sqlite3.Row) -> WatchedDiff:
        return WatchedDiff(
            diff_id=str(row["diff_id"]),
            lifecycle=str(row["lifecycle"]),
            latest_version_id=row["latest_version_id"],
            last_activity_at=float(row["last_activity_at"]),
            next_poll_at=float(row["next_poll_at"]),
            cursor=SourceCursor(
                latest_version_id=row["latest_version_id"],
                comments=row["comments_cursor"],
                ci=row["ci_cursor"],
            ),
            failure_count=int(row["failure_count"]),
            last_success_at=(
                float(row["last_success_at"]) if row["last_success_at"] is not None else None
            ),
        )
