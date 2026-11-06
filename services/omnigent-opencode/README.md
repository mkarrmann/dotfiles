# omnigent-opencode

An OpenCode 2 server for T3 Code whose sessions are Omnigent sessions. T3 Code's
OpenCode provider driver launches it as `<binaryPath> serve`, and every thread it
opens is an Omnigent session on this machine's execution host. Omnigent still
owns the session: harness, TUI, policies, children, watcher wakeups. This
process only translates T3 Code's view of it.

It replaces `bin/omnigent-acp` (T3 Code's generic ACP provider), whose limits
come from T3 Code's ACP path rather than from Omnigent. T3 Code's OpenCode driver
has active steering, fork from any turn, rollback, subagent threads,
server-initiated turns, structured questions and text generation, and its
contract is a public, typed one: `@opencode/client`, `@opencode/protocol` and
`@opencode/schema` 2.0.28, the versions T3 Code pins.

## Contract

The contract is what T3 Code v0.1.6's OpenCode v2 client sends and decodes
(`packages/provider-opencode/src/server/v2/` in T3 Code), not all of OpenCode.

- Launch: `--version` prints a 2.x version on stdout; `serve --hostname=H --port=P`
  prints `server listening on http://H:P` and requires HTTP Basic auth
  `opencode:$OPENCODE_PASSWORD` on every route. T3 Code kills the process group
  30 s after its last user lets go, so all state that must outlive a thread's
  provider session is persisted (see State).
- Responses are decoded by Effect schemas: required fields, `ses`/`msg_`/`per`/
  `frm_`/`evt_` id prefixes, epoch-millisecond times, no `null` for optional keys.
- `GET /api/event` is one volatile SSE feed of every session's events. Most
  `session.*` events carry `durable: {aggregateID, seq, version}`; an event that
  fails to decode is dropped silently, so tests decode every emitted event with
  T3 Code's own schema (see Tests).
- Executions are strictly serial per session, every prompt ends in exactly one
  execution end, and T3 Code routes every event of a session to its active turn.
  An `execution.started` with no T3 turn active is a server-initiated turn: T3
  Code asks itself for a continuation and shows it in the thread.

## Mapping

| T3 Code (OpenCode 2) | Omnigent |
|---|---|
| model `providerID/id`, variant | agent (e.g. `claude-native-ui`) / model option, reasoning effort |
| `session.create` | `POST /v1/sessions` for the agent, bound to this host and the thread's cwd |
| prompt (`delivery: queue`) | user message; the execution ends at the confirmed idle |
| prompt (`delivery: steer`) | user message to the busy session: codex-native `turn/steer`, claude-native paste; `inbox.delivered` at `session.input.consumed` |
| interrupt | `interrupt` event; the execution ends when the session is idle |
| `switchModel` / variant | `PATCH model_override` / `reasoning_effort` (a live switch) |
| agent `plan` | codex-native collaboration mode `plan`; the approval policy, under which edits are denied |
| fork `{before}` | `POST /v1/sessions/{id}/fork` with `up_to_response_id`, then bind the host |
| `revert.stage/commit` | fork at the boundary, then point the T3 session at the fork |
| `permission.asked` / reply | Omnigent elicitation / resolve |
| `form.created` / reply | Omnigent `ask_user_question` |
| subagent tool, child session | the harness's spawning call (Claude `Agent`/`Task`, Codex `spawn_agent`) and its Omnigent child session (`session.created` on the parent's stream, linked by the child's `tool_use_id` label) |
| background subagent report (`synthetic` inbox item) | Claude's `<task-notification>` input, which starts the parent's next turn |
| execution with no T3 turn | an Omnigent turn T3 Code did not start (watcher wakeup, wake notice, input from another client); under the daemon, the reply to T3 Code's turn on an "Agent" message (see Daemon) |
| `/compact` | `compact` event |
| text generation session (`T3 Code <op>`) | a one-shot `claude -p` |

## Approvals

T3 Code enforces nothing itself (`runtimePolicy.enforcement: "native"`): it
sends permission rules for the thread's runtime mode (last match wins) and
expects the server to apply them.

The harnesses run in the operator's permission mode (`bypassPermissions` in
`~/.omnigent/config.yaml`), so on their own they rarely ask. While a thread is
not in full access, or is in plan mode, this server adds an Omnigent session
policy, `t3-code-runtime-mode` (the built-in `ask_on_os_tools`), which asks
before every file and shell tool call. Omnigent enforces it in the harnesses'
PreToolUse hooks whatever their mode, and it is switched over REST
(`/v1/sessions/{id}/policies`). Switching the harness's own mode instead means
driving its terminal: that proved unreliable on a terminal still starting up,
and Claude cannot return to bypassPermissions.

`approvals.py` classifies each Omnigent elicitation by `policy_name`:

| Elicitation | What happens |
|---|---|
| this server's policy, or the harness's own prompt | the rules decide: `allow` accepts, `deny` declines, `ask` shows a `shell` / `edit` / `read` / ... request with `save` patterns. A path outside the thread's directory is also checked against `external_directory`. Anything no rule names is allowed, as under OpenCode's default rules |
| any other Omnigent policy ASK (operator guardrails such as `no_new_environments`) | always shown: a full-access thread does not switch guardrails off |
| `ask_user_question` (Claude AskUserQuestion, Codex requestUserInput) | a form. Answers go back keyed by question text (Claude) or by id (Codex) |
| Claude ExitPlanMode while the thread is in plan mode | declined, with a note to reply with the plan instead |

T3 Code's "allow for this session" adds allow rules for the request's `save`
patterns, so later matching requests are accepted here, on either harness.
How T3 Code's answers map onto Omnigent's verbs depends on who asked
(`approvals.resolution`):

| | Decline (the agent goes on without the call) | Cancel (the turn stops) |
|---|---|---|
| a policy | `cancel`: the call is denied with the policy's reason | `decline`: Omnigent aborts the turn |
| Claude's own prompt | `decline` with T3 Code's explanation as `feedback` | `decline`, then interrupt |
| Codex's own prompt | `decline`: Omnigent interrupts the turn regardless | `cancel` |

Known gaps: Codex hooks may not report `apply_patch` (file edits) to the
policy, so Codex edits may not be asked about. A policy denial gives the model
the policy's reason, not T3 Code's note.

## Daemon

T3 Code unloads a thread 30 minutes after its last turn
(`ProviderSessionManager.ts`, `DEFAULT_IDLE_TIMEOUT_MS`), drops what an OpenCode
server emits for it after that, and stops the server 30 s after it releases its
session; nothing an OpenCode server can see marks the unload. So an Omnigent
turn T3 Code did not start (a watcher's wakeup, a background task's report, a
message from the Omnigent app, the TUI or Google Chat) is shown only while the
thread happens to be loaded, unless the daemon runs:

- `omnigent-opencode daemon` serves the sessions on a Unix socket
  (`~/.local/state/omnigent-opencode/daemon.sock`, mode 0600) and keeps each
  session's Omnigent stream open, T3 Code listening or not, until the session has
  been quiet for `OMNIGENT_OPENCODE_WATCH_DAYS` (default 7) or its Omnigent
  session is gone. After a restart it follows the same sessions again.
- `serve`, which T3 Code still runs, forwards to the daemon when it answers on
  its socket (`proxy.py`), naming T3 Code's server by its home (`T3CODE_HOME`,
  else `~/.t3`); otherwise it serves on its own as before.
- Every outside turn is held: its events are kept, and the daemon runs
  `t3 thread send --base-dir HOME THREAD` with `[Omnigent] <the input>` (for a
  report, its summary), which T3 Code posts as an "Agent" message, loading the
  thread if it must and queueing behind a running turn. The prompt of the turn
  that starts is that text (after any context T3 Code prepends); it is not sent
  to Omnigent, but answered with the held events, renamed to its message, and
  then the rest live. A held turn's approvals are not listed until then, since
  T3 Code rejects what it finds when it resumes a thread.
- The thread is read from T3 Code's state, read-only: the provider thread whose
  native ref is the session, if its thread still uses it and is neither archived
  nor deleted (`wakes.py`). With no thread, the turn is shown as before, as one
  the agent started. While T3 Code is down the trigger is sent again, every 15 s
  up to every 5 minutes.
- A message typed in T3 Code before T3 Code's turn for a held run begins shows
  the run at once and is steered into it; the trigger's turn then ends empty.
- Omnigent's stream does not replay what a subscriber missed, so on every
  (re)connect the session's items after the last input this server handled are
  read, and each turn on an input from outside T3 Code is replayed (still
  running if it is the last and the session is busy).

launchd (`launchd/com.mkarrmann.omnigent-opencode.plist`, loaded by sync.sh)
or systemd (`systemd/omnigent-opencode.service`) runs the daemon wherever a T3
Code server runs; bin/t3-ensure enables it there. A running daemon keeps the
code it started with and reports a hash of it (`GET
/api/omnigent-opencode/status`); `omnigent-opencode daemon-stale` exits 0 when
the code on disk differs and nothing is in flight, which bin/t3-ensure
(init.sh) takes as its cue to restart it, and 2 when a turn would be lost. While
the daemon restarts, `serve` holds T3 Code's requests for up to 8 s. It logs to
`~/.local/state/omnigent-opencode/daemon.log`, rotated at 16 MiB.

Limits:

- `serve` decides once, at start, whether to forward. Started while the daemon
  is down, it serves on its own until T3 Code stops it; while the daemon is down
  under a forwarding `serve`, T3 Code's event stream fails and T3 Code starts
  the provider again at its next turn.
- Held runs live in memory. A daemon restarted while one waits (T3 Code down,
  say) does not show it again: T3 Code's queued trigger then runs an empty
  turn, and the turn is only in the session's history.
- Only the last 5 turns missed in a gap are replayed.

## State

T3 Code stores the OpenCode session id as the thread's native id and resumes it
with `session.get` after this process restarts. A session id is `ses_` followed
by its Omnigent session id, so the common case needs no lookup. What cannot be
recovered from Omnigent is kept in a SQLite file under
`$XDG_STATE_HOME/omnigent-opencode/` (default `~/.local/state/`):

- a session whose Omnigent session was replaced by a rollback;
- the T3 Code message id of each prompt, since T3 Code addresses forks,
  rollbacks and reconnect backfill by the ids it chose;
- the sessions followed, each with the last Omnigent input handled, when it was
  last active, and the T3 Code server that last opened it.

## Layout

- `cli.py`: `--version`, `serve` and `daemon`.
- `proxy.py`: `serve` forwarding to the daemon.
- `wakes.py`: a session's T3 Code thread, and `t3 thread send`.
- `wire.py`: OpenCode 2 ids, event envelopes and response shapes.
- `events.py`: the SSE feed and its keepalives.
- `omnigent.py`: Omnigent REST and SSE client and host and agent resolution.
- `turns.py`: Omnigent turn tracking, carried over from `bin/omnigent-acp`.
- `approvals.py`: elicitation classification, permission rules, the session policy, forms.
- `subagents.py`: spawning calls, child links and background reports.
- `history.py`: a session's history as OpenCode messages.
- `store.py`: what survives a restart.
- `textgen.py`: T3 Code's text generation as a one-shot `claude -p`.
- `server.py`: routes.
- `sessions.py`: per-session execution state machine.

## Tests

`uv run pytest` runs the offline suite. Tests that decode events and responses
with T3 Code's schemas need `~/repos/t3code` with its dependencies installed
(`pnpm install`); they are skipped otherwise. `test_t3_following.py` covers
replay after a gap and held runs; `test_t3_daemon.py` the forwarding path.

`tests/live/live_harness.py SCENARIO` runs scenarios against the real Omnigent
on this machine through T3 Code's client: `turn`, `steer`, `stop`, `approve`,
`decline`, `question`, `plan`, and `smoke` (model switch, image, compaction,
fork, rollback, text generation). They create real Omnigent sessions. Note
that `smoke`'s model switch makes Claude Code save the model as the default in
`~/.claude/settings.json` (see docs/t3code-omnigent.md, Limits).

