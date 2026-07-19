from __future__ import annotations

import json
import subprocess
from dataclasses import replace

import pytest

from omnigent_hub.config import HubConfig
from omnigent_hub.remote import RemoteClient, RemoteError


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


def test_remote_json_accepts_x2ssh_terminal_noise(hub_config: HubConfig) -> None:
    payload = active_payload(4)

    def run(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        del timeout
        stdout = f"\x1b]0;remote host\x07{json.dumps(payload)}\x1b[0mConnection closed\r\n"
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    client = RemoteClient(hub_config, runner=run, system="Darwin")

    assert client.json("standby.example.com", ("resolve", "--json")) == payload


def test_remote_json_accepts_x2ssh_stderr_output(hub_config: HubConfig) -> None:
    payload = active_payload(4)

    def run(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        del timeout
        stderr = f"ET status: connected {json.dumps(payload)} disconnected"
        return subprocess.CompletedProcess(argv, 0, "", stderr)

    client = RemoteClient(hub_config, runner=run, system="Darwin")

    assert client.json("standby.example.com", ("resolve", "--json")) == payload


def test_resolve_does_not_retry_successful_non_json_command(
    hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = []

    def fail_mint(owner_fbid: str) -> str:
        raise AssertionError(f"unexpected mint for {owner_fbid}")

    def run(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        del timeout
        commands.append(argv)
        return subprocess.CompletedProcess(argv, 0, "terminal noise only", "")

    monkeypatch.delenv("OMNIGENT_HA_DELEGATED_CAT", raising=False)
    monkeypatch.setattr("omnigent_hub.remote.mint_delegated_cat", fail_mint)
    client = RemoteClient(hub_config, runner=run, system="Darwin")

    with pytest.raises(RemoteError, match="no hub candidate returned"):
        client.resolve()

    assert len(commands) == 2


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


def test_resolve_uses_reachable_candidate_and_highest_epoch(
    hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OMNIGENT_HA_DELEGATED_CAT", "operator-secret")

    def run(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        del timeout
        host = argv[2]
        if host == "primary.example.com":
            return subprocess.CompletedProcess(argv, 1, "", "unreachable")
        return subprocess.CompletedProcess(argv, 0, json.dumps(active_payload(4)), "")

    mac_config = replace(hub_config, local_fqdn="mac.example.com")
    record, supplier, errors = RemoteClient(mac_config, runner=run, system="Darwin").resolve()

    assert record.epoch == 4
    assert supplier == "standby.example.com"
    assert "primary.example.com" in errors


def test_resolve_avoids_credentials_when_candidate_mount_is_readable(
    hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = []

    def fail_mint(owner_fbid: str) -> str:
        raise AssertionError(f"unexpected mint for {owner_fbid}")

    def run(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        del timeout
        commands.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(active_payload(4)), "")

    monkeypatch.delenv("OMNIGENT_HA_DELEGATED_CAT", raising=False)
    monkeypatch.setattr("omnigent_hub.remote.mint_delegated_cat", fail_mint)
    peer = replace(hub_config, local_fqdn="peer.example.com")

    record, _, _ = RemoteClient(peer, runner=run, system="Linux").resolve()

    assert record.epoch == 4
    assert len(commands) == 2
    assert all("OMNIGENT_HA_DELEGATED_CAT" not in command[-1] for command in commands)


def test_resolve_rejects_conflicting_same_epoch(
    hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OMNIGENT_HA_DELEGATED_CAT", "operator-secret")

    def run(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        del timeout
        host = argv[2]
        active = "primary.example.com" if host == "primary.example.com" else "standby.example.com"
        return subprocess.CompletedProcess(argv, 0, json.dumps(active_payload(5, active)), "")

    mac_config = replace(hub_config, local_fqdn="mac.example.com")
    with pytest.raises(RemoteError, match="conflicting active-hub records"):
        RemoteClient(mac_config, runner=run, system="Darwin").resolve()


def active_payload(epoch: int, active_hub: str = "standby.example.com") -> dict[str, object]:
    return {
        "format_version": 1,
        "epoch": epoch,
        "state": "active",
        "active_hub": active_hub,
        "activation_id": f"activation-{epoch}",
        "restored_generation": f"generation-{epoch}",
        "updated_at": "2026-07-18T22:00:00Z",
        "updated_by": "tester",
    }
