"""Omnigent REST and SSE client, and host and agent resolution.

Carried over from `bin/omnigent-acp`.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
from collections.abc import AsyncIterator
from typing import Any

import httpx

# Omnigent stamps a fork with its source; a fork also copies the source's labels, ours included.
FORK_SOURCE_LABEL = "omnigent.fork.source_id"
LABEL_SCAN_PAGE = 500
LABEL_SCAN_LIMIT = 2000
# A cold codex-native launch can hold the first stream connection for over 30s.
STREAM_CONNECT_S = 120.0
STREAM_OPENED = "omnigent-opencode.stream_opened"


class OmnigentError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status
        """The HTTP status Omnigent answered with, if it answered."""

    @property
    def gone(self) -> bool:
        return self.status in (404, 410)


class OmnigentClient:
    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        # trust_env=False: loopback must bypass the corp proxy.
        self._http = httpx.AsyncClient(base_url=base_url, trust_env=False, timeout=httpx.Timeout(30.0), transport=transport)

    async def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self._http.request(method, path, **kwargs)
        if response.is_error:
            raise OmnigentError(f"{method} {path} -> {response.status_code}: {response.text[:500]}", response.status_code)
        return response.json() if response.content else None

    async def _object(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        body = await self._json(method, path, **kwargs)
        if body is None:
            return {}
        if not isinstance(body, dict):
            raise OmnigentError(f"{method} {path} answered {type(body).__name__}, not an object")
        return body

    async def _paginate(
        self, path: str, params: dict[str, Any] | None = None, *, page_size: int = 100
    ) -> AsyncIterator[dict[str, Any]]:
        after: str | None = None
        while True:
            page = await self._json(
                "GET", path, params={**(params or {}), "limit": page_size, **({"after": after} if after else {})}
            )
            for row in page.get("data", []):
                yield row
            after = next_cursor(page, after)
            if after is None:
                return

    async def agents(self) -> list[dict[str, Any]]:
        return [a async for a in self._paginate("/v1/agents")]

    async def hosts(self) -> list[dict[str, Any]]:
        body = await self._json("GET", "/v1/hosts")
        return body.get("hosts") or body.get("data") or []

    async def model_options(self, host_id: str, harness: str) -> list[dict[str, Any]]:
        body = await self._json("GET", f"/v1/hosts/{host_id}/harnesses/{harness}/model-options")
        return [m for m in body.get("models") or [] if not m.get("hidden")]

    async def session(self, session_id: str) -> dict[str, Any]:
        return await self._object("GET", f"/v1/sessions/{session_id}")

    async def session_snapshot(self, session_id: str, *, items: bool) -> dict[str, Any]:
        params = {"include_items": str(items).lower(), "include_liveness": "false", "include_usage": "false"}
        return await self._object("GET", f"/v1/sessions/{session_id}", params=params)

    async def session_page(self, params: dict[str, Any]) -> dict[str, Any]:
        return await self._object("GET", "/v1/sessions", params=params)

    async def find_session_by_label(self, agent_id: str, key: str, value: str) -> dict[str, Any] | None:
        """The agent's session labelled ``key=value`` among its ``LABEL_SCAN_LIMIT`` most recently updated.

        ``GET /v1/sessions`` has no label filter, so this scans; a thread being
        loaded was almost always active recently.
        """
        params = {"agent_id": agent_id, "include_archived": "true", "sort_by": "updated_at"}
        scanned = 0
        async for session in self._paginate("/v1/sessions", params, page_size=LABEL_SCAN_PAGE):
            labels = session.get("labels") or {}
            if labels.get(key) == value and FORK_SOURCE_LABEL not in labels:
                return session
            scanned += 1
            if scanned >= LABEL_SCAN_LIMIT:
                break
        return None

    async def create_session(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._object("POST", "/v1/sessions", json=body)

    async def session_items(self, session_id: str) -> AsyncIterator[dict[str, Any]]:
        async for item in self._paginate(f"/v1/sessions/{session_id}/items", {"order": "asc"}, page_size=1000):
            yield item

    async def fork_session(self, session_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self._object("POST", f"/v1/sessions/{session_id}/fork", json=body)

    async def launch_runner(self, host_id: str, session_id: str, workspace: str) -> dict[str, Any]:
        return await self._object("POST", f"/v1/hosts/{host_id}/runners", json={"session_id": session_id, "workspace": workspace})

    async def host_home(self, host_id: str) -> str | None:
        """The host daemon's home directory, read off an entry of its listing; ``None`` when it is empty."""
        body = await self._json("GET", f"/v1/hosts/{host_id}/filesystem", params={"limit": 1})
        entries = [e for e in body.get("data") or [] if isinstance(e, dict) and isinstance(e.get("path"), str)]
        return os.path.dirname(entries[0]["path"]) if entries else None

    async def update_session(self, session_id: str, body: dict[str, Any]) -> None:
        await self._json("PATCH", f"/v1/sessions/{session_id}", json=body)

    async def session_policies(self, session_id: str) -> list[dict[str, Any]]:
        body = await self._json("GET", f"/v1/sessions/{session_id}/policies")
        policies = body.get("data") if isinstance(body, dict) else body
        return [p for p in policies or [] if isinstance(p, dict)]

    async def add_session_policy(self, session_id: str, policy: dict[str, Any]) -> dict[str, Any]:
        return await self._object("POST", f"/v1/sessions/{session_id}/policies", json=policy)

    async def update_session_policy(self, session_id: str, policy_id: str, body: dict[str, Any]) -> None:
        await self._json("PATCH", f"/v1/sessions/{session_id}/policies/{policy_id}", json=body)

    async def post_event(self, session_id: str, event: dict[str, Any]) -> dict[str, Any]:
        return await self._object("POST", f"/v1/sessions/{session_id}/events", json=event)

    async def resolve_elicitation(self, session_id: str, elicitation_id: str, body: dict[str, Any]) -> None:
        await self._json("POST", f"/v1/sessions/{session_id}/elicitations/{elicitation_id}/resolve", json=body)

    async def stream(self, session_id: str, connected: asyncio.Event) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        timeout = httpx.Timeout(30.0, read=60.0)  # the server heartbeats every 15s
        async with self._http.stream("GET", f"/v1/sessions/{session_id}/stream", timeout=timeout) as response:
            if response.is_error:
                await response.aread()
                raise OmnigentError(f"stream {session_id} -> {response.status_code}: {response.text[:500]}", response.status_code)
            connected.set()
            yield STREAM_OPENED, {}
            event_type: str | None = None
            data_lines: list[str] = []
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
                elif not line and data_lines:
                    raw = "\n".join(data_lines)
                    data_lines = []
                    if raw == "[DONE]":
                        return
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload, dict):
                        yield str(event_type or payload.get("type") or ""), payload
                    event_type = None

    async def aclose(self) -> None:
        await self._http.aclose()


def next_cursor(page: dict[str, Any], after: str | None) -> str | None:
    """The cursor for the page after ``page``, or ``None`` at the end or when the server makes no progress."""
    last = page.get("last_id")
    if not page.get("has_more") or not isinstance(last, str) or not last or last == after:
        return None
    return last


def host_matches(name: str, hostnames: set[str]) -> bool:
    name = name.lower()
    return name in hostnames or name.split(".")[0] in {h.split(".")[0] for h in hostnames}


def resolve_host(hosts: list[dict[str, Any]], spec: str) -> dict[str, Any]:
    online = [h for h in hosts if h.get("status") == "online"]
    if spec == "auto":
        local = {socket.getfqdn().lower(), socket.gethostname().lower()}
        matches = [h for h in online if host_matches(str(h.get("name") or ""), local)]
    else:
        lowered = spec.lower()
        matches = (
            [h for h in online if spec in (h.get("host_id"), h.get("name"))]
            or [h for h in online if host_matches(str(h.get("name") or ""), {lowered})]
            or [h for h in online if str(h.get("name") or "").lower().startswith(lowered)]
        )
    if len(matches) != 1:
        names = sorted(str(h.get("name")) for h in (matches or online)) or "none"
        found = "matched" if matches else "online hosts are"
        raise OmnigentError(f"host {spec!r} must match exactly one online Omnigent host; {found} {names}")
    return matches[0]


def resolve_agent(agents: list[dict[str, Any]], spec: str) -> dict[str, Any]:
    for agent in agents:
        if agent.get("id") == spec:
            return agent
    named = [a for a in agents if a.get("name") == spec]
    if len(named) != 1:
        raise OmnigentError(f"--agent {spec!r} matched {len(named)} Omnigent agents")
    return named[0]
