from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parents[3] / "bin/omnigent-server-url"


def run_script(tmp_path: Path, *args: str, host: str) -> subprocess.CompletedProcess[str]:
    topology = tmp_path / "topology.env"
    topology.write_text(
        "OMNIGENT_PRIMARY_FQDN=primary.example.com\n"
        "OMNIGENT_STANDBY_FQDN=standby.example.com\n"
        "OMNIGENT_PORT=6767\n",
        encoding="utf-8",
    )
    cache = tmp_path / "active-hub.json"
    cache.write_text(
        json.dumps(
            {
                "format_version": 1,
                "epoch": 3,
                "state": "active",
                "active_hub": "standby.example.com",
                "activation_id": "activation-3",
            }
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update(
        {
            "OMNIGENT_TOPOLOGY_FILE": str(topology),
            "OMNIGENT_HA_ROUTING_CACHE": str(cache),
            "OMNIGENT_LOCAL_FQDN": host,
        }
    )
    return subprocess.run([str(SCRIPT), *args], env=env, text=True, capture_output=True)


def test_active_hub_uses_loopback(tmp_path: Path) -> None:
    result = run_script(tmp_path, host="standby.example.com")
    assert result.returncode == 0
    assert result.stdout.strip() == "http://127.0.0.1:6767"


def test_inactive_hub_uses_loopback_proxy(tmp_path: Path) -> None:
    result = run_script(tmp_path, host="primary.example.com")
    assert result.returncode == 0
    assert result.stdout.strip() == "http://127.0.0.1:6767"


def test_peer_uses_active_hub_fqdn(tmp_path: Path) -> None:
    result = run_script(tmp_path, host="peer.facebook.com")
    assert result.returncode == 0
    assert result.stdout.strip() == "http://standby.example.com:6767"


def test_static_candidates_do_not_require_cache(tmp_path: Path) -> None:
    result = run_script(tmp_path, "--candidates", host="peer.facebook.com")
    assert result.returncode == 0
    assert result.stdout.splitlines() == ["primary.example.com", "standby.example.com"]


def test_transition_cache_fails_closed(tmp_path: Path) -> None:
    run_script(tmp_path, host="peer.facebook.com")
    cache = tmp_path / "active-hub.json"
    value = json.loads(cache.read_text(encoding="utf-8"))
    value.update(
        {
            "state": "transition",
            "active_hub": None,
            "activation_id": None,
            "transition_id": "transition-4",
        }
    )
    cache.write_text(json.dumps(value), encoding="utf-8")
    result = run_script_with_existing(tmp_path, host="peer.facebook.com")
    assert result.returncode == 1
    assert "fenced by transition transition-4" in result.stderr


def run_script_with_existing(tmp_path: Path, *, host: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "OMNIGENT_TOPOLOGY_FILE": str(tmp_path / "topology.env"),
            "OMNIGENT_HA_ROUTING_CACHE": str(tmp_path / "active-hub.json"),
            "OMNIGENT_LOCAL_FQDN": host,
        }
    )
    return subprocess.run([str(SCRIPT)], env=env, text=True, capture_output=True)
