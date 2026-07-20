---
name: phabricator-diff-watch
description: >-
  Use after successfully creating a Phabricator diff when this agent remains
  responsible for follow-up, or when the user explicitly asks to watch,
  follow, or report new review comments or CI results for the session's diff.
  Also use when a message beginning with [Diff watcher] wakes the session.
---

# Phabricator diff watch

Subscribe with `diff_watch__diff_watch_subscribe` only when all of these hold:

- You just created the diff, or the user explicitly requested a watch.
- This session owns the workspace needed to amend it.
- You expect to remain responsible for follow-up.
- The diff is not terminal.

Do not subscribe for a read-only review, temporary research or sub-agent work,
handed-off work, an unrelated diff merely seen in output, or a committed,
abandoned, or reverted diff. Do not resubscribe on later turns.

Use the default event set unless the user requests only `review_comment` or
only `ci_failure`. `diff_watch__diff_watch_status` checks this session's
preference. Use `diff_watch__diff_watch_unsubscribe` when the user asks to stop or responsibility is
handed off; normal diff completion retires automatically.

When a `[Diff watcher ...]` message arrives, treat its counts as a stale hint.
Load [[diff-comments]] for current review feedback and [[ci-signals]] for
current CI before editing. Address actionable findings in the existing
workspace, run focused tests, and amend the existing diff. Do not subscribe
again during the wake-up turn.
