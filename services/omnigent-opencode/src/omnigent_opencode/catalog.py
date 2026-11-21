"""The models T3 Code offers: one OpenCode provider per Omnigent agent.

A model slug `providerID/id` names an Omnigent agent and one of the models its
harness offers on this machine's host; a variant is a reasoning effort. An
agent whose harness offers no model list gets a single `default` model, which
leaves the choice to the agent spec.

Omnigent lists no models for the pi harnesses here: it reads only Pi's own
logins and models.json, while Meta's Pi registers its providers at runtime. So
a pi agent's models are the `PI_MODELS` this machine's Pi offers, each with the
thinking levels Pi reports for it, asked over Pi's RPC mode.

Each model offers the efforts its harness reports for it. Only a harness that
reports none for any model (claude-native) gets a ladder from here.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import signal
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
# The pi agent's models, in picker order: the gemini spec's default first. Pi's
# login also serves the rest of Google's catalog, Claude, GPT and Muse.
PI_MODELS: Final = ("google/gemini-4-neon", "google/gemini-4-carbon")
# An ephemeral Pi that only answers catalog questions: no session file, tools,
# extensions or MCP servers.
PI_RPC: Final = ("--mode", "rpc", "--no-session", "--no-tools", "--no-extensions", "--no-mcp")
PI_LIST_TIMEOUT_S: Final = 30.0
PI_STOP_TIMEOUT_S: Final = 5.0
# Left out of the picker wherever they appear in a model's names: Fable, which
# this account has no access to, and Haiku, Luna and GPT-5.x, deliberately not
# offered.
HIDDEN_MODELS: Final = ("fable", "haiku", "luna", "gpt-5.")


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
        """Every effort any of the agent's models takes: what its default model is offered."""
        offered = {e for m in self.models for e in _reported_efforts(m)}
        if not offered and self.harness.startswith("claude"):
            offered = set(CLAUDE_EFFORTS)
        return _in_order(offered)

    def model_efforts(self, option: dict[str, Any]) -> tuple[str, ...]:
        """The efforts `option` reports, or the agent's when none of its models report any."""
        if any(_reported_efforts(m) for m in self.models):
            return _in_order(_reported_efforts(option))
        return self.efforts

    def model_ids(self) -> list[str]:
        return [str(m["id"]) for m in self.models] or [DEFAULT_MODEL]

    def model_infos(self) -> list[JsonObject]:
        if not self.models:
            return [_model_info(self.name, DEFAULT_MODEL, f"{self.name} (agent default)", _variants(self.efforts), None)]
        return [
            _model_info(self.name, str(m["id"]), str(m.get("displayName") or m["id"]), _variants(self.model_efforts(m)), m)
            for m in self.models
        ]


def _reported_efforts(option: dict[str, Any]) -> set[str]:
    return {
        e["reasoningEffort"]
        for e in option.get("supportedReasoningEfforts") or []
        if isinstance(e, dict) and isinstance(e.get("reasoningEffort"), str)
    }


def _in_order(efforts: set[str]) -> tuple[str, ...]:
    return tuple(e for e in EFFORT_ORDER if e in efforts)


def _variants(efforts: tuple[str, ...]) -> list[Json]:
    return [{"id": e} for e in efforts]


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
    usable = [o for o in options if not any(u in n.lower() for n in _names(o) for u in HIDDEN_MODELS)]

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
    # False when an agent's model list could not be read, so it shows only its
    # default model; such a catalog is worth loading again.
    complete: bool = True

    def target(self, provider: str) -> AgentTarget | None:
        return self.targets.get(provider)

    def model_infos(self) -> list[JsonObject]:
        return [info for target in self.targets.values() for info in target.model_infos()]


async def load_catalog(client: OmnigentClient, host_spec: str, agent_specs: list[str]) -> Catalog:
    host = resolve_host(await client.hosts(), host_spec)
    agents = await client.agents()
    targets: dict[str, AgentTarget] = {}
    complete = True
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
                complete = False
        elif harness in PI_HARNESSES:
            pi_models = await _pi_models()
            complete = complete and pi_models is not None
            models = pi_models or []
        targets[name] = AgentTarget(name, str(agent["id"]), harness, tuple(models))
    if not targets:
        raise OmnigentError(f"none of the agents {agent_specs} exist on the Omnigent server")
    return Catalog(str(host["host_id"]), str(host.get("name") or host["host_id"]), targets, complete)


class PiError(Exception):
    """Pi's RPC mode did not answer a catalog question."""


async def _pi_models() -> list[dict[str, Any]] | None:
    """The `PI_MODELS` this machine's Pi offers: none without Pi, None when Pi can't say."""
    pi = shutil.which("pi")
    if pi is None:
        return []
    try:
        proc = await asyncio.create_subprocess_exec(
            pi,
            *PI_RPC,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        LOG.warning("starting pi failed: %s", exc)
        return None
    try:
        return await asyncio.wait_for(_ask_pi(proc), PI_LIST_TIMEOUT_S)
    except (OSError, TimeoutError, PiError) as exc:
        LOG.warning("pi's model list failed: %s", exc)
        return None
    finally:
        await _stop_pi(proc)


async def _stop_pi(proc: asyncio.subprocess.Process) -> None:
    """End Pi and everything it started.

    `pi` is a wrapper whose child, the real Pi, holds the pipes: killing only the
    wrapper leaves that child running and `wait()` blocked on the open pipe. Pi
    exits when its stdin closes; its process group is killed if it does not."""
    if proc.stdin is not None:
        proc.stdin.close()
    try:
        await asyncio.wait_for(proc.wait(), PI_STOP_TIMEOUT_S)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        await proc.wait()


async def _ask_pi(proc: asyncio.subprocess.Process) -> list[dict[str, Any]]:
    """Pi's models and, for each `PI_MODELS` one, the thinking levels it takes.

    Levels come from `get_available_thinking_levels` after `set_model`, not from
    the model's `thinkingLevelMap`, whose keys include levels it maps to null
    (unsupported) and omit ones Pi offers by default."""
    assert proc.stdin is not None and proc.stdout is not None
    stdin, stdout = proc.stdin, proc.stdout
    calls = 0

    async def call(command: dict[str, Any]) -> Any:
        nonlocal calls
        calls += 1
        call_id = f"catalog-{calls}"
        stdin.write((json.dumps({"id": call_id, **command}) + "\n").encode())
        await stdin.drain()
        while line := await stdout.readline():
            try:
                reply = json.loads(line)
            except ValueError:
                continue
            if isinstance(reply, dict) and reply.get("type") == "response" and reply.get("id") == call_id:
                if not reply.get("success"):
                    raise PiError(f"{command['type']}: {reply.get('error')}")
                return reply.get("data")
        raise PiError(f"pi exited before answering {command['type']}")

    listed = (await call({"type": "get_available_models"}) or {}).get("models") or []
    models = {f"{m.get('provider')}/{m.get('id')}": m for m in listed if isinstance(m, dict)}
    levels: dict[str, list[str]] = {}
    for slug in PI_MODELS:
        model = models.get(slug)
        if model is None or not model.get("reasoning"):
            continue
        await call({"type": "set_model", "provider": model["provider"], "modelId": model["id"]})
        levels[slug] = (await call({"type": "get_available_thinking_levels"}) or {}).get("levels") or []
    return pi_options(models, levels)


def pi_options(models: dict[str, dict[str, Any]], levels: dict[str, list[str]]) -> list[dict[str, Any]]:
    """Model options for the `PI_MODELS` among Pi's `models`, in `PI_MODELS` order;
    `levels` are each one's thinking levels in Pi's spelling (`off` is Omnigent's `none`)."""
    options: list[dict[str, Any]] = []
    for slug in PI_MODELS:
        model = models.get(slug)
        if model is None:
            continue
        option: dict[str, Any] = {"id": slug, "model": slug, "displayName": str(model.get("id") or slug)}
        window = model.get("contextWindow")
        if isinstance(window, int) and window > 0:
            option["contextWindow"] = window
        efforts = ["none" if level == "off" else level for level in levels.get(slug, [])]
        if any(e != "none" for e in efforts):
            option["supportedReasoningEfforts"] = [{"reasoningEffort": e} for e in efforts]
        options.append(option)
    return options
