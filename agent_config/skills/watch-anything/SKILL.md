---
name: watch-anything
description: >-
  Use when asked to babysit, monitor, watch, or report back on something that
  is not a Phabricator diff — a JustKnob or config rollout, a canary, a
  Chronos job, a deploy, a queue depth, a value in a dashboard or CLI — and
  whenever waiting for a condition that a command can report. Registers an
  out-of-process watch that wakes the session when the command's output
  changes, instead of spending model turns re-checking. Use
  phabricator-diff-watch instead for diffs and their CI. Trigger keywords:
  babysit, monitor, watch, poll, wait for, keep an eye on, let me know when,
  notify me when, check back, until it flips, until it rolls out, canary,
  rollout, JustKnob, JK.
  Also use when a message beginning with [Watcher] wakes the session.
---

# Watch anything

Wake this session when the output of a command changes. The command runs in the
watcher sidecar on an interval, not in this session, so the wait costs no
model turns and survives the session going idle.

This is the general case. For a Phabricator diff or its CI, use
`phabricator-diff-watch` instead — that surface knows what a diff is and gives
you review comments, CI failures, and automated-review findings as distinct
events. This one knows nothing about its subject and only reports "it changed".

## Subscribing

The tool is `mcp__watch__subscribe`; match on the suffix.

**If the tool is absent or its connection is dead, stop and say so.** Do not
substitute anything for it — not a polling loop, and not driving
`omnigent-watch-mcp` yourself over stdio. The stdio route does work, which
is the trap: it registers a real watch while leaving this session unable to
list or stop it, and it hides a broken deployment that would otherwise get
fixed. This has happened.

The usual cause is not a broken server. A harness binds its MCP servers once,
at session start, so a server that was installed or repaired *during* this
session stays dead here no matter how healthy it is. **A new session is the
fix**, and saying that is more useful than working around it.

```
subscribe(
  session_id = "<from sys_session_get_info>",
  subject  = "jk:presto/presto_batch:py_client_apply_bcp_client_info",
  command  = ["jk", "get", "presto/presto_batch:py_client_apply_bcp_client_info"],
  extract  = r"(\d+/\d+|true|false)",     # optional
  interval_seconds = 60,                   # optional, 30s..24h
)
```

- **`session_id`** is the **Omnigent** session to wake — call
  `sys_session_get_info` and pass the `session_id` it reports. It is not your
  harness's own session id: a Claude Code or Codex session id is a different
  identifier, sitting right there in your environment, and passing it is the
  natural mistake. The tool rejects it, but only after a round trip. If you are
  a subagent that will not outlive the watch, pass `parent_session_id` instead
  — otherwise the wake goes to a session that no longer exists, which is the
  one way to register a watch that fires correctly and still reaches nobody.
- **`subject`** must be namespaced `<prefix>:<identifier>`. It is the watch's
  identity, and the namespace is what keeps it from colliding with a diff id.
- **`command`** is an argv list, run directly — never through a shell. Pipes,
  redirection, globs, and `&&` are not available. Put those in a script and
  name the script.
- **`extract`** is a regular expression, optionally with one capture group.
  Use it whenever the output carries anything incidental.
- **`interval_seconds`** is held constant. Unlike a diff watch, a command watch
  does not back off when nothing is happening.

`status(session_id)` lists a session's watches and the exact command each
will keep running; `unsubscribe(session_id, subject=None)` stops one or
all of them.

## Judgment: keep it out of the poll

A watch answers "did this change", not "is this bad". Put the judgment *after*
the wake, not inside the poll:

1. The watch fingerprints something cheap and mechanical — 0 tokens per poll.
2. It wakes the session once, when that thing actually moves.
3. **The woken session delegates the analysis to a subagent**, so reading logs
   and metrics does not land in the main context.

That two-stage shape is why the trigger can be free and the analysis can be
expensive: you pay for judgment once, on a real change, instead of on every
poll. Prefer it.

You *can* invert it and make the command itself a judge — `command` is an argv,
so `["claude", "-p", "...print CHANGED or SAME"]` is legal, and
`timeout_seconds` goes up to 120 to accommodate it. Three reasons not to,
unless the condition genuinely cannot be expressed mechanically:

- Every poll is a model call, which is the cost the watch existed to avoid.
- The fingerprint is of the model's *output*, so any wording drift is a
  spurious wake. Constrain it to a single bare token, and pair it with
  `extract`.
- A model that cannot reach its condition tends to answer anyway, so a broken
  probe reads as a change rather than as a failure.

## Traps

- **`extract` is usually mandatory in practice.** A timestamp, request id,
  duration, or row count anywhere in the output makes the fingerprint change on
  every poll, and the watch wakes you every interval forever. Run the command
  twice by hand first and diff the output.
- **Subscribe before the change can happen.** The first reading is the
  baseline and never wakes anyone, so a watch registered after a rollout has
  already flipped baselines the value you were waiting for and stays silent
  forever. This has actually happened. If the value may already have moved,
  watch for a comparison against the expected value rather than for the raw
  value — for example `extract` on a command that prints `MATCH`/`NOMATCH`.
- **A failing command is not a change.** Non-zero exit routes into backoff and
  does not wake anyone, so a watch on a command that *starts* breaking goes
  quiet rather than lying. Check `status` if a watch seems too silent.
  A command that cannot run at all is caught at subscribe time instead —
  `subscribe` runs it once to take the baseline and fails the tool call
  rather than registering a watch that could never fire.
- **A watch that never fires expires.** Seven days without a single delivery
  ages it out, so a watch for something further away than that will be gone
  before the thing happens. Watch a nearer-term proxy, or re-subscribe.
- **Latency is not the interval.** A wake can lag the change by up to the
  interval plus the batch window (5 min) plus the minimum delivery gap
  (10 min). Fine for a rollout; wrong for anything that needs seconds.
- **The command runs with a reduced environment** (`PATH`, `HOME`, proxy and
  credential vars) and a 30-second timeout, with output capped. A command that
  needs an unusual variable will fail; wrap it in a script that sets it.

## Prefer purpose-built alerting

For production signals, an agent watching a value is a worse-engineered alert.
ODS and Scuba alerting exist, and rollout systems carry their own health checks
— a Configerator canary runs `customHealthCheckHook` and aborts on host-level
failure with nobody watching. Reach for those first, and use a watch for the
judgment a threshold cannot express, or for a one-off you do not want to build
an alert for.

See also [[waiting-without-polling]] for the general rule and for the
harness-level mechanisms (backgrounded condition loops, `/loop`) that are
cheaper still when the wait is short and the session is staying open anyway.
