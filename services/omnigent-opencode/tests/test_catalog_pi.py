"""A pi agent's T3 Code models come from this machine's `pi --list-models`."""

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
google     gemini-2.5-flash-lite   1.0M     65.5K    no        yes
google     gemini-4-neon           1.0M     65.5K    yes       yes
meta       muse-latest             1M       128K     yes       yes
"""


class StubOmnigent:
    async def hosts(self) -> list[dict[str, Any]]:
        return [{"host_id": "host_1", "name": socket.gethostname(), "status": "online"}]

    async def agents(self) -> list[dict[str, Any]]:
        return [
            {"id": "agent_gemini", "name": "gemini", "harness": "pi"},
            {"id": "agent_muse", "name": "muse", "harness": "acp:muse-core"},
        ]

    async def model_options(self, host_id: str, harness: str) -> list[dict[str, Any]]:
        raise AssertionError(f"no Omnigent model list is asked for {harness}")


def fake_pi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str) -> None:
    pi = tmp_path / "pi"
    pi.write_text(f"#!/bin/sh\n{script}\n")
    pi.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))


async def test_pi_agent_offers_its_default_then_the_gemini_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "listing").write_text(LISTING)
    fake_pi(tmp_path, monkeypatch, f'[ "$1" = --list-models ] && /usr/bin/cat {tmp_path / "listing"}')
    gemini = (await load_catalog(StubOmnigent(), "auto", ["gemini", "muse"])).targets["gemini"]  # type: ignore[arg-type]

    assert gemini.model_ids() == [DEFAULT_MODEL, "google/gemini-2.5-flash-lite", "google/gemini-4-neon"]
    assert gemini.efforts == PI_EFFORTS
    infos = {info["id"]: info for info in gemini.model_infos()}
    assert infos["google/gemini-4-neon"]["providerID"] == "gemini"
    assert infos["google/gemini-4-neon"]["limit"]["context"] == 1_000_000
    assert infos[DEFAULT_MODEL]["limit"]["context"] == 1_000_000


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
