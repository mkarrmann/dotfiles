from __future__ import annotations

import json
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path
from typing import Any

from omnigent_hub.config import HubConfig
from omnigent_hub.snapshot import restore_snapshot


class SmokeError(RuntimeError):
    pass


def restore_smoke(config: HubConfig, archive: Path) -> dict[str, Any]:
    config.local_state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="e2e-restore-", dir=config.local_state_dir))
    smoke_config = replace(
        config,
        data_dir=root / "state",
        local_state_dir=root / "admin",
        routing_cache=root / "routing.json",
    )
    smoke_config.local_state_dir.mkdir(mode=0o700, parents=True)
    port = _free_port()
    log_path = root / "server.log"
    process: subprocess.Popen[bytes] | None = None
    passed = False
    try:
        manifest = restore_snapshot(smoke_config, archive)
        with log_path.open("wb") as log:
            process = subprocess.Popen(
                [
                    str(config.omnigent_bin),
                    "server",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--database-uri",
                    f"sqlite:///{smoke_config.chat_db}",
                    "--artifact-location",
                    str(smoke_config.artifacts_dir),
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            health = _wait_json(f"http://127.0.0.1:{port}/health", process)
            sessions = _wait_json(f"http://127.0.0.1:{port}/v1/sessions?limit=1", process)
        data = sessions.get("data") if isinstance(sessions, dict) else None
        result = {
            "generation_id": manifest.get("generation_id"),
            "port": port,
            "health": health,
            "session_query_count": len(data) if isinstance(data, list) else None,
            "passed": True,
        }
        passed = True
        return result
    except Exception as exc:
        raise SmokeError(f"restore smoke failed; diagnostics preserved at {root}: {exc}") from exc
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if passed:
            shutil.rmtree(root, ignore_errors=True)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_json(url: str, process: subprocess.Popen[bytes]) -> dict[str, Any]:
    deadline = time.monotonic() + 60
    last_error = "not ready"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SmokeError(f"restored Omnigent server exited with {process.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                value = json.load(response)
            if isinstance(value, dict):
                return value
            last_error = "response was not a JSON object"
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            last_error = str(exc)
        time.sleep(0.5)
    raise SmokeError(f"timed out waiting for {url}: {last_error}")
