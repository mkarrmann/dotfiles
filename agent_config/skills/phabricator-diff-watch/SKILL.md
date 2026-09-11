---
name: phabricator-diff-watch
description: >-
  Use immediately after successfully creating or submitting a Phabricator diff
  or stack — the submit is itself the trigger, so subscribe in that same turn
  rather than asking or offering. Also use when the user asks to watch, follow,
  or report new review comments or CI results for the session's diffs. Prefer
  this over an in-turn CI polling loop: the sidecar polls out of process and
  survives the session going idle.
  Also use when a message beginning with [Watcher] wakes the session.
---

# Phabricator diff watch

Subscribe with the available `diff_subscribe` tool only when all of these
hold. The harnesses namespace it differently: the Omnigent SDK harnesses use
`watch__diff_subscribe`, and the native ones (Claude Code and Codex)
use `mcp__watch__diff_subscribe`. Match on the suffix.

**If the tool is absent or its connection is dead, stop and say so.** Do not
substitute anything for it — not a polling loop, and not driving
`omnigent-watch-mcp` yourself over stdio. The stdio route does work, which
is the trap: it registers a real watch while leaving this session unable to
list or stop it, and it hides a broken deployment. A harness binds its MCP
servers once, at session start, so a server repaired *during* this session
stays dead here; **a new session is the fix**.

- You just created or submitted a diff, or the user explicitly requested a
  watch.
- This session owns the workspace needed to amend it.
- You submitted in this session and the user has not handed the work off.
  **Submitting is itself what makes you responsible** — never treat ownership
  as something the user must grant first, and never offer the watch as an
  option instead of subscribing.
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
- **`diffs`** is required. Name every diff in the stack; one call covers all of
  them. Read the ids out of your own `jf submit` / `conf submit` output. Nothing
  is scraped from tool output on your behalf — an earlier version of this system
  did that and bound a session to a diff number that appeared in a test fixture.

Each diff is read once during the call, so an id that does not resolve is
reported back immediately rather than failing silently in the background. A
stack where one diff has already landed still subscribes the rest; the failures
are named in the reply.

Do not subscribe for a read-only review, temporary research or sub-agent work,
handed-off work, an unrelated diff merely seen in output, or a committed,
abandoned, or reverted diff. Do not resubscribe on later turns.

Use the default event set unless the user requests a subset of
`review_comment`, `ci_failure`, `ai_review`, or `ci_green`. `diff_status`
lists what a session is watching and `diff_unsubscribe` stops one diff or
all of them — both also take `session_id`. Normal diff completion retires
automatically, and so does a week of silence: a watch that has delivered
nothing for seven days is aged out, so a long-quiet diff you still care about
needs a fresh subscribe rather than an assumption that the old one held.

## When a wake arrives

When a `[Watcher ...]` message arrives, treat its counts as a stale hint.
One wake covers the whole stack and names each affected diff. Load
[[diff-comments]] for current review feedback and [[ci-signals]] for current CI
before editing. Address actionable findings in the existing workspace, run
focused tests, and amend the affected diffs. Attribute each finding to the diff
that introduced it rather than the tip. Do not subscribe again during the
wake-up turn.

See also [[watch-anything]] for the same machinery pointed at anything that is
not a diff.
