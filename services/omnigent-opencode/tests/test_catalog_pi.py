"""The models T3 Code is offered, each with the efforts its harness reports for it: a pi agent's from this
machine's Pi over its RPC mode, less the hidden ones."""

from __future__ import annotations

import asyncio
import json
import socket
import sys
from pathlib import Path
from typing import Any

import pytest

from omnigent_opencode import server
from omnigent_opencode.catalog import DEFAULT_MODEL, Catalog, load_catalog
from omnigent_opencode.omnigent import OmnigentError
from omnigent_opencode.store import Store

# What Meta's Pi answers, trimmed: `thinkingLevelMap` maps unsupported levels to null and
# omits ones offered by default, so only `get_available_thinking_levels` is authoritative.
PI_MODELS = [
    {"provider": "anthropic", "id": "claude-sonnet-5", "reasoning": True, "contextWindow": 1_000_000},
    {"provider": "google", "id": "gemini-4-carbon", "reasoning": True, "contextWindow": 1_048_576,
     "thinkingLevelMap": {"off": None, "minimal": None, "low": "LOW", "medium": "MEDIUM", "high": "HIGH"}},
    {"provider": "google", "id": "gemini-4-neon", "reasoning": True, "contextWindow": 1_048_576,
     "thinkingLevelMap": {"off": None, "minimal": None, "low": "LOW", "medium": "MEDIUM", "high": "HIGH", "xhigh": "XHIGH"}},
]  # fmt: skip
PI_LEVELS = {
    "anthropic/claude-sonnet-5": ["off", "minimal", "low", "medium", "high", "xhigh", "max"],
    "google/gemini-4-carbon": ["low", "medium", "high"],
    "google/gemini-4-neon": ["low", "medium", "high", "xhigh"],
}

# A Pi in RPC mode: one JSON command per line in, events and responses out, as `pi --mode rpc` does.
FAKE_PI = """\
import json, sys
models, levels, fail = json.loads(sys.argv[1]), json.loads(sys.argv[2]), sys.argv[3]
assert sys.argv[4:6] == ["--mode", "rpc"], sys.argv
current = None
print(json.dumps({"type": "extension_ui_request"}), flush=True)
for line in sys.stdin:
    command = json.loads(line)
    kind = command["type"]
    if kind == fail:
        sys.exit(1)
    if kind == "get_available_models":
        data = {"models": models}
    elif kind == "set_model":
        current = command["provider"] + "/" + command["modelId"]
        data = next(m for m in models if m["provider"] + "/" + m["id"] == current)
    elif kind == "get_available_thinking_levels":
        data = {"levels": levels.get(current, ["off"])}
    print(json.dumps({"type": "extension_ui_request"}), flush=True)
    print(json.dumps({"id": command["id"], "type": "response", "command": kind, "success": True, "data": data}), flush=True)
"""

CLAUDE_OPTIONS = [
    {"id": "sonnet", "model": "claude-sonnet-5-5", "displayName": "Sonnet 5.5"},
    {"id": "opus", "model": "claude-opus-5-5", "displayName": "Opus 5.5"},
    {"id": "haiku", "model": "claude-haiku-5-5", "displayName": "Haiku 5.5"},
]
SOL = ["low", "medium", "high", "xhigh", "max", "ultra"]
CODEX_LADDERS = {
    "gpt-6.1-sol": SOL,
    "gpt-6.2-sol": SOL,
    "gpt-5.6-sol": SOL,
    "gpt-5.6-luna": ["none", "low", "medium", "high", "xhigh", "max"],
    "gpt-6-luna": ["low", "medium", "high", "xhigh", "max"],
    "gpt-5.5": ["low", "medium", "high", "xhigh"],
}
CODEX_OPTIONS = [
    {"id": name, "model": name, "displayName": name, "supportedReasoningEfforts": [{"reasoningEffort": e} for e in ladder]}
    for name, ladder in CODEX_LADDERS.items()
]


class StubOmnigent:
    def __init__(self, options: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.options: dict[str, list[dict[str, Any]]] = (
            options if options is not None else {"claude-native": CLAUDE_OPTIONS, "codex-native": CODEX_OPTIONS}
        )

    async def hosts(self) -> list[dict[str, Any]]:
        return [{"host_id": "host_1", "name": socket.gethostname(), "status": "online"}]

    async def agents(self) -> list[dict[str, Any]]:
        return [
            {"id": "agent_claude", "name": "claude-native-ui", "harness": "claude-native"},
            {"id": "agent_codex", "name": "codex-native-ui", "harness": "codex-native"},
            {"id": "agent_gemini", "name": "gemini", "harness": "pi"},
        ]

    async def model_options(self, host_id: str, harness: str) -> list[dict[str, Any]]:
        if harness not in self.options:
            raise OmnigentError("the codex model probe failed — see the host log", 503)
        return self.options[harness]


def fake_pi(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail: str = "",
    models: list[dict[str, Any]] = PI_MODELS,
    levels: dict[str, list[str]] = PI_LEVELS,
) -> None:
    (tmp_path / "fake_pi.py").write_text(FAKE_PI)
    pi = tmp_path / "pi"
    args = " ".join(f"'{json.dumps(a)}'" for a in (models, levels))
    # Like Meta's `pi`, a wrapper whose child (not an exec'd replacement) holds the pipes.
    pi.write_text(f'#!/bin/sh\n{sys.executable} {tmp_path / "fake_pi.py"} {args} "{fail}" "$@"\n')
    pi.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))


def variants(info: dict[str, Any]) -> list[str]:
    return [v["id"] for v in info["variants"]]


async def test_pi_agent_offers_neon_then_carbon_with_the_levels_pi_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_pi(tmp_path, monkeypatch)
    catalog = await load_catalog(StubOmnigent(), "auto", ["gemini"])  # type: ignore[arg-type]
    gemini = catalog.targets["gemini"]

    assert catalog.complete
    assert gemini.model_ids() == ["google/gemini-4-neon", "google/gemini-4-carbon"]
    infos: dict[str, Any] = {str(info["id"]): info for info in gemini.model_infos()}
    assert variants(infos["google/gemini-4-neon"]) == ["low", "medium", "high", "xhigh"]
    assert variants(infos["google/gemini-4-carbon"]) == ["low", "medium", "high"]
    assert infos["google/gemini-4-neon"]["providerID"] == "gemini"
    assert infos["google/gemini-4-neon"]["limit"]["context"] == 1_048_576


async def test_pi_off_is_offered_as_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_pi(tmp_path, monkeypatch, models=[PI_MODELS[2]], levels={"google/gemini-4-neon": ["off", "low", "high"]})
    gemini = (await load_catalog(StubOmnigent(), "auto", ["gemini"])).targets["gemini"]  # type: ignore[arg-type]
    assert variants(gemini.model_infos()[0]) == ["none", "low", "high"]


async def test_pi_model_without_reasoning_offers_no_efforts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_pi(tmp_path, monkeypatch, models=[{**PI_MODELS[2], "reasoning": False}, PI_MODELS[1]])
    gemini = (await load_catalog(StubOmnigent(), "auto", ["gemini"])).targets["gemini"]  # type: ignore[arg-type]
    infos = {info["id"]: info for info in gemini.model_infos()}
    assert variants(infos["google/gemini-4-neon"]) == []
    assert variants(infos["google/gemini-4-carbon"]) == ["low", "medium", "high"]


async def test_pi_and_the_child_holding_its_pipes_are_ended(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_pi(tmp_path, monkeypatch)
    catalog = await asyncio.wait_for(load_catalog(StubOmnigent(), "auto", ["gemini"]), 10)  # type: ignore[arg-type]
    assert catalog.complete
    script = str(tmp_path / "fake_pi.py").encode()
    assert not [proc for proc in Path("/proc").glob("[0-9]*/cmdline") if script in _read(proc)]


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError:
        return b""


@pytest.mark.parametrize("fail", ["get_available_models", "set_model", "get_available_thinking_levels"])
async def test_pi_agent_falls_back_to_its_default_when_pi_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail: str
) -> None:
    fake_pi(tmp_path, monkeypatch, fail=fail)
    catalog = await load_catalog(StubOmnigent(), "auto", ["gemini"])  # type: ignore[arg-type]
    assert catalog.targets["gemini"].model_ids() == [DEFAULT_MODEL]
    assert not catalog.complete


async def test_pi_agent_falls_back_to_its_default_when_pi_lists_none_of_its_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_pi(tmp_path, monkeypatch, models=[PI_MODELS[0]])
    catalog = await load_catalog(StubOmnigent(), "auto", ["gemini"])  # type: ignore[arg-type]
    assert catalog.targets["gemini"].model_ids() == [DEFAULT_MODEL]
    assert catalog.complete


async def test_pi_agent_falls_back_to_its_default_without_pi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    catalog = await load_catalog(StubOmnigent(), "auto", ["gemini"])  # type: ignore[arg-type]
    assert catalog.targets["gemini"].model_ids() == [DEFAULT_MODEL]
    assert catalog.complete


async def test_each_codex_model_offers_its_own_ladder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    codex = (await load_catalog(StubOmnigent(), "auto", ["codex-native-ui"])).targets["codex-native-ui"]  # type: ignore[arg-type]
    assert {info["id"]: variants(info) for info in codex.model_infos()} == {"gpt-6.1-sol": SOL, "gpt-6.2-sol": SOL}


async def test_codex_sol_models_report_the_window_codex_does(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    codex = (await load_catalog(StubOmnigent(), "auto", ["codex-native-ui"])).targets["codex-native-ui"]  # type: ignore[arg-type]
    windows: dict[str, Any] = {str(info["id"]): info["limit"] for info in codex.model_infos()}
    assert {model: limit["context"] for model, limit in windows.items()} == {"gpt-6.1-sol": 875_900, "gpt-6.2-sol": 828_400}


async def test_claude_models_offer_claudes_whole_ladder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    claude = (await load_catalog(StubOmnigent(), "auto", ["claude-native-ui"])).targets["claude-native-ui"]  # type: ignore[arg-type]
    for info in claude.model_infos():
        assert variants(info) == ["low", "medium", "high", "xhigh", "max"]


async def test_a_failed_model_probe_makes_the_catalog_incomplete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    catalog = await load_catalog(StubOmnigent({"claude-native": CLAUDE_OPTIONS}), "auto", ["codex-native-ui"])  # type: ignore[arg-type]
    assert catalog.targets["codex-native-ui"].model_ids() == [DEFAULT_MODEL]
    assert not catalog.complete


async def test_haiku_luna_and_gpt_5_models_are_not_offered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    catalog = await load_catalog(StubOmnigent(), "auto", ["claude-native-ui", "codex-native-ui"])  # type: ignore[arg-type]
    assert catalog.targets["claude-native-ui"].model_ids() == ["sonnet", "opus"]
    assert catalog.targets["codex-native-ui"].model_ids() == ["gpt-6.1-sol", "gpt-6.2-sol"]


def app_for(stub: StubOmnigent, tmp_path: Path, agents: list[str]) -> server.App:
    config = server.Config(password=None, host_spec="auto", agent_specs=agents, default_directory=str(tmp_path))
    return server.App(config, stub, Store(None))  # type: ignore[arg-type]


async def reloaded(app: server.App) -> Catalog:
    for _ in range(100):
        catalog = await app.catalog()
        if catalog.complete:
            return catalog
        await asyncio.sleep(0.01)
    raise AssertionError("the catalog was not reloaded")


async def test_a_catalog_missing_an_agents_models_is_loaded_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(server, "CATALOG_RETRY_S", 0.0)
    stub = StubOmnigent({"claude-native": CLAUDE_OPTIONS})
    app = app_for(stub, tmp_path, ["codex-native-ui"])
    assert (await app.catalog()).targets["codex-native-ui"].model_ids() == [DEFAULT_MODEL]

    stub.options["codex-native"] = CODEX_OPTIONS
    catalog = await reloaded(app)
    assert catalog.targets["codex-native-ui"].model_ids() == ["gpt-6.1-sol", "gpt-6.2-sol"]
    await app.close_catalog()


async def test_a_complete_catalog_is_not_loaded_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(server, "CATALOG_RETRY_S", 0.0)
    app = app_for(StubOmnigent(), tmp_path, ["codex-native-ui"])
    first = await app.catalog()
    await asyncio.sleep(0.01)
    assert await app.catalog() is first
