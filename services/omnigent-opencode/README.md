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
| execution with no T3 turn | an Omnigent turn T3 Code did not start (watcher wakeup, wake notice, input from another client) |
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

## State

T3 Code stores the OpenCode session id as the thread's native id and resumes it
with `session.get` after this process restarts. A session id is `ses_` followed
by its Omnigent session id, so the common case needs no lookup. What cannot be
recovered from Omnigent is kept in a SQLite file under
`$XDG_STATE_HOME/omnigent-opencode/` (default `~/.local/state/`):

- a session whose Omnigent session was replaced by a rollback;
- the T3 Code message id of each prompt, since T3 Code addresses forks,
  rollbacks and reconnect backfill by the ids it chose.

## Layout

- `cli.py`: `--version` and `serve`.
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
(`pnpm install`); they are skipped otherwise.

`tests/live/live_harness.py SCENARIO` runs scenarios against the real Omnigent
on this machine through T3 Code's client: `turn`, `steer`, `stop`, `approve`,
`decline`, `question`, `plan`, and `smoke` (model switch, image, compaction,
fork, rollback, text generation). They create real Omnigent sessions. Note
that `smoke`'s model switch makes Claude Code save the model as the default in
`~/.claude/settings.json` (see docs/t3code-omnigent.md, Limits).

