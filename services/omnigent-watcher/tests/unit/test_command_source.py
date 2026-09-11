"""The generic source: wake when a command's output changes."""

from __future__ import annotations

import pytest

from omnigent_watcher.command_source import (
    SOURCE_NAME,
    CommandSource,
    CommandSpec,
)
from omnigent_watcher.domain import EventKind, Lifecycle
from omnigent_watcher.source_models import ReviewSourceError

ENV = {"PATH": "/usr/bin:/bin"}


def _source() -> CommandSource:
    return CommandSource(env=ENV)


def _spec(*argv: str, **kwargs: object) -> str:
    return CommandSpec(argv, **kwargs).to_json()  # type: ignore[arg-type]


async def test_output_becomes_one_fingerprinted_observation() -> None:
    result = await _source().poll("jk:demo", None, _spec("printf", "1/10"))

    assert result.source == SOURCE_NAME
    assert result.lifecycle is Lifecycle.ACTIVE
    assert result.ok_kinds == frozenset({EventKind.CHANGED})
    assert not result.failed_kinds
    (event,) = result.events[EventKind.CHANGED]
    assert event.subject == "jk:demo"
    assert event.fingerprint.startswith("sha256:")


async def test_same_output_fingerprints_identically_and_different_output_does_not() -> None:
    """This is the whole change-detection contract: the engine deduplicates on
    fingerprint, so a stable value must hash stably or every poll would wake."""
    source = _source()
    first = await source.poll("jk:demo", None, _spec("printf", "1/10"))
    again = await source.poll("jk:demo", None, _spec("printf", "1/10"))
    changed = await source.poll("jk:demo", None, _spec("printf", "10/10"))

    assert (
        first.events[EventKind.CHANGED][0].fingerprint
        == again.events[EventKind.CHANGED][0].fingerprint
    )
    assert (
        first.events[EventKind.CHANGED][0].fingerprint
        != changed.events[EventKind.CHANGED][0].fingerprint
    )
    # The external id is constant so the two readings collide on one row and
    # register as a change rather than accumulating as two findings.
    assert first.events[EventKind.CHANGED][0].external_id == (
        changed.events[EventKind.CHANGED][0].external_id
    )


async def test_extract_ignores_noise_that_would_otherwise_wake_every_poll() -> None:
    source = _source()
    spec = _spec("printf", "value=1/10 request_id=abc", extract=r"value=(\S+)")
    other = _spec("printf", "value=1/10 request_id=xyz", extract=r"value=(\S+)")

    first = await source.poll("jk:demo", None, spec)
    second = await source.poll("jk:demo", None, other)

    assert (
        first.events[EventKind.CHANGED][0].fingerprint
        == second.events[EventKind.CHANGED][0].fingerprint
    )


async def test_a_failing_command_is_a_failed_poll_not_a_change() -> None:
    """An outage must route into backoff. Reporting it as a new value would
    wake the session for a change that did not happen, then wake it again when
    the command recovered."""
    with pytest.raises(ReviewSourceError):
        await _source().poll("jk:demo", None, _spec("false"))


async def test_the_command_is_never_run_through_a_shell() -> None:
    """Metacharacters are inert because argv goes straight to execve."""
    result = await _source().poll("jk:demo", None, _spec("printf", "a; rm -rf /tmp/x && b"))

    assert result.ok_kinds == frozenset({EventKind.CHANGED})


def test_interval_bounds_and_argv_are_validated() -> None:
    with pytest.raises(ValueError, match="non-empty argv"):
        CommandSpec([])
    with pytest.raises(ValueError, match="interval_seconds"):
        CommandSpec(["true"], interval_seconds=1)
    with pytest.raises(ValueError, match="interval_seconds"):
        CommandSpec(["true"], interval_seconds=10**9)
    with pytest.raises(ValueError, match="at most one capturing group"):
        CommandSpec(["true"], extract=r"(a)(b)")
    with pytest.raises(ValueError, match="valid regular expression"):
        CommandSpec(["true"], extract="(")


def test_spec_round_trips_through_storage() -> None:
    spec = CommandSpec(["jk", "get", "a/b:c"], r"(\d+)", 300.0)
    restored = CommandSpec.from_json(spec.to_json())

    assert restored.argv == spec.argv
    assert restored.extract == spec.extract
    assert restored.interval_seconds == spec.interval_seconds


def test_subject_must_be_namespaced_so_it_cannot_collide_with_a_diff() -> None:
    source = _source()
    spec = _spec("true")

    source.validate_subject("jk:presto/presto_batch:my_knob", spec)
    with pytest.raises(ValueError, match="namespaced"):
        source.validate_subject("D12345", spec)
    with pytest.raises(ValueError, match="namespaced"):
        source.validate_subject("no-namespace", spec)


def test_a_command_watch_holds_its_interval_instead_of_backing_off() -> None:
    """The idle ladder would stretch a stable watch to daily polling, which
    defeats the point of watching for a change."""
    from omnigent_watcher.logic import successful_poll_delay

    spec = CommandSpec(["true"], interval_seconds=120.0)
    assert spec.interval_seconds == 120.0

    import datetime as dt

    long_ago = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)
    now = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    assert successful_poll_delay(long_ago, now, spec.interval_seconds) == 120.0


async def test_a_slow_command_may_raise_its_own_timeout() -> None:
    """The budget is per-watch, so a probe that asks a model to judge a
    condition is not held to the same 30s as a CLI lookup."""
    source = _source()
    spec = _spec("sleep", "2", interval_seconds=60, timeout_seconds=1)
    with pytest.raises(ReviewSourceError):
        await source.poll("jk:demo", None, spec)

    generous = _spec("sh", "-c", "sleep 2; printf ok", interval_seconds=60, timeout_seconds=10)
    result = await source.poll("jk:demo", None, generous)
    assert result.ok_kinds == frozenset({EventKind.CHANGED})


def test_timeout_is_bounded_and_cannot_outrun_its_interval() -> None:
    with pytest.raises(ValueError, match="timeout_seconds must be between"):
        CommandSpec(["true"], interval_seconds=300, timeout_seconds=0)
    with pytest.raises(ValueError, match="timeout_seconds must be between"):
        CommandSpec(["true"], interval_seconds=3600, timeout_seconds=600)
    # A command still running when its next poll is due would make the watch
    # spend all its time timing out.
    with pytest.raises(ValueError, match="must not exceed interval_seconds"):
        CommandSpec(["true"], interval_seconds=30, timeout_seconds=60)


def test_timeout_round_trips_and_defaults_for_older_specs() -> None:
    import json as _json

    spec = CommandSpec(["true"], None, 120.0, 90.0)
    assert CommandSpec.from_json(spec.to_json()).timeout_seconds == 90.0
    # A spec stored before this field existed still loads.
    legacy = _json.dumps({"argv": ["true"], "interval_seconds": 60.0})
    assert CommandSpec.from_json(legacy).timeout_seconds == 30.0


async def test_a_missing_executable_is_a_source_error_not_a_bare_oserror() -> None:
    """A command that cannot even be spawned must fail like any other bad poll.

    ``cat /missing-file`` exits non-zero and was already covered; a missing
    *binary* fails in ``create_subprocess_exec`` itself, and the bare OSError
    escaped ``poll()`` entirely -- past the engine's backoff, and out of the
    MCP tool as a FileNotFoundError instead of an actionable message. Found by
    running the real tool against the real server.
    """
    import pytest

    from omnigent_watcher.command_source import CommandSource, CommandSpec
    from omnigent_watcher.source_models import ReviewSourceError

    source = CommandSource(env={"PATH": "/usr/bin:/bin"})
    spec = CommandSpec(["/nonexistent/definitely-not-here"]).to_json()
    with pytest.raises(ReviewSourceError):
        await source.poll("jk:demo", None, spec)
