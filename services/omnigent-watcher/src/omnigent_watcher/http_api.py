"""The watcher's HTTP surface, mounted into the Omnigent server on the hub.

Watching is a hub-side activity. The sidecar that polls, the database it polls
from, and the sessions it wakes all live on the active hub, and only the hub
runs the sidecar at all -- every unit carries ``ExecCondition=omnigent-hub
gate``. The MCP tools, by contrast, run wherever the agent runs.

Until this existed the tools opened the database by *path*, which silently
meant "whichever machine I am on". On the hub that happened to be the real
database; on every other devserver it was an empty file no sidecar would ever
poll, so a watch registered there was accepted and then never fired. The
failure was invisible from the hub, which is where it kept being tested.

So the tools became HTTP clients and the work moved here. This module is
mounted by dotted path through the server's ``debug_router_modules`` key (see
``omnigent_config/server.yaml``), which reaches clients over the same
``127.0.0.1:6767`` forward the tools already use to validate a session. No new
tunnel, no second port to health-check.

The cost, stated plainly: the Omnigent server now imports this package, so a
watcher schema change means restarting the server as well as the sidecar. The
alternative was a second forwarded port, which would have duplicated the
tunnel-recovery logic that makes the existing forward reliable.

Trust: the server binds loopback only and this router inherits that posture,
the same as every other route on 6767. The caller has already validated the
session it names; this does not re-validate, because the only way to reach the
router is to already be inside the trust boundary.
"""

from __future__ import annotations

import asyncio
import time
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .command_source import SOURCE_NAME as COMMAND_SOURCE_NAME
from .command_source import CommandSource, CommandSpec
from .domain import (
    COMMAND_EVENT_KINDS,
    DIFF_EVENT_KINDS,
    EventDeliveryResult,
    EventKind,
    SessionSnapshot,
)
from .phabricator_source import SOURCE_NAME as PHABRICATOR_SOURCE_NAME
from .phabricator_source import PhabricatorReviewSource, bounded_source_environment
from .repository import StaleSchemaError, WatcherRepository
from .settings import ServiceSettings
from .watcher import SubscriptionError, Watcher

__all__ = ["DEBUG_ROUTERS", "router"]

# Binding a stack is one source read per subject and they are slow. Bounded
# rather than unbounded: the sidecar polls at 2, and this shares its budget.
_BIND_CONCURRENCY = 4

router = APIRouter()


class _CallerSession:
    """A ``SessionService`` answering for a session the caller already named.

    ``Watcher.subscribe`` re-checks that a session is not terminal, which
    normally costs a round trip to the very server this router is running
    inside. The MCP client validated the id before calling, so answering from
    that beats a self-call that could stall on the server's own event loop.
    """

    async def get(self, session_id: str) -> SessionSnapshot:
        return SessionSnapshot(session_id=session_id, labels={})


class _UnusedDelivery:
    """``Watcher`` needs a delivery service; subscribing never delivers.

    Waking a session is the sidecar's job, in the sidecar's process. Raising
    rather than quietly no-op'ing means a future path that starts delivering
    from here fails loudly instead of dropping wakes.
    """

    async def deliver_message(
        self, session_id: str, delivery_id: str, content: str
    ) -> EventDeliveryResult:
        raise RuntimeError("the watcher HTTP API does not deliver notifications")


def _settings() -> ServiceSettings | None:
    from pathlib import Path

    config = Path(__file__).resolve().parents[2] / "config.toml"
    if config.is_file():
        try:
            return ServiceSettings.load(config)
        except (OSError, ValueError):
            return None
    return None


def _repository() -> WatcherRepository:
    """Open the sidecar's database.

    ``migrate=False``: the sidecar owns the schema. Two processes migrating one
    database is how they end up disagreeing about what the tables mean, and the
    sidecar's copy of the old code is already loaded in memory.
    """
    from pathlib import Path

    from .database import DEFAULT_DATABASE_PATH, resolve

    settings = _settings()
    configured = (
        settings.database_path if settings is not None else Path(DEFAULT_DATABASE_PATH).expanduser()
    )
    try:
        return WatcherRepository(resolve(configured), migrate=False)
    except StaleSchemaError as exc:
        # Now an honest error: this runs on the hub, beside the sidecar that
        # can and does migrate, so restarting it is advice the operator can
        # actually follow.
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _engine(repository: WatcherRepository) -> Watcher:
    """The same engine the sidecar runs, for one synchronous subscribe.

    Sharing ``Watcher.subscribe`` rather than reimplementing it is the point:
    binding reads each subject for a baseline and applies the active-subject
    limit, and a second implementation would drift from the one the sidecar
    enforces.
    """
    settings = _settings()
    return Watcher(
        repository,
        (PhabricatorReviewSource(), CommandSource(env=bounded_source_environment())),
        _CallerSession(),
        _UnusedDelivery(),
        config=settings.watcher if settings is not None else None,
    )


class SubscribeRequest(BaseModel):
    session_id: Annotated[str, Field(min_length=1, max_length=128)]
    source: Annotated[str, Field(min_length=1, max_length=32)]
    subjects: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=256)]],
        Field(min_length=1, max_length=20),
    ]
    events: Annotated[list[str], Field(min_length=1, max_length=8)]
    spec: str | None = None


class SubscribeResponse(BaseModel):
    bound: list[str]
    failures: list[str]


class CancelRequest(BaseModel):
    session_id: Annotated[str, Field(min_length=1, max_length=128)]
    sources: Annotated[list[str], Field(min_length=1, max_length=8)]
    subject: str | None = None


class CancelResponse(BaseModel):
    detail: str


class StatusResponse(BaseModel):
    detail: str


def _kinds(source: str, events: list[str]) -> frozenset[EventKind]:
    """Validate requested event kinds against what the source can emit."""
    allowed = {
        PHABRICATOR_SOURCE_NAME: DIFF_EVENT_KINDS,
        COMMAND_SOURCE_NAME: COMMAND_EVENT_KINDS,
    }.get(source)
    if allowed is None:
        raise HTTPException(status_code=400, detail=f"no watch source named {source!r}")
    try:
        requested = frozenset(EventKind(value) for value in events)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"unknown event kind: {exc}") from exc
    unsupported = requested - allowed
    if unsupported:
        raise HTTPException(
            status_code=400,
            detail=f"{source} cannot emit: {', '.join(sorted(unsupported))}",
        )
    return requested


@router.post("/v1/watches", response_model=SubscribeResponse)
async def create_watches(request: SubscribeRequest) -> SubscribeResponse:
    """Bind each subject, then record the requests that survive a restart.

    One unreadable subject does not sink the rest: a stack routinely holds a
    diff that has just landed, and refusing the whole call over it would be
    worse than naming it. The request rows are written only for what bound, so
    a subject that could not be read now is not re-attempted forever.
    """
    kinds = _kinds(request.source, request.events)
    if request.source == COMMAND_SOURCE_NAME:
        try:
            CommandSpec.from_json(request.spec)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    repository = _repository()
    engine = _engine(repository)
    limit = asyncio.Semaphore(_BIND_CONCURRENCY)

    async def bind_one(subject: str) -> tuple[str, str | None]:
        async with limit:
            try:
                await engine.subscribe(
                    request.session_id,
                    subject,
                    kinds,
                    source_name=request.source,
                    spec=request.spec,
                )
            except SubscriptionError as exc:
                return subject, str(exc)
            return subject, None

    bound: list[str] = []
    failures: list[str] = []
    subjects = list(dict.fromkeys(request.subjects))
    for subject, error in await asyncio.gather(*(bind_one(s) for s in subjects)):
        if error is None:
            bound.append(subject)
        else:
            failures.append(f"{subject} ({error})")

    now = time.time()
    for subject in bound:
        await asyncio.to_thread(
            repository.request_watch,
            request.session_id,
            request.source,
            subject,
            kinds,
            spec=request.spec,
            now=now,
        )
    return SubscribeResponse(bound=bound, failures=failures)


@router.post("/v1/watches/cancel", response_model=CancelResponse)
async def cancel_watches_endpoint(request: CancelRequest) -> CancelResponse:
    from .watch_api import cancel_watches

    repository = _repository()
    detail = await asyncio.to_thread(
        cancel_watches,
        repository,
        request.session_id,
        request.subject,
        sources=frozenset(request.sources),
    )
    return CancelResponse(detail=detail)


@router.get("/v1/watches", response_model=StatusResponse)
async def list_watches(session_id: str, sources: str) -> StatusResponse:
    """``sources`` is comma-separated; it scopes one surface from the other."""
    from .watch_api import describe_watches

    repository = _repository()
    scoped = frozenset(part for part in sources.split(",") if part)
    if not scoped:
        raise HTTPException(status_code=400, detail="at least one source is required")
    detail = await asyncio.to_thread(describe_watches, repository, session_id, sources=scoped)
    return StatusResponse(detail=detail)


DEBUG_ROUTERS: list[tuple[Any, str, list[str]]] = [(router, "", ["watch"])]
