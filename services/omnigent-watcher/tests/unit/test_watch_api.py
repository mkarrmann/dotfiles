"""Stopping and listing watches, and the source scoping that separates them.

Both surfaces share this code, so ``sources`` is the only thing keeping a diff
unsubscribe away from a generic watch. Every bug these cover has happened.
"""

from __future__ import annotations

from pathlib import Path

from omnigent_watcher.command_source import SOURCE_NAME as COMMAND_SOURCE_NAME
from omnigent_watcher.command_source import CommandSpec
from omnigent_watcher.domain import (
    COMMAND_EVENT_KINDS,
    DIFF_EVENT_KINDS,
    SubscriptionState,
)
from omnigent_watcher.phabricator_source import SOURCE_NAME as PHABRICATOR_SOURCE_NAME
from omnigent_watcher.repository import WatcherRepository
from omnigent_watcher.watch_api import GENERIC_SOURCES, cancel_watches, describe_watches
from tests.support import command_poll, fixture, subscribe_snapshot

SESSION = "conv_test"
KNOB = "jk:presto/presto_batch:demo_knob"
DIFF_SOURCES = frozenset({PHABRICATOR_SOURCE_NAME})


def _spec(interval: float = 60.0) -> str:
    return CommandSpec(["true"], None, interval).to_json()


def _repository(tmp_path: Path) -> WatcherRepository:
    return WatcherRepository(tmp_path / "watcher.sqlite3")


def _generic_watch(repository: WatcherRepository, *, request: bool = True) -> None:
    if request:
        repository.request_watch(
            SESSION, COMMAND_SOURCE_NAME, KNOB, COMMAND_EVENT_KINDS, spec=_spec(), now=1000.0
        )
    repository.subscribe(
        SESSION,
        KNOB,
        COMMAND_EVENT_KINDS,
        command_poll(KNOB),
        now=1000.0,
        next_poll_at=1060.0,
        max_active_subjects=100,
        spec=_spec(),
    )


def _watch(repository: WatcherRepository) -> str:
    diff = fixture("active")
    repository.request_watch(
        SESSION, PHABRICATOR_SOURCE_NAME, diff.subject, DIFF_EVENT_KINDS, spec=None, now=1000.0
    )
    subscribe_snapshot(
        repository,
        SESSION,
        diff.subject,
        DIFF_EVENT_KINDS,
        diff,
        now=1000.0,
        next_poll_at=1060.0,
    )
    return str(diff.subject)


def test_cancelling_generic_watches_never_reaches_a_diff(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    subject = _watch(repository)
    _generic_watch(repository)

    cancel_watches(repository, SESSION, sources=GENERIC_SOURCES, now=2000.0)

    survivor = repository.subscription(SESSION, subject)
    assert survivor is not None and survivor.state is SubscriptionState.ACTIVE
    stopped = repository.subscription(SESSION, KNOB)
    assert stopped is not None and stopped.state is SubscriptionState.RETIRED


def test_cancelling_watches_never_reaches_a_generic_one(tmp_path: Path) -> None:
    """The mirror case, which only became reachable when both surfaces
    started writing to the same table."""
    repository = _repository(tmp_path)
    subject = _watch(repository)
    _generic_watch(repository)

    cancel_watches(repository, SESSION, sources=DIFF_SOURCES, now=2000.0)

    stopped = repository.subscription(SESSION, subject)
    assert stopped is not None and stopped.state is SubscriptionState.RETIRED
    survivor = repository.subscription(SESSION, KNOB)
    assert survivor is not None and survivor.state is SubscriptionState.ACTIVE
    # And its request survives, or the sidecar would quietly stop re-binding it.
    assert [row[2] for row in repository.active_watch_requests(SESSION)] == [KNOB]


def test_cancelling_reaches_a_watch_whose_request_was_already_cancelled(
    tmp_path: Path,
) -> None:
    """An orphan -- cancelled request, surviving subscription -- must be reachable.

    That is the exact state an older build of the tool left behind, and it was
    hit for real in production. Deriving targets from the stored requests would
    find nothing here and leave the watch polling with no way to stop it.
    """
    repository = _repository(tmp_path)
    _generic_watch(repository)
    assert repository.cancel_watch_requests(SESSION, now=1100.0) == 1
    orphan = repository.subscription(SESSION, KNOB)
    assert orphan is not None and orphan.state is SubscriptionState.ACTIVE

    cancel_watches(repository, SESSION, sources=GENERIC_SOURCES, now=2000.0)

    stopped = repository.subscription(SESSION, KNOB)
    assert stopped is not None and stopped.state is SubscriptionState.RETIRED


def test_status_shows_the_command_a_watch_will_keep_running(tmp_path: Path) -> None:
    """The argv outlives the turn that registered it, so it has to be legible
    from the status tool rather than only by reading the database."""
    repository = _repository(tmp_path)
    repository.request_watch(
        SESSION,
        COMMAND_SOURCE_NAME,
        KNOB,
        COMMAND_EVENT_KINDS,
        spec=CommandSpec(["cat", "/tmp/knob"], r"(\d+)", 120.0).to_json(),
        now=1000.0,
    )

    status = describe_watches(repository, SESSION, sources=GENERIC_SOURCES)

    assert KNOB in status
    assert "cat /tmp/knob" in status
    assert "every 120s" in status
    # Quoted, not repr'd: repr escapes the backslash and shows a pattern the
    # caller never typed.
    assert r"(\d+)" in status


def test_status_is_scoped_to_its_own_surface(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    subject = _watch(repository)
    _generic_watch(repository)

    generic = describe_watches(repository, SESSION, sources=GENERIC_SOURCES)
    assert KNOB in generic and subject not in generic

    diffs = describe_watches(repository, SESSION, sources=DIFF_SOURCES)
    assert subject in diffs and KNOB not in diffs
