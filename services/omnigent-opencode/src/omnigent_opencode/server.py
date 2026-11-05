"""The HTTP routes T3 Code v0.1.6's OpenCode 2 client calls, and the session registry."""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
from dataclasses import dataclass
from typing import Any, Final, Literal, cast
from urllib.parse import unquote

import httpx
from aiohttp import web

from . import history, textgen
from .catalog import DEFAULT_MODEL, Catalog, load_catalog
from .events import EventFeed
from .omnigent import OmnigentClient, OmnigentError
from .sessions import PROVIDER_LABEL, SESSION_LABEL, Session, conversation_named_by, session_id_for
from .store import Store
from .wire import ERROR_STATUS, ErrorTag, Json, JsonObject, error_body, event, location, message_id, now_ms, server_info

LOG = logging.getLogger(__name__)

USERNAME: Final = "opencode"


@dataclass
class Config:
    password: str
    host_spec: str
    agent_specs: list[str]
    default_directory: str


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
    def __init__(self, config: Config, client: OmnigentClient, store: Store) -> None:
        self.config = config
        self.client = client
        self.store = store
        self.feed = EventFeed()
        self.sessions: dict[str, Session] = {}
        self.text_sessions: dict[str, TextGenSession] = {}
        self._catalog: Catalog | None = None
        self._catalog_lock = asyncio.Lock()
        self._listed_directories: set[str] = set()
        self._loading: dict[str, asyncio.Future[Session]] = {}

    # -- catalog --------------------------------------------------------

    async def catalog(self) -> Catalog:
        async with self._catalog_lock:
            if self._catalog is None:
                self._catalog = await load_catalog(self.client, self.config.host_spec, self.config.agent_specs)
            return self._catalog

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
            title=snapshot.get("title") or None,
            created=int(created * 1000) if isinstance(created, (int, float)) else None,
        )
        self.sessions[session_id] = session
        return session

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
        permissions = list(body["permissions"]) if isinstance(body.get("permissions"), list) else []
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
            title=title if isinstance(title, str) and title else None,
        )
        session.permissions = permissions
        self.sessions[session_id] = session
        LOG.info("created Omnigent session %s as %s", conversation_id, session_id)
        return session.info()

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
            title=source.title,
        )
        session.agent = source.agent
        session.permissions = list(source.permissions)
        self.sessions[session_id] = session
        LOG.info("forked %s before %s as %s (Omnigent session %s)", source.id, before, session_id, forked)
        return session.info()

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
    if not header.startswith("Basic ") or user != USERNAME or password != expected:
        return ApiError("UnauthorizedError", "unauthorized").response()
    try:
        return cast(web.StreamResponse, await handler(request))
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
    return data(await app_of(request).create_session(await body_of(request)))


@routes.get("/api/session/active")
async def active(request: web.Request) -> web.Response:
    app = app_of(request)
    running: JsonObject = {sid: {"type": "running"} for sid, s in app.sessions.items() if s.running}
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
    await session.set_model(cast(JsonObject, model))
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
        raise ApiError(
            "InvalidRequestError",
            f"this thread's Omnigent session runs in {session.directory}; start a new thread to work in {directory}",
        )
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
    if not session.running:
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
    return data([p.shown for p in session.approvals.values()])


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
    return data([p.shown for p in session.forms.values()])


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
        await app.client.aclose()

    application.on_cleanup.append(shutdown)
    return application
