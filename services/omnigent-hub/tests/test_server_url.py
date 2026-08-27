from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[3] / "bin/omnigent-server-url"

# fbcode's /usr/local/bin/python3 prints this at every startup. read_cache
# shells out to `python3`, so this is the exact noise that once became the hub
# FQDN for every Linux client.
JEMALLOC_NOISE = "<jemalloc>: Invalid conf pair: experimental_infallible_new:true"


def _install_noisy_python(tmp_path: Path, *, stream: str) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    shim = bin_dir / "python3"
    redirect = " 1>&2" if stream == "stderr" else ""
    shim.write_text(
        f"#!/bin/sh\necho '{JEMALLOC_NOISE}'{redirect}\n"
        f'exec {shlex.quote(sys.executable)} "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return bin_dir


def run_script(
    tmp_path: Path, *args: str, host: str, noise: str | None = None
) -> subprocess.CompletedProcess[str]:
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
    if noise is not None:
        bin_dir = _install_noisy_python(tmp_path, stream=noise)
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
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


def test_stderr_noise_does_not_leak_into_the_hub_name(tmp_path: Path) -> None:
    # Regression: read_cache used to be captured with `2>&1`, and the `read`
    # that parses it consumes only the first line. A single line of interpreter
    # noise on stderr therefore replaced the hub FQDN, and omnigent-client-proxy
    # spent weeks dialling a host named after a jemalloc warning.
    for flag, expected in (
        ("--hub", "standby.example.com"),
        ("--epoch", "3"),
        ("--activation-id", "activation-3"),
    ):
        result = run_script(tmp_path, flag, host="peer.facebook.com", noise="stderr")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == expected
        assert JEMALLOC_NOISE not in result.stdout


def test_stderr_noise_does_not_break_is_hub(tmp_path: Path) -> None:
    # omnigent-agents-ensure, omnigent-dvsc-ensure, omnigent-google-chat-ensure
    # and omnigent-retire-legacy-standby all branch on this exit status, and the
    # last of them stops a running server when it reads false. Pin both answers.
    on_hub = run_script(tmp_path, "--is-hub", host="standby.example.com", noise="stderr")
    assert on_hub.returncode == 0, on_hub.stderr
    off_hub = run_script(tmp_path, "--is-hub", host="primary.example.com", noise="stderr")
    assert off_hub.returncode == 1


def test_stdout_noise_fails_closed(tmp_path: Path) -> None:
    # Separating the streams cannot catch noise written to stdout, so the hub
    # name is validated against the configured candidates as well. Failing
    # closed is deliberate: the client proxy restarts forever and recovers,
    # while a proxy pointed at a bogus host never does.
    result = run_script(tmp_path, "--hub", host="peer.facebook.com", noise="stdout")
    assert result.returncode == 1
    assert "unexpected hub" in result.stderr
    assert JEMALLOC_NOISE not in result.stdout


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
