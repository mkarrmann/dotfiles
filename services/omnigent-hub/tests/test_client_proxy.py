from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parents[3] / "bin/omnigent-client-proxy"


def test_client_proxy_launches_module_with_backend_forward(tmp_path: Path) -> None:
    resolver = write_resolver(tmp_path, active="active.example.com", port="6767")
    fake_python = tmp_path / "python"
    fake_python.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n', encoding="utf-8")
    fake_python.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "OMNIGENT_LOCAL_FQDN": "peer.example.com",
            "OMNIGENT_SERVER_URL_BIN": str(resolver),
            "OMNIGENT_SSH_BIN": "/fake/ssh",
            "OMNIGENT_HUB_PYTHON": str(fake_python),
            "OMNIGENT_CLIENT_BACKEND_PORT": "6768",
        }
    )

    result = subprocess.run([str(SCRIPT)], env=env, text=True, capture_output=True)

    assert result.returncode == 0, result.stderr
    args = result.stdout.splitlines()
    assert args[:2] == ["-m", "omnigent_hub.client_proxy"]
    assert _value_after(args, "--hub") == "active.example.com"
    assert _value_after(args, "--server-port") == "6767"
    assert _value_after(args, "--backend-port") == "6768"
    assert _value_after(args, "--ssh-bin") == "/fake/ssh"


def test_client_proxy_refuses_to_forward_active_hub_to_itself(tmp_path: Path) -> None:
    resolver = write_resolver(tmp_path, active="active.example.com", port="6767")
    env = os.environ.copy()
    env.update(
        {
            "OMNIGENT_LOCAL_FQDN": "active.example.com",
            "OMNIGENT_SERVER_URL_BIN": str(resolver),
            "OMNIGENT_SSH_BIN": "/does/not/matter",
            "OMNIGENT_HUB_PYTHON": "/does/not/matter",
        }
    )

    result = subprocess.run([str(SCRIPT)], env=env, text=True, capture_output=True)

    assert result.returncode == 1
    assert "refusing to proxy the active hub to itself" in result.stderr


def _value_after(args: list[str], flag: str) -> str:
    return args[args.index(flag) + 1]


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
