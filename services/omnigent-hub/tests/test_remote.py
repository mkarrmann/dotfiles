from __future__ import annotations

import json
import subprocess

import pytest

from omnigent_hub.config import HubConfig
from omnigent_hub.remote import RemoteClient


def test_mac_remote_passes_ephemeral_cat_without_exposing_it(
    hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = []
    mint_count = 0
    monkeypatch.delenv("OMNIGENT_HA_DELEGATED_CAT", raising=False)

    def mint(owner_fbid: str) -> str:
        nonlocal mint_count
        mint_count += 1
        assert owner_fbid == hub_config.owner_fbid
        return "delegated-secret"

    def run(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        del timeout
        commands.append(argv)
        return subprocess.CompletedProcess(
            argv,
            0,
            json.dumps(
                {
                    "format_version": 1,
                    "epoch": 1,
                    "state": "active",
                    "active_hub": "primary.example.com",
                    "activation_id": "activation-1",
                    "restored_generation": None,
                    "updated_at": "2026-07-18T22:00:00Z",
                    "updated_by": "tester",
                }
            ),
            "",
        )

    monkeypatch.setattr("omnigent_hub.remote.mint_delegated_cat", mint)
    client = RemoteClient(hub_config, runner=run, system="Darwin")

    client.json("standby.example.com", ("resolve", "--json"))
    result = client.run("standby.example.com", ("gate", "--json"), check=False)

    assert mint_count == 1
    assert all(command[:3] == ["x2ssh", "-et", "standby.example.com"] for command in commands)
    assert all("OMNIGENT_HA_DELEGATED_CAT=delegated-secret" in command[-1] for command in commands)
    assert all("~/bin/omnigent-hub" in command[-1] for command in commands)
    assert "delegated-secret" not in " ".join(result.argv)


def test_remote_reuses_operator_cat(hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []
    monkeypatch.setenv("OMNIGENT_HA_DELEGATED_CAT", "operator-secret")

    def fail_mint(owner_fbid: str) -> str:
        raise AssertionError(f"unexpected mint for {owner_fbid}")

    def run(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        del timeout
        commands.append(argv)
        return subprocess.CompletedProcess(argv, 0, "{}", "")

    monkeypatch.setattr("omnigent_hub.remote.mint_delegated_cat", fail_mint)
    client = RemoteClient(hub_config, runner=run, system="Darwin")

    client.json("standby.example.com", ("local-status", "--json"))

    assert "OMNIGENT_HA_DELEGATED_CAT=operator-secret" in commands[0][-1]


def test_local_remote_never_mints_cat(
    hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_mint(owner_fbid: str) -> str:
        raise AssertionError(f"unexpected mint for {owner_fbid}")

    def run(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        del timeout
        return subprocess.CompletedProcess(argv, 0, "{}", "")

    monkeypatch.setattr("omnigent_hub.remote.mint_delegated_cat", fail_mint)
    client = RemoteClient(hub_config, runner=run, system="Linux")

    client.json("primary.example.com", ("local-status", "--json"))
