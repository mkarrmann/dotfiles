"""Resource-bounded watcher orchestration over the durable repository."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime

from .domain import (
    Batch,
    Clock,
    DeliveryService,
    EventDeliveryStatus,
    EventKind,
    ReviewSource,
    SessionService,
    Subscription,
    SubscriptionState,
    SystemClock,
    WatchedDiff,
    WatcherConfig,
)
from .logic import deterministic_jitter, failure_poll_delay, successful_poll_delay
from .repository import SubscriptionConstraintError, WatcherRepository
from .source_models import DiffLifecycle, DiffSnapshot

_logger = logging.getLogger(__name__)


class SubscriptionError(RuntimeError):
    """A safe, actionable subscription reconciliation failure."""


class DiffWatcher:
    """Sidecar-owned polling, batching, liveness, and delivery engine."""

    def __init__(
        self,
        repository: WatcherRepository,
        source: ReviewSource,
        sessions: SessionService,
        delivery: DeliveryService,
        *,
        clock: Clock | None = None,
        config: WatcherConfig | None = None,
        owner: str | None = None,
    ) -> None:
        self.repository = repository
        self.source = source
        self.sessions = sessions
        self.delivery = delivery
        self.clock = clock or SystemClock()
        self.config = config or WatcherConfig()
        self.owner = owner or uuid.uuid4().hex
        self.last_source_error_category: str | None = None

    async def subscribe(
        self,
        session_id: str,
        diff_id: str,
        event_types: frozenset[EventKind],
    ) -> tuple[Subscription, bool]:
        existing = await asyncio.to_thread(self.repository.subscription, session_id, diff_id)
        existing_for_session = await asyncio.to_thread(self.repository.subscription, session_id)
        if (
            existing_for_session is not None
            and existing_for_session.state is not SubscriptionState.RETIRED
            and existing_for_session.diff_id != diff_id
        ):
            raise SubscriptionError("session already watches a different diff")
        watch = await asyncio.to_thread(self.repository.watch, diff_id)
        if (
            existing is None
            and watch is None
            and (
                await asyncio.to_thread(self.repository.active_diff_count)
                >= self.config.max_active_diffs
            )
        ):
            raise SubscriptionError("diff watcher active-diff limit reached")
        session = await self.sessions.get(session_id)
        if session.terminal:
            raise SubscriptionError("session is closed or no longer exists")
        snapshot = await self.source.snapshot(diff_id, None)
        if snapshot.lifecycle.terminal:
            raise SubscriptionError("diff is terminal or missing")
        if EventKind.REVIEW_COMMENT in event_types and snapshot.comments.status != "ok":
            raise SubscriptionError("could not establish the review-comment baseline")
        if EventKind.CI_FAILURE in event_types and snapshot.ci.status != "ok":
            raise SubscriptionError("could not establish the CI baseline")
        now_dt = self.clock.now()
        now = now_dt.timestamp()
        delay = self._success_delay(snapshot, now_dt)
        if watch is not None:
            # Apply transitions for existing subscribers before the new
            # caller's baseline updates the shared source-event rows.
            await asyncio.to_thread(
                self.repository.apply_snapshot,
                snapshot,
                now=now,
                next_poll_at=now + delay,
                batch_window_seconds=self.config.batch_window_seconds,
            )
        try:
            return await asyncio.to_thread(
                self.repository.subscribe,
                session_id,
                diff_id,
                event_types,
                snapshot,
                now=now,
                next_poll_at=now + delay,
                max_active_diffs=self.config.max_active_diffs,
            )
        except SubscriptionConstraintError as exc:
            raise SubscriptionError(str(exc)) from exc

    async def unsubscribe(self, session_id: str) -> bool:
        return await asyncio.to_thread(
            self.repository.unsubscribe,
            session_id,
            now=self.clock.now().timestamp(),
        )

    async def run_iteration(self) -> None:
        """Run one deterministic scheduler cycle without sleeping."""
        now = self.clock.now().timestamp()
        # Retire/suspend sessions before claiming an external source poll. This
        # keeps a lifecycle deadline that coincides with a diff deadline from
        # spending one final network request on a dead session.
        for session_id in await asyncio.to_thread(
            self.repository.liveness_due,
            now,
            self.config.liveness_probe_seconds,
            self.config.suspended_liveness_probe_seconds,
        ):
            await self._check_liveness(session_id)

        due_before = await asyncio.to_thread(self.repository.due_batches, now)
        ready_batches: set[str] = set()
        for batch in due_before:
            if await self._batch_ready_for_refresh(batch, now):
                ready_batches.add(batch.batch_id)
        refresh_results: dict[str, bool] = {}
        for diff_id in dict.fromkeys(
            batch.diff_id for batch in due_before if batch.batch_id in ready_batches
        ):
            watch = await asyncio.to_thread(
                self.repository.claim_watch,
                diff_id,
                now=now,
                owner=self.owner,
                lease_seconds=self.config.poll_lease_seconds,
            )
            refresh_results[diff_id] = await self._poll_watch(watch) if watch is not None else False

        claimed = await asyncio.to_thread(
            self.repository.claim_due_watches,
            now=now,
            owner=self.owner,
            lease_seconds=self.config.poll_lease_seconds,
            limit=self.config.poll_concurrency,
        )
        semaphore = asyncio.Semaphore(self.config.poll_concurrency)

        async def poll_one(watch: WatchedDiff) -> None:
            async with semaphore:
                await self._poll_watch(watch)

        if claimed:
            await asyncio.gather(*(poll_one(watch) for watch in claimed))

        for batch in await asyncio.to_thread(self.repository.due_batches, now):
            if batch.batch_id not in ready_batches:
                continue
            if batch.diff_id in refresh_results and not refresh_results[batch.diff_id]:
                await self._defer(batch, now)
                continue
            await self._flush_batch(batch)

        await asyncio.to_thread(
            self.repository.prune,
            now=now,
            retention_seconds=self.config.completed_retention_seconds,
        )

    async def _batch_ready_for_refresh(self, batch: Batch, now: float) -> bool:
        session = await self.sessions.get(batch.session_id)
        if session.terminal:
            await asyncio.to_thread(
                self.repository.retire_subscription,
                batch.subscription_id,
                "session_terminal",
                now=now,
            )
            return False
        if not session.reachable:
            await asyncio.to_thread(
                self.repository.suspend_or_retire_session,
                batch.session_id,
                now=now,
                terminal_reason=None,
                suspend_after=self.config.unavailable_suspend_seconds,
            )
            await self._defer(batch, now)
            return False
        await asyncio.to_thread(
            self.repository.mark_session_usable,
            batch.session_id,
            now=now,
        )
        if not session.can_accept_input:
            await self._defer(batch, now)
            return False
        return True

    async def _poll_watch(self, watch: WatchedDiff) -> bool:
        try:
            snapshot = await self.source.snapshot(watch.diff_id, watch.cursor)
            now_dt = self.clock.now()
            now = now_dt.timestamp()
            source_failed = snapshot.comments.status == "error" or snapshot.ci.status == "error"
            self.last_source_error_category = (
                self._snapshot_error_category(snapshot) if source_failed else None
            )
            if snapshot.lifecycle.terminal:
                delay = (
                    failure_poll_delay(watch.failure_count + 1, watch.diff_id)
                    if snapshot.lifecycle is DiffLifecycle.MISSING
                    else self._success_delay(snapshot, now_dt)
                )
                await asyncio.to_thread(
                    self.repository.apply_snapshot,
                    snapshot,
                    now=now,
                    next_poll_at=now + delay,
                    batch_window_seconds=self.config.batch_window_seconds,
                )
                if snapshot.lifecycle is DiffLifecycle.MISSING:
                    await asyncio.to_thread(
                        self.repository.partial_poll_failed,
                        watch.diff_id,
                        next_poll_at=now + delay,
                    )
                    return False
                return True
            if snapshot.comments.status == "error" and snapshot.ci.status == "error":
                delay = failure_poll_delay(watch.failure_count + 1, watch.diff_id)
                await asyncio.to_thread(
                    self.repository.poll_failed,
                    watch.diff_id,
                    self.owner,
                    next_poll_at=now + delay,
                )
                return False
            delay = (
                failure_poll_delay(watch.failure_count + 1, watch.diff_id)
                if source_failed
                else self._success_delay(snapshot, now_dt)
            )
            await asyncio.to_thread(
                self.repository.apply_snapshot,
                snapshot,
                now=now,
                next_poll_at=now + delay,
                batch_window_seconds=self.config.batch_window_seconds,
            )
            if source_failed:
                await asyncio.to_thread(
                    self.repository.partial_poll_failed,
                    watch.diff_id,
                    next_poll_at=now + delay,
                )
                return False
            return True
        except asyncio.CancelledError:
            await asyncio.to_thread(
                self.repository.release_lease,
                watch.diff_id,
                self.owner,
            )
            raise
        except Exception as exc:  # noqa: BLE001 - source boundary
            category = getattr(exc, "category", None)
            category_value = getattr(category, "value", None)
            self.last_source_error_category = (
                category_value if isinstance(category_value, str) else "unavailable"
            )
            now = self.clock.now().timestamp()
            delay = failure_poll_delay(watch.failure_count + 1, watch.diff_id)
            await asyncio.to_thread(
                self.repository.poll_failed,
                watch.diff_id,
                self.owner,
                next_poll_at=now + delay,
            )
            _logger.warning("diff watcher source poll failed for %s", watch.diff_id)
            return False

    async def _check_liveness(self, session_id: str) -> None:
        now = self.clock.now().timestamp()
        session = await self.sessions.get(session_id)
        if session.terminal:
            reason = (
                "deleted" if not session.exists else "archived" if session.archived else "closed"
            )
            await asyncio.to_thread(
                self.repository.suspend_or_retire_session,
                session_id,
                now=now,
                terminal_reason=reason,
                suspend_after=self.config.unavailable_suspend_seconds,
            )
        elif session.reachable:
            await asyncio.to_thread(self.repository.mark_session_usable, session_id, now=now)
        else:
            await asyncio.to_thread(
                self.repository.suspend_or_retire_session,
                session_id,
                now=now,
                terminal_reason=None,
                suspend_after=self.config.unavailable_suspend_seconds,
            )

    async def _flush_batch(self, batch: Batch) -> None:
        now = self.clock.now().timestamp()
        session = await self.sessions.get(batch.session_id)
        if session.terminal:
            await asyncio.to_thread(
                self.repository.retire_subscription,
                batch.subscription_id,
                "session_terminal",
                now=now,
            )
            return
        if not session.reachable:
            await asyncio.to_thread(
                self.repository.suspend_or_retire_session,
                batch.session_id,
                now=now,
                terminal_reason=None,
                suspend_after=self.config.unavailable_suspend_seconds,
            )
            await self._defer(batch, now)
            return
        await asyncio.to_thread(self.repository.mark_session_usable, batch.session_id, now=now)
        if not session.can_accept_input:
            await self._defer(batch, now)
            return
        subscription = await asyncio.to_thread(
            self.repository.subscription,
            batch.session_id,
            batch.diff_id,
        )
        if subscription is None or subscription.state.value == "retired":
            return
        if (
            subscription.last_delivery_at is not None
            and now < subscription.last_delivery_at + self.config.minimum_delivery_interval_seconds
        ):
            await asyncio.to_thread(
                self.repository.defer_batch,
                batch.batch_id,
                now=now,
                retry_at=(
                    subscription.last_delivery_at + self.config.minimum_delivery_interval_seconds
                ),
            )
            return
        prepared = await asyncio.to_thread(
            self.repository.prepare_batch,
            batch.batch_id,
            now=now,
        )
        if prepared is None:
            return
        current = await asyncio.to_thread(self.repository.batch, batch.batch_id)
        if current is None or current.summary is None:
            await self._defer(batch, now)
            return
        try:
            result = await self.delivery.deliver_message(
                current.session_id,
                current.batch_id,
                current.summary,
            )
        except Exception:  # noqa: BLE001 - retry with the same stable batch id
            await self._defer(current, now)
            return
        if result.status in {
            EventDeliveryStatus.ACCEPTED,
            EventDeliveryStatus.ALREADY_ACCEPTED,
        }:
            await asyncio.to_thread(
                self.repository.deliver_batch,
                current.batch_id,
                now=now,
            )
        elif result.status is EventDeliveryStatus.TERMINAL:
            await asyncio.to_thread(
                self.repository.retire_subscription,
                current.subscription_id,
                "delivery_terminal",
                now=now,
            )
        else:
            await self._defer(current, now)

    async def _defer(self, batch: Batch, now: float) -> None:
        await asyncio.to_thread(
            self.repository.defer_batch,
            batch.batch_id,
            now=now,
            retry_at=now + self.config.delivery_retry_seconds,
        )

    def _success_delay(self, snapshot: DiffSnapshot, now: datetime) -> float:
        if self.config.poll_interval_override_seconds is not None:
            return self.config.poll_interval_override_seconds
        base = successful_poll_delay(snapshot, now)
        cycle = int(now.timestamp() // max(base, 1.0))
        return deterministic_jitter(base, snapshot.diff_id, cycle)

    @staticmethod
    def _snapshot_error_category(snapshot: DiffSnapshot) -> str:
        categories = {
            component.error.category.value
            for component in (snapshot.comments, snapshot.ci)
            if component.status == "error" and component.error is not None
        }
        return next(iter(categories)) if len(categories) == 1 else "partial"
