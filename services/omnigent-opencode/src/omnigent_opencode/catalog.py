"""The models T3 Code offers: one OpenCode provider per Omnigent agent.

A model slug `providerID/id` names an Omnigent agent and one of the models its
harness offers on this machine's host; a variant is a reasoning effort. An
agent whose harness offers no model list gets a single `default` model, which
leaves the choice to the agent spec.

Omnigent lists no models for the pi harnesses here: it reads only Pi's own
logins and models.json, while Meta's Pi registers its providers at runtime. So
a pi agent's models come from this machine's `pi --list-models`, behind a
`default` entry for the spec's model.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
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
# Claude Code's `[1m]` suffix selects the 1M-token window; Omnigent's claude-native
# catalog reports no contextWindow, and older probes put the suffix only on `model`.
LONG_CONTEXT: Final = 1_000_000
LONG_CONTEXT_SUFFIX: Final = "[1m]"
# Models whose standard window is already 1M tokens, with or without the suffix.
# Codex's catalog reports no contextWindow; codex_config/config.template.toml's
# model_context_window gives the plain Sol ids the window of their `[1m]` twins.
LONG_CONTEXT_MODELS: Final = (
    "gpt-6.1-sol",
    "gpt-6.2-sol",
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-haiku-5",
    "claude-fable-5",
    "claude-opus-4-6",
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-sonnet-4-6",
)
DEFAULT_OUTPUT: Final = 32_000
PI_HARNESSES: Final = frozenset({"pi", "pi-native"})
# The pi agent here is the Gemini one; Pi's login also serves Claude, GPT and Muse.
PI_PROVIDERS: Final = ("google",)
# Pi's ladder as Omnigent spells it (`none` is Pi's `off`), up to the highest a
# Gemini model takes; Omnigent clamps a level to the ones the model offers.
PI_EFFORTS: Final = ("none", "minimal", "low", "medium", "high")
PI_LIST_TIMEOUT_S: Final = 30.0
# Claude Code lists Fable, which this account has no access to.
UNAVAILABLE_MODELS: Final = ("fable",)


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


def _names(option: dict[str, Any]) -> tuple[str, str]:
    return str(option["id"]), str(option.get("model") or "")


def _long_context(option: dict[str, Any]) -> bool:
    return any(n.endswith(LONG_CONTEXT_SUFFIX) for n in _names(option))


def _natively_long_context(option: dict[str, Any]) -> bool:
    return any(n.startswith(LONG_CONTEXT_MODELS) for n in _names(option))


def _offered(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The harness's models less those this account cannot run and the `[1m]` twin
    of a model also offered plainly: the picker shows one entry per model, under the
    plain id, which Claude Code's catalog keeps when it drops its 1M entries."""
    usable = [o for o in options if not any(u in n.lower() for n in _names(o) for u in UNAVAILABLE_MODELS)]

    def base(option: dict[str, Any]) -> str:
        model, underlying = _names(option)
        return (underlying or model).removesuffix(LONG_CONTEXT_SUFFIX)

    plain = {base(o) for o in usable if not _long_context(o)}
    return [o for o in usable if not (_long_context(o) and base(o) in plain)]


def _context_window(model: str, option: dict[str, Any] | None) -> int:
    reported = option.get("contextWindow") if option else None
    if isinstance(reported, int) and reported > 0:
        return reported
    if option:
        long = _long_context(option) or _natively_long_context(option)
    else:
        long = model.endswith(LONG_CONTEXT_SUFFIX) or model.startswith(LONG_CONTEXT_MODELS)
    return LONG_CONTEXT if long else DEFAULT_CONTEXT


def _model_info(provider: str, model: str, name: str, variants: list[Json], option: dict[str, Any] | None) -> JsonObject:
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
        "limit": {"context": _context_window(model, option), "output": DEFAULT_OUTPUT},
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
                models = _offered(await client.model_options(host["host_id"], catalog_harness))
            except (OmnigentError, httpx.HTTPError) as exc:
                # One harness's failed probe must not hide every agent; this one offers its default model.
                LOG.warning("model options for %s on %s failed: %s", name, host.get("name"), exc)
        elif harness in PI_HARNESSES:
            models = await _pi_models(name)
        targets[name] = AgentTarget(name, str(agent["id"]), harness, tuple(models))
    if not targets:
        raise OmnigentError(f"none of the agents {agent_specs} exist on the Omnigent server")
    return Catalog(str(host["host_id"]), str(host.get("name") or host["host_id"]), targets)


async def _pi_models(agent: str) -> list[dict[str, Any]]:
    """`agent`'s default plus the `PI_PROVIDERS` models `pi --list-models` offers; none when Pi can't say."""
    pi = shutil.which("pi")
    if pi is None:
        return []
    try:
        proc = await asyncio.create_subprocess_exec(
            pi, "--list-models", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), PI_LIST_TIMEOUT_S)
    except (OSError, TimeoutError) as exc:
        LOG.warning("pi --list-models failed: %s", exc)
        return []
    if proc.returncode != 0:
        LOG.warning("pi --list-models exited %s", proc.returncode)
        return []
    listed = parse_pi_models(stdout.decode(errors="replace"))
    if not listed:
        return []
    default: dict[str, Any] = {
        "id": DEFAULT_MODEL,
        "displayName": f"{agent} (agent default)",
        "supportedReasoningEfforts": [{"reasoningEffort": e} for e in PI_EFFORTS],
    }
    # The spec's model is one of these; the smallest window is the one it surely has.
    windows = [o["contextWindow"] for o in listed if "contextWindow" in o]
    if windows:
        default["contextWindow"] = min(windows)
    return [default, *listed]


def parse_pi_models(listing: str) -> list[dict[str, Any]]:
    """The `PI_PROVIDERS` rows of `pi --list-models`' table, as model options with `provider/model` ids."""
    lines = listing.splitlines()
    start = next((i for i, line in enumerate(lines) if line.split()[:2] == ["provider", "model"]), None)
    if start is None:
        return []
    options: list[dict[str, Any]] = []
    for line in lines[start + 1 :]:
        cols = line.split()
        if len(cols) < 5 or cols[0] not in PI_PROVIDERS:
            continue
        provider, model, context, _output, thinking = cols[:5]
        option: dict[str, Any] = {"id": f"{provider}/{model}", "model": f"{provider}/{model}", "displayName": model}
        window = _pi_tokens(context)
        if window:
            option["contextWindow"] = window
        if thinking == "yes":
            option["supportedReasoningEfforts"] = [{"reasoningEffort": e} for e in PI_EFFORTS]
        options.append(option)
    return options


def _pi_tokens(size: str) -> int | None:
    """Pi's rounded token counts: `1.0M`, `65.5K`, `8192`."""
    scale = {"K": 1_000, "M": 1_000_000}.get(size[-1:].upper(), 1)
    try:
        return int(float(size[:-1] if scale > 1 else size) * scale)
    except ValueError:
        return None
