#!/usr/bin/env python3
"""
StopFailure hook that resumes a turn an API rate limit ended.

Registered by sync.sh with matcher ``rate_limit`` and ``asyncRewake``. Claude
Code ignores a StopFailure hook's output, but an ``asyncRewake`` hook runs in
the background and, when it exits 2, wakes Claude with its stderr as a system
reminder -- even if the session is idle. So this waits out the limit, then
wakes the session with an instruction to continue.

It stays quiet instead when the transcript shows the session moved on during
the wait (the user typed, a background task woke it), or when the limit has
already absorbed every automatic resume since the last real turn.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

Entry = Mapping[str, object]

MARKER = "[auto-resume]"
WAKE_MESSAGE = (
    f"{MARKER} Your previous turn ended on a transient API rate limit (HTTP 429)."
    " Continue where you left off."
)
# Wait before the first, second and third consecutive resume. A fourth
# consecutive limit is left for the user.
BACKOFF_SECONDS = (60, 120, 240)

WAKE = 2
STAY_IDLE = 0


def read_transcript(path: Path) -> list[Entry]:
    entries: list[Entry] = []
    with path.open(encoding="utf-8") as transcript:
        for line in transcript:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                # A line Claude Code is still writing.
                continue
            if isinstance(entry, dict):
                entries.append(entry)
    return entries


def conversation(entries: Sequence[Entry]) -> list[Entry]:
    """The user and assistant turns, without the metadata Claude Code interleaves."""
    return [entry for entry in entries if entry.get("type") in ("user", "assistant")]


def is_rate_limit_error(entry: Entry) -> bool:
    return (
        entry.get("type") == "assistant"
        and entry.get("isApiErrorMessage") is True
        and (entry.get("error") == "rate_limit" or entry.get("apiErrorStatus") == 429)
    )


def is_auto_resume(entry: Entry) -> bool:
    return entry.get("type") == "user" and MARKER in json.dumps(entry.get("message"))


def consecutive_rate_limits(turns: Sequence[Entry]) -> int:
    """Rate-limit errors at the end of the conversation, bridged only by this hook's own wakes."""
    count = 0
    for entry in reversed(turns):
        if is_rate_limit_error(entry):
            count += 1
        elif not is_auto_resume(entry):
            break
    return count


def main(
    hook_input: Mapping[str, object],
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    transcript_path = hook_input.get("transcript_path")
    if hook_input.get("error") != "rate_limit" or not isinstance(transcript_path, str):
        return STAY_IDLE
    path = Path(transcript_path)

    turns = conversation(read_transcript(path))
    if not turns or not is_rate_limit_error(turns[-1]):
        return STAY_IDLE
    attempt = consecutive_rate_limits(turns)
    if attempt > len(BACKOFF_SECONDS):
        return STAY_IDLE
    failed_turn = turns[-1].get("uuid")

    sleep(BACKOFF_SECONDS[attempt - 1])

    turns = conversation(read_transcript(path))
    if not turns or turns[-1].get("uuid") != failed_turn:
        return STAY_IDLE
    print(WAKE_MESSAGE, file=sys.stderr)
    return WAKE


if __name__ == "__main__":
    sys.exit(main(json.load(sys.stdin)))
