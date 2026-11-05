"""T3 Code's text generation: thread titles, commit messages, branch names, PR text.

T3 Code generates text through a throwaway OpenCode session titled
`T3 Code <operation>` that runs one prompt with every tool on `ask`. Opening an
Omnigent session for that would launch a whole native harness per title, so
such a session runs the prompt through a one-shot `claude -p`, as Omnigent's own
title generator does, and reports it as one execution.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
from typing import Final

from .wire import Json, JsonObject

LOG = logging.getLogger(__name__)

TITLE_PREFIX: Final = "T3 Code "
TIMEOUT_S: Final = 170.0


def is_text_generation(title: object, permissions: object) -> bool:
    """T3 Code's text generation session: its title and its one ask-everything rule (textGeneration.ts)."""
    return (
        isinstance(title, str)
        and title.startswith(TITLE_PREFIX)
        and permissions == [{"action": "*", "resource": "*", "effect": "ask"}]
    )


def command() -> list[str]:
    override = os.environ.get("OMNIGENT_OPENCODE_TEXTGEN_COMMAND")
    if override:
        return shlex.split(override)
    argv = ["claude", "-p", "--tools", "", "--output-format", "text", "--no-session-persistence", "--effort", "low"]
    model = os.environ.get("OMNIGENT_OPENCODE_TEXTGEN_MODEL")
    if model:
        argv += ["--model", model]
    return argv


async def generate(prompt: str, cwd: str) -> str:
    process = await asyncio.create_subprocess_exec(
        *command(),
        cwd=cwd if os.path.isdir(cwd) else None,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(TIMEOUT_S):
            stdout, stderr = await process.communicate(prompt.encode())
    except (TimeoutError, asyncio.CancelledError):
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if process.returncode != 0:
        raise RuntimeError(f"text generation exited {process.returncode}: {stderr.decode(errors='replace')[-500:]}")
    return stdout.decode(errors="replace").strip()


def session_info(session_id: str, title: str, directory: str, model: Json, created: int) -> JsonObject:
    return {
        "id": session_id,
        "projectID": "global",
        "model": model,
        "cost": 0,
        "tokens": {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}},
        "time": {"created": created, "updated": created},
        "title": title,
        "location": {"directory": directory},
    }
