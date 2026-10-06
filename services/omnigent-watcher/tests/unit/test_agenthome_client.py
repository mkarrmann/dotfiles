from __future__ import annotations

import os
import stat
from collections.abc import Sequence
from pathlib import Path

import pytest

from omnigent_watcher.agenthome_client import (
    AgentHomeClient,
    AgentHomeDeliveryService,
    AgentHomeError,
    CliResult,
    agent_home_id,
    run_meta,
)
from omnigent_watcher.domain import EventDeliveryStatus

AGENT = "ah_rrO5GPFnu14SVsq9ExhBFJ"
SESSION = f"agenthome:{AGENT}"
HOST = "devvm1.example.facebook.com"
NOT_FOUND = CliResult(1, {"status": "error", "error_code": "not_found"})


class FakeMeta:
    """Answers ``meta ah.session`` calls from canned results, recording each."""

    def __init__(
        self,
        *,
        listing: CliResult | None = None,
        inspect: CliResult | Sequence[CliResult] | None = None,
        message: CliResult | None = None,
    ) -> None:
        self.listing = listing or CliResult(0, [])
        self._inspect = (
            [inspect]
            if isinstance(inspect, CliResult)
            else list(inspect or [CliResult(0, {"rows": []})])
        )
        self.message = message or CliResult(0, {"status": "queued"})
        self.calls: list[tuple[tuple[str, ...], str | None]] = []

    async def __call__(self, argv: Sequence[str], stdin: str | None) -> CliResult:
        call = tuple(argv)
        self.calls.append((call, stdin))
        action = call[1]
        if action == "list":
            return self.listing
        if action == "inspect":
            return self._inspect.pop(0) if len(self._inspect) > 1 else self._inspect[0]
        if action == "message":
            return self.message
        raise AssertionError(f"unexpected meta call {call}")

    def actions(self) -> list[str]:
        return [call[1] for call, _ in self.calls]


def _row(**overrides: object) -> dict[str, object]:
    return {"agent_id": AGENT, "host": HOST, "state": "idle", "is_running": False, **overrides}


def _transcript(*user_texts: str) -> CliResult:
    rows: list[dict[str, object]] = [{"turn": 0, "role": "assistant", "text": "[Watcher b1] no"}]
    for turn, text in enumerate(user_texts, start=1):
        rows.append({"turn": turn, "role": "user", "text": text})
    return CliResult(0, {"session_id": AGENT, "rows": rows})


def test_only_prefixed_ids_address_agent_home() -> None:
    assert agent_home_id(SESSION) == AGENT
    assert agent_home_id("agenthome:") is None
    # Agent Home's own id shapes are not enough: a bare id is an Omnigent id.
    assert agent_home_id(AGENT) is None
    assert agent_home_id("conv_85209e85779b406aa9e5b78b2a0a43c2") is None


@pytest.mark.parametrize(
    ("row", "can_accept"),
    [
        (_row(), True),
        (_row(state="running", is_running=True), False),
        (_row(state="waiting_on_approval"), False),
        (_row(state="waiting_on_user"), False),
    ],
)
async def test_a_listed_session_is_live_and_only_idle_takes_input(
    row: dict[str, object], can_accept: bool
) -> None:
    meta = FakeMeta(listing=CliResult(0, [_row(agent_id="ah_other"), row]))
    snapshot = await AgentHomeClient(runner=meta).get(SESSION)
    assert snapshot.exists and snapshot.reachable and not snapshot.terminal
    assert snapshot.can_accept_input is can_accept
    assert meta.actions() == ["list"]


async def test_a_session_off_every_host_is_unreachable_never_gone() -> None:
    """Agent Home's not_found also covers a session whose host failed to
    answer, so reporting it as gone would retire live watches at once."""
    meta = FakeMeta(inspect=NOT_FOUND)
    snapshot = await AgentHomeClient(runner=meta).get(SESSION)
    assert not snapshot.terminal
    assert not snapshot.reachable
    assert meta.actions() == ["list"]


async def test_one_listing_serves_a_cycle_of_lookups() -> None:
    meta = FakeMeta(listing=CliResult(0, [_row(), _row(agent_id="ah_other")]))
    client = AgentHomeClient(runner=meta)
    await client.get(SESSION)
    await client.get("agenthome:ah_other")
    assert meta.actions() == ["list"]


@pytest.mark.parametrize("listing", [CliResult(-1), CliResult(1, {"error_code": "x"})])
async def test_a_cli_failure_is_unreachable_rather_than_raising(listing: CliResult) -> None:
    """Raising would abort the worker's whole cycle, Omnigent sessions included."""
    meta = FakeMeta(listing=listing)
    snapshot = await AgentHomeClient(runner=meta).get(SESSION)
    assert not snapshot.terminal
    assert not snapshot.reachable
    assert not snapshot.can_accept_input


async def test_a_listed_host_is_reused_for_the_send() -> None:
    meta = FakeMeta(listing=CliResult(0, [_row()]))
    client = AgentHomeClient(runner=meta)
    await client.get(SESSION)
    await client.send(SESSION, "[Watcher b1] wake")
    argv, stdin = meta.calls[-1]
    assert argv[:2] == ("ah.session", "message")
    assert argv[argv.index("--to") + 1] == AGENT
    assert argv[argv.index("--host") + 1] == HOST
    assert "--text=-" in argv
    assert stdin == "[Watcher b1] wake"


async def test_a_send_without_a_known_host_lets_meta_discover_it() -> None:
    meta = FakeMeta()
    await AgentHomeClient(runner=meta).send(SESSION, "wake")
    assert "--host" not in meta.calls[-1][0]


async def test_the_receipt_is_the_marker_in_a_user_turn_on_the_owning_host() -> None:
    meta = FakeMeta(
        listing=CliResult(0, [_row()]), inspect=_transcript("hi", "[Watcher b1] CI failed")
    )
    client = AgentHomeClient(runner=meta)
    receipt = await client.delivery_receipt(SESSION, "b1")
    assert receipt is not None
    assert receipt.status is EventDeliveryStatus.ALREADY_ACCEPTED
    # The assistant row quoting the marker does not count.
    assert await client.delivery_receipt(SESSION, "b2") is None
    inspect = next(argv for argv, _ in meta.calls if argv[1] == "inspect")
    assert inspect[inspect.index("--host") + 1] == HOST


@pytest.mark.parametrize(
    "meta",
    [
        # Off every host: only the stored transcript could answer, and it lags.
        FakeMeta(inspect=_transcript()),
        FakeMeta(listing=CliResult(0, [_row()]), inspect=NOT_FOUND),
        FakeMeta(listing=CliResult(0, [_row()]), inspect=CliResult(-1)),
    ],
)
async def test_a_receipt_that_cannot_be_read_live_stays_unknown(meta: FakeMeta) -> None:
    with pytest.raises(AgentHomeError):
        await AgentHomeClient(runner=meta).delivery_receipt(SESSION, "b1")


def _delivery(meta: FakeMeta, mode: str = "enabled") -> AgentHomeDeliveryService:
    return AgentHomeDeliveryService(
        AgentHomeClient(runner=meta), mode=mode, allowlist=frozenset(), verify_delays=(0, 0)
    )


async def test_an_idle_session_is_sent_the_wake() -> None:
    meta = FakeMeta(listing=CliResult(0, [_row()]))
    result = await _delivery(meta).deliver_message(SESSION, "b1", "[Watcher b1] wake")
    assert result.status is EventDeliveryStatus.ACCEPTED
    assert meta.actions() == ["list", "inspect", "message"]


async def test_an_accepted_batch_is_not_sent_twice() -> None:
    meta = FakeMeta(listing=CliResult(0, [_row()]), inspect=_transcript("[Watcher b1] wake"))
    result = await _delivery(meta).deliver_message(SESSION, "b1", "[Watcher b1] wake")
    assert result.status is EventDeliveryStatus.ALREADY_ACCEPTED
    assert "message" not in meta.actions()


async def test_a_busy_session_is_not_sent() -> None:
    meta = FakeMeta(listing=CliResult(0, [_row(state="running", is_running=True)]))
    result = await _delivery(meta).deliver_message(SESSION, "b1", "wake")
    assert result.status is EventDeliveryStatus.NOT_SENT
    assert "message" not in meta.actions()


async def test_a_session_off_every_host_is_not_sent() -> None:
    meta = FakeMeta()
    result = await _delivery(meta).deliver_message(SESSION, "b1", "wake")
    assert result.status is EventDeliveryStatus.NOT_SENT
    assert "message" not in meta.actions()


async def test_a_failed_send_that_landed_is_recognised() -> None:
    meta = FakeMeta(
        listing=CliResult(0, [_row()]),
        inspect=[CliResult(0, {"rows": []}), _transcript("[Watcher b1] wake")],
        message=CliResult(-1),
    )
    result = await _delivery(meta).deliver_message(SESSION, "b1", "[Watcher b1] wake")
    assert result.status is EventDeliveryStatus.ALREADY_ACCEPTED


async def test_a_failed_send_with_no_receipt_is_deferred() -> None:
    meta = FakeMeta(listing=CliResult(0, [_row()]), message=CliResult(1, {"error_code": "x"}))
    result = await _delivery(meta).deliver_message(SESSION, "b1", "wake")
    assert result.status is EventDeliveryStatus.DEFERRED


async def test_log_only_never_calls_meta() -> None:
    meta = FakeMeta()
    service = _delivery(meta, mode="log_only")
    assert (await service.deliver_message(SESSION, "b1", "wake")).status is (
        EventDeliveryStatus.ACCEPTED
    )
    assert await service.delivery_receipt(SESSION, "b1") is None
    assert meta.calls == []


def _fake_meta_executable(directory: Path, script: str) -> dict[str, str]:
    executable = directory / "meta"
    executable.write_text("#!/bin/sh\n" + script)
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    return {"PATH": f"{directory}:{os.environ['PATH']}"}


async def test_run_meta_keeps_the_error_payload_and_feeds_stdin(tmp_path: Path) -> None:
    env = _fake_meta_executable(
        tmp_path,
        'cat > "$(dirname "$0")/stdin"\n'
        "echo 'Warning: OAuth token is expired or invalid.'\n"
        'echo \'{"status":"error","error_code":"not_found"}\'\n'
        "exit 1\n",
    )
    result = await run_meta(["ah.session", "message"], "the wake", env=env)
    assert result.returncode == 1
    assert result.error_code == "not_found"
    assert (tmp_path / "stdin").read_text() == "the wake"


async def test_run_meta_reports_a_hung_cli_as_a_failure(tmp_path: Path) -> None:
    env = _fake_meta_executable(tmp_path, "sleep 30\n")
    result = await run_meta(["ah.session", "list"], env=env, timeout_seconds=0.2)
    assert result == CliResult(-1)


async def test_run_meta_reaps_the_cli_when_the_worker_is_cancelled(tmp_path: Path) -> None:
    import asyncio

    env = _fake_meta_executable(tmp_path, 'echo $$ > "$(dirname "$0")/pid"\nsleep 30\n')
    task = asyncio.create_task(run_meta(["ah.session", "list"], env=env))
    pid_file = tmp_path / "pid"
    for _ in range(100):
        if pid_file.exists() and pid_file.read_text().strip():
            break
        await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    pid = int(pid_file.read_text())
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.02)
    else:
        pytest.fail("meta was left running after cancellation")
