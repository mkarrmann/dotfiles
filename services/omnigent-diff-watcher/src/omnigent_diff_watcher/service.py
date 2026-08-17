"""Standalone scheduler and session-label reconciliation."""

from __future__ import annotations

import asyncio
import logging
import time

from .domain import EventKind, SubscriptionState
from .omnigent_client import OmnigentClient, OmnigentDeliveryService, desired_watch
from .phabricator_source import PhabricatorReviewSource
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
        sessions = await self.client.list_sessions()
        for item in sessions:
            session_id = item.get("id")
            if not isinstance(session_id, str):
                continue
            desired = desired_watch(item)
            existing_all = await asyncio.to_thread(
                self.repository.subscriptions_for_session, session_id
            )
            existing_by_diff = {row.diff_id: row for row in existing_all}
            if desired is None:
                if any(row.state is not SubscriptionState.RETIRED for row in existing_all):
                    await self.watcher.unsubscribe(session_id)
                continue
            diff_ids, raw_events = desired
            event_types = frozenset(EventKind(value) for value in raw_events)

            # Retire diffs the session no longer claims, without disturbing the
            # ones it still does. A stack shrinks as its diffs land.
            for row in existing_all:
                if row.diff_id in diff_ids or row.state is SubscriptionState.RETIRED:
                    continue
                await asyncio.to_thread(
                    self.repository.retire_subscription,
                    row.id,
                    "preference_removed",
                    now=time.time(),
                )

            for diff_id in diff_ids:
                existing = existing_by_diff.get(diff_id)
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
                    await self.watcher.subscribe(session_id, diff_id, event_types)
                except SubscriptionError as exc:
                    # One unusable diff (terminal, missing, or a stale label
                    # entry) must not stop the rest of the stack from binding.
                    _logger.warning(
                        "could not reconcile session=%s diff=%s: %s", session_id, diff_id, exc
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
