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


def test_peer_uses_loopback_ssh_proxy(tmp_path: Path) -> None:
    result = run_script(tmp_path, host="peer.facebook.com")
    assert result.returncode == 0
    assert result.stdout.strip() == "http://127.0.0.1:6767"


def test_static_candidates_do_not_require_cache(tmp_path: Path) -> None:
    result = run_script(tmp_path, "--candidates", host="peer.facebook.com")
    assert result.returncode == 0
    assert result.stdout.splitlines() == ["primary.example.com", "standby.example.com"]


def _fence_cache(tmp_path: Path) -> None:
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


def test_transition_cache_fails_closed(tmp_path: Path) -> None:
    run_script(tmp_path, host="peer.facebook.com")
    _fence_cache(tmp_path)
    result = run_script_with_existing(tmp_path, host="peer.facebook.com", system="Linux")
    assert result.returncode == 1
    assert "fenced by transition transition-4" in result.stderr


def test_transition_cache_does_not_fence_the_mac(tmp_path: Path) -> None:
    # The Darwin branch returns the loopback URL before consulting the routing
    # cache, deliberately: the Mac reaches whichever hub is live through
    # mac_proxy's existing ET forwards, so a handoff in progress is not a
    # reason to fail its clients. Pinned here so the divergence from
    # test_transition_cache_fails_closed stays intentional.
    run_script(tmp_path, host="peer.facebook.com")
    _fence_cache(tmp_path)
    result = run_script_with_existing(tmp_path, host="peer.facebook.com", system="Darwin")
    assert result.returncode == 0
    assert result.stdout.strip() == "http://127.0.0.1:6767"


def run_script_with_existing(
    tmp_path: Path, *, host: str, system: str
) -> subprocess.CompletedProcess[str]:
    # The script branches on `uname -s`, so the platform is stubbed rather than
    # inherited. Otherwise each behaviour is only reachable on the OS that
    # exhibits it, and the other silently goes untested -- which is how the
    # Darwin path came to have no coverage at all.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake_uname = bin_dir / "uname"
    fake_uname.write_text(
        '#!/bin/sh\nif [ "$1" = "-s" ]; then echo '
        + system
        + '; else exec /usr/bin/uname "$@"; fi\n',
        encoding="utf-8",
    )
    fake_uname.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "OMNIGENT_TOPOLOGY_FILE": str(tmp_path / "topology.env"),
            "OMNIGENT_HA_ROUTING_CACHE": str(tmp_path / "active-hub.json"),
            "OMNIGENT_LOCAL_FQDN": host,
            "PATH": f"{bin_dir}:{env['PATH']}",
        }
    )
    return subprocess.run([str(SCRIPT)], env=env, text=True, capture_output=True)
