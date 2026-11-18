"""Run with python3 -m unittest discover -s tests -p test_resume_after_rate_limit.py.

Covers claude_config/hooks/resume-after-rate-limit.py, the StopFailure hook that
wakes a session an API rate limit stopped. Drives the hook's entry point with
Claude Code's hook input and a transcript file shaped like the ones Claude Code
writes; tests/test-resume-after-rate-limit-e2e.py runs it in a real session.
"""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK_PATH = ROOT / "claude_config/hooks/resume-after-rate-limit.py"

loader = importlib.machinery.SourceFileLoader("resume_after_rate_limit", str(HOOK_PATH))
hook = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(hook)

_uuid = 0


def entry(kind: str, **fields: object) -> dict[str, object]:
    global _uuid
    _uuid += 1
    return {"type": kind, "uuid": f"u{_uuid}", **fields}


def user(text: str) -> dict[str, object]:
    return entry("user", message={"role": "user", "content": text})


def assistant(text: str) -> dict[str, object]:
    return entry("assistant", message={"role": "assistant", "content": [{"type": "text", "text": text}]})


def rate_limited() -> dict[str, object]:
    return entry(
        "assistant",
        isApiErrorMessage=True,
        apiErrorStatus=429,
        error="rate_limit",
        message={"role": "assistant", "content": [{"type": "text", "text": "API Error: Request rejected (429)"}]},
    )


def auto_resume() -> dict[str, object]:
    return entry(
        "user",
        isMeta=True,
        message={"role": "user", "content": f"<system-reminder>\n{hook.WAKE_MESSAGE}\n</system-reminder>"},
    )


def turn_metadata() -> list[dict[str, object]]:
    return [
        entry("system", subtype="turn_duration"),
        {"type": "last-prompt"},
        {"type": "ai-title"},
        {"type": "file-history-snapshot"},
    ]


class Result:
    def __init__(self, code: int, slept: list[float], stderr: str) -> None:
        self.code = code
        self.slept = slept
        self.stderr = stderr


class ResumeAfterRateLimitTest(unittest.TestCase):
    def run_hook(
        self,
        transcript: list[dict[str, object]],
        during_wait: list[dict[str, object]] | None = None,
        error: str = "rate_limit",
    ) -> Result:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            path.write_text("".join(json.dumps(e) + "\n" for e in transcript))
            slept: list[float] = []

            def sleep(seconds: float) -> None:
                slept.append(seconds)
                with path.open("a") as out:
                    out.writelines(json.dumps(e) + "\n" for e in during_wait or [])

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = hook.main(
                    {"hook_event_name": "StopFailure", "error": error, "transcript_path": str(path)},
                    sleep=sleep,
                )
            return Result(code, slept, stderr.getvalue())

    def assert_wakes_after(self, result: Result, seconds: float) -> None:
        self.assertEqual(result.code, 2)
        self.assertEqual(result.slept, [seconds])
        self.assertIn("[auto-resume]", result.stderr)

    def assert_stays_idle(self, result: Result) -> None:
        self.assertEqual(result.code, 0)
        self.assertEqual(result.stderr, "")

    def test_wakes_after_a_rate_limit(self) -> None:
        result = self.run_hook([user("do the thing"), assistant("on it"), rate_limited(), *turn_metadata()])
        self.assert_wakes_after(result, 60)

    def test_waits_longer_for_each_consecutive_limit(self) -> None:
        second = [user("go"), rate_limited(), auto_resume(), rate_limited()]
        self.assert_wakes_after(self.run_hook(second), 120)
        third = [user("go"), rate_limited(), auto_resume(), rate_limited(), auto_resume(), rate_limited()]
        self.assert_wakes_after(self.run_hook(third), 240)

    def test_leaves_a_fourth_consecutive_limit_to_the_user(self) -> None:
        transcript = [user("go")]
        for _ in range(3):
            transcript += [rate_limited(), *turn_metadata(), auto_resume()]
        result = self.run_hook([*transcript, rate_limited()])
        self.assert_stays_idle(result)
        self.assertEqual(result.slept, [])

    def test_stays_quiet_when_the_user_types_during_the_wait(self) -> None:
        result = self.run_hook([user("go"), rate_limited()], during_wait=[user("continue")])
        self.assert_stays_idle(result)

    def test_stays_quiet_when_a_background_task_wakes_the_session(self) -> None:
        result = self.run_hook(
            [user("go"), rate_limited()],
            during_wait=[user("<task-notification>done</task-notification>"), assistant("the task finished")],
        )
        self.assert_stays_idle(result)

    def test_metadata_written_during_the_wait_is_not_moving_on(self) -> None:
        result = self.run_hook([user("go"), rate_limited()], during_wait=turn_metadata())
        self.assert_wakes_after(result, 60)

    def test_a_typed_message_restarts_the_count(self) -> None:
        transcript = [user("go"), rate_limited(), auto_resume(), rate_limited(), user("continue"), rate_limited()]
        self.assert_wakes_after(self.run_hook(transcript), 60)

    def test_a_successful_turn_restarts_the_count(self) -> None:
        transcript = [user("go"), rate_limited(), auto_resume(), rate_limited(), auto_resume(),
                      assistant("working"), rate_limited()]
        self.assert_wakes_after(self.run_hook(transcript), 60)

    def test_ignores_a_session_whose_last_turn_was_not_rate_limited(self) -> None:
        result = self.run_hook([user("go"), rate_limited(), user("continue"), assistant("done")])
        self.assert_stays_idle(result)
        self.assertEqual(result.slept, [])

    def test_ignores_other_api_errors(self) -> None:
        result = self.run_hook([user("go"), rate_limited()], error="server_error")
        self.assert_stays_idle(result)
        self.assertEqual(result.slept, [])

    def test_tolerates_a_line_still_being_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            path.write_text(json.dumps(user("go")) + "\n" + json.dumps(rate_limited()) + "\n" + '{"type": "assis')
            with contextlib.redirect_stderr(io.StringIO()):
                code = hook.main({"error": "rate_limit", "transcript_path": str(path)}, sleep=lambda _: None)
            self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
