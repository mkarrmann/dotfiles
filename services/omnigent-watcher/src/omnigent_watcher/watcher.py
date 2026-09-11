"""Resource-bounded watcher orchestration over the durable repository."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Iterable
from datetime import datetime

from .domain import (
    Batch,
    Clock,
    DeliveryService,
    EventDeliveryStatus,
    EventKind,
    Lifecycle,
    PollResult,
    SessionService,
    Subscription,
    SubscriptionState,
    SystemClock,
    WatchedSubject,
    WatcherConfig,
    WatchSource,
)
from .logic import deterministic_jitter, failure_poll_delay, successful_poll_delay
from .repository import SubscriptionConstraintError, WatcherRepository
from .source_models import ReviewSourceError

_logger = logging.getLogger(__name__)


class SubscriptionError(RuntimeError):
    """A safe, actionable subscription reconciliation failure."""


class Watcher:
    """Sidecar-owned polling, batching, liveness, and delivery engine."""

    def __init__(
        self,
        repository: WatcherRepository,
        sources: Iterable[WatchSource],
        sessions: SessionService,
        delivery: DeliveryService,
        *,
        clock: Clock | None = None,
        config: WatcherConfig | None = None,
        owner: str | None = None,
    ) -> None:
        # Every source registers the same way. There is deliberately no default
        # and no privileged positional one: this engine has no opinion about
        # what it watches, and a fallback source silently routes an unlabelled
        # subject to whichever implementation happened to be listed first.
        self.sources: dict[str, WatchSource] = {source.name: source for source in sources}
        if not self.sources:
            raise ValueError("a watcher needs at least one source")
        self.repository = repository
        self.sessions = sessions
        self.delivery = delivery
        self.clock = clock or SystemClock()
        self.config = config or WatcherConfig()
        self.owner = owner or uuid.uuid4().hex
        self.last_source_error_category: str | None = None

    def source_for(self, name: str) -> WatchSource:
        """Resolve a watch's source by name."""
        resolved = self.sources.get(name)
        if resolved is None:
            raise SubscriptionError(f"no watch source named {name!r} is configured")
        return resolved

    async def subscribe(
        self,
        session_id: str,
        subject: str,
        event_types: frozenset[EventKind],
        *,
        source_name: str,
        spec: str | None = None,
    ) -> tuple[Subscription, bool]:
        existing = await asyncio.to_thread(self.repository.subscription, session_id, subject)
        watch = await asyncio.to_thread(self.repository.watch, subject)
        if (
            existing is None
            and watch is None
            and (
                await asyncio.to_thread(self.repository.active_subject_count)
                >= self.config.max_active_subjects
            )
        ):
            raise SubscriptionError("watcher active-subject limit reached")
        session = await self.sessions.get(session_id)
        if session.terminal:
            raise SubscriptionError("session is closed or no longer exists")
        source = self.source_for(source_name)
        # An existing watch keeps the spec it was created with; a second
        # subscriber to the same subject shares its poll rather than silently
        # redefining how it is read.
        spec = watch.spec if watch is not None else spec
        try:
            source.validate_subject(subject, spec)
        except ValueError as exc:
            raise SubscriptionError(str(exc)) from exc
        unsupported = event_types - source.event_kinds
        if unsupported:
            raise SubscriptionError(f"{source.name} cannot emit: {', '.join(sorted(unsupported))}")
        try:
            result = await source.poll(subject, None, spec)
        except ReviewSourceError as exc:
            # A subject that does not resolve fails here rather than in the
            # validation below, so without this every other failure mode would
            # be reported as SubscriptionError and this one alone would escape.
            raise SubscriptionError(f"could not read the subject: {exc}") from exc
        if result.lifecycle is not Lifecycle.ACTIVE:
            raise SubscriptionError("subject is terminal or missing")
        missing_baseline = event_types & result.failed_kinds
        if missing_baseline:
            raise SubscriptionError(
                f"could not establish a baseline for: {', '.join(sorted(missing_baseline))}"
            )
        now_dt = self.clock.now()
        now = now_dt.timestamp()
        delay = self._success_delay(result, now_dt)
        if watch is not None:
            # Apply transitions for existing subscribers before the new
            # caller's baseline updates the shared source-event rows.
            await asyncio.to_thread(
                self.repository.apply_poll,
                result,
                now=now,
                next_poll_at=now + delay,
                batch_window_seconds=self.config.batch_window_seconds,
            )
        try:
            return await asyncio.to_thread(
                self.repository.subscribe,
                session_id,
                subject,
                event_types,
                result,
                now=now,
                next_poll_at=now + delay,
                max_active_subjects=self.config.max_active_subjects,
                spec=spec,
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
        # Age out quiet watches first, so one that is about to be retired does
        # not spend a liveness probe or a source poll on its way out.
        for session_id, subject in await asyncio.to_thread(
            self.repository.retire_idle_watches,
            now=now,
            max_idle_seconds=self.config.idle_retire_seconds,
        ):
            _logger.info("watcher retired idle watch on %s for session %s", subject, session_id)
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
        for subject in dict.fromkeys(
            subject
            for batch in due_before
            if batch.batch_id in ready_batches
            for subject in batch.subjects
        ):
            watch = await asyncio.to_thread(
                self.repository.claim_watch,
                subject,
                now=now,
                owner=self.owner,
                lease_seconds=self.config.poll_lease_seconds,
            )
            refresh_results[subject] = await self._poll_watch(watch) if watch is not None else False

        claimed = await asyncio.to_thread(
            self.repository.claim_due_watches,
            now=now,
            owner=self.owner,
            lease_seconds=self.config.poll_lease_seconds,
            limit=self.config.poll_concurrency,
        )
        semaphore = asyncio.Semaphore(self.config.poll_concurrency)

        async def poll_one(watch: WatchedSubject) -> None:
            async with semaphore:
                await self._poll_watch(watch)

        if claimed:
            await asyncio.gather(*(poll_one(watch) for watch in claimed))

        for batch in await asyncio.to_thread(self.repository.due_batches, now):
            if batch.batch_id not in ready_batches:
                continue
            # Any diff in the batch failing to refresh defers the whole wake:
            # a stale count for one diff would misreport the stack.
            if any(refresh_results.get(subject) is False for subject in batch.subjects):
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
            await self._retire_session(batch.session_id, "session_terminal", now)
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

    async def _poll_watch(self, watch: WatchedSubject) -> bool:
        try:
            source = self.source_for(watch.source)
            result = await source.poll(watch.subject, watch.cursor, watch.spec)
            now_dt = self.clock.now()
            now = now_dt.timestamp()
            source_failed = bool(result.failed_kinds)
            self.last_source_error_category = result.error_category if source_failed else None
            if result.lifecycle is not Lifecycle.ACTIVE:
                delay = (
                    failure_poll_delay(watch.failure_count + 1, watch.subject)
                    if result.lifecycle is Lifecycle.MISSING
                    else self._success_delay(result, now_dt)
                )
                await asyncio.to_thread(
                    self.repository.apply_poll,
                    result,
                    now=now,
                    next_poll_at=now + delay,
                    batch_window_seconds=self.config.batch_window_seconds,
                )
                if result.lifecycle is Lifecycle.MISSING:
                    await asyncio.to_thread(
                        self.repository.partial_poll_failed,
                        watch.subject,
                        next_poll_at=now + delay,
                    )
                    return False
                return True
            if result.totally_failed:
                delay = failure_poll_delay(watch.failure_count + 1, watch.subject)
                await asyncio.to_thread(
                    self.repository.poll_failed,
                    watch.subject,
                    self.owner,
                    next_poll_at=now + delay,
                )
                return False
            delay = (
                failure_poll_delay(watch.failure_count + 1, watch.subject)
                if source_failed
                else self._success_delay(result, now_dt)
            )
            await asyncio.to_thread(
                self.repository.apply_poll,
                result,
                now=now,
                next_poll_at=now + delay,
                batch_window_seconds=self.config.batch_window_seconds,
            )
            if source_failed:
                await asyncio.to_thread(
                    self.repository.partial_poll_failed,
                    watch.subject,
                    next_poll_at=now + delay,
                )
                return False
            return True
        except asyncio.CancelledError:
            await asyncio.to_thread(
                self.repository.release_lease,
                watch.subject,
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
            delay = failure_poll_delay(watch.failure_count + 1, watch.subject)
            await asyncio.to_thread(
                self.repository.poll_failed,
                watch.subject,
                self.owner,
                next_poll_at=now + delay,
            )
            _logger.warning("watcher source poll failed for %s", watch.subject)
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
            await self._retire_session(batch.session_id, "session_terminal", now)
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
        live = await asyncio.to_thread(
            self.repository.subscriptions_for_session,
            batch.session_id,
            states=(SubscriptionState.ACTIVE,),
        )
        if not live:
            return
        deliveries = [row.last_delivery_at for row in live if row.last_delivery_at is not None]
        last_delivery_at = max(deliveries) if deliveries else None
        if (
            last_delivery_at is not None
            and now < last_delivery_at + self.config.minimum_delivery_interval_seconds
        ):
            await asyncio.to_thread(
                self.repository.defer_batch,
                batch.batch_id,
                now=now,
                retry_at=last_delivery_at + self.config.minimum_delivery_interval_seconds,
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
            await self._retire_session(current.session_id, "delivery_terminal", now)
        else:
            await self._defer(current, now)

    async def _retire_session(self, session_id: str, reason: str, now: float) -> None:
        """Retire every subscription a session owns; batches are session-wide."""
        for row in await asyncio.to_thread(
            self.repository.subscriptions_for_session,
            session_id,
            states=(SubscriptionState.ACTIVE, SubscriptionState.SUSPENDED),
        ):
            await asyncio.to_thread(
                self.repository.retire_subscription,
                row.id,
                reason,
                now=now,
            )

    async def _defer(self, batch: Batch, now: float) -> None:
        await asyncio.to_thread(
            self.repository.defer_batch,
            batch.batch_id,
            now=now,
            retry_at=now + self.config.delivery_retry_seconds,
        )

    def _success_delay(self, result: PollResult, now: datetime) -> float:
        if self.config.poll_interval_override_seconds is not None:
            return self.config.poll_interval_override_seconds
        base = successful_poll_delay(result.last_activity_at, now, result.poll_hint_seconds)
        cycle = int(now.timestamp() // max(base, 1.0))
        return deterministic_jitter(base, result.subject, cycle)
