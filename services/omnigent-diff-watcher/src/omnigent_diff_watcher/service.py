"""Standalone scheduler and watch-request reconciliation."""

from __future__ import annotations

import asyncio
import logging
import time

from .command_source import SOURCE_NAME as COMMAND_SOURCE_NAME
from .command_source import CommandSource
from .domain import SubscriptionState
from .omnigent_client import OmnigentClient, OmnigentDeliveryService
from .phabricator_source import PhabricatorReviewSource, bounded_source_environment
from .repository import WatcherRepository
from .settings import ServiceSettings
from .watcher import DiffWatcher, SubscriptionError

_logger = logging.getLogger(__name__)

# A binding that fails is retried on a doubling delay rather than every cycle.
# Reconciliation is desired-state, so nothing records that a subject could not
# be bound and the next cycle simply tries again -- which for a permanently
# unusable subject means forever, at one HTTP call and one `jf` subprocess per
# cycle. Deliberately not a classification of which errors are permanent: the
# same decay serves a terminal diff and a Phabricator outage, and misjudging
# "permanent" is how a subject that later becomes readable never binds.
RECONCILE_BACKOFF_BASE_SECONDS = 60.0
RECONCILE_BACKOFF_MAX_SECONDS = 6 * 60 * 60.0


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
        # (session_id, subject) -> (consecutive failures, earliest next attempt).
        # In memory rather than a table: the cost being avoided is a long-lived
        # process retrying every 15s, and a restart re-attempting each binding
        # once is the correct behaviour anyway.
        self._deferred_bindings: dict[tuple[str, str], tuple[int, float]] = {}

    def _binding_deferred(self, session_id: str, subject: str, now: float) -> bool:
        entry = self._deferred_bindings.get((session_id, subject))
        return entry is not None and now < entry[1]

    def _defer_binding(self, session_id: str, subject: str, now: float) -> float:
        failures, _ = self._deferred_bindings.get((session_id, subject), (0, 0.0))
        failures += 1
        delay = float(
            min(
                RECONCILE_BACKOFF_BASE_SECONDS * 2 ** (failures - 1),
                RECONCILE_BACKOFF_MAX_SECONDS,
            )
        )
        self._deferred_bindings[(session_id, subject)] = (failures, now + delay)
        return delay

    def _binding_succeeded(self, session_id: str, subject: str) -> None:
        self._deferred_bindings.pop((session_id, subject), None)

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
        """Re-bind whatever the recorded watch requests still ask for.

        Every watch is registered synchronously by the MCP tool, which binds it
        before returning, so this is a *recovery* pass rather than the path a
        watch normally takes: it re-establishes subscriptions after a restart,
        and picks up rows written while the sidecar was down.

        It replaced a sweep over session labels. That sweep was how a watch used
        to be declared at all, and it is why the watcher needed to poll
        ``GET /v1/sessions`` and parse two labels off every session in the
        install.
        """
        wanted = await self.reconcile_requested_watches()
        # A binding nobody wants any more takes its backoff entry with it, so a
        # session that re-registers later starts fresh rather than inheriting a
        # six-hour deferral.
        for key in self._deferred_bindings.keys() - wanted:
            del self._deferred_bindings[key]

    async def reconcile_requested_watches(self) -> set[tuple[str, str]]:
        """Bind the watches sessions have recorded, whatever their source.

        Additive: a request stays the desired state until the session cancels it
        or the session itself dies, so a watch survives the session going quiet.

        :returns: The ``(session_id, subject)`` bindings still wanted, so the
            caller can expire backoff entries for the ones that are not.
        """
        wanted: set[tuple[str, str]] = set()
        for (
            session_id,
            source_name,
            subject,
            spec,
            event_types,
        ) in await asyncio.to_thread(self.repository.active_watch_requests):
            wanted.add((session_id, subject))
            existing = await asyncio.to_thread(self.repository.subscription, session_id, subject)
            if (
                existing is not None
                and existing.event_types == event_types
                and existing.state is not SubscriptionState.RETIRED
            ):
                self._binding_succeeded(session_id, subject)
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
            if self._binding_deferred(session_id, subject, time.time()):
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
                delay = self._defer_binding(session_id, subject, time.time())
                _logger.warning(
                    "could not bind watch session=%s subject=%s: %s (retrying in %.0fs)",
                    session_id,
                    subject,
                    exc,
                    delay,
                )
            else:
                self._binding_succeeded(session_id, subject)
        return wanted

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
