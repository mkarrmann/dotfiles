"""An Omnigent server stand-in: the REST routes this server calls, and a session stream the test feeds."""

from __future__ import annotations

import asyncio
import itertools
import json
import socket
from dataclasses import dataclass, field
from typing import Any

from aiohttp import web

HOST_ID = "host_local"
# What a fork's model_override or reasoning_effort takes to reset to the agent's default.
RESET_ALIASES = frozenset({"default", "off", "reset"})


@dataclass
class FakeSession:
    id: str
    body: dict[str, Any]
    streams: list[asyncio.Queue[str | None]] = field(default_factory=list)
    posted: list[dict[str, Any]] = field(default_factory=list)
    patches: list[dict[str, Any]] = field(default_factory=list)
    status: str = "idle"
    items: list[dict[str, Any]] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)
    policies: list[dict[str, Any]] = field(default_factory=list)
    pending_ids: list[str] = field(default_factory=list)
    started: bool = True
    """Whether the runner has initialized the session; Omnigent refuses a compact until it has."""
    runner_online: bool = True
    pending_inputs: list[dict[str, Any]] = field(default_factory=list)
    pending_elicitations: list[dict[str, Any]] = field(default_factory=list)
    children: list[dict[str, Any]] = field(default_factory=list)
    """Rows of `GET /v1/sessions/{id}/child_sessions`, each at its `depth` below the session (default 1)."""


class FakeOmnigent:
    def __init__(self, harness: str = "claude-native") -> None:
        self.harness = harness
        self.refuse_messages = False
        self.new_session_labels: dict[str, str] = {}
        """Labels each session this fake creates starts with, as Omnigent's own (e.g. last usage) would be."""
        self.sessions: dict[str, FakeSession] = {}
        self._ids = itertools.count(1)
        self.app = web.Application()
        r = self.app.router
        r.add_get("/v1/hosts", self.hosts)
        r.add_get("/v1/agents", self.agents)
        r.add_get("/v1/hosts/{host}/harnesses/{harness}/model-options", self.model_options)
        r.add_post("/v1/sessions", self.create)
        r.add_get("/v1/sessions/{id}", self.snapshot)
        r.add_patch("/v1/sessions/{id}", self.patch)
        r.add_post("/v1/sessions/{id}/events", self.post_event)
        r.add_get("/v1/sessions/{id}/stream", self.stream)
        r.add_get("/v1/sessions/{id}/items", self.items)
        r.add_post("/v1/sessions/{id}/fork", self.fork)
        r.add_post("/v1/hosts/{host}/runners", self.launch)
        self.forks: list[tuple[str, dict[str, Any]]] = []
        self.launched: list[dict[str, Any]] = []
        r.add_post("/v1/sessions/{id}/elicitations/{eid}/resolve", self.resolve)
        r.add_get("/v1/sessions/{id}/policies", self.policies)
        r.add_post("/v1/sessions/{id}/policies", self.add_policy)
        r.add_patch("/v1/sessions/{id}/policies/{pid}", self.patch_policy)
        r.add_get("/v1/sessions/{id}/child_sessions", self.child_sessions)
        r.add_post("/v1/watches/cancel", self.cancel_watches)
        r.add_get("/v1/scheduled-tasks", self.list_scheduled_tasks)
        r.add_patch("/v1/scheduled-tasks/{tid}", self.patch_scheduled_task)
        self.watch_cancels: list[dict[str, Any]] = []
        self.watch_gate: asyncio.Event | None = None
        """While set to an unset event, a watch cancel waits for it."""
        self.watch_blocked = asyncio.Event()
        """Set once a watch cancel waits on `watch_gate`."""
        self.watch_cancel_status = 200
        self.scheduled_tasks: list[dict[str, Any]] = []
        self.resolved: list[tuple[str, str, dict[str, Any]]] = []
        self._resolve_seen = asyncio.Event()

    # -- what tests drive ----------------------------------------------

    def only(self) -> FakeSession:
        (session,) = self.sessions.values()
        return session

    def push(self, session_id: str, event_type: str, data: dict[str, Any]) -> None:
        frame = f"event: {event_type}\ndata: {json.dumps({'type': event_type, **data})}\n\n"
        for queue in self.sessions[session_id].streams:
            queue.put_nowait(frame)

    async def resolutions(self, count: int, timeout: float = 5.0) -> list[tuple[str, str, dict[str, Any]]]:
        async with asyncio.timeout(timeout):
            while len(self.resolved) < count:
                self._resolve_seen.clear()
                await self._resolve_seen.wait()
        return self.resolved

    def add_child(self, labels: dict[str, str]) -> str:
        """A subagent session, as a native harness creates it."""
        session_id = f"conv_{next(self._ids):04d}"
        self.sessions[session_id] = FakeSession(session_id, {}, labels=labels)
        return session_id

    def drop(self, session_id: str) -> None:
        """End every open stream of the session, as a network blip or an Omnigent restart does."""
        for queue in self.sessions[session_id].streams:
            queue.put_nowait(None)

    async def connected(self, session_id: str) -> None:
        while not self.sessions[session_id].streams:
            await asyncio.sleep(0.01)

    # -- routes ---------------------------------------------------------

    async def hosts(self, request: web.Request) -> web.Response:
        return web.json_response({"hosts": [{"host_id": HOST_ID, "name": socket.gethostname(), "status": "online"}]})

    async def agents(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "data": [
                    {"id": "agent_claude", "name": "claude-native-ui", "harness": self.harness},
                    {"id": "agent_codex", "name": "codex-native-ui", "harness": "codex-native"},
                ],
                "has_more": False,
            }
        )

    async def model_options(self, request: web.Request) -> web.Response:
        if request.match_info["harness"] == "codex-native":
            return web.json_response(
                {
                    "models": [
                        {
                            "id": "gpt-5",
                            "displayName": "GPT-5",
                            "isDefault": True,
                            "supportedReasoningEfforts": [{"reasoningEffort": "high"}],
                        },
                        {"id": "gpt-6.1-sol", "model": "gpt-6.1-sol", "displayName": "GPT-6.1 Sol"},
                        {"id": "gpt-6.1-sol[1m]", "model": "gpt-6.1-sol[1m]", "displayName": "GPT-6.1 Sol (1M)"},
                        {"id": "gpt-6.2-sol", "model": "gpt-6.2-sol", "displayName": "GPT-6.2 Sol"},
                    ]
                }
            )
        return web.json_response(
            {
                "models": [
                    {"id": "opus", "model": "claude-opus-5-5", "displayName": "Opus 5.5", "isDefault": True},
                    {"id": "claude-opus-5-5[1m]", "model": "claude-opus-5-5[1m]", "displayName": "Opus 5.5 (1M context)"},
                    {"id": "sonnet-1m", "model": "claude-sonnet-5-5[1m]", "displayName": "Sonnet 5.5 (1M context)"},
                    {"id": "haiku[1m]", "displayName": "Haiku (1M context)"},
                    {"id": "claude-fable-5-1", "model": "claude-fable-5-1", "displayName": "Fable"},
                ]
            }
        )

    async def create(self, request: web.Request) -> web.Response:
        session_id = f"conv_{next(self._ids):04d}"
        self.sessions[session_id] = FakeSession(session_id, await request.json(), labels=dict(self.new_session_labels))
        return web.json_response({"id": session_id})

    async def snapshot(self, request: web.Request) -> web.Response:
        session = self.sessions.get(request.match_info["id"])
        if session is None:
            return web.json_response({"error": "not found"}, status=404)
        body = session.body
        return web.json_response(
            {
                "id": session.id,
                "agent_id": body.get("agent_id"),
                "workspace": body.get("workspace"),
                "model_override": body.get("model_override"),
                "reasoning_effort": body.get("reasoning_effort"),
                "status": session.status,
                "items": [],
                "pending_inputs": session.pending_inputs,
                "pending_elicitations": session.pending_elicitations,
                "runner_online": None if request.query.get("include_liveness") == "false" else session.runner_online,
                "labels": session.labels,
                "created_at": 1730000000,
            }
        )

    async def patch(self, request: web.Request) -> web.Response:
        self.sessions[request.match_info["id"]].patches.append(await request.json())
        return web.json_response({})

    async def post_event(self, request: web.Request) -> web.Response:
        session = self.sessions.get(request.match_info["id"])
        if session is None:
            return web.json_response({"error": "not found"}, status=404)
        body = await request.json()
        if body.get("type") == "message" and self.refuse_messages:
            return web.json_response({"error": "runner unavailable"}, status=503)
        if body.get("type") == "compact" and not session.started:
            return web.json_response(
                {"error": {"code": "invalid_input", "message": "/compact is not available for this session type."}},
                status=400,
            )
        session.posted.append(body)
        if body.get("type") == "stop_session":
            session.runner_online = False
            return web.json_response({"queued": False})
        if body.get("type") == "retry_session":
            session.started = True
            return web.json_response({"queued": False, "recovered": True, "recovery": "native_terminal_ready"})
        if body.get("type") == "message":
            n = next(self._ids)
            session.pending_ids.append(f"pending_{n}")
            return web.json_response({"queued": True, "pending_id": f"pending_{n}", "item_id": f"item_{n}"})
        return web.json_response({"queued": False})

    async def fork(self, request: web.Request) -> web.Response:
        source = self.sessions[request.match_info["id"]]
        body = await request.json()
        self.forks.append((source.id, body))
        items = list(source.items)
        up_to = body.get("up_to_response_id")
        if up_to:
            last = max(n for n, i in enumerate(items) if i.get("response_id") == up_to)
            items = items[: last + 1]
        fork_id = f"conv_{next(self._ids):04d}"
        fork_body = dict(source.body)
        for key in ("agent_id", "model_override", "reasoning_effort"):
            if key in body:
                fork_body[key] = None if body[key] in RESET_ALIASES else body[key]
        self.sessions[fork_id] = FakeSession(fork_id, fork_body, items=[{**i, "id": f"copy_{i['id']}"} for i in items])
        return web.json_response({"id": fork_id}, status=201)

    async def launch(self, request: web.Request) -> web.Response:
        self.launched.append(await request.json())
        return web.json_response({})

    async def items(self, request: web.Request) -> web.Response:
        items = list(self.sessions[request.match_info["id"]].items)
        after = request.query.get("after")
        if after:
            items = items[next(n for n, i in enumerate(items) if i["id"] == after) + 1 :]
        if request.query.get("order") == "desc":
            items.reverse()
        limit = int(request.query.get("limit", "1000"))
        return web.json_response({"data": items[:limit], "has_more": len(items) > limit})

    async def resolve(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.resolved.append((request.match_info["id"], request.match_info["eid"], body))
        self._resolve_seen.set()
        return web.json_response({})

    async def policies(self, request: web.Request) -> web.Response:
        return web.json_response({"object": "list", "data": self.sessions[request.match_info["id"]].policies})

    async def add_policy(self, request: web.Request) -> web.Response:
        policy = {
            **(await request.json()),
            "id": f"pol_{len(self.sessions[request.match_info['id']].policies) + 1}",
            "enabled": True,
            "source": "session",
        }
        self.sessions[request.match_info["id"]].policies.append(policy)
        return web.json_response(policy)

    async def patch_policy(self, request: web.Request) -> web.Response:
        policy = next(p for p in self.sessions[request.match_info["id"]].policies if p["id"] == request.match_info["pid"])
        policy.update(await request.json())
        return web.json_response(policy)

    async def child_sessions(self, request: web.Request) -> web.Response:
        """Direct children only, unless `descendants`; Omnigent's own default `limit` is 20."""
        depth = int(request.query.get("max_depth", "3")) if request.query.get("descendants") == "true" else 1
        rows = [c for c in self.sessions[request.match_info["id"]].children if c.get("depth", 1) <= depth]
        limit = int(request.query.get("limit", "20"))
        return web.json_response({"object": "list", "data": rows[:limit], "has_more": len(rows) > limit})

    async def cancel_watches(self, request: web.Request) -> web.Response:
        if self.watch_gate is not None:
            self.watch_blocked.set()
            await self.watch_gate.wait()
        if self.watch_cancel_status != 200:
            return web.json_response({"error": "watcher down"}, status=self.watch_cancel_status)
        self.watch_cancels.append(await request.json())
        return web.json_response({"detail": "Cancelled."})

    async def list_scheduled_tasks(self, request: web.Request) -> web.Response:
        tasks = self.scheduled_tasks if request.query.get("kind") == "all" else []
        return web.json_response({"scheduled_tasks": tasks})

    async def patch_scheduled_task(self, request: web.Request) -> web.Response:
        task = next(t for t in self.scheduled_tasks if t["id"] == request.match_info["tid"])
        task.update(await request.json())
        return web.json_response(task)

    async def stream(self, request: web.Request) -> web.StreamResponse:
        session = self.sessions.get(request.match_info["id"])
        if session is None:
            return web.json_response({"error": "not found"}, status=404)
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        session.streams.append(queue)
        try:
            while (frame := await queue.get()) is not None:
                await response.write(frame.encode())
        finally:
            session.streams.remove(queue)
        return response
