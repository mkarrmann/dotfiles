---
name: phabricator-diff-watch
description: >-
  Use after successfully creating or submitting a Phabricator diff or stack
  when this agent remains responsible for follow-up, or when the user asks to
  watch, follow, or report new review comments or CI results for the session's
  diffs. Prefer this over an in-turn CI polling loop: the sidecar polls out of
  process and survives the session going idle.
  Also use when a message beginning with [Diff watcher] wakes the session.
---

# Phabricator diff watch

Subscribe with the available `diff_watch_subscribe` tool only when all of these
hold. Omnigent/Claude names it `diff_watch__diff_watch_subscribe`; native Codex
names it `mcp__diff_watch__diff_watch_subscribe`.

- You just created or submitted a diff, or the user explicitly requested a
  watch.
- This session owns the workspace needed to amend it.
- You expect to remain responsible for follow-up.
- At least one diff is not terminal.

The tools take no diff argument. Subscribing covers **every** diff this session
created, so a stack needs one call, not one per diff. Diffs are recognized from
the real Phabricator URLs in commit or `jf submit` output, so subscribe after
the submit that prints them.

Do not subscribe for a read-only review, temporary research or sub-agent work,
handed-off work, an unrelated diff merely seen in output, or a committed,
abandoned, or reverted diff. Do not resubscribe on later turns.

Use the default event set unless the user requests only `review_comment` or
only `ci_failure`. Use the corresponding `diff_watch_status` tool to check this
session's preference and `diff_watch_unsubscribe` when the user asks to stop or
responsibility is handed off; normal diff completion retires automatically.

When a `[Diff watcher ...]` message arrives, treat its counts as a stale hint.
One wake covers the whole stack and names each affected diff. Load
[[diff-comments]] for current review feedback and [[ci-signals]] for current CI
before editing. Address actionable findings in the existing workspace, run
focused tests, and amend the affected diffs. Attribute each finding to the diff
that introduced it rather than the tip. Do not subscribe again during the
wake-up turn.
