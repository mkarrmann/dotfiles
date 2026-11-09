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


class FakeOmnigent:
    def __init__(self, harness: str = "claude-native") -> None:
        self.harness = harness
        self.refuse_messages = False
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
                        }
                    ]
                }
            )
        return web.json_response(
            {
                "models": [
                    {"id": "opus", "displayName": "Opus", "isDefault": True},
                    {"id": "opus-1m", "model": "claude-opus-5-5[1m]", "displayName": "Opus (1M context)"},
                    {"id": "sonnet[1m]", "model": "sonnet[1m]", "displayName": "Sonnet (1M context)"},
                ]
            }
        )

    async def create(self, request: web.Request) -> web.Response:
        session_id = f"conv_{next(self._ids):04d}"
        self.sessions[session_id] = FakeSession(session_id, await request.json())
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
                "status": session.status,
                "items": [],
                "pending_inputs": [],
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
        session.posted.append(body)
        if body.get("type") == "message":
            n = next(self._ids)
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
        self.sessions[fork_id] = FakeSession(fork_id, dict(source.body), items=[{**i, "id": f"copy_{i['id']}"} for i in items])
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
