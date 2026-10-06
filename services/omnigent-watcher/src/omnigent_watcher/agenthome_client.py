"""Agent Home 2.0 (dm-core) sessions, reached through the ``meta ah.session`` CLI.

A watch addressed to ``agenthome:<agent_id>`` wakes an Agent Home session
instead of an Omnigent one. The prefix is explicit rather than inferred from
the id's shape: Agent Home's Claude sessions are ``ah_<token>`` but its Codex
sessions are bare UUIDs, and an id that silently routed to the wrong backend
would be accepted and never fire.

The CLI is the supported contract. dm-core-server also answers HTTP on a local
socket, but that surface is internal to Agent Home and can change underneath
this adapter, and it only reaches sessions on the local host; ``meta`` routes
through www to whichever host owns the session.

Session lookups never raise and never report a session as gone. The worker
probes Omnigent and Agent Home sessions in one scheduler cycle, and an
exception there would abort the cycle for both; a CLI failure is reported as an
unreachable session instead, which defers its batch and, if it persists,
suspends only that session's watches. "Gone" is not reported because Agent
Home cannot say it reliably: ``inspect`` answers ``not_found`` for a session
with no stored record yet, which includes one whose host merely failed to
answer. An archived session therefore suspends after a day and retires with
the seven-day idle limit instead of at once.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass

from .domain import EventDeliveryResult, EventDeliveryStatus, SessionSnapshot
from .phabricator_source import bounded_source_environment

_logger = logging.getLogger(__name__)

AGENT_HOME_PREFIX = "agenthome:"
# Listing fans out to every host the user owns; a cold `meta` start alone
# can take several seconds. Bounded tightly because the worker's cycle is
# sequential: a hung CLI delays Omnigent sessions' wakes too.
_CLI_TIMEOUT_SECONDS = 45.0
# One listing serves every Agent Home session probed in the same cycle.
_LISTING_TTL_SECONDS = 15.0
# The abbreviated transcript keeps each entry's head, which is where the batch
# marker sits (``logic.render_batch_summary``), and drops the oldest turns
# first once over budget, so a recent wake stays visible.
_RECEIPT_CHAR_BUDGET = 200_000
_RECEIPT_HEAD_CHARS = 160


def agent_home_id(session_id: str) -> str | None:
    """The dm-core agent id a watch session id addresses, if it is Agent Home's."""
    if not session_id.startswith(AGENT_HOME_PREFIX):
        return None
    agent_id = session_id.removeprefix(AGENT_HOME_PREFIX)
    return agent_id or None


def is_agent_home(session_id: str) -> bool:
    return agent_home_id(session_id) is not None


@dataclass(frozen=True)
class CliResult:
    """One ``meta`` invocation. ``payload`` is its decoded JSON stdout, if any.

    ``meta -o json`` reports failures as JSON on stdout with a non-zero exit,
    so the payload is kept on failure too: ``error_code`` is how a session that
    does not exist is told apart from a CLI that could not answer.
    """

    returncode: int
    payload: object = None

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def error_code(self) -> str | None:
        if isinstance(self.payload, dict):
            code = self.payload.get("error_code")
            return code if isinstance(code, str) else None
        return None


CliRunner = Callable[[Sequence[str], str | None], Awaitable[CliResult]]


async def run_meta(
    argv: Sequence[str],
    stdin: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    timeout_seconds: float = _CLI_TIMEOUT_SECONDS,
) -> CliResult:
    """Run ``meta`` argv-only. A spawn failure or timeout is ``returncode=-1``."""
    try:
        process = await asyncio.create_subprocess_exec(
            "meta",
            *argv,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=dict(env) if env is not None else bounded_source_environment(),
            # Its own process group, so a timeout reaps the whole tree. Killing
            # only `meta` leaves any child holding stdout, and asyncio's wait()
            # does not return until every pipe closes.
            start_new_session=True,
        )
    except OSError:
        _logger.warning("could not start meta for an Agent Home session call")
        return CliResult(-1)
    try:
        async with asyncio.timeout(timeout_seconds):
            stdout, _ = await process.communicate(
                stdin.encode("utf-8") if stdin is not None else None
            )
    except TimeoutError:
        _kill_group(process.pid)
        await process.wait()
        return CliResult(-1)
    except BaseException:
        # Cancelled with the worker; do not leave `meta` running behind it.
        _kill_group(process.pid)
        raise
    returncode = process.returncode if process.returncode is not None else -1
    return CliResult(returncode, _decode(stdout))


def _kill_group(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signal.SIGKILL)


def _decode(stdout: bytes) -> object:
    """Decode JSON stdout, skipping any non-JSON warning lines ``meta`` prints first."""
    lines = stdout.decode("utf-8", errors="replace").splitlines()
    for index, line in enumerate(lines):
        if line.startswith(("[", "{")):
            try:
                decoded: object = json.loads("\n".join(lines[index:]))
            except json.JSONDecodeError:
                return None
            return decoded
    return None


class AgentHomeClient:
    """The session lookups, receipts, and sends the watcher needs, over ``meta``."""

    def __init__(self, *, runner: CliRunner | None = None) -> None:
        self._runner: CliRunner = runner or run_meta
        # agent id -> owning host, from the last listing that saw it. A stale
        # entry makes the host-scoped call fail rather than answer wrongly; the
        # next listing refreshes it.
        self._hosts: dict[str, str] = {}
        self._listing: tuple[float, list[object]] | None = None

    async def close(self) -> None:
        return None

    async def _live_sessions(self) -> list[object] | None:
        now = time.monotonic()
        if self._listing is not None and now - self._listing[0] < _LISTING_TTL_SECONDS:
            return self._listing[1]
        listing = await self._runner(("ah.session", "list", "--all", "-o", "json"), None)
        if not listing.ok or not isinstance(listing.payload, list):
            return None
        self._listing = (now, listing.payload)
        return listing.payload

    async def get(self, session_id: str) -> SessionSnapshot:
        agent_id = _require_agent_id(session_id)
        rows = await self._live_sessions()
        for row in rows or ():
            if isinstance(row, dict) and row.get("agent_id") == agent_id:
                host = row.get("host")
                if isinstance(host, str) and host:
                    self._hosts[agent_id] = host
                # Only an idle session takes the wake now. A running one would
                # queue it, but deferring keeps the Omnigent semantics: the
                # batch keeps absorbing feedback until it can be acted on, and
                # waiting_on_user / waiting_on_approval are not interrupted.
                idle = row.get("state") == "idle" and row.get("is_running") is not True
                return SessionSnapshot(
                    session_id=session_id,
                    labels={},
                    reachable=True,
                    can_accept_input=idle,
                )
        return _unreachable(session_id)

    async def delivery_receipt(
        self, session_id: str, delivery_id: str
    ) -> EventDeliveryResult | None:
        """Find the batch marker in the session's recent user turns.

        Read from the owning host only. Without ``--host``, ``inspect`` falls
        back to the stored transcript when the live read fails, and that copy
        can lag a wake that was accepted -- which would read as "not sent" and
        send it again.

        :raises AgentHomeError: If the live transcript could not be read, so
            the caller keeps the receipt unknown rather than assuming it absent.
        """
        agent_id = _require_agent_id(session_id)
        host = self._hosts.get(agent_id)
        if host is None:
            await self.get(session_id)
            host = self._hosts.get(agent_id)
        if host is None:
            raise AgentHomeError("Agent Home session is not live on any host")
        argv = [
            "ah.session",
            "inspect",
            "--session-id",
            agent_id,
            "-o",
            "json",
            "--char-budget",
            str(_RECEIPT_CHAR_BUDGET),
            "--head-chars",
            str(_RECEIPT_HEAD_CHARS),
            "--host",
            host,
        ]
        result = await self._runner(argv, None)
        if not result.ok or not isinstance(result.payload, dict):
            raise AgentHomeError("Agent Home transcript could not be read")
        rows = result.payload.get("rows")
        if not isinstance(rows, list):
            raise AgentHomeError("Agent Home transcript was malformed")
        marker = f"[Watcher {delivery_id}]"
        for row in rows:
            if (
                isinstance(row, dict)
                and row.get("role") == "user"
                and isinstance(row.get("text"), str)
                and marker in row["text"]
            ):
                # The abbreviated view carries no per-turn timestamp.
                return EventDeliveryResult(EventDeliveryStatus.ALREADY_ACCEPTED)
        return None

    async def send(self, session_id: str, content: str) -> CliResult:
        agent_id = _require_agent_id(session_id)
        argv = ["ah.session", "message", "--to", agent_id, "--text=-", "-o", "json"]
        host = self._hosts.get(agent_id)
        if host is not None:
            argv += ["--host", host]
        return await self._runner(argv, content)


class AgentHomeError(RuntimeError):
    """A redacted Agent Home CLI failure safe for service logs."""


class AgentHomeDeliveryService:
    """``DeliveryService`` for Agent Home sessions; mirrors ``OmnigentDeliveryService``."""

    def __init__(
        self,
        client: AgentHomeClient,
        *,
        mode: str,
        allowlist: frozenset[str],
        verify_delays: Sequence[float] = (0.5, 2.0, 5.0),
    ) -> None:
        if mode not in {"log_only", "enabled"}:
            raise ValueError("delivery mode must be log_only or enabled")
        self._client = client
        self._mode = mode
        self._allowlist = allowlist
        self._verify_delays = tuple(verify_delays)

    async def delivery_receipt(
        self, session_id: str, delivery_id: str
    ) -> EventDeliveryResult | None:
        if self._mode == "log_only":
            return None
        return await self._client.delivery_receipt(session_id, delivery_id)

    async def deliver_message(
        self,
        session_id: str,
        delivery_id: str,
        content: str,
    ) -> EventDeliveryResult:
        if self._mode == "log_only":
            _logger.info("would deliver batch=%s session=%s", delivery_id, session_id)
            return EventDeliveryResult(EventDeliveryStatus.ACCEPTED)
        if self._allowlist and session_id not in self._allowlist:
            return EventDeliveryResult(EventDeliveryStatus.NOT_SENT)
        try:
            receipt = await self._client.delivery_receipt(session_id, delivery_id)
        except AgentHomeError:
            return EventDeliveryResult(EventDeliveryStatus.NOT_SENT)
        if receipt is not None:
            return receipt
        session = await self._client.get(session_id)
        if session.terminal:
            return EventDeliveryResult(EventDeliveryStatus.TERMINAL)
        if not session.can_accept_input:
            return EventDeliveryResult(EventDeliveryStatus.NOT_SENT)
        sent = await self._client.send(session_id, content)
        if sent.ok:
            return EventDeliveryResult(EventDeliveryStatus.ACCEPTED)
        # A failed or timed-out send may still have reached the session: the
        # CLI can lose its reply after dm-core accepted the turn.
        return await self._verify_uncertain_delivery(session_id, delivery_id)

    async def _verify_uncertain_delivery(
        self, session_id: str, delivery_id: str
    ) -> EventDeliveryResult:
        for delay in self._verify_delays:
            await asyncio.sleep(delay)
            try:
                receipt = await self._client.delivery_receipt(session_id, delivery_id)
            except AgentHomeError:
                continue
            if receipt is not None:
                return receipt
        return EventDeliveryResult(EventDeliveryStatus.DEFERRED)


def _require_agent_id(session_id: str) -> str:
    agent_id = agent_home_id(session_id)
    if agent_id is None:
        raise ValueError(f"{session_id!r} is not an Agent Home session id")
    return agent_id


def _unreachable(session_id: str) -> SessionSnapshot:
    return SessionSnapshot(
        session_id=session_id, labels={}, reachable=False, can_accept_input=False
    )
