from __future__ import annotations

import json
import os
import platform
import shlex
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from omnigent_hub.config import HubConfig
from omnigent_hub.models import ActiveHubRecord

ProcessRunner = Callable[[list[str], float], subprocess.CompletedProcess[str]]


class RemoteError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RemoteResult:
    host: str
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


def run_process(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        check=False,
        text=True,
        capture_output=True,
        timeout=timeout,
    )


class RemoteClient:
    def __init__(
        self,
        config: HubConfig,
        *,
        runner: ProcessRunner = run_process,
        system: str | None = None,
    ) -> None:
        self._config = config
        self._runner = runner
        self._system = system or platform.system()
        self._remote_binary = "~/bin/omnigent-hub"
        self._delegated_cat: str | None = None

    def run(
        self,
        host: str,
        args: Sequence[str],
        *,
        timeout: float = 180,
        check: bool = True,
        use_delegated_cat: bool = True,
    ) -> RemoteResult:
        self._config.topology.validate_hub(host)
        command = " ".join([self._remote_binary, *(shlex.quote(arg) for arg in args)])
        if host == self._config.local_fqdn:
            argv = [
                str(self._config.dotfiles / "services/omnigent-hub/.venv/bin/omnigent-hub"),
                *args,
            ]
        elif self._system == "Darwin":
            if use_delegated_cat:
                command = self._with_delegated_cat(command)
            argv = ["x2ssh", "-et", host, "-c", f"zsh -lc {shlex.quote(command)}"]
        else:
            if use_delegated_cat:
                command = self._with_delegated_cat(command)
            argv = ["ssh", "-o", "BatchMode=yes", host, command]
        try:
            completed = self._runner(argv, timeout)
        except subprocess.TimeoutExpired as exc:
            raise RemoteError(f"command timed out on {host}: {' '.join(args)}") from exc
        result = RemoteResult(
            host=host,
            argv=("remote", host, *args),
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise RemoteError(f"command failed on {host}: {detail}")
        return result

    def _with_delegated_cat(self, command: str) -> str:
        if self._delegated_cat is None:
            self._delegated_cat = os.environ.get("OMNIGENT_HA_DELEGATED_CAT") or mint_delegated_cat(
                self._config.owner_fbid
            )
        return f"OMNIGENT_HA_DELEGATED_CAT={shlex.quote(self._delegated_cat)} {command}"

    def json(
        self,
        host: str,
        args: Sequence[str],
        *,
        timeout: float = 180,
        use_delegated_cat: bool = True,
    ) -> dict[str, Any]:
        result = self.run(host, args, timeout=timeout, use_delegated_cat=use_delegated_cat)
        return self._json_result(result)

    @staticmethod
    def _json_result(result: RemoteResult) -> dict[str, Any]:
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError:
            value = _embedded_json_object(result.stdout, result.stderr)
            if value is None:
                raise RemoteError(
                    f"command on {result.host} did not return JSON "
                    f"(stdout_bytes={len(result.stdout)}, stderr_bytes={len(result.stderr)})"
                ) from None
        if not isinstance(value, dict):
            raise RemoteError(f"command on {result.host} returned non-object JSON")
        return value

    def resolve(self) -> tuple[ActiveHubRecord, str, dict[str, str]]:
        responses: list[tuple[ActiveHubRecord, str]] = []
        errors: dict[str, str] = {}
        for host in self._config.topology.hubs:
            try:
                result = self.run(
                    host,
                    ("resolve", "--json"),
                    check=False,
                    use_delegated_cat=False,
                )
                if result.returncode == 0:
                    value = self._json_result(result)
                else:
                    value = self.json(host, ("resolve", "--json"))
                responses.append((ActiveHubRecord.from_dict(value, self._config.topology), host))
            except (RemoteError, ValueError) as exc:
                errors[host] = str(exc)
        if not responses:
            raise RemoteError(f"no hub candidate returned a valid record: {errors}")
        highest_epoch = max(record.epoch for record, _ in responses)
        highest = [(record, host) for record, host in responses if record.epoch == highest_epoch]
        canonical = highest[0][0]
        for record, host in highest[1:]:
            if record != canonical:
                raise RemoteError(
                    f"conflicting active-hub records at epoch {highest_epoch}: "
                    f"{highest[0][1]} and {host}"
                )
        return canonical, highest[0][1], errors


def _embedded_json_object(*streams: str) -> dict[str, Any] | None:
    """Return the largest complete JSON object embedded in terminal output."""
    decoder = json.JSONDecoder()
    candidates: list[tuple[int, dict[str, Any]]] = []
    for stream in streams:
        for start, char in enumerate(stream):
            if char != "{":
                continue
            try:
                value, end = decoder.raw_decode(stream, start)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                candidates.append((end - start, value))
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: candidate[0])[1]


def mint_delegated_cat(owner_fbid: str) -> str:
    result = subprocess.run(
        [
            "clicat",
            "create-delegated",
            "--signer_type",
            "FBID",
            "--signer_id",
            owner_fbid,
            "--token_timeout_seconds",
            "900",
            "--base64_url",
        ],
        check=False,
        text=True,
        capture_output=True,
        timeout=30,
    )
    token = result.stdout.strip()
    if result.returncode != 0 or not token:
        stderr = result.stderr.strip()
        detail = stderr.splitlines()[-1] if stderr else "unknown error"
        raise RemoteError(f"could not mint delegated Persistent Storage credential: {detail}")
    return token
