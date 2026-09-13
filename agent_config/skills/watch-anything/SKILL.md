---
name: watch-anything
description: >-
  Use for persistent Omnigent command watches when native notifications are
  unavailable or lack the required lifetime, and when a generic [Watcher ...]
  notification arrives. Suitable for delayed job or sampled status follow-up.
  Read waiting-without-polling to choose a mechanism; use
  phabricator-diff-watch for Meta diff CI and review feedback.
---

# Watch anything

Use Omnigent's command watcher for a persistent wait that needs to wake the
same Omnigent session later. The worker runs cheap probes outside model turns;
subscription, notification handling, and any model invoked by the probe still
cost tokens. The worker and session server must remain available for delivery.

## When this custom tool helps

Prefer harness-native completion notifications, monitors, or event channels
when they meet the task's needs. See [[waiting-without-polling]]. A build you
just launched usually needs its native completion notification, not this tool.

This tool is useful when an external job may finish after the harness process
exits, or the current harness lacks a suitable notification mechanism. Its
requests and pending notifications are stored outside the harness and can
survive worker restarts, provided the target Omnigent session remains usable.
For example, watch a particular export job's status on the server and resume
its owning conversation when a new terminal status appears.

For Meta diff follow-up, use [[phabricator-diff-watch]]. That integration
understands CI failures, CI green, human comments, and AI-review findings
across a stack; a generic output comparison has none of that context.

This is a sampled state watcher, with minutes of latency. Recurrence works:
`A → B (delivered) → A` can notify again. Transitions between polls are missed,
and `A → B → A` before delivery may be coalesced away. Use a native monitor or
event source when every transition matters.
Unsubscribe after the task completes; generic watches do not infer completion.

## Subscribing

The tool is `mcp__watch__subscribe`; match on the suffix.

Commands run beside the Omnigent server that owns the session: locally on a
desktop, on the active hub for work sessions. They do not necessarily run on
the agent's machine. Use cheap read-only commands and absolute paths available
on that server, with credentials that work in the worker's environment.

If this tool is absent or its connection is dead, report the missing capability.
Use a suitable native alternative if it meets the task's needs; disclose a
shorter lifetime or narrower coverage. Do not silently replace it with repeated
model polling, or manually drive `omnigent-watch-mcp` over stdio or its HTTP API.
That can create a persistent watch the session cannot list or stop through its
tools, while hiding broken registration.

Check runtime, worker/API, and MCP registration before recommending a new
session. A repaired registration may need a fresh native session; a missing
runtime needs setup. Do not install or restart services without authorization.

```
subscribe(
  session_id = "<from sys_session_get_info>",
  subject  = "job:export-123",
  command  = ["cat", "/absolute/path/to/export-123-status"],
  extract  = r"status=(\w+)",             # match the actual output
  interval_seconds = 60,                  # optional, 30s..24h
)
```

- **`session_id`** is the Omnigent ID from `sys_session_get_info`, not the
  harness's own thread ID. A short-lived helper should target
  `parent_session_id` when the parent owns follow-up. A durable child can own
  its own watch, but do not assume its later externally triggered turns will
  automatically notify the parent; target the actual owner.
- **`subject`** must be namespaced `<prefix>:<identifier>` and identify the
  particular job or dependency. It shares one command specification across
  sessions on the server; conflicting commands, extraction, intervals, or
  timeouts are rejected. Use a new unique name to change the specification.
- **`command`** is argv, executed directly rather than through a shell. Put
  pipes or other shell syntax in a script if needed.
- **`extract`** selects the stable value, optionally using one capture group.
  Exclude timestamps and request IDs. No match or a nonparticipating optional
  capture is a probe error that preserves the previous value and backs off.
  A capture that actually matched an empty string is valid.
- **`interval_seconds`** is the nominal delay for successful command polls,
  with ±10% scheduling jitter; quiet subjects do not use the diff watcher's
  slower idle ladder.

`status(session_id)` shows recorded subscription state, command, failure count,
poll/retry schedule, result time, session delivery time, and any attempted and
queued notifications. It is not a worker health check; result times include
baseline and partial reads, and last poll-attempt timestamps and error categories
are not persisted.
`unsubscribe(session_id, subject=None)` stops one or all generic watches.

Check for an already-satisfied condition and handle it immediately. Subscribe
before triggering the work when possible; otherwise recheck after registering
to close the check/subscribe race. Subscription reads a silent baseline:
printing `MATCH` for an already-completed job does not itself cause a wake.

## Handling a wake

Continue other work or finish the subscribing turn. Delivery is deferred while
the session is busy, so holding its turn open can delay the notification.

On a wake, confirm the task still needs follow-up, read the current state of
the named subject, and apply its completion criteria. Retries check for earlier
acceptance first; absent a receipt, they refresh the source and replace obsolete
attempts. Newer observed state remains queued separately, so acknowledgment of
an older attempt does not handle it. Notifications can still race changes or
cancellation and can repeat. For substantial log or metric analysis,
delegate a bounded investigation to a subagent; a simple completion check
needs no extra agent. Stop the watch when done or when ownership ends.

Keep model judgment after the notification. Do not put a model invocation into
each probe just to ask whether anything changed: it restores model polling and
can introduce false changes from wording drift. When recurring judgment is
necessary, follow the scheduled-pass guidance in [[waiting-without-polling]].

## Operational limits

- **Probe failures are not change events.** Non-zero exit routes into backoff
  without waking the agent. Subscription catches a command that cannot run
  initially, but later failures can leave a watch silent.
- **Seven days without a session delivery retires a watch.** The timer starts
  at its baseline if nothing was delivered, and expiry can discard deferred
  feedback. Any watcher delivery to the session advances its live watches'
  timers; repeating an active subscription does not. Do not use this as an
  indefinite reminder.
- **Minutes of latency are expected.** The poll interval, five-minute batch
  window, ten-minute minimum delivery spacing, and busy/unreachable-session
  deferral all affect latency. This is unsuitable for urgent alerts or quick
  build feedback.
- **Outages can lose transitions.** A stopped worker does not sample; server
  or session unavailability delays delivery and can suspend polling. Stored
  requests and pending batches require the same usable session when resumed.
- **Duplicate suppression is best effort.** Retries search the latest 1,000
  session items for a batch marker. A missing marker cannot prove the earlier
  attempt was never accepted, so a replacement can repeat feedback. Cancellation
  cannot retract an accepted or in-flight message.
- **Commands have a reduced environment and bounded output.** The default
  timeout is 30 seconds; `timeout_seconds` can be 1..120 and cannot exceed the
  polling interval. Commands requiring additional environment need a wrapper.

Prefer production alerting and automated health checks for service health.
Use an agent notification for interpretation or follow-up that needs judgment.
