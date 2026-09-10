"""Watch anything a command can report: wake when its output changes.

The Phabricator source knows what a diff is. This one deliberately knows
nothing about its subject -- it runs the command it was given, hashes the
output, and reports a change when the hash moves. That is the whole contract,
and it is what makes "wake me when X changes" work for an X the watcher has
never heard of.

The command is stored as an argv list and executed with ``execve``, never
through a shell, so no part of a subject or spec is ever interpreted as shell
syntax. Storing it is not a privilege escalation -- an agent that can subscribe
can already run commands -- but it *is* a longer-lived one, so the argv is
recorded in the database and readable through the status tool.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from .domain import (
    COMMAND_EVENT_KINDS,
    EventKind,
    Lifecycle,
    NormalizedEvent,
    PollResult,
)
from .source_command import (
    SourceCommandError,
    SourceCommandErrorCategory,
    run_text_command,
)
from .source_models import (
    ReviewSourceError,
    SourceErrorCategory,
    fingerprint,
)

__all__ = ["SOURCE_NAME", "CommandSource", "CommandSpec"]

SOURCE_NAME = "command"

# Subjects for every source share one primary key, so a non-Phabricator source
# must namespace its own to stay unable to collide with a bare ``D123``.
SUBJECT_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}:[\x20-\x7e]{1,200}$")

MAX_ARGV_LENGTH = 32
MAX_ARG_LENGTH = 512
# A command watch reports no external activity, so the engine's idle ladder --
# which stretches a quiet diff out to daily polling -- would silently defeat a
# watch whose entire job is catching a change promptly. The cadence is stated
# by the subscriber instead, and held constant.
DEFAULT_INTERVAL_SECONDS = 60.0
MIN_INTERVAL_SECONDS = 30.0
MAX_INTERVAL_SECONDS = 24 * 60 * 60.0

# Per-watch command timeout. 30s suits a CLI lookup, but some legitimate probes
# are slower -- a `meta`/`jf` round trip, or a command that asks a model to
# judge whether a condition has been met. The ceiling is the watcher's poll
# lease (``WatcherConfig.poll_lease_seconds``, 120s): a command that outran the
# lease could have its watch claimed by another poller mid-run. Raise both
# together or not at all.
DEFAULT_TIMEOUT_SECONDS = 30.0
MIN_TIMEOUT_SECONDS = 1.0
MAX_TIMEOUT_SECONDS = 120.0
# One observation per subject: the value either matches the last reading or it
# does not, so the identity never varies and only the fingerprint moves.
OBSERVATION_ID = "value"

_CATEGORY_BY_COMMAND_ERROR = {
    SourceCommandErrorCategory.AUTH: SourceErrorCategory.AUTH,
    SourceCommandErrorCategory.RATE_LIMIT: SourceErrorCategory.RATE_LIMIT,
    SourceCommandErrorCategory.TIMEOUT: SourceErrorCategory.TIMEOUT,
    SourceCommandErrorCategory.OUTPUT_LIMIT: SourceErrorCategory.MALFORMED,
    SourceCommandErrorCategory.MALFORMED: SourceErrorCategory.MALFORMED,
    SourceCommandErrorCategory.EXIT: SourceErrorCategory.UNAVAILABLE,
}


class CommandSpec:
    """How to read one subject: an argv, what part matters, and how often."""

    __slots__ = ("argv", "extract", "interval_seconds", "timeout_seconds")

    def __init__(
        self,
        argv: Sequence[str],
        extract: str | None = None,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.argv = tuple(argv)
        self.extract = extract
        self.interval_seconds = float(interval_seconds)
        self.timeout_seconds = float(timeout_seconds)
        self._validate()

    def _validate(self) -> None:
        if not self.argv:
            raise ValueError("watch command requires a non-empty argv")
        if not MIN_INTERVAL_SECONDS <= self.interval_seconds <= MAX_INTERVAL_SECONDS:
            raise ValueError(
                f"interval_seconds must be between {MIN_INTERVAL_SECONDS:g} "
                f"and {MAX_INTERVAL_SECONDS:g}"
            )
        if not MIN_TIMEOUT_SECONDS <= self.timeout_seconds <= MAX_TIMEOUT_SECONDS:
            raise ValueError(
                f"timeout_seconds must be between {MIN_TIMEOUT_SECONDS:g} "
                f"and {MAX_TIMEOUT_SECONDS:g}"
            )
        if self.timeout_seconds > self.interval_seconds:
            # Otherwise a slow command is still running when its next poll is
            # due, and the watch spends all its time timing out.
            raise ValueError("timeout_seconds must not exceed interval_seconds")
        if len(self.argv) > MAX_ARGV_LENGTH:
            raise ValueError(f"watch command argv may hold at most {MAX_ARGV_LENGTH} arguments")
        for arg in self.argv:
            if not isinstance(arg, str) or not arg:
                raise ValueError("watch command argv must contain non-empty strings")
            if len(arg) > MAX_ARG_LENGTH:
                raise ValueError(f"watch command arguments may be at most {MAX_ARG_LENGTH} chars")
        if self.extract is not None:
            try:
                compiled = re.compile(self.extract)
            except re.error as exc:
                raise ValueError(f"extract is not a valid regular expression: {exc}") from exc
            if compiled.groups > 1:
                raise ValueError("extract may define at most one capturing group")

    def to_json(self) -> str:
        payload: dict[str, object] = {
            "argv": list(self.argv),
            "interval_seconds": self.interval_seconds,
            "timeout_seconds": self.timeout_seconds,
        }
        if self.extract is not None:
            payload["extract"] = self.extract
        return json.dumps(payload, sort_keys=True)

    @classmethod
    def from_json(cls, raw: str | None) -> CommandSpec:
        if not raw:
            raise ValueError("a command watch requires a stored spec")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("stored watch spec is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("stored watch spec must be an object")
        extract = payload.get("extract")
        if extract is not None and not isinstance(extract, str):
            raise ValueError("stored watch spec extract must be a string")
        argv = payload.get("argv")
        if not isinstance(argv, list):
            raise ValueError("stored watch spec argv must be a list")
        interval = payload.get("interval_seconds", DEFAULT_INTERVAL_SECONDS)
        if isinstance(interval, bool) or not isinstance(interval, (int, float)):
            raise ValueError("stored watch spec interval_seconds must be a number")
        timeout = payload.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError("stored watch spec timeout_seconds must be a number")
        return cls(argv, extract, float(interval), float(timeout))

    def observed_value(self, stdout: str) -> str:
        """Reduce raw output to the part whose change should wake a session.

        Without ``extract`` the whole output is the value, which is right for a
        command that prints one thing and wrong for anything carrying a
        timestamp or a request id -- those would fire on every poll.
        """
        if self.extract is None:
            return stdout.strip()
        match = re.search(self.extract, stdout)
        if match is None:
            return ""
        return (match.group(1) if match.re.groups else match.group(0)).strip()


class CommandSource:
    """Poll a subject by running its stored command and hashing the result."""

    def __init__(self, *, env: Mapping[str, str]) -> None:
        # No timeout here: it is per-watch and travels in the spec, so two
        # subjects polled by the same source can have different budgets.
        self._env = dict(env)

    @property
    def name(self) -> str:
        return SOURCE_NAME

    @property
    def event_kinds(self) -> frozenset[EventKind]:
        return COMMAND_EVENT_KINDS

    def validate_subject(self, subject: str, spec: str | None) -> None:
        if not SUBJECT_PATTERN.fullmatch(subject):
            raise ValueError(
                "subject must be namespaced as <prefix>:<identifier>, "
                "for example jk:presto/presto_batch:my_knob"
            )
        CommandSpec.from_json(spec)

    def describe(self, counts: Mapping[EventKind, int]) -> str:
        count = counts.get(EventKind.CHANGED, 0)
        return f"{count} change" if count == 1 else f"{count} changes"

    async def poll(
        self,
        subject: str,
        cursor: str | None,
        spec: str | None = None,
    ) -> PollResult:
        del cursor
        command = CommandSpec.from_json(spec)
        observed_at = datetime.now(UTC)
        try:
            stdout = await run_text_command(
                command.argv,
                env=self._env,
                timeout_seconds=command.timeout_seconds,
            )
        except SourceCommandError as exc:
            # A command that cannot run is a failed poll, not a change. Raising
            # here routes it into the engine's existing backoff rather than
            # letting an outage look like the value flipping.
            raise ReviewSourceError(
                _CATEGORY_BY_COMMAND_ERROR.get(exc.category, SourceErrorCategory.UNAVAILABLE)
            ) from exc

        value = command.observed_value(stdout)
        event = NormalizedEvent(
            subject=subject,
            kind=EventKind.CHANGED,
            external_id=OBSERVATION_ID,
            version_id="",
            fingerprint=fingerprint(value),
            changed_at=observed_at,
        )
        return PollResult(
            subject=subject,
            source=SOURCE_NAME,
            lifecycle=Lifecycle.ACTIVE,
            state_label="active",
            latest_version_id=None,
            last_activity_at=observed_at,
            observed_at=observed_at,
            cursor=None,
            status="ok",
            events={EventKind.CHANGED: (event,)},
            ok_kinds=COMMAND_EVENT_KINDS,
            poll_hint_seconds=command.interval_seconds,
        )
