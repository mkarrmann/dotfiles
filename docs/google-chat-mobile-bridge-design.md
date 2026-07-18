# Omnigent Google Chat mobile bridge

**Status:** Implemented and deployed from
`~/dotfiles/services/omnigent-google-chat`. Production
Phase 0 verified Meta Bot identity, root push delivery, phone reply readback,
request-ID idempotency, and two-second uncached-list latency. Per-thread phone
notification preferences were explicitly deferred for user configuration. A
live CodeCompanion session completed the full
CodeCompanion -> Google Chat -> phone -> same Omnigent session -> Google Chat
round trip, followed by a bridge-only restart with no repeated input, thread,
or output.

**Scope:** Observe and message Omnigent sessions running on a devserver from
Google Chat on a phone, without exposing the devserver, changing
`omnigent-server`, or replacing the Omnigent agent runtime.

**Primary decision:** Run a small outbound-only bridge beside Omnigent on the
devserver. It mirrors selected Omnigent sessions into one private Google Chat
space, with one thread per session, and forwards human thread replies to the
same Omnigent sessions.

---

## 1. Summary

The Agent Home design makes AgentCloud the agent and Omnigent a projection of
that agent. That is the wrong tradeoff for this use case. The requirement is to
keep running the real Omnigent-configured agent, including its system prompt,
MCP servers, tools, policies, routing, and sub-agents, while adding a convenient
phone client.

Google Chat can be that client:

```text
Google Chat mobile app
          |
          | managed Google Chat service
          |
          v
meta google.chat.message
          ^
          | outbound send/list commands
          |
omnigent-google-chat bridge (devserver)
          |
          | loopback/authenticated HTTP + SSE
          v
omnigent-server <----> runner <----> actual Omnigent agent
```

The bridge opens no public webhook, subscriber, or Internet-facing port. It
polls one Google Chat space through sanctioned Meta CLI commands and talks to
the local or otherwise authenticated Omnigent API.

The implementation is a private service under
`~/dotfiles/services/omnigent-google-chat`, modeled on Omnigent's
`integrations/slack`. Keeping it outside the open-source Omnigent repository
contains the Meta-only CLI, identity, and deployment assumptions while using
only Omnigent's public HTTP/SSE surface. The Slack code supplies useful
Omnigent HTTP, SSE, runner-recovery, SQLite, and event-extraction patterns. It
is not a direct transport substitution: the Slack integration creates sessions
from Slack and waits synchronously for each Slack-initiated turn. This design
instead attaches passively to sessions created by CodeCompanion, the web UI,
or other Omnigent clients, and keeps input and output processing independent so
a phone message can steer or queue work while an agent is already running.

No `omnigent-server` changes are required for the MVP.

---

## 2. Goals

### 2.1 User-visible goals

- An Omnigent session started on the devserver appears as a Google Chat thread.
- The thread shows enough durable context to check progress from a phone:
  user messages, final assistant messages, waiting/blocked state, failures, and
  completion.
- A human reply in that thread is submitted to the same Omnigent session.
- A reply sent while the agent is running reaches Omnigent within the active
  polling interval and uses Omnigent's normal queue/steer behavior.
- CodeCompanion, the web UI, CLI clients, and Google Chat remain views of one
  Omnigent session and one durable transcript.
- Bridge and devserver restarts do not duplicate Google Chat output or resubmit
  a phone message automatically.
- Nothing listens for inbound Internet traffic on the devserver.

### 2.2 Engineering goals

- Preserve the full Omnigent agent runtime and feature set.
- Reuse existing Omnigent REST/SSE APIs and Slack integration patterns.
- Use the sanctioned `meta` CLI rather than calling Google APIs directly or
  storing Google OAuth credentials.
- Store only correlation, cursors, and delivery state locally; do not maintain a
  second transcript database.
- Prefer durable Omnigent items over token deltas as the source of chat output.
- Make uncertain inbound delivery visible rather than risking duplicate agent
  instructions.
- Keep the first release single-user, single-space, and single-host.
- Keep phone-to-Omnigent latency below 15 seconds while active without a webhook
  or event subscriber.

---

## 3. Non-goals

- Replacing the Omnigent web UI or CodeCompanion.
- Running agents inside Google Chat or AgentCloud.
- Creating new Omnigent sessions from Google Chat in the MVP. Sessions start
  through normal Omnigent clients and are then mirrored.
- Mirroring every token, reasoning block, tool call, tool output, terminal
  command, or file diff into Google Chat.
- Resolving tool approvals from Google Chat in the MVP. The bridge may notify
  that a session needs attention; approval remains in an existing Omnigent UI.
- Supporting shared/multi-user Google Chat spaces. Only an allowlisted human
  identity may control sessions.
- Supporting attachments or images as phone input in the MVP.
- Providing an external Slack integration for Meta work.
- Using `meta events.stream` in the MVP. It remains an optional later latency
  optimization after the polling implementation is proven.
- Guaranteeing exactly-once delivery across an Omnigent POST whose response is
  lost. The bridge chooses an explicit at-most-once recovery policy for that
  narrow ambiguity.

---

## 4. Verified building blocks

"Verified" in this section means the source/API or CLI help exposes the stated
contract. It does not prove the current user is entitled, Meta Bot posts notify
the phone, or production output has the expected shape; Phase 0 verifies those
runtime properties.

### 4.1 Existing Omnigent Slack integration

`integrations/slack` currently provides:

- `OmnigentClient` using the Sessions HTTP API;
- user-message submission through
  `POST /v1/sessions/{session_id}/events`;
- session SSE parsing from `GET /v1/sessions/{session_id}/stream`;
- durable item fallback through
  `GET /v1/sessions/{session_id}/items`;
- session creation and runner launch/binding;
- per-thread serialization;
- SQLite thread/session and event-dedup storage;
- assistant text, error, delta, and terminal-status extraction;
- tests around request shapes, SSE parsing, runner recovery, duplicate events,
  long output, and failed progress updates.

The following Slack behavior is not reusable as-is:

- a Slack mention creates the Omnigent session;
- only Slack-created thread mappings are known;
- the SSE stream is opened only while processing a Slack turn;
- the per-thread worker waits for terminal status before accepting the next
  message into Omnigent.

The Google Chat bridge therefore reuses transport-neutral code and patterns,
not `SlackOmnigentService`'s control flow.

### 4.2 Google Chat message CLI

The current Meta CLI exposes `google.chat.message` actions including `send`,
`list`, `read-batch`, `get`, `edit`, and `find-dm`.

Relevant verified `send` capabilities:

- send to a space;
- start a new thread or reply with `--reply-in-thread`;
- return a human-readable summary followed by a final raw-JSON line containing
  message/thread resource identities;
- act as Meta Bot with `--as-meta-bot`;
- use `--request-id` for idempotent message creation;
- read message text from stdin;
- mention a user when phone notification/thread following requires it;
- apply a stable message prefix.

`--as-meta-bot` works only with a space (`--space-name`), not a direct-message
target (`--to`). The MVP therefore requires a private space and does not support
a self-DM fallback.

Relevant verified `list` capabilities:

- filter by space and thread;
- filter by creation time;
- order oldest first;
- return raw Google Chat JSON;
- bypass the per-user cache with `--skip-cache`;
- page up to 200 messages per request.

The correct namespace is `meta google.chat.message`, not
`meta google.chat`.

### 4.3 Optional Agent Events stream

`meta events.stream` currently advertises source `google.chat` with:

```text
entity types:    message, reaction
container type:  space
event types:     message.created, message.updated, message.deleted,
                 reaction.created, reaction.updated, reaction.deleted
```

`meta events.stream tail` supports:

- explicit isolated subscriptions;
- JSON-line output and quiet mode;
- source, event-type, container, actor, and actor-type filters;
- heartbeats and stall recovery;
- historical catch-up;
- Iris sequence IDs suitable for a persisted resume watermark.

These flags and source metadata are verified from the CLI surface, but runtime
event shape, filtering, resume behavior, and delivery have not been validated.
They are not MVP dependencies. A later optimization may use the event stream as
a wake-up channel while retaining Google Chat message listing as the durable
source.

---

## 5. Architecture

One daemon contains four cooperating components:

```text
SessionReconciler
  - lists eligible Omnigent sessions
  - creates/reuses one Google Chat thread per session
  - starts/stops SessionMirror tasks

SessionMirror (one per selected active session)
  - follows Omnigent SSE
  - reconciles durable /items
  - emits concise Google Chat messages

GoogleChatPoller
  - lists new messages from the one configured space
  - uses overlap plus message-name deduplication
  - forwards allowlisted human replies to mapped sessions

GoogleChatSender
  - serializes `meta google.chat.message send`
  - uses deterministic request IDs
  - records returned message/thread identities
```

All components share one SQLite store in WAL mode.

### 5.1 Why input and output are independent

The Slack integration's `run_turn` method opens SSE, submits input, and blocks
until the session becomes idle. Its per-thread dispatcher therefore delays the
next Slack reply until the current turn finishes.

That is not acceptable for a phone check-in client: a message sent while an
agent is working should reach Omnigent on the next poll as a steer or queued
follow-up, without waiting for the active turn to finish. The Google Chat bridge
has:

- a long-lived passive output mirror; and
- a short input path that POSTs each accepted phone message and returns without
  waiting for the response.

Omnigent remains the sole authority for queueing and steering semantics.

### 5.2 Why space-level polling first

For a personal check-in client, 10-15 second reply latency is acceptable. One
space-level read is substantially simpler than an Iris subscriber: it removes
event-shape assumptions, container filtering, watermark recovery, heartbeat
supervision, and an internal subscriber port.

The poller never starts one command per thread. Each cycle lists the dedicated
space once, walks any result pages, and routes messages locally by thread name.
It uses an overlap window because timestamp filters can be inclusive, have
limited precision, or race messages created near the cursor. Durable message
resource names are the deduplication authority.

Polling is adaptive:

- 10 seconds while any mapped Omnigent session is running, waiting, or recently
  active;
- 30 seconds while all mapped sessions are idle;
- an immediate poll after creating a thread or posting bridge output.

An optional Phase 3 Agent Events consumer may trigger immediate extra polls. It
does not replace polling or message-name deduplication.

---

## 6. Session selection and thread mapping

### 6.1 Safe default: explicit opt-in

The default discovery mode mirrors only sessions on the configured Omnigent
host that carry a configured label, for example:

```text
omnigent.google_chat.enabled="true"
```

CodeCompanion can set that label at session creation through its existing
Omnigent label defaults. The web/CLI can set it through the normal session
update surface.

This avoids permanently copying every agent transcript into Google Chat merely
because it ran on the same host, and prevents a shared Omnigent server from
projecting an opted-in session that belongs to another machine.

### 6.2 Personal convenience mode

An optional `host-active` mode mirrors all non-archived sessions that:

- are bound to the configured Omnigent host;
- have been updated within a configured time window;
- are not explicitly labeled `omnigent.google_chat.enabled=false`.

This mode is convenient for a single-user private space, but is not the default.

### 6.3 Creating a thread

For an eligible unmapped session:

1. Fetch its current snapshot and latest durable items.
2. Send one root message as Meta Bot to the configured private space.
3. Derive a deterministic UUIDv5 request ID from
   `"root:<omnigent-session-id>"` so a lost CLI response can be retried without
   creating a second root.
4. Mention the configured user in the root if Phase 0 shows that this is needed
   to generate a phone notification or follow the new thread.
5. Parse the returned message name and thread resource name.
6. Persist the mapping before starting passive mirroring.
7. Reconcile durable items newer than the mapping cursor.

The root message contains only stable orientation data:

```text
[Omnigent] <session title>
Workspace: <short workspace>
Session: <session id>
Status: <current status>

Reply in this thread to message the same agent.
```

Phone notification behavior is not assumed from the existence of the send
flag. Phase 0 must verify that a Meta Bot root reaches the phone, whether the
thread becomes followed, and whether an explicit self-mention is required.

Do not include the system prompt, agent bundle, environment variables, or
credentials.

### 6.4 Detaching

`!detach` in a mapped thread disables further mirroring and input for that
mapping. It does not stop or delete the Omnigent session and does not delete
Google Chat history.

Archiving/deleting an Omnigent session marks the mapping inactive and posts at
most one concise notice.

---

## 7. Omnigent to Google Chat flow

### 7.1 Reconnect-safe stream setup

Omnigent SSE is live-tail, not durable replay. A `SessionMirror` starts as
follows:

1. Open the session SSE stream and buffer incoming events.
2. Page `GET /v1/sessions/{id}/items` from the stored item cursor to the current
   durable head.
3. Emit each eligible durable item through an idempotent Google Chat request ID.
4. Drain buffered SSE, deduplicating durable item IDs already reconciled.
5. Continue processing live events.

On disconnect, repeat the procedure with exponential backoff and jitter. This
closes the snapshot/stream race without requiring server changes.

### 7.2 What is mirrored

Default `concise` mode sends:

- user messages originating outside this Google Chat thread, prefixed with
  their available attribution;
- completed assistant message items;
- session failures and interruption;
- waiting/blocked notifications that require human attention;
- approval-needed notifications without actionable approve/reject controls;
- a short completion/status notice only when no assistant message made the
  outcome clear.

It does not send:

- token deltas;
- reasoning text;
- full tool arguments or output;
- terminal commands or logs;
- file contents or diffs;
- heartbeats, presence, resource churn, or routine running/idle edges.

An optional `status-only` mode sends state changes and no transcript content.
There is deliberately no default `full` mode.

### 7.3 Notification policy

Mirroring a message and notifying the phone are separate decisions. A message
may appear in the session thread without interrupting the user.

The MVP notification policy is:

- mention the configured user for waiting/blocked, approval-needed, and failed
  states because they require human attention;
- make mentions for successful session completion configurable, enabled by
  default for the personal bridge;
- do not mention routine assistant output, mirrored user messages,
  interruption confirmations, or other informational updates.

Use the Meta Bot mention mechanism validated in Phase 0; do not embed a display
name and assume it behaves as a real mention. Mention policy is applied before
chunking, and at most the first chunk of one logical notification contains the
mention. Retries preserve the same content and request ID.

Following a thread may be sufficient for unmentioned bot replies to notify the
phone, but the design does not assume that behavior. Phase 0 verifies it with
the Chat app backgrounded or the phone locked. If unmentioned replies are
silent but mentioned replies notify reliably, the bridge uses mentions only
for the attention classes above. If even a mentioned Meta Bot thread reply does
not produce a push notification, the mobile-notification goal has failed and
the implementation does not proceed on the assumption that Chat will alert the
user.

### 7.4 Durable items are authoritative

`response.output_item.done` and `/items` provide durable messages. Deltas may be
used only to decide that a session is active; they are never copied token by
token to Google Chat.

When a session reaches `session.status: idle` or `failed`, reconcile `/items`
before posting a terminal notice. `response.completed` alone is not a session
terminal because orchestrator agents can complete an intermediate response,
wait for sub-agents, and resume. The Slack integration already captures this
distinction.

### 7.5 Idempotent sends

Every durable Omnigent-derived Google Chat message uses a stable request ID:

```text
UUIDv5(bridge-namespace, "item:<session-id>:<item-id>:<part>")
UUIDv5(bridge-namespace, "status:<session-id>:status-transition-<n>:<status>")
```

The bridge persists `{status, generation}` for each session. Observing the same
status reuses its generation; observing a different status increments it. This
gives ID-less `session.status` events a stable identity across reconnects and
process restarts without suppressing a later run that reaches the same terminal
state. Time and nullable `updated_at` values are not identities.

The SQLite outbound row is created before invoking `meta`. Retrying the same
request ID is safe because Google Chat returns the existing message.

Google Chat send failures never stop or fail the Omnigent agent. They mark the
mirror degraded, retry with bounded backoff, and surface bridge health in logs.

### 7.6 Message length

The sender splits long final answers on paragraph/code-block boundaries using a
conservative configured limit. All chunks use deterministic part-specific
request IDs and remain in the same thread.

The bridge never truncates silently. If output exceeds the configured total
mirror limit, it posts a short notice directing the user to Omnigent rather than
copying an unbounded transcript into Google Chat.

---

## 8. Google Chat to Omnigent flow

### 8.1 Space-level poll

Every poll lists messages from the one configured space using raw JSON,
`--skip-cache`, oldest-first ordering, and pagination. The `created-after`
filter starts before the stored high-water timestamp by a fixed overlap (for
example, two minutes). The poller then:

1. validates the exact configured space;
2. sorts messages by `(create_time, message_name)`;
3. skips every previously claimed message name;
4. routes mapped threads locally;
5. durably records the new high-water tuple only after the page is processed.

Timestamp is an optimization, not identity. A message with an old/equal
timestamp but unseen resource name is still processed. Dedup rows have a
retention comfortably longer than the overlap window.

On startup, the poller begins from the stored cursor. With no cursor, it starts
at bridge installation time by default so enabling the integration does not
replay an entire existing space. An explicit import option may backfill a
bounded interval.

### 8.2 Message acceptance

The bridge accepts a message only if:

- its space exactly matches the configured resource name;
- its thread has an active session mapping;
- its message resource name has not been claimed;
- its actor is human and matches the configured allowed actor identity;
- it is not a bridge prefix/control echo;
- it contains supported, non-empty text within the configured size limit.

Default authorization is self-only. A private space is necessary but not
sufficient authorization.

### 8.3 Echo suppression

Bridge output is sent with `--as-meta-bot`; phone input is authored by the
allowlisted human. Verify actor identity in every fetched message and retain
outbound Google Chat message names in SQLite.

These independent checks prevent a bridge post from becoming a new Omnigent
prompt. The `[Omnigent]` prefix is a tertiary check only.

Meta Bot identity is a hard MVP requirement. If `--as-meta-bot` is unavailable,
unauthorized, or does not produce a distinct non-human sender, startup fails
closed. Do not silently fall back to posting as the same human identity; tracked
message IDs cannot cover the race where a send succeeds but its response is
lost before the ID is recorded.

### 8.4 Input commands

Within a mapped thread:

- normal text -> Omnigent user `message` event;
- `!stop` -> Omnigent `interrupt` event;
- `!status` -> fetch/post current session status without messaging the agent;
- `!detach` -> disable the mapping after confirmation.

Unknown `!` commands are rejected and never forwarded to the agent. Attachments
are rejected with a concise explanation in the MVP.

### 8.5 Submitting a user message

For normal text, POST the same shape used by the Slack integration:

```json
{
  "type": "message",
  "data": {
    "role": "user",
    "content": [{"type": "input_text", "text": "..."}]
  }
}
```

The bridge does not open a turn-specific SSE stream or wait for completion. The
independent `SessionMirror` observes resulting durable items and status.

If the session's host-bound runner is offline, rely first on the server's normal
host relaunch path. If explicit recovery is needed for an unbound session, use
the configured host/workspace. Do not select a random runner from another host,
which the Slack integration currently permits for bot-created sessions.

### 8.6 Ambiguous Omnigent POST

The Sessions message endpoint has no bridge-supplied idempotency key today. A
process crash after Omnigent accepts a phone message but before the bridge marks
it submitted cannot distinguish accepted from lost.

For the personal MVP, choose at-most-once recovery:

1. Insert the Google Chat message receipt as `dispatching` before POST.
2. On success, mark it `submitted`.
3. Retry only a provable pre-delivery failure, such as failure to establish the
   connection to Omnigent. A validated 4xx is a definitive rejection and is not
   retried automatically.
4. Treat read timeout, connection loss after connect, 5xx response, or restart
   with stale `dispatching` as `ambiguous` and do not submit automatically.
5. Post/log an actionable warning; the user may resend the instruction.

This prefers a rare dropped instruction over executing an instruction twice.
A future optional Omnigent `client_event_id` could make submission exactly-once,
but is not required for the first implementation.

---

## 9. Persistence model

Use one SQLite database with WAL mode and a single-process lock.

### 9.1 `session_threads`

| Column | Purpose |
|---|---|
| `omnigent_session_id` | Primary key |
| `space_name` | Exact Google Chat space resource |
| `thread_name` | Unique Google Chat thread resource |
| `root_message_name` | Root message resource |
| `title` | Last displayed title |
| `last_item_position` | Omnigent durable reconciliation cursor |
| `state` | active/detached/archived/error |
| timestamps | creation/update/reconciliation audit |

### 9.2 `gchat_inbound`

| Column | Purpose |
|---|---|
| `message_name` | Primary key; Google Chat durable identity |
| `thread_name` | Mapping lookup |
| `actor_id` | Authorization audit |
| `created_at_google` | Poll ordering/high-water reconciliation |
| `text_sha256` | Detect changed/replayed message content without storing it |
| `state` | claimed/dispatching/submitted/ambiguous/rejected |
| `error` | Sanitized failure reason |
| timestamps | lifecycle audit |

Do not persist the phone message body in this table. It already exists in
Google Chat and, once accepted, in the Omnigent transcript.

### 9.3 `gchat_outbound`

| Column | Purpose |
|---|---|
| `request_id` | Primary key; stable idempotency key |
| `omnigent_session_id` | Owning session |
| `source_kind` | item/status/root/notice |
| `source_id` | Omnigent item/event identity |
| `part_index` | Long-message chunk number |
| `message_name` | Returned Google Chat message identity |
| `state` | pending/sent/failed/suppressed |
| `attempt_count`, `error` | Retry/health information |
| timestamps | lifecycle audit |

### 9.4 `bridge_state`

Stores singleton configuration fingerprints and cursors:

- configured space resource;
- Google Chat reconciliation `(create_time, message_name)` high-water tuple;
- poll interval/backoff state when useful for diagnostics;
- schema version.

If Phase 3 enables Agent Events, add its subscriber/watermark state in a schema
migration. Do not carry unused Iris state in the polling MVP.

Changing the configured space requires an explicit reset/migration; never reuse
thread mappings in a different space silently.

---

## 10. Meta CLI adapter

Add a narrow `MetaGoogleChatClient` rather than scattering subprocess calls.

### 10.1 Process execution

- Use `asyncio.create_subprocess_exec` with an argv list, never `shell=True`.
- Resolve a configured trusted `meta` executable at startup.
- Send authored message text through stdin, not shell interpolation.
- Request JSON/raw JSON for every machine-consumed command.
- Apply explicit startup, execution, and idle timeouts.
- Bound stdout/stderr capture and redact message text from normal logs.
- Record action-level elapsed time for slow and timed-out calls without
  recording authored text.
- Parse JSON strictly and fail loud on missing message/thread identities.
- Include a stable `--caller` where supported.

### 10.2 Sending

Equivalent invocation:

```bash
meta google.chat.message send \
  --space-name='spaces/...' \
  --as-meta-bot \
  --reply-in-thread='spaces/.../threads/...' \
  --request-id='<deterministic-uuid>' \
  --message-prefix='[Omnigent]' \
  --stdin \
  --raw-json
```

Omit `--reply-in-thread` only for the idempotent session root.
The production CLI currently writes a display summary before the final JSON
line even with `--raw-json`; the adapter accepts only a valid whole output or a
valid final JSON line and still fails loud on missing identities.

### 10.3 Reading

Equivalent reconciliation invocation:

```bash
meta google.chat.message list \
  --space-name='spaces/...' \
  --created-after='<cursor time>' \
  --oldest \
  --limit=200 \
  --raw-json \
  --skip-cache
```

`<cursor time>` is the stored timestamp minus the overlap window. Follow page
tokens until the current head, then deduplicate/sort locally. Thread-specific
reads may use `--thread` for diagnostics, but one space-level reconciliation is
preferable to N polls for N sessions.

### 10.4 Poll lifecycle

- Run at most one Google Chat list subprocess at a time.
- Schedule the next poll from completion time so a slow command cannot overlap
  another invocation.
- Use the active/idle intervals from Section 5.2 with small jitter.
- Back off boundedly on CLI/service failures without advancing the cursor.
- Trigger an immediate poll after root/thread output and after recovering from
  an error.
- Emit health when the last successful poll exceeds a configured threshold.

### 10.5 Optional Agent Events optimization

Phase 3 may supervise `meta events.stream tail` and trigger an immediate poll on
`google.chat message.created`. It must not parse event text into agent input or
advance the Google Chat message cursor. Polling remains the authoritative path,
so an event-stream failure affects latency only.

---

## 11. Security and data handling

### 11.1 Space and actor allowlists

- Configure one exact Google Chat `spaces/...` resource.
- Use a private space with minimal membership.
- Accept commands only from the configured actor identity.
- Reject messages forwarded/cross-posted from unknown actors.
- Do not accept a space, actor, Omnigent URL, host, or session ID from message
  text as trusted configuration.

### 11.2 Authentication

The `meta` CLI acts through the user's sanctioned Meta identity. The bridge does
not store Google credentials or OAuth refresh tokens.

Omnigent authentication uses the existing integration configuration
(`X-Forwarded-Email`, session cookie, or the deployment's normal mechanism).
Secrets are provided through the existing user-service environment and never
posted to Google Chat or written to SQLite.

### 11.3 Data minimization

Internal Google Chat is a durable collaboration surface, not ephemeral terminal
output. Even in a private internal space:

- mirror only explicitly selected sessions by default;
- default to concise final messages and actionable status;
- exclude reasoning, tool payloads, logs, commands, diffs, and file contents;
- cap per-message and per-session mirrored output;
- avoid putting sensitive content in process arguments or logs;
- provide `status-only` mode for sensitive work;
- document that detaching stops future copies but does not erase existing Chat
  history.

If a session contains content that should not be copied to Google Chat, do not
opt it in.

### 11.4 Control safety

Phone messages are untrusted agent input even when authored by the owner. They
flow through the normal Omnigent input-policy path. Tool approval remains in
Omnigent so a terse or spoofed chat command cannot approve an execution.

`!stop` is the only destructive control in the MVP and is session-scoped. The
bridge posts confirmation naming the affected session.

---

## 12. Failure and recovery behavior

| Failure | Required behavior |
|---|---|
| Omnigent server unavailable | Keep mappings/cursors, reconnect with backoff |
| Session SSE disconnect | Reopen stream, reconcile `/items`, dedup by item ID |
| Runner offline | Use normal host relaunch; never choose an unrelated host |
| Google Chat send timeout | Retry same request ID |
| Poll overlap returns old messages | Dedup by durable message resource name |
| Poll/list command fails | Do not advance cursor; back off and retry |
| Poll falls behind | Page oldest-first from overlap cursor until current head |
| Meta Bot unavailable/not distinct | Fail startup/input closed; no self-post fallback |
| Unmentioned bot thread replies are silent | Mention only human-attention notifications per Section 7.3 |
| Mentioned bot thread replies are silent | Fail the Phase 0 mobile-notification gate |
| Cached/incomplete Chat read | Use raw JSON with `--skip-cache` |
| Chat message changed after claim | Reject changed hash; do not resubmit |
| Omnigent POST definite failure | Retry boundedly |
| Omnigent POST ambiguous | Mark ambiguous; never automatic resubmit |
| Restart finds stale `dispatching` input | Mark ambiguous and post one idempotent thread warning |
| Unknown actor/space/thread | Reject and audit without contacting Omnigent |
| Mapping missing | Ignore ordinary reply; optionally post safe orientation |
| Session archived/deleted | Disable mapping; do not recreate session |
| SQLite unavailable/corrupt | Fail closed for input; do not run stateless |

Outbound mirroring is best effort and must never affect the running agent.
Inbound control fails closed whenever authorization or dedup state is
unavailable.

---

## 13. Configuration

Example environment:

```text
OMNIGENT_BASE_URL=http://127.0.0.1:6767
# Omit for an explicit local single-user server; otherwise configure the
# deployment's normal header/cookie identity.
# OMNIGENT_AUTH_EMAIL=<user>@meta.com
OMNIGENT_GCHAT_SPACE=spaces/...
OMNIGENT_GCHAT_ALLOWED_ACTOR_ID=<intern-fbid-or-verified-chat-id>
OMNIGENT_GCHAT_META_BOT_ACTOR_ID=<verified-bot-chat-id>
OMNIGENT_GCHAT_MENTION_UNIXNAME=<unixname>
OMNIGENT_GCHAT_HOST_ID=<omnigent-host-id>
OMNIGENT_GCHAT_PHASE0_VALIDATED=false
OMNIGENT_GCHAT_DISCOVERY=label
OMNIGENT_GCHAT_LABEL=omnigent.google_chat.enabled
OMNIGENT_GCHAT_DATABASE=~/.omnigent/google-chat.sqlite3
OMNIGENT_GCHAT_MIRROR_MODE=concise
OMNIGENT_GCHAT_MENTION_ON_COMPLETION=true
OMNIGENT_GCHAT_MENTION_ON_ROOT=true
OMNIGENT_GCHAT_SESSION_LOOKBACK_HOURS=24
OMNIGENT_GCHAT_MAX_MESSAGE_CHARS=12000
OMNIGENT_GCHAT_MAX_SESSION_CHARS=100000
OMNIGENT_GCHAT_ACTIVE_POLL_SECONDS=10
OMNIGENT_GCHAT_IDLE_POLL_SECONDS=30
OMNIGENT_GCHAT_POLL_OVERLAP_SECONDS=120
OMNIGENT_GCHAT_RECENT_ACTIVE_SECONDS=120
META_CLI=/usr/local/bin/meta
LOG_LEVEL=INFO
```

Validate at startup:

- Omnigent is reachable and the authenticated identity can list/read/write the
  selected sessions;
- the configured host exists;
- the Google Chat space resource is exact and accessible;
- Meta Bot can send to the space as a distinct non-human actor (no fallback);
- root and thread-reply notifications reach the configured phone under the
  Phase 0-validated mention/follow rules;
- the human actor identity can be resolved and is a space member;
- the SQLite directory is private and writable;
- only one bridge instance owns the database.

Do not create a new Google Chat space automatically in the MVP. Provision and
review the private space once, then configure its immutable resource name.

---

## 14. Implementation layout

Private deployment layout:

```text
services/omnigent-google-chat/
  README.md
  pyproject.toml
  .env.example
  src/omnigent_google_chat/
    __init__.py
    __main__.py
    app.py
    config.py
    models.py
    store.py
    meta_chat.py
    omnigent.py
    discovery.py
    mirror.py
    inbound.py
    phase_zero.py
    text.py
  tests/
```

The authoritative user unit lives at
`~/dotfiles/systemd/omnigent-google-chat.service`; machine-specific settings
live at `~/.config/omnigent-google-chat.env` and are not source controlled.

The installed user unit writes owner-only diagnostics to
`~/.omnigent/google-chat.log` because user-journal access is unavailable on the
target devserver. It references the host-provisioned x509 certificate directly,
declares the CLI `PATH`, and does not isolate `/tmp`; this lets systemd and
interactive `meta` invocations use the same authentication-cache lifecycle.
The CLI execution timeout remains configurable independently from polling.
Live A/B validation showed that sharing `/tmp` does not remove the periodic
credential-refresh delay: a call killed by a 30-second bound completed
successfully in 59.4 seconds under a 60-second bound. The deployed default is
therefore 90 seconds. Poll state advances only after success, and failures use
short bounded backoff, so increasing the process bound does not weaken dedup or
at-most-once input semantics.

Responsibilities:

- `meta_chat.py`: all `meta` subprocess construction, JSON parsing, sending,
  paginated space reads, and polling/backoff.
- `omnigent.py`: transport-neutral client, SSE parsing, item pagination,
  submission, interrupt, session/host lookup, and runner recovery.
- `discovery.py`: session filtering and mapping lifecycle.
- `mirror.py`: per-session SSE/item reconciliation and output policy.
- `inbound.py`: polled-message validation, commands, dedup, and Omnigent POST.
- `store.py`: the four SQLite tables and atomic state transitions.
- `text.py`: safe formatting, attribution, chunking, and redaction.

Reuse from the Slack integration by extraction or direct adaptation:

- `OmnigentAuth` and HTTP error handling;
- SSE parser;
- assistant/error/terminal extraction;
- host-aware runner launch/wait logic;
- SQLite WAL/event-claim patterns;
- output splitting tests.

Do not reuse the Slack Bolt app, Slack text dialect, or synchronous
`SlackOmnigentService._run_turn` flow.

For the MVP, copying a small transport-neutral helper is acceptable if
extracting a shared package would delay validation. If both integrations remain
maintained, consolidate the Omnigent HTTP/SSE client immediately afterward.

---

## 15. Test strategy

### 15.1 Meta CLI adapter tests

- exact argv construction with no shell;
- message body passed via stdin;
- root and threaded send parsing;
- deterministic request IDs;
- raw list pagination, overlap, tuple cursor, and message-name deduplication;
- adaptive active/idle polling, non-overlap, backoff, and recovery;
- bounded stdout/stderr and timeout behavior;
- no message content in normal logs.

Use a fake `meta` executable in tests. Unit tests must not contact Google Chat.

### 15.2 Store tests

- unique session/thread mapping;
- duplicate inbound message claims across overlapping polls;
- safe tuple high-water advancement only after full-page processing;
- outbound retry with the same request ID;
- stale `dispatching` becomes `ambiguous`, not `pending`;
- configured-space fingerprint mismatch fails closed;
- WAL/restart behavior and single-process lock.

### 15.3 Omnigent/mirror tests

- attach to an existing session without creating one;
- buffer SSE while paginating items;
- reconcile missed items after disconnect;
- item-ID and Google request-ID dedup;
- intermediate `response.completed` does not end an orchestrated turn;
- final assistant messages and failure/waiting notices;
- attention states mention the configured user, completion follows its setting,
  and routine mirrored messages do not mention;
- only the first chunk of a logical attention notification contains a mention;
- no token/reasoning/tool spam;
- Google Chat send failure does not affect Omnigent;
- host-bound runner relaunch stays on the configured host.

### 15.4 Inbound tests

- self-authored human thread reply submits once;
- Meta Bot output never loops back;
- wrong actor, space, or unmapped thread fails closed;
- normal text, `!stop`, `!status`, `!detach`, unknown command;
- active-run reply POSTs immediately without waiting for idle;
- provable pre-connect POST failure retries; 4xx rejects and ambiguous outcomes
  do not;
- timeout/restart is marked ambiguous and not replayed;
- changed message content after claim is rejected;
- attachments and oversized input are rejected.

### 15.5 End-to-end manual tests

1. Start an Omnigent session from CodeCompanion on the configured devserver.
2. Opt it into Google Chat and verify one root thread appears.
3. Generate user/assistant activity from CodeCompanion and verify concise
   ordered thread updates without routine mentions.
4. Reply from the phone and verify the same Omnigent session receives it.
5. Reply while the agent is working and verify Omnigent queue/steer behavior.
6. Use `!status` and `!stop`.
7. Restart only the bridge; verify no duplicate threads or messages.
8. Disconnect SSE during a turn; verify `/items` repair.
9. Fail several poll commands; verify the cursor does not advance and recovery
   catches every message once.
10. Force an Omnigent POST timeout; verify ambiguous input is not replayed.
11. Attempt input from another actor/unmapped thread; verify rejection.
12. Confirm no inbound devserver listener was created.
13. With Chat backgrounded or the phone locked, verify waiting/blocked,
    approval-needed, and failed replies notify through a mention; verify
    completion follows its configured mention policy.

---

## 16. Phase 0 validation gates

Before implementing the full daemon, write a disposable read-only/spike script
and verify the actual production behavior:

1. Send an idempotent Meta Bot root to the private space and capture the exact
   message/thread JSON.
2. With Chat backgrounded or the phone locked, confirm the root creates a push
   notification in the actual phone app.
3. Follow the thread using the intended phone workflow, post an unmentioned
   Meta Bot reply, and determine whether that reply creates a push notification
   with Chat still backgrounded or the phone locked.
4. Post a Meta Bot reply containing a real self-mention and confirm that it
   creates a push notification, even if the unmentioned reply also notified.
   Pin the resulting root-follow and attention-mention behavior in
   configuration and tests.
5. Confirm `--as-meta-bot` works for the user without registration/approval and
   produces a sender distinguishable from the allowlisted human.
6. Reply from the phone, then fetch the reply with one raw uncached space-level
   message list and confirm exact space, thread, message, actor, text, and
   timestamp fields.
7. Repeat a list with a two-minute overlap and confirm message resource-name
   deduplication is sufficient across inclusive/equal timestamps.
8. Retry the same deterministic send request ID and confirm no duplicate root.
9. Measure one `message list --skip-cache` command and confirm a 10-second active
   poll interval is operationally reasonable.

CLI help verifies that the flags exist; only this spike verifies authorization,
phone notification, identity separation, idempotency, and real response shape.
If Meta Bot cannot provide a distinct actor, or if a real mention in a bot
thread reply cannot provide a phone notification, stop rather than falling back
to self-authored output or a bridge that silently depends on manual checking.

---

## 17. Rollout plan

### Phase 0: Google Chat transport spike

Validate the gates above with no Omnigent transcript content. Go/no-go result in
less than one day.

### Phase 1: read-only mirror

Implement configuration, store, explicit session mapping, durable item
reconciliation, Meta Bot root/thread sends, and systemd lifecycle. Verify from
the phone before enabling input.

### Phase 2: phone replies

Add adaptive space polling, allowlisted human reply consumption, immediate
Omnigent message POST, `!status`, `!stop`, and ambiguous-delivery handling.

### Phase 3: automatic discovery and hardening

Add label/host-active discovery, health reporting, output caps,
sensitive-session controls, and failure/restart soak tests. Optionally use
Agent Events only to trigger earlier polls after separately validating its
runtime contract.

The implemented hardening includes durable status-transition generations,
bounded retries for the startup member/identity lookup, an idempotent Chat
warning for inbound delivery left ambiguous by restart, and action-level Meta
CLI latency diagnostics.

### Deferred

- Google Chat session creation commands;
- approval resolution;
- attachments;
- shared spaces/multiple controllers;
- rich cards;
- Agent Events latency optimization;
- common package extraction shared with Slack.

---

## 18. Effort estimate

Assuming Phase 0 passes:

| Milestone | Focused effort |
|---|---:|
| Transport/phone-notification spike | 0.5 day |
| Poll-only read/reply first cut | 1-2 days |
| Discovery, restart, security, and tests | 1-2 days |
| Reliable personal daily driver | 2-4 days total |

This is materially smaller than the Agent Home client-plane bridge because:

- Omnigent remains the runtime and source of truth;
- there is no second agent journal to translate;
- no response ownership rewrite is needed in CodeCompanion;
- no AgentCloud CAT, mTLS, node attachment, Fleet, or ambiguous C5 command
  reconciliation is involved;
- the existing Slack integration already proves most Omnigent-side contracts.

---

## 19. Decision

For the requirement "run agents through Omnigent on a devserver, then check and
message those same agents from a phone," implement the Google Chat bridge and
do not implement the Agent Home bridge.

Revisit Agent Home only if its native UI is itself a requirement and the loss of
Omnigent runtime behavior is acceptable.

---

## 20. References

Omnigent:

- `integrations/slack/README.md`
- `integrations/slack/src/omnigent_slack/omnigent.py`
- `integrations/slack/src/omnigent_slack/service.py`
- `integrations/slack/src/omnigent_slack/store.py`
- `integrations/slack/src/omnigent_slack/dispatcher.py`
- `integrations/slack/tests/`
- `omnigent/server/routes/sessions.py`
- `docs/QUEUE_STEER_DESIGN.md`

Verified Meta CLI surfaces:

- `meta google.chat.message send --help`
- `meta google.chat.message list --help`
- `meta google.chat.message read-batch --help`
- `meta events.stream tail --help`
- `meta events.stream list-sources --source google.chat`
