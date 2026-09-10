"""Deny a long foreground sleep in any Omnigent-hosted session.

Blocking a turn on ``sleep`` spends a whole model turn to produce nothing. In a
measured Presto canary-watch session, eight such turns cost roughly 1.4M tokens
and 75 minutes of dead wall-clock and returned only a timestamp. Every harness
can detach the wait instead, so the same wait costs nothing.

Enforced here rather than as a per-harness ``PreToolUse`` hook because one
policy covers every harness Omnigent fronts: ``native_policy_hook`` funnels each
harness's native tool call through the policy engine with its tool name intact.
Native hooks would cover only the harnesses wired by hand, and would miss Codex
entirely — Omnigent runs it in a private per-session ``CODEX_HOME`` that
inherits only ``config.toml`` (``_CODEX_HOME_COPY_FILES`` in
``omnigent.inner.codex_executor``) and into which Omnigent writes its own
``hooks.json``, so ``~/.codex/hooks.json`` is never read.

The trade-off is deliberate: a session started outside Omnigent is not gated.
The guidance still reaches it through the ``waiting-without-polling`` skill.

What counts as a blocking wait lives in :mod:`foreground_wait`.

Contract (verified against omnigent 0.5.1):
- Declared as a *factory* in ``POLICY_REGISTRY``; the server calls it once at
  build time with ``arguments`` from server config, and the returned closure is
  the per-event evaluator.
- The evaluator returns ``None`` to abstain (ALLOW) or ``{"result": ...,
  "reason": ...}``.
- HACK: policy exceptions fail *closed* to DENY and would block the tool, so the
  evaluator catches everything and abstains. A convenience gate must never be
  able to break a session.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

# Sibling module; PYTHONPATH points at this directory (see
# systemd/omnigent-server.service).
import foreground_wait

_ACTIONS = {"deny": "DENY", "ask": "ASK"}


def block_foreground_wait(
    *,
    action: str = "deny",
) -> Callable[[dict[str, Any]], dict[str, Any] | None]:
    """Build a policy callable that gates a blocking foreground sleep.

    :param action: What a gated command yields — ``"deny"`` (block it) or
        ``"ask"`` (park for human approval). Defaults to ``"deny"``.
    :returns: A one-argument policy callable returning a policy response on a
        gated command, or ``None`` to abstain.
    :raises ValueError: If *action* is not ``"deny"`` / ``"ask"`` — loud at
        config load, rather than a gate that quietly never fires.
    """
    try:
        result_kind = _ACTIONS[action]
    except KeyError:
        raise ValueError(f"action must be one of {sorted(_ACTIONS)}, got {action!r}")

    def _policy(event: dict[str, Any]) -> dict[str, Any] | None:
        try:
            if event.get("type") != "tool_call":
                return None
            data = event.get("data")
            if not isinstance(data, dict):
                return None
            tool_name = data.get("name")
            seconds = foreground_wait.blocking_wait_seconds(
                tool_name, data.get("arguments")
            )
            if not seconds:
                return None
            return {
                "result": result_kind,
                "reason": foreground_wait.denial_reason(seconds, tool_name),
            }
        except Exception:
            return None

    return _policy


POLICY_REGISTRY: list[dict[str, Any]] = [
    {
        "handler": "no_foreground_wait.block_foreground_wait",
        "kind": "factory",
        "name": "Block a blocking foreground wait",
        "description": (
            "Deny a shell command that blocks the turn on a long sleep. "
            "Waiting in-turn spends a model turn to produce nothing; every "
            "harness can detach the wait instead."
        ),
        "params_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["deny", "ask"],
                    "description": "Whether a gated command is blocked or parked for approval.",
                },
            },
            "additionalProperties": False,
        },
    },
]
