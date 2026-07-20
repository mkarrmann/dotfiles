"""Bounded subprocess transport for normalized review-source snapshots."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from enum import StrEnum

from pydantic import ValidationError

from .source_models import DiffSnapshot

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_OUTPUT_LIMIT_BYTES = 1024 * 1024
_STDERR_LIMIT_BYTES = 16 * 1024
_META_OAUTH_WARNING_PREFIX = b"Warning: OAuth token is expired or invalid."


class SourceCommandErrorCategory(StrEnum):
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    OUTPUT_LIMIT = "output_limit"
    EXIT = "exit"
    MALFORMED = "malformed"


class SourceCommandError(RuntimeError):
    """A redacted source failure safe for watcher health and logs."""

    def __init__(self, category: SourceCommandErrorCategory, summary: str) -> None:
        super().__init__(summary)
        self.category = category


async def _read_limited(
    stream: asyncio.StreamReader,
    limit: int,
) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while chunk := await stream.read(min(64 * 1024, limit - size + 1)):
        size += len(chunk)
        if size > limit:
            raise SourceCommandError(
                SourceCommandErrorCategory.OUTPUT_LIMIT,
                "review source exceeded its output limit",
            )
        chunks.append(chunk)
    return b"".join(chunks)


async def run_snapshot_command(
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    output_limit_bytes: int = DEFAULT_OUTPUT_LIMIT_BYTES,
) -> DiffSnapshot:
    """Run an argv-only source command and validate its JSON snapshot."""
    payload = await run_json_command(
        argv,
        env=env,
        timeout_seconds=timeout_seconds,
        output_limit_bytes=output_limit_bytes,
    )
    try:
        return DiffSnapshot.model_validate(payload)
    except ValidationError as exc:
        raise SourceCommandError(
            SourceCommandErrorCategory.MALFORMED,
            "review source returned an invalid snapshot",
        ) from exc


async def run_json_command(
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    output_limit_bytes: int = DEFAULT_OUTPUT_LIMIT_BYTES,
) -> object:
    """Run an argv-only command and return bounded decoded JSON."""
    if not argv or any(not isinstance(arg, str) or not arg for arg in argv):
        raise ValueError("review source argv must contain non-empty strings")
    if timeout_seconds <= 0 or output_limit_bytes <= 0:
        raise ValueError("review source limits must be positive")

    process = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=dict(env),
    )
    assert process.stdout is not None
    assert process.stderr is not None

    stdout_task = asyncio.create_task(_read_limited(process.stdout, output_limit_bytes))
    stderr_task = asyncio.create_task(_read_limited(process.stderr, _STDERR_LIMIT_BYTES))
    try:
        async with asyncio.timeout(timeout_seconds):
            stdout, _stderr = await asyncio.gather(stdout_task, stderr_task)
            return_code = await process.wait()
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        stdout_task.cancel()
        stderr_task.cancel()
        raise SourceCommandError(
            SourceCommandErrorCategory.TIMEOUT,
            "review source timed out",
        ) from exc
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        stdout_task.cancel()
        stderr_task.cancel()
        raise

    if return_code != 0:
        lowered = _stderr.lower()
        if any(
            marker in lowered
            for marker in (
                b"oauth",
                b"authentication",
                b"not authenticated",
                b"access token",
                b"jf auth",
            )
        ):
            category = SourceCommandErrorCategory.AUTH
            summary = "review source authentication failed"
        elif any(marker in lowered for marker in (b"rate limit", b"too many requests", b"throttl")):
            category = SourceCommandErrorCategory.RATE_LIMIT
            summary = "review source was rate limited"
        else:
            category = SourceCommandErrorCategory.EXIT
            summary = f"review source exited unsuccessfully (code {return_code})"
        raise SourceCommandError(
            category,
            summary,
        )
    try:
        return _decode_json_output(stdout)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SourceCommandError(
            SourceCommandErrorCategory.MALFORMED,
            "review source returned invalid JSON",
        ) from exc


def _decode_json_output(stdout: bytes) -> object:
    """Decode JSON, tolerating only Meta CLI's known stdout auth warning."""
    try:
        return json.loads(stdout)
    except (json.JSONDecodeError, UnicodeDecodeError) as original:
        lines = stdout.splitlines()
        if len(lines) < 2 or not lines[0].startswith(_META_OAUTH_WARNING_PREFIX):
            raise
        try:
            return json.loads(b"\n".join(lines[1:]))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise original from None
