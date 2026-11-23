"""The HTTP routes T3 Code v0.1.6's OpenCode 2 client calls, and the session registry."""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import logging
import os
import sqlite3
import time
from collections.abc import Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal, cast
from urllib.parse import unquote

import httpx
from aiohttp import web

from . import approvals, history, textgen
from .catalog import DEFAULT_MODEL, Catalog, load_catalog
from .events import EventFeed
from .omnigent import OmnigentClient, OmnigentError
from .sessions import PROVIDER_LABEL, SESSION_LABEL, Session, Wake, conversation_named_by, session_id_for
from .store import Store
from .t3state import Thread, thread_for
from .wire import ERROR_STATUS, ErrorTag, Json, JsonObject, error_body, event, location, message_id, now_ms, server_info

LOG = logging.getLogger(__name__)

USERNAME: Final = "opencode"
# The forwarding server T3 Code runs names the T3 Code server it serves (`proxy`).
T3_HOME_HEADER: Final = "X-Omnigent-OpenCode-T3-Home"
SWEEP_S: Final = 3600.0
RELEASE_SWEEP_S: Final = 60.0
# A session T3 Code used this recently is not released, whatever its thread says.
RELEASE_GRACE_MS: Final = 120_000
# omnigent-watcher's watch sources (services/omnigent-watcher); a new one must be added here.
WATCH_SOURCES: Final = ["phabricator", "command"]
# Omnigent session states with no turn under way and no human awaited.
RELEASABLE_STATUSES: Final = frozenset({"idle", "failed"})
# How soon a catalog missing an agent's models (a failed probe) is loaded again.
CATALOG_RETRY_S: Final = 60.0
STATUS_PATH: Final = "/api/omnigent-opencode/status"
# What a fork's model_override or reasoning_effort takes to reset to the agent's default.
OMNIGENT_RESET: Final = "default"


@dataclass
class Config:
    password: str | None
    """T3 Code's password for this server; `None` behind the forwarding server, which checks it (`proxy`)."""
    host_spec: str
    agent_specs: list[str]
    default_directory: str
    watch_ms: int | None = None
    """Follow each session this long after its last activity, T3 Code listening or not; `None` follows only while asked."""


class ApiError(Exception):
    def __init__(self, tag: ErrorTag, message: str, **fields: Any) -> None:
        super().__init__(message)
        self.tag = tag
        self.fields = fields

    def response(self) -> web.Response:
        return web.json_response(error_body(self.tag, str(self), **self.fields), status=ERROR_STATUS[self.tag])


@dataclass
class TextGenSession:
    id: str
    title: str
    directory: str
    model: Json
    created: int
    running: bool = False

    def info(self) -> JsonObject:
        return textgen.session_info(self.id, self.title, self.directory, self.model, self.created)


class App:
    def __init__(self, config: Config, client: OmnigentClient, store: Store, wake: Wake | None = None) -> None:
        self.config = config
        self.client = client
        self.store = store
        self.wake = wake
        self.code: str | None = None
        """The code this process started with, which the daemon reports (`cli.daemon_stale`)."""
        self.feed = EventFeed()
        self.sessions: dict[str, Session] = {}
        self.text_sessions: dict[str, TextGenSession] = {}
        self._catalog: Catalog | None = None
        self._catalog_lock = asyncio.Lock()
        self._catalog_loaded = 0.0
        self._catalog_reload: asyncio.Task[None] | None = None
        self._listed_directories: set[str] = set()
        self._loading: dict[str, asyncio.Future[Session]] = {}
        self._opened_at: dict[str, int] = {}
        self.release_grace_ms = RELEASE_GRACE_MS
        """When each followed session was last loaded, which a just-opened session's `active` does not say."""
        self._background: set[asyncio.Future[None]] = set()

    # -- catalog --------------------------------------------------------

    async def catalog(self) -> Catalog:
        """The catalog, loaded once; one missing an agent's models is reloaded in the background."""
        async with self._catalog_lock:
            if self._catalog is None:
                self._catalog = await load_catalog(self.client, self.config.host_spec, self.config.agent_specs)
                self._catalog_loaded = time.monotonic()
            elif (
                not self._catalog.complete
                and self._catalog_reload is None
                and time.monotonic() - self._catalog_loaded >= CATALOG_RETRY_S
            ):
                self._catalog_reload = asyncio.create_task(self._reload_catalog())
            return self._catalog

    async def _reload_catalog(self) -> None:
        try:
            self._catalog = await load_catalog(self.client, self.config.host_spec, self.config.agent_specs)
        except (OmnigentError, httpx.HTTPError) as exc:
            LOG.warning("reloading the model catalog failed: %s", exc)
        finally:
            self._catalog_loaded = time.monotonic()
            self._catalog_reload = None

    async def close_catalog(self) -> None:
        if self._catalog_reload is not None:
            self._catalog_reload.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._catalog_reload

    # -- sessions -------------------------------------------------------

    async def session(self, session_id: str) -> Session:
        session = self.sessions.get(session_id)
        if session is not None:
            return session
        loading = self._loading.get(session_id)
        if loading is None:
            loading = asyncio.ensure_future(self._load(session_id))
            self._loading[session_id] = loading
            loading.add_done_callback(lambda _: self._loading.pop(session_id, None))
        return await asyncio.shield(loading)

    async def _load(self, session_id: str) -> Session:
        """An OpenCode session this process has not seen since it started: rebuild it from its Omnigent session."""
        conversation_id = self.store.conversation(session_id) or conversation_named_by(session_id)
        if conversation_id is None:
            raise ApiError("SessionNotFoundError", f"no session {session_id}", sessionID=session_id)
        try:
            snapshot = await self.client.session_snapshot(conversation_id, items=False)
        except OmnigentError as exc:
            if exc.gone:
                raise ApiError(
                    "SessionNotFoundError", f"Omnigent session {conversation_id} is gone", sessionID=session_id
                ) from exc
            raise
        catalog = await self.catalog()
        labels = snapshot.get("labels") or {}
        # A fork runs a clone of its source's agent, so the provider is also recorded in a label.
        target = (
            catalog.targets.get(str(labels.get(PROVIDER_LABEL)))
            or next((t for t in catalog.targets.values() if t.agent_id == snapshot.get("agent_id")), None)
            or catalog.targets.get(str(snapshot.get("agent_name")))
        )
        if target is None:
            raise ApiError(
                "SessionNotFoundError",
                f"Omnigent session {conversation_id} runs agent {snapshot.get('agent_name')}, which this server does not offer",
                sessionID=session_id,
            )
        model: JsonObject = {"providerID": target.name, "id": str(snapshot.get("model_override") or DEFAULT_MODEL)}
        if snapshot.get("reasoning_effort"):
            model["variant"] = str(snapshot["reasoning_effort"])
        created = snapshot.get("created_at")
        session = Session(
            session_id=session_id,
            conversation_id=conversation_id,
            target=target,
            directory=str(snapshot.get("workspace") or self.config.default_directory),
            model=model,
            client=self.client,
            store=self.store,
            publish=self.feed.publish,
            adopt=self.adopt,
            wake=self.wake,
            title=snapshot.get("title") or None,
            created=int(created * 1000) if isinstance(created, (int, float)) else None,
        )
        return self._register(session)

    def _register(self, session: Session) -> Session:
        self.sessions[session.id] = session
        if session.parent is None:
            self.store.watch(session.id, session.conversation_id, now_ms())
            self._opened_at[session.id] = now_ms()
            if self.store.released(session.id):
                # Only T3 Code loads a released session (`follow` skips them), so it is in use again.
                self.store.unrelease(session.id)
                self._in_background(self._resume_tasks(session.id))
        return session

    async def _unfollow(self, session: Session) -> None:
        """Does not yield to the event loop: `Session.close` only cancels tasks."""
        for followed in [session, *session.children.values()]:
            await followed.close()
            self.sessions.pop(followed.id, None)
        self._opened_at.pop(session.id, None)

    def _in_background(self, work: Coroutine[Any, Any, None]) -> None:
        task = asyncio.ensure_future(work)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def follow(self) -> None:
        """Follow every session active within the watch window, picking up those followed before a restart, and
        stop following each that has been quiet longer. A session no longer followed keeps its row, and with it the
        last input handled, for when T3 Code opens it again."""
        assert self.config.watch_ms is not None
        while True:
            try:
                cutoff = now_ms() - self.config.watch_ms
                await asyncio.gather(*(self._follow(sid) for sid in self.store.watched(cutoff) if sid not in self.sessions))
                for sid, session in list(self.sessions.items()):
                    quiet = session.active < cutoff and not session.running and not session.steers and not session.held
                    if session.parent is None and quiet:
                        LOG.info("no longer following %s: quiet since %d", sid, session.active)
                        await self._unfollow(session)
            except Exception:  # noqa: BLE001 - the next pass tries again
                LOG.exception("following sessions failed")
            await asyncio.sleep(SWEEP_S)

    async def release_done(self) -> None:
        """Release, every minute, each followed session whose T3 Code thread is settled, archived or deleted."""
        while True:
            await asyncio.sleep(RELEASE_SWEEP_S)
            await self.release_pass()

    async def release_pass(self) -> None:
        assert self.config.watch_ms is not None
        try:
            for sid in self.store.watched(now_ms() - self.config.watch_ms):
                try:
                    await self._release_if_done(sid)
                except (OmnigentError, httpx.HTTPError) as exc:
                    LOG.warning("releasing %s failed: %s", sid, exc)
                except Exception:  # noqa: BLE001 - the next pass tries again
                    LOG.exception("releasing %s failed", sid)
        except Exception:  # noqa: BLE001 - the next pass tries again
            LOG.exception("releasing sessions failed")

    def _unused(self, session: Session) -> bool:
        """Nothing under way, and T3 Code has neither used nor opened it lately."""
        last_used = max(session.active, self._opened_at.get(session.id, 0))
        return not (session.running or session.steers or session.held) and now_ms() - last_used >= self.release_grace_ms

    async def _thread_done(self, home: Path, session_id: str) -> Thread | None:
        """The session's T3 Code thread if it is settled, archived or deleted."""
        try:
            thread = await asyncio.to_thread(thread_for, home, session_id)
        except sqlite3.Error as exc:
            LOG.warning("could not read T3 Code's threads in %s: %s", home, exc)
            return None
        return thread if thread is not None and thread.done else None

    async def _release_if_done(self, session_id: str) -> None:
        """Release the session if its T3 Code thread is done with: retire its and its subagents' watches and pause
        their scheduled tasks, so nothing wakes it, stop following it, and stop its Omnigent runner. Nothing is
        archived: a message, from T3 Code or anywhere, relaunches the runner and resumes the transcript, and T3 Code
        opening the session has it followed again and its tasks resumed."""
        home = self.home_of(session_id)
        if home is None or session_id in self._loading:
            return
        thread = await self._thread_done(home, session_id)
        if thread is None:
            return
        session = self.sessions.get(session_id)
        if session is not None and not self._unused(session):
            return
        conversation_id = (
            session.conversation_id
            if session is not None
            else self.store.conversation(session_id) or conversation_named_by(session_id)
        )
        if conversation_id is None:
            return
        try:
            state: dict[str, Any] | None = await self.client.liveness(conversation_id)
        except OmnigentError as exc:
            if not exc.gone:
                raise
            state = None
        if state is not None:
            if state.get("status") not in RELEASABLE_STATUSES or state.get("pending_inputs") or state.get("pending_elicitations"):
                return
            descendants = await self.client.descendants(conversation_id)
            if any(child.get("busy") for child in descendants):
                return
            await self._silence(session_id, {conversation_id, *(str(c["id"]) for c in descendants if c.get("id"))})
        # T3 Code may have opened the session meanwhile; nothing from this check to the release awaits.
        again = await self._thread_done(home, session_id)
        if (
            again is None
            or session_id in self._loading
            or self.sessions.get(session_id) is not session
            or (session is not None and not self._unused(session))
        ):
            LOG.info("not releasing %s: T3 Code took it up again", session_id)
            self._in_background(self._resume_tasks(session_id))
            return
        if session is not None:
            await self._unfollow(session)
        self.store.release(session_id, now_ms())
        stopping = state is not None and state.get("runner_online") is True
        LOG.info(
            "releasing %s (Omnigent %s): thread %s %s, %s",
            session_id,
            conversation_id,
            thread.id,
            thread.status,
            "stopping its runner" if stopping else "its runner is down" if state is not None else "Omnigent session gone",
        )
        if stopping:
            await self.client.stop_session(conversation_id)

    async def _silence(self, session_id: str, conversations: set[str]) -> None:
        """Retire the conversations' watches and pause their scheduled tasks, recording those for `_resume_tasks`."""
        for conversation in sorted(conversations):
            try:
                detail = await self.client.cancel_watches(conversation, WATCH_SOURCES)
            except OmnigentError as exc:
                if exc.status != 404:
                    raise
                detail = "omnigent-watcher is not mounted"
            LOG.info("watches of %s: %s", conversation, detail)
        for task in await self.client.scheduled_tasks():
            if task.get("session_id") not in conversations or task.get("state") != "active":
                continue
            task_id = str(task["id"])
            try:
                await self.client.set_scheduled_task_state(task_id, "paused")
            except OmnigentError as exc:
                if exc.status not in (404, 409):
                    raise
                continue
            self.store.add_paused_task(session_id, task_id)
            LOG.info("paused scheduled task %s of %s", task_id, task["session_id"])

    async def _resume_tasks(self, session_id: str) -> None:
        for task_id in self.store.paused_tasks(session_id):
            try:
                await self.client.set_scheduled_task_state(task_id, "active")
            except OmnigentError as exc:
                if not exc.gone:
                    LOG.warning("could not resume scheduled task %s of %s: %s", task_id, session_id, exc)
                    continue
            except httpx.HTTPError as exc:
                LOG.warning("could not resume scheduled task %s of %s: %s", task_id, session_id, exc)
                continue
            self.store.drop_paused_task(session_id, task_id)
            LOG.info("resumed scheduled task %s of %s", task_id, session_id)

    async def _follow(self, session_id: str) -> None:
        try:
            await (await self.session(session_id)).connect()
        except Exception as exc:  # noqa: BLE001 - one session's failure is not the others'
            LOG.warning("could not follow %s yet: %s", session_id, exc)

    @property
    def busy(self) -> bool:
        """Whether a restart now would lose something: a running execution, a steer, or a held run."""
        return any(s.running or s.steers or s.held for s in self.sessions.values()) or any(
            t.running for t in self.text_sessions.values()
        )

    def note_home(self, session_id: str, home: str) -> None:
        """The T3 Code server whose home is `home` opened the session."""
        if session_id in self.sessions and self.store.t3_home(session_id) != home:
            self.store.set_t3_home(session_id, home)

    def home_of(self, session_id: str) -> Path | None:
        home = self.store.t3_home(session_id)
        return Path(home) if home else None

    def adopt(self, conversation_id: str, parent: Session) -> Session:
        """A subagent's Omnigent session, as a child of the session that spawned it."""
        session_id = session_id_for(conversation_id)
        existing = self.sessions.get(session_id)
        if existing is not None:
            existing.parent = parent
            return existing
        child = Session(
            session_id=session_id,
            conversation_id=conversation_id,
            target=parent.target,
            directory=parent.directory,
            model=dict(parent.model),
            client=self.client,
            store=self.store,
            publish=self.feed.publish,
            parent=parent,
            adopt=self.adopt,
        )
        self.sessions[session_id] = child
        return child

    async def create_session(self, body: dict[str, Any]) -> JsonObject:
        directory = str((body.get("location") or {}).get("directory") or self.config.default_directory)
        model = cast(JsonObject, body.get("model") or {})
        title = body.get("title")
        if textgen.is_text_generation(title, body.get("permissions")):
            text_session = TextGenSession(f"ses_textgen_{message_id()[4:]}", str(title), directory, model, now_ms())
            self.text_sessions[text_session.id] = text_session
            return text_session.info()
        permissions = list(body["permissions"]) if isinstance(body.get("permissions"), list) else []
        thread = approvals.thread_key(permissions)
        kept = None if thread is None else await self._thread_session(thread)
        if kept is not None:
            LOG.warning(
                "T3 Code asked for a new session for %s, whose Omnigent session %s still exists: keeping it",
                thread,
                kept.conversation_id,
            )
            kept.permissions = permissions
            kept.rules_changed()
            kept.recap_due = True
            return kept.info()
        catalog = await self.catalog()
        provider = str(model.get("providerID") or next(iter(catalog.targets)))
        target = catalog.target(provider)
        if target is None:
            raise ApiError("InvalidRequestError", f"no Omnigent agent named {provider}")
        request: dict[str, Any] = {
            "agent_id": target.agent_id,
            "host_id": catalog.host_id,
            "workspace": directory,
            "labels": {"t3.client": "t3code"},
        }
        if model.get("id") and model.get("id") != DEFAULT_MODEL:
            request["model_override"] = model["id"]
        if model.get("variant"):
            request["reasoning_effort"] = model["variant"]
        if isinstance(title, str) and title:
            request["title"] = title
        created = await self.client.create_session(request)
        conversation_id = str(created["id"])
        session_id = session_id_for(conversation_id)
        await self.client.update_session(
            conversation_id, {"labels": {**request["labels"], SESSION_LABEL: session_id, PROVIDER_LABEL: target.name}}
        )
        session = Session(
            session_id=session_id,
            conversation_id=conversation_id,
            target=target,
            directory=directory,
            model={
                "providerID": target.name,
                "id": str(model.get("id") or DEFAULT_MODEL),
                **({"variant": model["variant"]} if model.get("variant") else {}),
            },
            client=self.client,
            store=self.store,
            publish=self.feed.publish,
            adopt=self.adopt,
            wake=self.wake,
            title=title if isinstance(title, str) and title else None,
        )
        session.permissions = permissions
        self._register(session)
        if thread is not None:
            self.store.set_thread_session(thread, session_id)
        LOG.info("created Omnigent session %s as %s", conversation_id, session_id)
        return session.info()

    async def _thread_session(self, thread: str) -> Session | None:
        """The session T3 Code thread `thread` runs on, while its Omnigent session exists."""
        session_id = self.store.thread_session(thread)
        if session_id is None:
            return None
        try:
            return await self.session(session_id)
        except ApiError as exc:
            if exc.tag == "SessionNotFoundError":
                return None
            raise

    def note_rules(self, session: Session) -> None:
        """Tie a root session to the thread its rules were written for (a child gets its root thread's rules)."""
        thread = approvals.thread_key(session.permissions)
        if thread is not None and session.parent is None:
            self.store.set_thread_session(thread, session.id)

    async def _fork_conversation(self, source: Session, before: str | None) -> str:
        """An Omnigent session holding `source`'s history strictly before user message `before`, bound to this host."""
        if source.running or source.steers:
            raise ApiError("ConflictError", "the session is busy; wait for its turn to end")
        try:
            point = await source.fork_point(before)
        except LookupError as exc:
            raise ApiError("MessageNotFoundError", f"no message {before} in {source.id}", messageID=str(before)) from exc
        catalog = await self.catalog()
        if point == "":
            # Nothing comes before the boundary: a fresh session of the same agent.
            request: dict[str, Any] = {
                "agent_id": source.target.agent_id,
                "host_id": catalog.host_id,
                "workspace": source.directory,
            }
            if source.model.get("id") not in (None, DEFAULT_MODEL):
                request["model_override"] = source.model["id"]
            if source.model.get("variant"):
                request["reasoning_effort"] = source.model["variant"]
            return str((await self.client.create_session(request))["id"])
        body: dict[str, Any] = {} if point is None else {"up_to_response_id": point}
        if source.title:
            body["title"] = source.title
        forked = str((await self.client.fork_session(source.conversation_id, body))["id"])
        await self.client.launch_runner(catalog.host_id, forked, source.directory)
        return forked

    async def fork(self, source: Session, before: str | None) -> JsonObject:
        forked = await self._fork_conversation(source, before)
        session_id = session_id_for(forked)
        await self.client.update_session(
            forked, {"labels": {"t3.client": "t3code", SESSION_LABEL: session_id, PROVIDER_LABEL: source.target.name}}
        )
        self.store.copy_session(source.id, session_id)
        if before is not None:
            self.store.drop_prompts_from(session_id, before)
        session = Session(
            session_id=session_id,
            conversation_id=forked,
            target=source.target,
            directory=source.directory,
            model=dict(source.model),
            client=self.client,
            store=self.store,
            publish=self.feed.publish,
            adopt=self.adopt,
            wake=self.wake,
            title=source.title,
        )
        session.agent = source.agent
        session.permissions = list(source.permissions)
        self._register(session)
        LOG.info("forked %s before %s as %s (Omnigent session %s)", source.id, before, session_id, forked)
        return session.info()

    async def switch_agent(self, session: Session, model: JsonObject) -> None:
        """Move the session to another agent: Omnigent forks its session onto that agent, rebuilding the history
        in the agent's own transcript, and the session continues there."""
        if session.running or session.steers:
            raise ApiError("ConflictError", "the session is busy; wait for its turn to end")
        catalog = await self.catalog()
        target = catalog.target(str(model.get("providerID")))
        if target is None:
            raise ApiError("InvalidRequestError", f"no Omnigent agent named {model.get('providerID')}")
        body: dict[str, Any] = {
            "agent_id": target.agent_id,
            # Omitted, the fork would keep the source agent's model and effort.
            "model_override": OMNIGENT_RESET if model.get("id") in (None, DEFAULT_MODEL) else str(model["id"]),
            "reasoning_effort": str(model.get("variant") or OMNIGENT_RESET),
        }
        if session.title:
            body["title"] = session.title
        replaced = session.conversation_id
        forked = str((await self.client.fork_session(replaced, body))["id"])
        await self.client.launch_runner(catalog.host_id, forked, session.directory)
        await self.client.update_session(
            forked, {"labels": {"t3.client": "t3code", SESSION_LABEL: session.id, PROVIDER_LABEL: target.name}}
        )
        await session.retarget(forked, target, model)
        LOG.info("switched %s to %s: Omnigent session %s replaces %s", session.id, target.name, forked, replaced)

    async def commit_revert(self, session: Session) -> None:
        """Roll the session back to before its staged message: its Omnigent session becomes a fork cut there."""
        boundary = session.staged_revert
        if boundary is None:
            raise ApiError("InvalidRequestError", "no revert is staged")
        replaced = session.conversation_id
        forked = await self._fork_conversation(session, boundary)
        await self.client.update_session(
            forked, {"labels": {"t3.client": "t3code", SESSION_LABEL: session.id, PROVIDER_LABEL: session.target.name}}
        )
        await session.rebind(forked)
        self.store.drop_prompts_from(session.id, boundary)
        session.staged_revert = None
        LOG.info("rolled %s back before %s: Omnigent session %s replaces %s", session.id, boundary, forked, replaced)

    async def text_generation(self, text_session: TextGenSession, prompt: str) -> None:
        sid = text_session.id
        assistant = message_id()
        text_session.running = True
        self.feed.publish(event("session.execution.started", {"sessionID": sid}))
        try:
            text = await textgen.generate(prompt, text_session.directory)
        except Exception as exc:  # noqa: BLE001 - every failure ends the execution
            LOG.warning("text generation for %s failed: %s", text_session.title, exc)
            self.feed.publish(
                event("session.execution.failed", {"sessionID": sid, "error": {"type": "unknown", "message": str(exc)}})
            )
        else:
            self.feed.publish(event("session.text.started", {"sessionID": sid, "assistantMessageID": assistant, "ordinal": 0}))
            self.feed.publish(
                event("session.text.ended", {"sessionID": sid, "assistantMessageID": assistant, "ordinal": 0, "text": text})
            )
            self.feed.publish(event("session.execution.succeeded", {"sessionID": sid}))
        finally:
            text_session.running = False

    def list_directory(self, directory: str) -> None:
        """T3 Code waits for these after an empty command list before it trusts it (status.ts)."""
        if directory in self._listed_directories:
            return
        self._listed_directories.add(directory)

        def announce() -> None:
            for kind in ("command.updated", "skill.updated"):
                self.feed.publish(event(kind, {}, location=location(directory)))

        asyncio.get_running_loop().call_later(0.05, announce)


# -- routes ---------------------------------------------------------------

routes = web.RouteTableDef()
APP_KEY: Final = web.AppKey("omnigent_opencode", App)


def app_of(request: web.Request) -> App:
    return request.app[APP_KEY]


async def body_of(request: web.Request) -> dict[str, Any]:
    if not request.can_read_body:
        return {}
    body = await request.json()
    if not isinstance(body, dict):
        raise ApiError("InvalidRequestError", "the request body must be an object")
    return body


def directory_of(request: web.Request) -> str:
    return request.query.get("location[directory]") or request.query.get("directory") or app_of(request).config.default_directory


def no_content() -> web.Response:
    return web.Response(status=204)


def data(value: Json) -> web.Response:
    return web.json_response({"data": value})


@web.middleware
async def guard(request: web.Request, handler: Any) -> web.StreamResponse:
    expected = app_of(request).config.password
    header = request.headers.get("Authorization", "")
    try:
        user, _, password = base64.b64decode(header.removeprefix("Basic "), validate=True).decode().partition(":")
    except (binascii.Error, UnicodeDecodeError):
        user, password = "", ""
    if expected is not None and (not header.startswith("Basic ") or user != USERNAME or password != expected):
        return ApiError("UnauthorizedError", "unauthorized").response()
    try:
        response = cast(web.StreamResponse, await handler(request))
        if "session_id" in request.match_info:
            app = app_of(request)
            sid = session_id_param(request)
            if sid in app.sessions:
                app.sessions[sid].touch()
            home = request.headers.get(T3_HOME_HEADER)
            if home:
                app.note_home(sid, home)
        return response
    except ApiError as exc:
        return exc.response()
    except ValueError as exc:
        return ApiError("InvalidRequestError", str(exc)).response()
    except (OmnigentError, httpx.HTTPError) as exc:
        LOG.warning("%s %s failed: %s", request.method, request.path, exc)
        return web.json_response({"message": str(exc)}, status=502)


@routes.get("/api/info")
async def info(request: web.Request) -> web.Response:
    return web.json_response(server_info([f"{request.scheme}://{request.host}"]))


@routes.get(STATUS_PATH)
async def status(request: web.Request) -> web.Response:
    app = app_of(request)
    return web.json_response({"code": app.code, "busy": app.busy})


@routes.get("/api/event")
async def events(request: web.Request) -> web.StreamResponse:
    return await app_of(request).feed.handle(request)


@routes.get("/api/model")
async def models(request: web.Request) -> web.Response:
    catalog = await app_of(request).catalog()
    return web.json_response({"location": location(directory_of(request)), "data": cast(Json, catalog.model_infos())})


@routes.get("/api/provider")
async def providers(request: web.Request) -> web.Response:
    return web.json_response({"location": location(directory_of(request)), "data": []})


def agent_info(agent_id: str, permissions: list[Json]) -> JsonObject:
    return {
        "id": agent_id,
        "name": agent_id,
        "request": {"settings": {}, "headers": {}, "body": {}},
        "mode": "primary",
        "hidden": False,
        "permissions": permissions,
    }


@routes.get("/api/agent")
async def agents(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "location": location(directory_of(request)),
            "data": [
                agent_info("build", [{"action": "*", "resource": "*", "effect": "allow"}]),
                agent_info("plan", [{"action": "edit", "resource": "*", "effect": "deny"}]),
            ],
        }
    )


@routes.get("/api/command")
async def commands(request: web.Request) -> web.Response:
    directory = directory_of(request)
    app_of(request).list_directory(directory)
    return web.json_response({"location": location(directory), "data": []})


@routes.get("/api/skill")
async def skills(request: web.Request) -> web.Response:
    return web.json_response({"location": location(directory_of(request)), "data": []})


@routes.post("/api/session")
async def create_session(request: web.Request) -> web.Response:
    app = app_of(request)
    created = await app.create_session(await body_of(request))
    home = request.headers.get(T3_HOME_HEADER)
    if home:
        app.note_home(str(created["id"]), home)
    return data(created)


@routes.get("/api/session/active")
async def active(request: web.Request) -> web.Response:
    app = app_of(request)
    running: JsonObject = {sid: {"type": "running"} for sid, s in app.sessions.items() if s.shown_running}
    running.update({sid: {"type": "running"} for sid, s in app.text_sessions.items() if s.running})
    return data(running)


def session_id_param(request: web.Request) -> str:
    return unquote(request.match_info["session_id"])


@routes.get("/api/session/{session_id}")
async def get_session(request: web.Request) -> web.Response:
    app = app_of(request)
    sid = session_id_param(request)
    if sid in app.text_sessions:
        return data(app.text_sessions[sid].info())
    return data((await app.session(sid)).info())


@routes.patch("/api/session/{session_id}")
async def update_session(request: web.Request) -> web.Response:
    app = app_of(request)
    sid = session_id_param(request)
    body = await body_of(request)
    if sid in app.text_sessions:
        return no_content()
    session = await app.session(sid)
    if isinstance(body.get("permissions"), list) and body["permissions"] != session.permissions:
        session.permissions = list(body["permissions"])
        session.rules_changed()
        app.note_rules(session)
    if isinstance(body.get("title"), str) and body["title"] != session.title:
        session.title = body["title"]
        await app.client.update_session(session.conversation_id, {"title": session.title})
    return no_content()


@routes.delete("/api/session/{session_id}")
async def remove_session(request: web.Request) -> web.Response:
    app = app_of(request)
    sid = session_id_param(request)
    if app.text_sessions.pop(sid, None) is None:
        LOG.info("not deleting Omnigent session behind %s: T3 Code only removes text generation sessions", sid)
    return no_content()


@routes.post("/api/session/{session_id}/agent")
async def switch_agent(request: web.Request) -> web.Response:
    session = await app_of(request).session(session_id_param(request))
    await session.set_agent(str((await body_of(request)).get("agent") or "build"))
    return no_content()


@routes.post("/api/session/{session_id}/model")
async def switch_model(request: web.Request) -> web.Response:
    session = await app_of(request).session(session_id_param(request))
    model = (await body_of(request)).get("model")
    if not isinstance(model, dict):
        raise ApiError("InvalidRequestError", "model is required")
    if model.get("providerID") == session.target.name:
        await session.set_model(cast(JsonObject, model))
    else:
        await app_of(request).switch_agent(session, cast(JsonObject, model))
    return no_content()


@routes.put("/api/experimental/session/{session_id}/instructions/entries/{key}")
async def put_instructions(request: web.Request) -> web.Response:
    app = app_of(request)
    sid = session_id_param(request)
    body = await body_of(request)
    if sid not in app.text_sessions:
        (await app.session(sid)).instructions = cast(Json, body.get("value"))
    return no_content()


@routes.put("/api/experimental/mcp/{server}")
async def add_mcp(request: web.Request) -> web.Response:
    raise ApiError("InvalidRequestError", "this server does not take MCP servers from T3 Code")


@routes.delete("/api/experimental/mcp/{server}")
async def remove_mcp(request: web.Request) -> web.Response:
    return no_content()


def inbox_echo(
    session_id: str, inbox_id: str, kind: Literal["user", "synthetic", "compaction"], text: str | None, delivery: str
) -> JsonObject:
    payload: JsonObject = {} if text is None else {"text": text}
    return {
        "id": inbox_id,
        "sessionID": session_id,
        "time": {"created": now_ms()},
        "type": kind,
        "payload": payload,
        "delivery": delivery,
    }


@routes.post("/api/session/{session_id}/prompt")
async def prompt(request: web.Request) -> web.Response:
    app = app_of(request)
    sid = session_id_param(request)
    body = await body_of(request)
    text = str(body.get("text") or "")
    inbox_id = str(body.get("id") or message_id())
    delivery: Literal["queue", "steer"] = "steer" if body.get("delivery") == "steer" else "queue"
    text_session = app.text_sessions.get(sid)
    if text_session is not None:
        asyncio.get_running_loop().create_task(app.text_generation(text_session, text))
        return data(inbox_echo(sid, inbox_id, "user", text, delivery))
    session = await app.session(sid)
    files = [
        unquote(f["uri"].removeprefix("file://"))
        for f in body.get("files") or []
        if isinstance(f, dict) and str(f.get("uri", "")).startswith("file://")
    ]
    try:
        await session.prompt(inbox_id, text, delivery, files)
    except (OmnigentError, httpx.HTTPError) as exc:
        raise ApiError("InvalidRequestError", f"Omnigent did not take the message: {exc}") from exc
    return data(inbox_echo(sid, inbox_id, "user", text, delivery))


@routes.post("/api/session/{session_id}/synthetic")
async def synthetic(request: web.Request) -> web.Response:
    app = app_of(request)
    sid = session_id_param(request)
    body = await body_of(request)
    text = str(body.get("text") or "")
    if body.get("resume") is False and sid not in app.text_sessions:
        session = await app.session(sid)
        if session.approvals:
            # T3 Code explains a decline this way just before it rejects; it becomes the reason the agent is given.
            session.note_decline(text)
    return data(inbox_echo(sid, message_id(), "synthetic", text, str(body.get("delivery") or "steer")))


@routes.post("/api/session/{session_id}/interrupt")
async def interrupt(request: web.Request) -> web.Response:
    app = app_of(request)
    sid = session_id_param(request)
    if sid in app.text_sessions:
        return web.json_response({"interrupted": app.text_sessions[sid].running})
    return web.json_response({"interrupted": await (await app.session(sid)).interrupt()})


@routes.delete("/api/session/{session_id}/inbox/{inbox_id}")
async def cancel_inbox(request: web.Request) -> web.Response:
    session = await app_of(request).session(session_id_param(request))
    session.cancel_steer(unquote(request.match_info["inbox_id"]))
    return no_content()


@routes.post("/api/session/{session_id}/compact")
async def compact(request: web.Request) -> web.Response:
    sid = session_id_param(request)
    body = await body_of(request)
    inbox_id = str(body.get("id") or message_id())
    await (await app_of(request).session(sid)).compact(inbox_id)
    return data(inbox_echo(sid, inbox_id, "compaction", None, "steer"))


@routes.post("/api/session/{session_id}/command")
async def command(request: web.Request) -> web.Response:
    name = str((await body_of(request)).get("name") or "")
    raise ApiError("CommandNotFoundError", f"no command {name}", command=name)


@routes.post("/api/session/{session_id}/fork")
async def fork(request: web.Request) -> web.Response:
    app = app_of(request)
    source = await app.session(session_id_param(request))
    before = (await body_of(request)).get("before")
    return data(await app.fork(source, str(before) if before else None))


@routes.post("/api/session/{session_id}/move")
async def move(request: web.Request) -> web.Response:
    session = await app_of(request).session(session_id_param(request))
    directory = (await body_of(request)).get("directory")
    if isinstance(directory, str) and directory and directory != session.directory:
        # Omnigent records a workspace by its real path, so after a restart the session may name T3 Code's
        # directory differently (/private/tmp for /tmp); a refusal here makes T3 Code abandon the session.
        if os.path.realpath(directory) != os.path.realpath(session.directory):
            raise ApiError(
                "InvalidRequestError",
                f"this thread's Omnigent session runs in {session.directory}; start a new thread to work in {directory}",
            )
        session.directory = directory
    return no_content()


@routes.post("/api/session/{session_id}/revert/stage")
async def stage_revert(request: web.Request) -> web.Response:
    session = await app_of(request).session(session_id_param(request))
    message = str((await body_of(request)).get("messageID") or "")
    if session.running or session.steers:
        raise ApiError("ConflictError", "the session is busy; wait for its turn to end")
    if not any(m.get("id") == message and m.get("type") in ("user", "synthetic") for m in await session.messages()):
        raise ApiError("MessageNotFoundError", f"no message {message} in {session.id}", messageID=message)
    session.staged_revert = message
    return data({"messageID": message})


@routes.post("/api/session/{session_id}/revert/commit")
async def commit_revert(request: web.Request) -> web.Response:
    app = app_of(request)
    await app.commit_revert(await app.session(session_id_param(request)))
    return no_content()


@routes.delete("/api/session/{session_id}/revert")
async def clear_revert(request: web.Request) -> web.Response:
    session = await app_of(request).session(session_id_param(request))
    session.staged_revert = None
    # T3 Code waits for an execution to end after clearing a revert, as OpenCode 2.0.18's clear runs one.
    if not session.shown_running:
        app_of(request).feed.publish(event("session.execution.started", {"sessionID": session.id}))
        app_of(request).feed.publish(event("session.execution.succeeded", {"sessionID": session.id}))
    return no_content()


@routes.get("/api/session/{session_id}/message")
async def messages(request: web.Request) -> web.Response:
    app = app_of(request)
    sid = session_id_param(request)
    if sid in app.text_sessions:
        return web.json_response({"data": [], "cursor": {}})
    try:
        limit = max(1, min(200, int(request.query.get("limit", "100"))))
    except ValueError as exc:
        raise ApiError("InvalidRequestError", "limit must be an integer") from exc
    session = await app.session(sid)
    return web.json_response(
        history.page(await session.messages(), order=request.query.get("order"), cursor=request.query.get("cursor"), limit=limit)
    )


@routes.get("/api/session/{session_id}/permission")
async def permissions(request: web.Request) -> web.Response:
    app = app_of(request)
    sid = session_id_param(request)
    if sid in app.text_sessions:
        return data([])
    session = await app.session(sid)
    return data([p.shown for p in session.approvals.values() if p.visible])


@routes.post("/api/session/{session_id}/permission/{request_id}/reply")
async def reply_permission(request: web.Request) -> web.Response:
    app = app_of(request)
    sid = session_id_param(request)
    request_id = unquote(request.match_info["request_id"])
    body = await body_of(request)
    if sid in app.text_sessions:
        return no_content()
    session = await app.session(sid)
    message = body.get("message")
    if not await session.reply(request_id, str(body.get("decision") or "reject"), message if isinstance(message, str) else None):
        raise ApiError("PermissionNotFoundError", f"no pending permission {request_id}", requestID=request_id)
    return no_content()


@routes.get("/api/session/{session_id}/form")
async def forms(request: web.Request) -> web.Response:
    app = app_of(request)
    sid = session_id_param(request)
    if sid in app.text_sessions:
        return data([])
    session = await app.session(sid)
    return data([p.shown for p in session.forms.values() if p.visible])


@routes.post("/api/session/{session_id}/form/{form_id}/reply")
async def reply_form(request: web.Request) -> web.Response:
    app = app_of(request)
    sid = session_id_param(request)
    key = unquote(request.match_info["form_id"])
    body = await body_of(request)
    answer = body.get("answer")
    if not isinstance(answer, dict):
        raise ApiError("InvalidRequestError", "answer must be an object")
    if sid in app.text_sessions or not await (await app.session(sid)).answer_form(key, answer):
        raise ApiError("FormNotFoundError", f"no pending form {key}", id=key)
    return no_content()


@routes.delete("/api/session/{session_id}/form/{form_id}")
async def cancel_form(request: web.Request) -> web.Response:
    app = app_of(request)
    sid = session_id_param(request)
    key = unquote(request.match_info["form_id"])
    if sid in app.text_sessions or not await (await app.session(sid)).cancel_form(key):
        raise ApiError("FormNotFoundError", f"no pending form {key}", id=key)
    return no_content()


def make_app(app: App) -> web.Application:
    application = web.Application(middlewares=[guard])
    application[APP_KEY] = app
    application.add_routes(routes)

    async def shutdown(_: web.Application) -> None:
        for session in app.sessions.values():
            await session.close()
        await app.close_catalog()
        await app.client.aclose()

    application.on_cleanup.append(shutdown)
    return application
