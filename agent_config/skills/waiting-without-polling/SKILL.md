---
name: waiting-without-polling
description: >-
  Use before waiting on anything that completes later — a rollout or canary,
  a CI run, a build, a deploy, a Chronos job, a JustKnob or config mutation,
  a long test — and whenever asked to babysit, monitor, watch, keep an eye on,
  poll, or report back when something lands, finishes, or goes green. Covers
  how to wait without spending model turns: backgrounded condition loops that
  wake you on exit, /loop for recurring judgment passes, the Phabricator diff
  watcher, and the per-harness mechanics for Claude Code, Codex, and
  Omnigent-hosted sessions. Trigger keywords: babysit, monitor, watch, poll,
  wait for, keep an eye on, let me know when, notify me when, check back,
  until it lands, until CI is green, sleep, background, canary, rollout.
---

# Waiting without polling

## The rule

**Never block a turn waiting, and never re-poll in your own context.** Both spend
a full model turn to learn nothing.

Measured, in one Presto canary-watch session: a turn costs ~205k tokens
(dominated by re-reading the growing transcript). Eight `sleep`-and-recheck
turns cost **~1.4M tokens and 75 minutes of dead wall-clock**, and returned only
a timestamp. Backgrounding the same wait costs nothing.

Note both halves. Dropping `sleep` but still issuing a check every few turns is
the *same* mistake — the turn is the unit of cost, not the sleep.

## Pick the cheapest thing that can decide

| Deciding the condition needs… | Use | Cost |
|---|---|---|
| A comparison, exit code, threshold | backgrounded condition loop | 0 |
| Judgment ("does this look wrong?") | a recurring pass, proposed to the user | ~205k/turn |
| Phabricator diff / CI | `phabricator-diff-watch` | 0 |

Most watching is mechanical. Reach for a model only when interpreting the
result genuinely needs one — and note the two compose well: a free mechanical
trigger gating an expensive judgment pass.

## Recipes

### Claude Code — backgrounded loop (the default)

`run_in_background: true` on Bash. The harness re-invokes you when the process
exits, so the wait is free.

```bash
base=$(some-check)
while [ $i -lt 144 ]; do          # always bound it; 144 x 5min = 12h
  sleep 300; i=$((i+1))
  now=$(some-check)
  [ -z "$now" ] && continue        # transient failure is not a change
  [ "$now" != "$base" ] && { echo "CHANGED: $base -> $now"; exit 0; }
done
echo "no change after 12h"
```

### Codex

No auto-wake on exit. Detach and push into the thread instead:

```bash
setsid nohup sh -c 'until cond; do sleep 300; done;
  codex queue --thread "$THREAD" --message "condition met"' &
```

Or hold the process in a `unified_exec` session.

### Recurring judgment passes

`/loop` exists for this ("check the deploy every 5 minutes" is its documented
example). **Propose it; do not self-start it** — at ~205k/turn a 5-minute loop
costs ~2.5M tokens/hour, which is the user's spend decision.

Pair it with a backgrounded watcher rather than replacing one: per
`ScheduleWakeup`'s own guidance, never short-interval-poll work that already
notifies on completion — use a long fallback (1200s+) as a heartbeat instead.

## Traps

- **Capture the baseline before the thing can change.** A watcher started after
  a change has already propagated records the *new* value as its baseline and
  never fires. This has actually happened: a JustKnob watcher missed its own
  rollout because it began after the value flipped locally. If the baseline
  might already be stale, compare against a known-expected value, not against
  whatever you read first.
- **Distinguish "no change" from "check failed."** An empty or erroring probe is
  not a value; `continue`, don't treat it as a difference.
- **Always bound the loop** so a stuck watcher dies rather than lingering.
- **A foreground `sleep` of 60s or more is refused** in any Omnigent-hosted
  session, by `omnigent_config/policy_modules/no_foreground_wait.py`. It is a
  backstop, not the guidance: it fires after you have already spent the turn, it
  only sees `sleep` rather than the re-poll-across-turns half of the problem,
  and a session started outside Omnigent is not gated at all.

## Prefer purpose-built alerting

For production signals, an agent watching a dashboard is a worse-engineered
alert. Meta already has ODS and Scuba alerting, and rollout systems carry their
own health checks — a Configerator canary runs `customHealthCheckHook` and
aborts on host-level failure without anyone watching. Reach for those first, and
use an agent for the judgment a threshold cannot express.

Beware the inverse, too: a naive threshold alert on a cohort metric will fire
falsely. Comparing a treated cohort against "everything else" mixes populations;
compare within a matched control and stratify, or Simpson's paradox will hand
you a confident wrong answer.
