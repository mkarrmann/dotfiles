"""The models T3 Code is offered: a pi agent's from this machine's `pi --list-models`, less the hidden ones."""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

import pytest

from omnigent_opencode.catalog import DEFAULT_MODEL, PI_EFFORTS, load_catalog

LISTING = """\
Pi at Meta (https://www.npmjs.com/package/@earendil-works/pi-coding-agent)
Using AI Gateway (Anthropic upstream)
provider   model                   context  max-out  thinking  images
anthropic  claude-sonnet-5         1M       128K     yes       yes
google     gemini-3.1-pro-preview  1.0M     65.5K    yes       yes
google     gemini-4-carbon         1.0M     65.5K    yes       yes
google     gemini-4-neon           1.0M     65.5K    yes       yes
meta       muse-latest             1M       128K     yes       yes
"""

CLAUDE_OPTIONS = [
    {"id": "sonnet", "model": "claude-sonnet-5-5", "displayName": "Sonnet 5.5"},
    {"id": "opus", "model": "claude-opus-5-5", "displayName": "Opus 5.5"},
    {"id": "haiku", "model": "claude-haiku-5-5", "displayName": "Haiku 5.5"},
]
CODEX_OPTIONS = [
    {"id": name, "model": name, "displayName": name}
    for name in ("gpt-6.1-sol", "gpt-6.2-sol", "gpt-5.6-sol", "gpt-5.6-luna", "gpt-6-luna", "gpt-5.5")
]


class StubOmnigent:
    async def hosts(self) -> list[dict[str, Any]]:
        return [{"host_id": "host_1", "name": socket.gethostname(), "status": "online"}]

    async def agents(self) -> list[dict[str, Any]]:
        return [
            {"id": "agent_claude", "name": "claude-native-ui", "harness": "claude-native"},
            {"id": "agent_codex", "name": "codex-native-ui", "harness": "codex-native"},
            {"id": "agent_gemini", "name": "gemini", "harness": "pi"},
        ]

    async def model_options(self, host_id: str, harness: str) -> list[dict[str, Any]]:
        return {"claude-native": CLAUDE_OPTIONS, "codex-native": CODEX_OPTIONS}[harness]


def fake_pi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str) -> None:
    pi = tmp_path / "pi"
    pi.write_text(f"#!/bin/sh\n{script}\n")
    pi.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))


async def test_pi_agent_offers_neon_then_carbon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "listing").write_text(LISTING)
    fake_pi(tmp_path, monkeypatch, f'[ "$1" = --list-models ] && /usr/bin/cat {tmp_path / "listing"}')
    gemini = (await load_catalog(StubOmnigent(), "auto", ["gemini"])).targets["gemini"]  # type: ignore[arg-type]

    assert gemini.model_ids() == ["google/gemini-4-neon", "google/gemini-4-carbon"]
    assert gemini.efforts == PI_EFFORTS
    infos = {info["id"]: info for info in gemini.model_infos()}
    assert infos["google/gemini-4-neon"]["providerID"] == "gemini"
    assert infos["google/gemini-4-neon"]["limit"]["context"] == 1_000_000


@pytest.mark.parametrize("script", ["exit 1", "echo 'Pi at Meta'"])
async def test_pi_agent_falls_back_to_its_default_when_pi_lists_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str
) -> None:
    fake_pi(tmp_path, monkeypatch, script)
    catalog = await load_catalog(StubOmnigent(), "auto", ["gemini"])  # type: ignore[arg-type]
    assert catalog.targets["gemini"].model_ids() == [DEFAULT_MODEL]


async def test_pi_agent_falls_back_to_its_default_without_pi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    catalog = await load_catalog(StubOmnigent(), "auto", ["gemini"])  # type: ignore[arg-type]
    assert catalog.targets["gemini"].model_ids() == [DEFAULT_MODEL]


async def test_haiku_luna_and_gpt_5_models_are_not_offered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    catalog = await load_catalog(StubOmnigent(), "auto", ["claude-native-ui", "codex-native-ui"])  # type: ignore[arg-type]
    assert catalog.targets["claude-native-ui"].model_ids() == ["sonnet", "opus"]
    assert catalog.targets["codex-native-ui"].model_ids() == ["gpt-6.1-sol", "gpt-6.2-sol"]
