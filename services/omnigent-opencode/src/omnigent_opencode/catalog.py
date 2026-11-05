"""The models T3 Code offers: one OpenCode provider per Omnigent agent.

A model slug `providerID/id` names an Omnigent agent and one of the models its
harness offers on this machine's host; a variant is a reasoning effort. An
agent whose harness offers no model list gets a single `default` model, which
leaves the choice to the agent spec.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Final

import httpx

from .omnigent import OmnigentClient, OmnigentError, resolve_agent, resolve_host
from .wire import Json, JsonObject

LOG = logging.getLogger(__name__)

DEFAULT_MODEL: Final = "default"
# Omnigent's ladder and Claude's subset (omnigent/util/reasoning_effort.py).
EFFORT_ORDER: Final = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
CLAUDE_EFFORTS: Final = frozenset({"low", "medium", "high", "xhigh", "max"})
# SDK harnesses launch the same binaries as the native ones; the host
# model-options route only accepts the native names (see bin/omnigent-models).
CATALOG_HARNESS: Final = {
    "claude-native": "claude-native",
    "claude-sdk": "claude-native",
    "codex-native": "codex-native",
    "codex": "codex-native",
    "codex-sdk": "codex-native",
}
DEFAULT_CONTEXT: Final = 200_000
DEFAULT_OUTPUT: Final = 32_000


@dataclass(frozen=True)
class AgentTarget:
    """An Omnigent agent T3 Code can start sessions with, on this machine's host."""

    name: str
    agent_id: str
    harness: str
    models: tuple[dict[str, Any], ...]

    @property
    def native(self) -> bool:
        return self.harness.endswith("-native")

    @property
    def native_plan(self) -> bool:
        # claude-native's plan permission mode is not used: Omnigent cannot switch
        # back to bypassPermissions after launch, so leaving Plan would downgrade it.
        return self.harness == "codex-native"

    @property
    def efforts(self) -> tuple[str, ...]:
        offered = {
            e["reasoningEffort"]
            for m in self.models
            for e in m.get("supportedReasoningEfforts") or []
            if isinstance(e, dict) and isinstance(e.get("reasoningEffort"), str)
        }
        if not offered and self.harness.startswith("claude"):
            offered = set(CLAUDE_EFFORTS)
        return tuple(e for e in EFFORT_ORDER if e in offered)

    def model_ids(self) -> list[str]:
        return [str(m["id"]) for m in self.models] or [DEFAULT_MODEL]

    def model_infos(self) -> list[JsonObject]:
        variants: list[Json] = [{"id": e} for e in self.efforts]
        if not self.models:
            return [_model_info(self.name, DEFAULT_MODEL, f"{self.name} (agent default)", variants, None)]
        return [_model_info(self.name, str(m["id"]), str(m.get("displayName") or m["id"]), variants, m) for m in self.models]


def _model_info(provider: str, model: str, name: str, variants: list[Json], option: dict[str, Any] | None) -> JsonObject:
    context = option.get("contextWindow") if option else None
    return {
        "id": model,
        "modelID": model,
        "providerID": provider,
        "name": name,
        "capabilities": {"tools": True, "input": ["text", "image"], "output": ["text"]},
        "variants": variants,
        "time": {"released": 0},
        "cost": [],
        "status": "active",
        "enabled": True,
        "limit": {"context": context if isinstance(context, int) and context > 0 else DEFAULT_CONTEXT, "output": DEFAULT_OUTPUT},
    }


@dataclass(frozen=True)
class Catalog:
    host_id: str
    host_name: str
    targets: dict[str, AgentTarget]

    def target(self, provider: str) -> AgentTarget | None:
        return self.targets.get(provider)

    def model_infos(self) -> list[JsonObject]:
        return [info for target in self.targets.values() for info in target.model_infos()]


async def load_catalog(client: OmnigentClient, host_spec: str, agent_specs: list[str]) -> Catalog:
    host = resolve_host(await client.hosts(), host_spec)
    agents = await client.agents()
    targets: dict[str, AgentTarget] = {}
    for spec in agent_specs:
        try:
            agent = resolve_agent(agents, spec)
        except OmnigentError:
            continue
        harness = str(agent.get("harness"))
        catalog_harness = CATALOG_HARNESS.get(harness)
        name = str(agent.get("name") or spec)
        models: list[dict[str, Any]] = []
        if catalog_harness:
            try:
                models = await client.model_options(host["host_id"], catalog_harness)
            except (OmnigentError, httpx.HTTPError) as exc:
                # One harness's failed probe must not hide every agent; this one offers its default model.
                LOG.warning("model options for %s on %s failed: %s", name, host.get("name"), exc)
        targets[name] = AgentTarget(name, str(agent["id"]), harness, tuple(models))
    if not targets:
        raise OmnigentError(f"none of the agents {agent_specs} exist on the Omnigent server")
    return Catalog(str(host["host_id"]), str(host.get("name") or host["host_id"]), targets)
