---
name: phabricator-diff-watch
description: >-
  Use immediately after successfully creating or submitting a Phabricator diff
  or stack — the submit is itself the trigger, so arrange notifications in
  that same turn rather than asking or offering. Also use when asked to watch
  CI or review feedback for the session's diffs. Covers the custom Meta
  integration, the default for Meta feedback. Avoid repeated model status checks.
  Also use when a [Watcher ...] notification names a Phabricator diff.
---

# Phabricator diff watch

## Choosing the notification mechanism

Use the custom `diff_subscribe` integration by default for Meta CI/review
follow-up. The general preference for native notifications in
[[waiting-without-polling]] does not establish equivalence here.

The custom watcher is useful because it understands Meta's diff and CI sources:
it reports CI failures before the whole run settles, CI green, human review
comments, and AI-review findings as distinct events. One subscription can cover
the submitted stack, and session-scoped batching combines related updates.
Requests persist outside the harness, so later feedback can wake the owning
Omnigent conversation even after its native process exits, provided the session
remains usable and the worker/server can deliver.

For example, after submitting a stack, subscribe once and continue other work;
a failing signal or new AI finding can bring you back to the affected diff.
A local build-exit notification does not establish coverage of later Meta CI
or review comments. A native monitor or event channel still needs an adapter
that reads and interprets those Meta sources.

Substitute another integration only after verifying its concrete implementation
covers the requested events: failures while CI is still running, current-version
CI green, unresolved human comments, and AI findings from both signalview and
Arctic, as applicable. Also verify coverage across the named stack and delivery
to the owning session for the required lifetime. Cite the implementation,
documentation, or test evidence; do not infer this from a generic notification
tool being present. Describe partial coverage as partial. Avoid duplicate
observers for events already covered.

## When to subscribe

Subscribe with the available `diff_subscribe` tool only when all of these
hold. The harnesses namespace it differently: the Omnigent SDK harnesses use
`watch__diff_subscribe`, and the native ones (Claude Code and Codex)
use `mcp__watch__diff_subscribe`. Match on the suffix.

If the tool is absent or its connection is dead, report the monitoring gap.
Only use a replacement whose Meta coverage and lifetime have been verified as
above; disclose any uncovered events rather than claiming equivalence.
Do not substitute repeated model polling or manually drive `omnigent-watch-mcp`
over stdio or its HTTP API: that can register a watch this session cannot list
or stop through its tools and hide a broken deployment. Check the missing
component; repaired MCP registration may need a fresh session, while a missing
runtime, worker, or API needs setup. Do not install or restart without
authorization.

- You just created or submitted a diff, or the user explicitly requested a
  watch.
- This session owns the workspace needed to amend it.
- You submitted in this session and the user has not handed the work off.
  **Submitting is itself what makes you responsible** — never treat ownership
  as something the user must grant first, and never offer monitoring as an
  option instead of arranging notifications.
- At least one diff is not terminal.

## Calling it

Two arguments matter, and **neither is inferred**:

```
diff_subscribe(
  session_id = "<from sys_session_get_info>",
  diffs      = ["D116563979", "D116338876"],   # the whole stack, one call
  events     = None,                            # optional subset
)
```

- **`session_id`** is the **Omnigent** session to wake — call
  `sys_session_get_info` and pass the `session_id` it reports. It is not your
  harness's own session id: a Claude Code or Codex session id is a different
  identifier sitting right there in your environment, and passing it is the
  natural mistake. If you are a subagent that will not outlive the watch, pass
  `parent_session_id` instead — otherwise the wake goes to a session that no
  longer exists.
- **`diffs`** is required. Name every diff in the stack, using at most 20 IDs
  per call. Read the ids out of your own `jf submit` / `conf submit` output.
  Nothing is scraped from tool output on your behalf — an earlier version of
  this system did that and bound a session to a diff number in a test fixture.

Each diff is read once during the call, so an id that does not resolve is
reported back immediately. Binding takes a silent baseline: existing failures
and findings do not themselves trigger a wake, so inspect current CI and review
feedback when subscribing. A diff whose requested feedback cannot all be read
fails to bind, while successfully bound siblings remain subscribed. Inspect
the reply and address partial coverage: failed diffs receive no durable request
and need a retry after the read problem is resolved. Already-landed diffs can
be left retired.

Do not subscribe for a read-only review, temporary research or sub-agent work,
handed-off work, an unrelated diff merely seen in output, or a committed,
abandoned, or reverted diff. Do not renew successful active watches on later
turns.

One successful subscription follows the named diffs through amendments and
repeated feedback. An observed finding that resolves and reopens can notify
again, even if acknowledgment of an earlier occurrence arrives late. Changes
between polls can still be missed; this is sampled feedback, not a full history.

Use the default event set unless the user requests a subset of
`review_comment`, `ci_failure`, `ai_review`, or `ci_green`. `diff_status`
shows recorded subscription state and pending notifications; it is not a worker
health check. `diff_unsubscribe` stops one diff or all of them. Both take
`session_id`. Normal diff completion retires automatically without sending a
completion notification. Seven days without a watcher delivery to the session
also retires its watches, even when feedback is deferred. Until the first
delivery, the timer starts at each baseline; repeating an active subscription
does not reset it. An expired watch still worth following needs a fresh
subscribe.

Expect variable latency: five-minute batching and ten-minute spacing between
session notifications, plus adaptive polling. Pending CI requests roughly
one-minute polling; quiet diffs can back off to roughly daily. Busy or
unreachable sessions and partial source failures can defer an entire stack
notification. A stopped worker misses transitions; persisted watches require
the same usable session to resume delivery.

## When a wake arrives

When a `[Watcher ...]` message names Phabricator diffs, treat its counts as a
hint to read current feedback. Generic job notifications belong to
[[watch-anything]]. A diff wake can cover the whole stack and names each
affected diff. Load
[[diff-comments]] for current review feedback and [[ci-signals]] for current CI
before editing. Address actionable findings in the existing workspace, run
focused tests, and amend the affected diffs. Attribute each finding to the diff
that introduced it rather than the tip. Do not subscribe again during the
wake-up turn. Retries check earlier acceptance before refreshing sources and
replacing obsolete attempts, while newer feedback stays queued independently.
Duplicate suppression is best effort. Cancellation or retirement cannot retract
an accepted or in-flight message; affected active siblings remain queued and
can receive repeated feedback if the earlier acceptance was uncertain.

See also [[watch-anything]] for the same machinery pointed at anything that is
not a diff.
