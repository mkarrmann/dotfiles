from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parents[3] / "bin/omnigent-client-proxy"


def test_client_proxy_builds_ssh_loopback_forward(tmp_path: Path) -> None:
    resolver = write_resolver(tmp_path, active="active.example.com", port="6767")
    ssh = tmp_path / "ssh"
    ssh.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n", encoding="utf-8")
    ssh.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "OMNIGENT_LOCAL_FQDN": "peer.example.com",
            "OMNIGENT_SERVER_URL_BIN": str(resolver),
            "OMNIGENT_SSH_BIN": str(ssh),
        }
    )

    result = subprocess.run([str(SCRIPT)], env=env, text=True, capture_output=True)

    assert result.returncode == 0
    args = result.stdout.splitlines()
    assert "ClearAllForwardings=yes" not in args
    assert "ExitOnForwardFailure=yes" in args
    assert "ServerAliveInterval=15" in args
    assert "127.0.0.1:6767:127.0.0.1:6767" in args
    assert args[-1] == "active.example.com"


def test_client_proxy_refuses_to_forward_active_hub_to_itself(tmp_path: Path) -> None:
    resolver = write_resolver(tmp_path, active="active.example.com", port="6767")
    env = os.environ.copy()
    env.update(
        {
            "OMNIGENT_LOCAL_FQDN": "active.example.com",
            "OMNIGENT_SERVER_URL_BIN": str(resolver),
            "OMNIGENT_SSH_BIN": "/does/not/matter",
        }
    )

    result = subprocess.run([str(SCRIPT)], env=env, text=True, capture_output=True)

    assert result.returncode == 1
    assert "refusing to proxy the active hub to itself" in result.stderr


def write_resolver(tmp_path: Path, *, active: str, port: str) -> Path:
    resolver = tmp_path / "omnigent-server-url"
    resolver.write_text(
        "#!/bin/sh\n"
        f'test "$1" = --hub && echo {active} && exit 0\n'
        f'test "$1" = --port && echo {port} && exit 0\n'
        "exit 2\n",
        encoding="utf-8",
    )
    resolver.chmod(0o755)
    return resolver
