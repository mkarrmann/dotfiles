"""Standalone scheduler and session-label reconciliation."""

from __future__ import annotations

import asyncio
import logging
import time

from .command_source import SOURCE_NAME as COMMAND_SOURCE_NAME
from .command_source import CommandSource
from .domain import EventKind, SubscriptionState
from .omnigent_client import OmnigentClient, OmnigentDeliveryService, desired_watch
from .phabricator_source import SOURCE_NAME as PHABRICATOR_SOURCE_NAME
from .phabricator_source import PhabricatorReviewSource, bounded_source_environment
from .repository import WatcherRepository
from .settings import ServiceSettings
from .watcher import DiffWatcher, SubscriptionError

_logger = logging.getLogger(__name__)


class DiffWatcherService:
    def __init__(
        self,
        settings: ServiceSettings,
        *,
        client: OmnigentClient | None = None,
    ) -> None:
        self.settings = settings
        self.client = client or OmnigentClient(settings.server_url)
        self.repository = WatcherRepository(settings.database_path)
        self.watcher = DiffWatcher(
            self.repository,
            PhabricatorReviewSource(),
            self.client,
            OmnigentDeliveryService(
                self.client,
                mode=settings.delivery_mode,
                allowlist=settings.delivery_session_allowlist,
            ),
            config=settings.watcher,
            sources={
                COMMAND_SOURCE_NAME: CommandSource(env=bounded_source_environment()),
            },
        )
        self._next_reconcile = 0.0

    async def run(self) -> None:
        try:
            while True:
                try:
                    await self.run_iteration()
                    delay = await asyncio.to_thread(self._next_delay)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    _logger.exception("diff watcher scheduler iteration failed")
                    delay = self.settings.scheduler_error_retry_seconds
                await asyncio.sleep(max(0.05, delay))
        finally:
            await asyncio.to_thread(
                self.repository.release_owner_leases,
                self.watcher.owner,
            )
            await self.client.close()

    async def run_iteration(self) -> None:
        now = time.time()
        if now >= self._next_reconcile:
            await self.reconcile_subscriptions()
            self._next_reconcile = now + self.settings.reconcile_interval_seconds
        await self.watcher.run_iteration()

    async def reconcile_subscriptions(self) -> None:
        await self.reconcile_requested_watches()
        sessions = await self.client.list_sessions()
        for item in sessions:
            session_id = item.get("id")
            if not isinstance(session_id, str):
                continue
            desired = desired_watch(item)
            # Scoped to diffs: a session's labels describe only its stack, so
            # retiring "everything the labels no longer claim" must not reach a
            # generic watch, which is declared through watch_requests instead.
            existing_all = await asyncio.to_thread(
                self.repository.subscriptions_for_session,
                session_id,
                sources=(PHABRICATOR_SOURCE_NAME,),
            )
            existing_by_diff = {row.subject: row for row in existing_all}
            if desired is None:
                for row in existing_all:
                    if row.state is SubscriptionState.RETIRED:
                        continue
                    await asyncio.to_thread(
                        self.repository.retire_subscription,
                        row.id,
                        # Matches repository.unsubscribe, which this replaced
                        # when the sweep became source-scoped.
                        "unsubscribed",
                        now=time.time(),
                    )
                continue
            subjects, raw_events = desired
            event_types = frozenset(EventKind(value) for value in raw_events)

            # Retire diffs the session no longer claims, without disturbing the
            # ones it still does. A stack shrinks as its diffs land.
            for row in existing_all:
                if row.subject in subjects or row.state is SubscriptionState.RETIRED:
                    continue
                await asyncio.to_thread(
                    self.repository.retire_subscription,
                    row.id,
                    "preference_removed",
                    now=time.time(),
                )

            for subject in subjects:
                existing = existing_by_diff.get(subject)
                if (
                    existing is not None
                    and existing.event_types == event_types
                    and existing.state is not SubscriptionState.RETIRED
                ):
                    continue
                if (
                    existing is not None
                    and existing.state is SubscriptionState.RETIRED
                    and existing.retired_reason not in {"unsubscribed", "preference_removed"}
                ):
                    continue
                try:
                    await self.watcher.subscribe(session_id, subject, event_types)
                except SubscriptionError as exc:
                    # One unusable diff (terminal, missing, or a stale label
                    # entry) must not stop the rest of the stack from binding.
                    _logger.warning(
                        "could not reconcile session=%s diff=%s: %s", session_id, subject, exc
                    )

    async def reconcile_requested_watches(self) -> None:
        """Bind generic watches a session recorded through the MCP tool.

        Unlike the label path this is additive: a request stays the desired
        state until the session cancels it or the session itself dies, so a
        watch survives the session going quiet.
        """
        for (
            session_id,
            source_name,
            subject,
            spec,
            event_types,
        ) in await asyncio.to_thread(self.repository.active_watch_requests):
            existing = await asyncio.to_thread(self.repository.subscription, session_id, subject)
            if (
                existing is not None
                and existing.event_types == event_types
                and existing.state is not SubscriptionState.RETIRED
            ):
                continue
            # A watch retired for cause (its session died, delivery went
            # terminal) must not be resurrected on the next cycle.
            if (
                existing is not None
                and existing.state is SubscriptionState.RETIRED
                and existing.retired_reason not in {"unsubscribed", "preference_removed"}
            ):
                await asyncio.to_thread(
                    self.repository.cancel_watch_requests,
                    session_id,
                    now=time.time(),
                    subject=subject,
                )
                continue
            try:
                await self.watcher.subscribe(
                    session_id,
                    subject,
                    event_types,
                    source_name=source_name,
                    spec=spec,
                )
            except SubscriptionError as exc:
                _logger.warning(
                    "could not bind watch session=%s subject=%s: %s",
                    session_id,
                    subject,
                    exc,
                )

    def _next_delay(self) -> float:
        now = time.time()
        repository_deadline = self.repository.next_wake_at(
            active_probe_seconds=self.settings.watcher.liveness_probe_seconds,
            suspended_probe_seconds=(self.settings.watcher.suspended_liveness_probe_seconds),
        )
        deadlines = [self._next_reconcile]
        if repository_deadline is not None:
            deadlines.append(repository_deadline)
        return max(0.05, min(deadlines) - now)
