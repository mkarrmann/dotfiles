# Agent Home <-> Omnigent session bridge

**Status:** Proposed design

**Scope:** Use one Claude Code or Codex session concurrently from Agent Home,
Omnigent, and CodeCompanion without changing AgentCloud, Agent Home, or any
`fbcode` target.

**Primary decision:** Implement an Omnigent-owned AgentCloud C5 client. Do not
interpose on the CLI process, shadow `claude` on `PATH`, or emulate a native CLI
protocol.

---

## 1. Summary

AgentCloud already supports the required multi-client topology. Agent Home and
an Omnigent bridge can attach separate WebSocket clients to the same AgentCloud
session. Inputs from either client enter one durable AgentCloud journal; both
clients receive the same semantic session events.

The integration therefore belongs on AgentCloud's **client plane**, not between
the AgentCloud node and the Claude/Codex process:

```text
                                    AgentCloud prod orchestrator
                                  /             |               \
                                 / C5 client    | node protocol  \ C5 client
                                /               |                 \
                     Agent Home phone     AgentCloud node       Omnigent bridge
                                                |                    |
                                                | real CLI           | internal HTTP
                                                v                    v
                                         Claude Code/Codex     omnigent-server
                                                                    |
                                                                    | REST + SSE
                                                                    v
                                                               CodeCompanion
```

The bridge has two placements:

1. An AgentCloud Fleet reconciler in the existing Omnigent host daemon discovers
   phone-created sessions and creates the corresponding Omnigent sessions.
2. A per-session bridge manager in the existing Omnigent runner attaches to one
   AgentCloud session, submits editor input, replays durable history, and mirrors
   AgentCloud events into Omnigent.

This is not a separately supervised sidecar. The host and runner already own
credentials, workspace selection, runner launch, shutdown, and the authenticated
connection to `omnigent-server`.

The current Omnigent `external_*` event APIs are useful building blocks, but are
not a complete reliable bridge contract. The implementation must add:

- durable AgentCloud-to-Omnigent bindings and cursors;
- atomic, idempotent ingestion keyed by AgentCloud durable sequence number;
- explicit external response start/completion/failure/cancellation events;
- response IDs on live deltas;
- durable outbound-input state for ambiguous C5 delivery;
- a response-ownership router in CodeCompanion.

Without those changes the happy path can be demonstrated, but reconnects can
duplicate transcript items and a phone turn can incorrectly complete an editor
request.

---

## 2. Goals

### 2.1 User-visible goals

- A session created from CodeCompanion appears in Agent Home and can be driven
  from the phone.
- A supported session created from Agent Home appears in Omnigent and can be
  resumed in CodeCompanion.
- Phone and editor inputs share the same Claude/Codex context and transcript.
- Output, tool calls, tool results, usage, failures, and interrupts appear in
  Omnigent and CodeCompanion with correct turn ownership.
- Restarting the Omnigent host, runner, or server does not create a second
  AgentCloud session or duplicate durable transcript items.
- The integration opens no inbound devserver port and introduces no public
  tunnel.

### 2.2 Engineering goals

- No changes to AgentCloud, Agent Home, or `fbcode`.
- Use AgentCloud's documented C5 journal as the source of truth.
- Preserve the existing Omnigent REST/SSE contract for ordinary sessions.
- Keep AgentCloud-specific protocol code in focused modules.
- Make uncertain delivery visible. Never blindly retry a non-idempotent C5
  `create` or `input` command.
- Start with Claude Code, but keep the bridge protocol-neutral enough for Codex.

---

## 3. Non-goals

- Showing an `omnigent` harness name in Agent Home. Agent Home will show the
  real AgentCloud harness, `claude_code` or `codex`.
- Replacing AgentCloud's journal or the real CLI's native resume mechanism.
- Making Omnigent policies authoritative for tools executed inside AgentCloud.
  Omnigent can observe those tools, but cannot intercept them before execution.
- Applying the Omnigent agent's system prompt or MCP/tool specification to the
  AgentCloud harness. The real Claude/Codex process uses its AgentCloud and
  workspace configuration; the Omnigent agent supplies integration metadata,
  display defaults, and Omnigent-side access policy only.
- Mirroring AgentCloud child sessions into Omnigent's parent/child tree in the
  first release. Fleet exposes the parent relationship, so this can be added
  later.
- Supporting arbitrary laptops in the first production gate. The initial
  transport is devserver-only, where direct Meta mTLS is available. Proxy or
  x2pagentd transport can follow after separate validation.
- Deleting an AgentCloud session when an Omnigent row is deleted. C5 has no
  client delete command. Omnigent deletion stops mirroring and removes its local
  binding only.

---

## 4. Verified constraints

This section records behavior verified in the current source, rather than
behavior proposed by this design.

### 4.1 AgentCloud client protocol

The production client endpoint is:

```text
wss://agentcloud-orchestrator-prod.playground.x2p.facebook.net/ws/chat?v=1
```

`v=1` is the current protocol version. A version mismatch is rejected with HTTP
426. Client and server envelopes are:

```json
{"sub": 0, "payload": {"cmd": "fleet", "auth": {"cat": {"payload": "..."}}}}
{"sub": 0, "msg": {"type": "fleet", "sessions": []}}
```

The wire types are in
`$FBSOURCE/fbcode/agentcloud/proto/src/lib.rs` (`WsClientMsg`, `WsServerMsg`,
`ClientCmd`, `ServerMsg`, and `WireFrame`).

One WebSocket may attach to only one session. Fleet discovery must use a
separate pre-attach connection. Agent Home and Omnigent may attach to the same
session on separate sockets; both receive the same journal frames.

The root session flow is:

```json
{"sub":0,"payload":{"cmd":"create","auth":{"cat":{"payload":"CAT"}},"workspace":"/repo","title":"omnigent:<omnigent-id>","harness":"claude_code","options":{"model":"...","effort":"..."}}}
{"sub":1,"payload":{"cmd":"attach","auth":{"cat":{"payload":"CAT"}},"session_id":"<agentcloud-id>"}}
{"sub":1,"payload":{"cmd":"attach_node","node_id":"<configured-node>"}}
{"sub":1,"payload":{"cmd":"input","text":"prompt","apply":"end_of_turn"}}
{"sub":1,"payload":{"cmd":"interrupt"}}
{"sub":1,"payload":{"cmd":"page","before":1234,"limit":500}}
```

After `attach`, the server sends `hello` with a durable `boundary`, current
folded state, and live ephemeral values. History is requested backwards with
`page`; pages contain durable records older than `before`, ordered oldest first,
with a maximum of 500 records.

Wire frames are one of:

- `durable`: replayable `SessionEvent`, with a global sequence number;
- `value`: complete current value for a live keyed partial;
- `delta`: increment to a keyed partial;
- `retract`: removal of a keyed partial.

The durable semantic events needed by this bridge include `user_input`,
`input_applied`, `run_started`, `run_finished`, `model_call_started`,
`model_call_settled`, `block`, `tool_intent`, and `tool_result`. These are
defined in `$FBSOURCE/fbcode/agentcloud/session/src/event.rs`.

Run/input ordering is load-bearing: AgentCloud journals `RunStarted` before the
starting `InputApplied` records, and one run may consume several queued inputs.
`InputApplied` names only the `UserInput` sequence; it has no run field. A client
projection opens the run on `RunStarted`, then maps every later applied input to
that open run. Mid-run `after_tool_round` input is steering; `end_of_turn` stays
queued until the next run.

### 4.2 C5 retry semantics

`attach_node` is durable and idempotent. `create` and `input` are not.

`input` has no client-provided idempotency key. A successful input becomes a
durable `UserInput` whose sequence number is its server identity. If the socket
drops after the server commits it but before the bridge sees the echo, the
bridge cannot know delivery from the write result alone. It must reconnect,
page history, and reconcile before deciding whether to retry.

`create` has the same ambiguous-delivery problem. The bridge must use a unique,
deterministic title and reconcile Fleet before retrying.

### 4.3 Node attachment

`create` does not select a node. The client must attach the desired node
immediately. The hosted CLI currently has an approximately 30 second bind
budget. A session with several attached candidate nodes is ambiguous and may
not start.

The integration must use exactly one configured AgentCloud node. It may
idempotently re-attach that node, but must not detach another node selected by
the user. A conflicting or multi-node session is surfaced as an error.

### 4.4 Fleet discovery

`fleet` includes the AgentCloud session ID, title, workspace, harness, parent,
running state, durable head, attached nodes, and timestamps. `list` omits the
harness and is not sufficient for discovery.

The protocol defines `fleet_delta`, but the current production handler should
be treated as snapshot-only. The reconciler must poll snapshots until push
deltas are demonstrated in production.

### 4.5 Authentication

There are two authentication layers:

1. The WSS transport presents Meta identity (direct x509 on a devserver).
2. `create`, `fleet`, `list`, and `attach` carry a CAT. Post-attach commands use
   the principal bound to the socket.

For production the CAT verifier is:

```text
SERVICE_IDENTITY:agentcloud.orchestrator.prod
```

Equivalent `clicat` arguments are:

```text
create-all
--signer_types USER
--verifier_type SERVICE_IDENTITY
--verifier_id agentcloud.orchestrator.prod
--token_timeout_seconds 10800
--request_timeout_milliseconds 10000
--base64_url
--save <temporary-json>
--overwrite
```

The CAT is the `crypto_auth_tokens` value in that JSON. The normal devserver
combined x509 PEM is:

```text
/var/facebook/credentials/$USER/x509/$USER.pem
```

The reference clients also handle laptop/Nest proxy behavior. Reproducing that
behavior in Python is the highest transport risk and is intentionally outside
the initial devserver gate.

### 4.6 Current Omnigent behavior

Omnigent already has a useful split between a thin input injector and a
runner-owned forwarder. `NativeServerHarness` injects a prompt while a separate
forwarder owns output. The AgentCloud bridge follows that ownership model, but
AgentCloud is a remote journal rather than a runner-owned native server.

The current `POST /v1/sessions/{id}/events` external event family can persist
conversation items and publish deltas, usage, interruption, and session status.
It has three gaps for this integration:

1. `external_conversation_item` has no server-side source deduplication.
2. `external_session_status: idle` publishes `session.status`, not
   `response.completed`.
3. `external_output_text_delta` has no response ID, so interleaved ownership
   cannot be represented.

The current native-terminal single-writer path is also coupled to the presence
of an Omnigent terminal. AgentCloud has no Omnigent terminal, so the capability
must be expressed independently as `external_transcript_owner`.

### 4.7 Current CodeCompanion behavior

CodeCompanion opens the Omnigent SSE stream before posting a message. It
completes a foreground request only after `response.completed`,
`response.failed`, `response.cancelled`, `response.error`, or
`session.interrupted`.

Today the session routes every update to either the foreground handler or the
background observer. It does not route by response ID. If a phone run is active
when the editor submits, the phone run's terminal event can complete the editor
request. This is a correctness bug for any multi-writer session and must be
fixed for this design.

---

## 5. Rejected approaches

### 5.1 `PATH` shadow / CLI tee

The previous version of this document proposed installing a wrapper named
`claude` in the managed AgentCloud node's `PATH`, relaying the real CLI's bytes,
and teeing stream-json into Omnigent.

That topology is rejected:

- It observes the node-to-CLI protocol, not the AgentCloud journal. Phone input
  can be seen, but editor input still requires unsafe two-writer stdin
  multiplexing.
- It depends on undocumented `PATH` inheritance through managed launchers.
- It intercepts every Claude session on the node and increases the blast radius
  of a bridge failure.
- It must separately reproduce AgentCloud's semantic translation and resume
  behavior.
- A process reattach can separate the wrapper lifecycle from the client
  lifecycle.
- It cannot provide end-to-end idempotency for Omnigent persistence.

The C5 client plane already exposes the semantic, journaled representation the
tee was trying to reconstruct.

### 5.2 Native protocol emulation

Making Omnigent pretend to be Claude stream-json or Codex app-server couples it
to a private protocol and misrepresents which agent is running. It also loses
AgentCloud's journal semantics. Rejected.

### 5.3 Existing AgentCloud command-line clients

`agentcloudctl` has useful machine-readable commands but currently mints the
wrong verifier form for the production playground. `agentcloud-repl` derives
production auth correctly, but its one-shot completion semantics are unsafe in
a multi-client session and its persistent mode is an interactive TUI.

The reusable Rust and TypeScript clients have `fbcode` visibility restricted to
AgentCloud targets. Depending on them from the Omnigent repository would require
an AgentCloud change. Shelling out to either CLI also fails to provide one
persistent, cursor-aware stream.

### 5.4 Public Omnigent access from the phone

Opening or tunneling a devserver port does not integrate with Agent Home and is
not an acceptable security model. Rejected.

---

## 6. Proposed architecture

### 6.1 Components

```text
omnigent host daemon
  AgentCloudFleetReconciler
    - CAT + mTLS
    - periodic Fleet snapshots
    - phone-first filtering and claiming
    - creates/binds Omnigent session and asks host to launch runner

one omnigent runner per mirrored session
  AgentCloudBridgeManager
    - create/reconcile or attach AgentCloud session
    - one attached C5 WebSocket
    - attach configured node
    - catch up durable history
    - serialize editor input and interrupt commands
    - fold live C5 values/deltas
    - translate frames
    - publish idempotent external-frame batches

omnigent-server
  ExternalBindingStore
    - unique AgentCloud <-> Omnigent binding
    - durable source cursor and live response state
    - durable outbound input records
    - source-sequence receipts

  external-frame ingestion
    - one transaction: dedup, persist items/state, advance cursor
    - after commit: publish standard Omnigent SSE events

CodeCompanion
  Omnigent session reducer/router
    - response-scoped accumulators
    - foreground ownership by origin pending ID and response ID
    - unrelated phone turns go to the background observer
```

### 6.2 Why the host owns discovery

A per-session runner cannot discover a phone-created AgentCloud session because
no Omnigent row or runner exists yet. The host daemon is the correct owner
because it already:

- runs on the machine that has Meta credentials and the AgentCloud node;
- knows its stable Omnigent host ID;
- can validate local workspaces;
- receives requests to launch per-session runners;
- has an existing long-lived lifecycle and shutdown path.

Add `omnigent/host/agentcloud_sync.py` and start it from the host connection
lifecycle in `omnigent/host/connect.py`. Do not add another systemd service.

### 6.3 Why the runner owns the attached session

The attached C5 connection is per Omnigent session and should live and die with
that session's runner. This preserves existing ownership boundaries for runner
authentication, event forwarding, interrupt, idle reaping, and host restart.

Add a built-in `agentcloud-native` harness. The community plugin interface is
not sufficient because it intentionally cannot register native lifecycle
contributions. Add a new integration mode such as `REMOTE_SESSION` rather than
mislabeling it `NATIVE_SERVER`: Omnigent does not start or own the remote server.

The harness capabilities should include:

```text
integration_mode: remote-session
external_transcript_owner: true
resume: warm-reattach
interrupt: true
streaming: true
live_queue: true
handles_tools_internally: true
```

`external_transcript_owner` generalizes the current native-terminal message
bypass: the external journal is the sole durable writer for user and assistant
items, regardless of whether a terminal exists.

C5 `Input` carries text only. The first release must reject image/file-only or
mixed attachment input before creating an outbound row. It must not silently
drop an attachment or render a local user item for content AgentCloud never
received. A later design can upload files through an AgentCloud-supported
mechanism if one is added to the client contract.

---

## 7. Data model

Labels are useful for display and filtering, but are not a transactional cursor
store. Add first-class rows to the conversation database.

### 7.1 `external_session_bindings`

One row per mirrored session:

| Column | Type | Notes |
|---|---|---|
| `workspace_id` | bigint | Tenant key |
| `conversation_id` | UUID | PK/FK to Omnigent conversation |
| `provider` | varchar(32) | `agentcloud` |
| `external_session_id` | varchar(128) | AgentCloud session ID |
| `external_harness` | varchar(32) | `claude_code` or `codex` |
| `node_id` | varchar(256) | Desired AgentCloud node |
| `endpoint_fingerprint` | varchar(128) | Hash/identifier, never credentials |
| `protocol_version` | integer | Initially `1` |
| `last_durable_seq` | bigint | Highest atomically ingested durable seq |
| `last_boundary` | bigint nullable | Latest observed Hello boundary |
| `active_run_seq` | bigint nullable | Current AgentCloud run |
| `active_response_id` | varchar(64) nullable | Omnigent response identity |
| `cumulative_input_tokens` | bigint | Durable usage folded so far |
| `cumulative_output_tokens` | bigint | Durable usage folded so far |
| `status` | varchar(32) | binding/attached/reconnecting/conflict/error |
| `last_error` | text nullable | Sanitized operator-visible reason |
| `created_at`, `updated_at` | timestamp | Audit/reconciliation |

Unique constraints:

```text
(workspace_id, conversation_id)
(workspace_id, provider, external_session_id)
```

Do not reuse `conversation_metadata.external_session_id`. That field is the
underlying CLI's native resume handle in existing integrations and has different
ownership semantics.

Project non-authoritative labels for search/debugging:

```text
omnigent.external_runtime=agentcloud
omnigent.agentcloud.session_id=<id>
omnigent.agentcloud.harness=claude_code|codex
omnigent.agentcloud.node_id=<id>
```

### 7.2 `external_ingest_receipts`

Each accepted durable C5 frame gets a receipt:

| Column | Type | Notes |
|---|---|---|
| binding identity | composite | Same tenant/provider/session identity |
| `source_seq` | bigint | AgentCloud durable seq |
| `payload_sha256` | binary(32) | Detect divergent replay/protocol bugs |
| `event_count` | integer | Number of translated events |
| `publish_payload` | compressed JSON | Stable SSE outbox entries for this frame |
| `published_at` | timestamp nullable | Set after outbox delivery |
| `accepted_at` | timestamp | Audit |

Primary key: `(workspace_id, provider, external_session_id, source_seq)`.

A retry with the same sequence and hash is a no-op. The same sequence with a
different hash is a hard conflict and stops the bridge. This should never occur;
silently accepting it would hide corruption or protocol skew.

`payload_sha256` is computed over canonical JSON (UTF-8, sorted object keys,
fixed separators, no insignificant whitespace), not over the received WebSocket
bytes. The same semantic frame must hash identically after reconnect.

After successful publication and a retention interval, the large
`publish_payload` may be cleared while retaining the sequence, hash, count, and
timestamps for dedup/audit.

Receipts may be compacted below a conservative watermark after the binding
cursor is durable and old enough. Keeping them initially is simpler and makes
recovery auditable.

### 7.3 `external_outbound_inputs`

Each editor-originated message is durable before it is sent to C5:

| Column | Type | Notes |
|---|---|---|
| `conversation_id` | UUID | Owning Omnigent session |
| `pending_id` | varchar(64) | Client/server correlation key, unique |
| `text` | text | Exact C5 text |
| `content` | compressed JSON | Original blocks, including attachments |
| `apply` | varchar(32) | `end_of_turn` |
| `state` | varchar(32) | pending/sending/confirmed/applied/finished/ambiguous/failed |
| `pre_send_seq` | bigint nullable | Highest C5 sequence observed before send |
| `agentcloud_input_seq` | bigint nullable | Bound `UserInput` seq |
| `agentcloud_run_seq` | bigint nullable | Bound `RunStarted` seq |
| `response_id` | varchar(64) nullable | Omnigent response identity |
| `created_by` | varchar(128) nullable | Original actor |
| timestamps/error | mixed | Retry and operator diagnostics |

`pending_id` is supplied by CodeCompanion when available and generated by the
server otherwise. Reusing an existing pending ID with identical content is
idempotent; reusing it with different content is a 409 conflict.

This table replaces the in-memory-only pending input record for AgentCloud
sessions. Existing native terminal integrations may continue using the current
index until separately migrated.

---

## 8. Omnigent internal APIs

These are hidden runner/host APIs, authenticated with existing host/runner
identity. They are not public client endpoints.

### 8.1 Binding claim

```http
PUT /v1/sessions/{conversation_id}/external-bindings/agentcloud
```

```json
{
  "external_session_id": "...",
  "external_harness": "claude_code",
  "node_id": "...",
  "endpoint_fingerprint": "prod-v1",
  "protocol_version": 1
}
```

The operation is idempotent for an identical binding. It returns 409 if either
side is already bound differently. This is the race arbiter between Fleet
reconciliation, editor-first creation, and duplicate host daemons.

### 8.2 Ordered external frame ingestion

```http
POST /v1/sessions/{conversation_id}/external-frames
```

```json
{
  "source": {
    "provider": "agentcloud",
    "session_id": "...",
    "seq": 417,
    "payload_sha256": "..."
  },
  "events": [
    {"type": "external_response_started", "data": {"response_id": "resp_ac_..."}},
    {"type": "external_conversation_item", "data": {"item_type": "message", "response_id": "resp_ac_...", "item_data": {}}}
  ]
}
```

The server transaction must:

1. lock/validate the binding;
2. check the source receipt;
3. reject a sequence lower than the cursor unless it is an identical receipt;
4. persist all durable conversation items and response/binding state;
5. insert the receipt and its publish outbox payload;
6. advance `last_durable_seq`;
7. commit;
8. publish standard SSE events from the outbox in the submitted order;
9. mark the receipt published.

No SSE event is published before commit. Each outbox entry has a stable event ID
derived from `(provider, external session, source seq, event index)`. Server
startup and a lightweight background dispatcher drain unpublished receipts. A
crash after publish but before `published_at` can redeliver an event, so SSE
consumers deduplicate a JSON `event_id` (and may also receive it as the SSE `id`
field). This gives at-least-once live delivery
without duplicating durable items. A database transaction plus an in-memory
publish call alone is insufficient: it can lose an SSE edge between commit and
process death.

If the HTTP reply is lost, retrying the same batch returns success. It does not
append durable items again; it may help schedule the still-unpublished outbox
receipt.

The bridge sends every durable frame, even if translation produces an empty
`events` array. Otherwise ignored AgentCloud events would prevent the cursor
from representing the actual processed boundary.

### 8.3 External response lifecycle

Add ingestion-only events:

- `external_response_started`
- `external_response_completed`
- `external_response_failed`
- `external_response_cancelled`

They publish the normal Responses API events (`response.created`, then exactly
one terminal) and update durable binding state. A started event carries:

```json
{
  "response_id": "resp_ac_<session-hash>_r<run-seq>",
  "origin_pending_ids": ["pending_..."],
  "model": "claude_code"
}
```

`origin_pending_ids` is empty for phone-only runs. It may contain more than one
entry because AgentCloud can consume several queued inputs when it starts one
run. Terminal events must be idempotent by source sequence and carry cumulative
usage when available.

Extend `external_output_text_delta`, reasoning deltas, and tool-output deltas
with an optional `response_id`. The server includes it on the standard SSE
event. Existing producers that omit it retain their current single-response
behavior.

When an external user item persists, extend `session.input.consumed` with the
item's `response_id` and optional `cleared_pending_id`. A phone message has no
cleared pending ID; an editor echo names the exact optimistic input it commits.
Outstanding `external_outbound_inputs` are projected into the session snapshot,
so an AP-server restart does not make an accepted-but-unconfirmed editor message
disappear.

### 8.4 Public message correlation

Extend `SessionEventInput` with optional `client_request_id` for user messages.
CodeCompanion generates a random, process-unique ID before posting:

```json
{
  "type": "message",
  "client_request_id": "cc_<uuid>",
  "data": {
    "role": "user",
    "content": [{"type": "input_text", "text": "..."}]
  }
}
```

For an `external_transcript_owner` session, the server:

1. creates the durable outbound row using this ID as `pending_id`;
2. does not append a user conversation item yet;
3. forwards the message and pending ID to the runner;
4. returns `202 {"queued": true, "pending_id": "cc_<uuid>"}`.

The C5 `UserInput` echo is the sole writer of the durable user item. It carries
the matched pending ID so the exact optimistic bubble is cleared. Phone input,
which has no outbound row, creates a normal external user item with no cleared
pending ID.

---

## 9. Session lifecycle

### 9.1 Editor-first creation

1. CodeCompanion creates an Omnigent session using the `agentcloud-native`
   harness, a concrete Omnigent host/workspace, and `background_updates=true`.
2. The host launches the session runner.
3. The runner asks its bridge manager to ensure a binding.
4. With no binding, the manager sends AgentCloud `create` using:
   - title `omnigent:<omnigent-conversation-id>`;
   - exact absolute workspace;
   - configured remote harness (`claude_code` initially);
   - the session's initial model/effort options.
5. On `created`, the manager claims the binding in Omnigent.
6. It opens a new connection, sends `attach`, validates `hello`, and sends
   `attach_node` if necessary.
7. It catches up history before accepting editor input.
8. The session is now visible to Agent Home through the ordinary AgentCloud
   fleet.

If the create socket drops before `created`, the manager must not create again.
It polls Fleet for the exact deterministic title, workspace, and harness:

- zero matches after the reconciliation window: create may be retried;
- one match: claim and attach it;
- more than one: mark conflict and require operator selection.

### 9.2 Phone-first discovery

The host Fleet reconciler polls every five seconds with jitter. Until production
Fleet deltas are verified, each poll opens a pre-attach WebSocket, sends one
`fleet` command, reads the replacement snapshot, and closes it; a Fleet
subscription cannot be reused as an attached session connection. A production
configuration chooses one discovery policy:

1. `attached-node` (safe default): import supported sessions already attached
   only to the configured node.
2. `workspace-roots`: import supported sessions whose workspace resolves under
   an allowed root and whose attached-node set is empty or exactly the configured
   node. This mode may attach the node and provides the most seamless phone-first
   experience.
3. `title-prefix`: require a configured title prefix in addition to either rule.

Never import a session attached to another node or multiple nodes. Never accept
a workspace based on string prefix alone; resolve/canonicalize it and enforce
path containment on the host.

For an unbound candidate the reconciler:

1. asks `omnigent-server` whether the AgentCloud ID is already bound;
2. creates an Omnigent session on this host with the configured agent,
   `agentcloud-native` harness, workspace, and display labels;
3. atomically claims the binding;
4. asks the normal host machinery to launch the session runner;
5. lets the runner attach and page the full AgentCloud history.

The unique binding constraint makes duplicate polls and duplicate host daemons
converge on one Omnigent row. A losing, newly-created empty row is deleted or
archived by the reconciler.

A title of the exact form `omnigent:<conversation-id>` is an editor-first
reconciliation marker, not a phone-first import request. The reconciler first
looks up that existing Omnigent conversation, validates host/workspace/access,
and attempts to bind it. It never creates a second Omnigent row for such a
session. This also repairs an editor-first create whose `created` reply was lost.

### 9.3 CodeCompanion-originated turn

1. CodeCompanion generates `client_request_id`, opens/reuses SSE, marks that ID
   as its foreground ownership token, and posts the user message.
2. `omnigent-server` durably records an outbound input and forwards it to the
   runner without persisting a transcript item.
3. The runner serializes bridge-originated sends. It records the current C5
   observed sequence, marks the input `sending`, and sends `input` with
   `apply: end_of_turn`.
4. The attached stream returns a durable `UserInput`. The bridge matches it to
   the outstanding outbound row, records its AgentCloud input sequence, and
   ingests the user item with the pending ID. The item's persistence response ID
   may be input-scoped; run lifecycle identity is assigned later.
5. AgentCloud emits `RunStarted` first, followed by one `InputApplied` for every
   queued input consumed into that run. The bridge opens the run projection,
   collects that starting batch, and maps each applied input to the run.
6. The bridge emits `external_response_started` under a run-derived response ID
   with every matched editor pending ID in `origin_pending_ids`.
7. Blocks, tools, and usage are mirrored under that response ID.
8. `RunFinished` emits exactly one external terminal lifecycle event.

### 9.4 Phone-originated turn

1. Agent Home sends input directly to AgentCloud.
2. The bridge receives `UserInput` with no matching outbound row and persists it
   as an external user message.
3. `RunStarted` followed by its starting `InputApplied` batch creates a
   run-derived response ID. A phone-only batch has no origin pending IDs.
4. CodeCompanion routes this response to its background observer, even if an
   editor request is queued.
5. Output and the terminal event are persisted/published normally.

### 9.5 Concurrent input

Use C5 `end_of_turn` for all editor messages. Do not use `interrupt` or
`after_tool_round` as the default; they intentionally steer an active run.

AgentCloud owns the authoritative queue. The bridge may have more than one
outbound input, but sends them in FIFO order and permits only one unconfirmed
bridge send at a time. Phone input can still interleave because it arrives on a
different socket.

There is an irreducible ambiguity when the phone and editor concurrently submit
identical text under the same principal. C5 provides no client input ID. The
bridge can preserve journal order, but cannot prove which identical `UserInput`
came from which client. The implementation must log/metric this case and choose
the oldest outstanding matching editor input; it must not claim stronger
exactly-once origin semantics than C5 exposes.

### 9.6 Interrupt

Interrupt is session-wide in AgentCloud. Stopping from CodeCompanion interrupts
the currently active run, including a phone-originated run. The UI and docs
should present it as a shared-session stop.

On a socket failure after sending interrupt, reconnect and inspect
`hello.state.running` and subsequent `RunFinished` before retrying. A queued
input cannot currently be removed through C5. CodeCompanion should not describe
an already-sent queued message as cancelled.

---

## 10. Response ownership in CodeCompanion

This is required for correctness, not an optional UI improvement.

### 10.1 Reducer changes

Replace the single `current_response_id` accumulator with response-scoped state:

```text
responses[response_id] = {
  text,
  reasoning,
  origin_pending_ids,
  status
}
```

Prefer the explicit `response_id` on every delta/item/lifecycle event. Retain
the current single-current-response fallback only for older Omnigent producers
that omit it.

Maintain a bounded set of stable external `event_id` values. A replayed outbox
event with an already-seen ID is ignored before it touches response text or
terminal state. Durable item reconciliation continues to deduplicate by item ID.

### 10.2 Router changes

The session owns a map from response ID to sink:

```text
response_owners[response_id] = foreground handler | observer
pending_owners[pending_id] = foreground handler
```

Routing rules:

1. `response.created` whose `origin_pending_ids` contains the active editor
   request binds that response ID to the foreground handler.
2. A response with no matching origin is always routed to the observer.
3. Deltas, items, status, and terminal events route by response ID.
4. Only the terminal for the foreground-owned response fires
   `RequestFinished` and detaches that handler.
5. `session.input.consumed` with the foreground pending ID marks the optimistic
   editor message committed and does not render a duplicate.
6. `session.input.consumed` without a matching pending ID is a phone/other-client
   message and always reaches the observer/transcript path, even while a
   foreground handler exists.
7. On stream reconnect, durable `/items` reconciliation remains authoritative;
   partial text is best effort until live-state replay is implemented.

Because CodeCompanion knows `client_request_id` before POST, a fast
`response.created` cannot race ownership establishment. If a run consumes
several editor inputs from different clients, each client whose pending ID is in
the set legitimately owns the same combined response on its own SSE connection.

### 10.3 Session attachment

Set `background_updates=true` for the AgentCloud Omnigent adapter. Phone-first
sessions can still be opened with `/omnigent_resume` initially.

For automatic attachment, make the existing public `CodeCompanion.chat()` API
forward `omnigent_session_id` to `Chat.new`, then add a small discovery command
that filters Omnigent sessions by the AgentCloud labels and workspace. This is
cleaner than creating a chat and immediately calling a private resume path.

---

## 11. AgentCloud event translation

The durable journal is authoritative. Live keyed frames are previews.

| AgentCloud event | Omnigent effect |
|---|---|
| `UserInput` | Persist user item; record input seq; bind/clear pending ID when matched |
| `RunStarted` | Open run projection and begin collecting the starting input batch |
| `InputApplied` | Map the named input seq to the open run (starting input or mid-run steering) |
| `Block(Text)` | Durable assistant message item |
| `Block(Thinking)` | Reasoning item if retained by product policy; otherwise omit from durable UI |
| `Block(ToolUse)` | Durable `function_call` keyed by AgentCloud call ID |
| `ToolIntent` | Execution metadata/fallback function call; dedup against `Block(ToolUse)` by call ID |
| `ToolResult` | Durable `function_call_output` paired by intent/call ID |
| `ModelCallSettled.usage` | Add to bridge cumulative input/output totals; emit cumulative external usage |
| `RunFinished(completed)` | `external_response_completed`, then idle status |
| `RunFinished(interrupted)` | `external_response_cancelled` plus `session.interrupted`, then idle |
| `RunFinished(failed)` | `external_response_failed` and failed session status with sanitized error |
| child link events | Ignore in v1; retain in metrics/logs |
| unknown additive event | Advance cursor with zero translated events; debug metric, do not crash |

Do not emit both `Block(ToolUse)` and `ToolIntent` as separate visible tool
calls. `Block(ToolUse)` is the model transcript record; `ToolIntent` is the
write-ahead execution record. Use call ID to merge them and use `ToolIntent` as
a fallback only if no block exists.

Tool results reference intent sequence numbers. The bridge retains the bounded
intent-seq -> call-ID mapping needed to construct Omnigent output items and can
rebuild it from replayed durables.

`UserInput.principal` is the authoritative AgentCloud actor. When it can be
losslessly represented in Omnigent, use it for `created_by`; for a matched
editor input, also verify it is compatible with the outbound row's actor. Never
trust actor identity supplied by an unauthenticated translated payload.

Response IDs are derived from `RunStarted` sequence numbers. They are
deterministic and shorter than the 64-character DB limit, for example:

```text
resp_ac_<12-char-session-hash>_r<run-seq>
```

User items may use an input-scoped persistence ID until the run is known; UI
ownership always uses the run response ID. Steering input applied to an
already-active run is persisted as user history and associated with that active
response for lifecycle routing; it does not open a second response.

The current AgentCloud implementations journal `RunStarted` before all starting
`InputApplied` records. Immediately before folding `RunStarted`, the bridge's
local projection already has the ordered queue of non-interrupt input IDs. It
snapshots those IDs as the expected starting batch, buffers the un-ingested
`RunStarted` and subsequent frames until every expected `InputApplied` arrives,
then ingests/publishes `response.created` before any model output. A zero-input
run can publish immediately. A missing or different applied set is protocol
skew: reconnect and replay rather than guessing ownership.

One run can consume multiple queued `end_of_turn` and `after_tool_round` inputs.
A later `after_tool_round` `InputApplied` while the run is already producing
output is steering and does not modify the response's original ownership set.
Editor input always uses `end_of_turn`, so it cannot become mid-run steering.

Conversation item IDs remain Omnigent-generated. Source sequence identity lives
in the ingest receipt.

### 11.1 Live streaming

Implement durability first, then enable live streaming before the user-facing
rollout.

For `SessionKey::Block`:

- `value` replaces the bridge's complete local accumulation;
- `delta` folds into that accumulation;
- `retract` removes it;
- a durable `Block` is authoritative and evicts the preview.

The bridge emits only the text suffix not already sent for that response/block.
If a replacement diverges from the prior prefix, stop emitting that preview and
wait for the durable block rather than duplicating or rewriting chat text.

Live frames are not covered by the durable cursor. A reconnect installs
`hello.ephemerals` as replacement state, then resumes new frames. Missing live
deltas affect latency only; the eventual durable block repairs history.

---

## 12. Reconnect and recovery

### 12.1 Attached-session reconnect

For each reconnect:

1. Mint/refresh CAT if necessary.
2. Open WSS and `attach` the known AgentCloud session ID.
3. Receive `hello(boundary, state, ephemerals)`.
4. Validate workspace and attached nodes against the binding.
5. Start buffering live frames.
6. Page backwards from `boundary + 1`, 500 records at a time, until a page
   reaches `last_durable_seq` or `done=true`.
7. Merge recovered durables and buffered live durables by sequence, remove
   duplicates, and process in ascending order.
8. Install `hello.ephemerals` as live replacement state, then fold buffered/new
   live frames.
9. Reconcile outbound inputs before sending another command.

`hello.state.tokens` seeds the cumulative usage baseline after reconnect.
Replayed `ModelCallSettled` events advance usage only when their durable source
sequence is newly accepted, so catch-up cannot bill or display tokens twice.

The bridge's processing loop is single-threaded per binding. It awaits each
external-frame ingestion acknowledgement before advancing locally, preventing a
higher sequence from overtaking a lower one.

### 12.2 Outbound input reconciliation

For an input in `sending` with no bound AgentCloud sequence:

1. Replay every durable through the reconnect boundary.
2. Search `UserInput` records after `pre_send_seq` for the exact text,
   apply mode, and principal, excluding sequences already assigned to another
   outbound row.
3. One match: mark confirmed and continue.
4. No match after complete catch-up: retry once with a new recorded pre-send
   sequence.
5. Multiple matches: mark ambiguous, do not retry, and surface an operator
   error. Blind retry risks executing the prompt twice.

After `UserInput` is confirmed, `RunStarted` and its later `InputApplied` record
complete the normal mapping even if the original sender is gone.

### 12.3 Slow consumer and takeover

AgentCloud closes a subscription whose consumer falls behind. The bridge uses a
bounded receive queue, prioritizes draining into the ordered ingest loop, and
reconnects/page-recovers on overflow or close.

The server pings approximately every five seconds and closes after roughly 30
seconds without inbound traffic/pongs. The WebSocket client must handle ping/
pong automatically and expose keepalive failures in health state.

A fenced/taken-over session may close with a restart-style error. Treat it as a
reconnect, not a new create.

### 12.4 Process restarts

- **Runner restart:** reload binding/outbound rows, attach, replay from cursor.
- **Host restart:** Fleet reconciliation finds existing bindings and ensures
  their runners; it does not create new Omnigent sessions.
- **Omnigent server restart:** DB state survives. SSE clients reconnect and
  hydrate durable items; binding status is projected in the snapshot.
- **AgentCloud orchestrator restart:** reconnect to the same session and replay.
- **AgentCloud session missing/corrupt:** mark binding error. Never implicitly
  create a replacement because that would split the conversation.

### 12.5 Runner reaping and phone-only activity

Phone turns do not create an ordinary Omnigent task, so existing task-only idle
guards cannot determine bridge liveness. `AgentCloudBridgeManager` must report
active work while it is catching up, has an unconfirmed outbound command, or
`hello.state.running`/the folded C5 state shows an active run. Wire that signal
into the runner's existing `has_active_work` decision so the runner is not
reaped during phone work.

An idle, fully caught-up bridge may be reaped. The host Fleet reconciler compares
Fleet `last_seq`/`running` with the persisted binding cursor; if a phone advances
an idle session whose runner is absent, it launches the runner again. The runner
attaches and pages missed durables. This bounds idle resource use while keeping
phone-first wakeup latency to approximately one Fleet poll interval.

---

## 13. Authentication and security

Add `omnigent/agentcloud_auth.py` with an `AgentCloudAuthProvider` protocol and
a `ClicatAuthProvider` implementation.

Requirements:

- cache CAT only in memory;
- refresh five minutes before the three-hour expiry;
- create the `clicat --save` file with mode 0600 in a private temporary
  directory and unlink it immediately after parsing;
- never log CATs, x509 material, full WebSocket headers, or command JSON that
  contains auth;
- apply timeouts to CAT mint and WebSocket handshake;
- use an `ssl.SSLContext` that loads the configured combined PEM;
- validate the production hostname and certificate normally;
- accept no arbitrary endpoint from a session label or client request;
- expose only an endpoint profile selected in trusted host configuration.

The bridge uses outbound WSS and the existing authenticated runner tunnel. It
opens no listening port.

Fleet results belong to the CAT principal. In a multi-user Omnigent deployment,
the host daemon must bind them only to conversations owned by the corresponding
Omnigent identity; the internal binding claim still enforces host/session access
and owner-level authorization. A shared service CAT or cross-user Fleet import
is out of scope.

First-release transport support is explicit:

```text
supported: Linux devserver, direct Meta x509, production or local ac_dev URL
not yet supported: laptop/Nest proxy/x2pagentd transport
```

Fail with an actionable transport error if the production handshake returns
403; do not silently downgrade TLS or route through an unapproved proxy.

---

## 14. Configuration

Host-scoped configuration, not per-session user input:

```toml
[agentcloud]
enabled = true
profile = "prod"
url = "wss://agentcloud-orchestrator-prod.playground.x2p.facebook.net/ws/chat?v=1"
node_id = "<dedicated-node-id>"
default_remote_harness = "claude_code"
omnigent_agent_id = "<agent-id>"
discovery_policy = "attached-node"
workspace_roots = ["/home/user/repos"]
fleet_poll_seconds = 5
```

The endpoint should normally come from `profile`, with the literal URL shown
only for clarity. Validate:

- URL is WSS for production;
- node ID is non-empty;
- agent exists and uses the built-in `agentcloud-native` harness;
- workspace roots are absolute, canonical directories;
- poll interval is below the hosted CLI bind budget when empty-node discovery
  is enabled.

Session model and effort are creation-time AgentCloud options. C5 currently has
no client command to mutate them on a bound root session. Omnigent must not
pretend a later PATCH changed the running AgentCloud harness. For v1, reject a
live model/effort change with a clear 409 or document it as applying only to a
new session.

AgentCloud owns tool permission behavior. `ToolIntent` is already a durable
write-ahead execution record, not an Omnigent pre-execution approval request.
The bridge must not synthesize an actionable Omnigent elicitation after seeing
it. Approval mirroring would require a distinct pre-execution C5 event and
reply command that do not exist in the verified client contract.

---

## 15. Failure behavior

| Failure | Required behavior |
|---|---|
| HTTP 426 | Mark protocol skew; require bridge update |
| CAT mint/verification failure | Keep binding, stop commands, expose auth error |
| WSS 403 | Report unsupported transport/proxy or x509 failure |
| Lost `create` reply | Fleet-reconcile deterministic title before retry |
| Lost `input` echo | Replay and match history; never blind retry |
| Duplicate durable frame | Receipt no-op if hash matches |
| Same seq, different hash | Hard conflict; stop ingestion |
| Omnigent ingest timeout | Retry same seq/hash; server dedups |
| Slow C5 consumer | Reconnect and page from durable cursor |
| Missing configured node | Attach it and wait within bind budget |
| Other/multiple nodes | Conflict; do not detach automatically |
| Phone run while editor request exists | Route by response ownership, not foreground presence |
| Bridge exits mid-run | Runner restart, attach, replay, recover terminal |
| AgentCloud session disappears | Binding error; no implicit replacement |
| Omnigent session deleted | Close bridge/remove local binding; leave AgentCloud session |
| Unknown additive AgentCloud event | Record empty translated batch and metric |

Bridge health should be visible in the session snapshot:

```text
agentcloud_binding_status
agentcloud_last_durable_seq
agentcloud_last_boundary
agentcloud_reconnect_count
agentcloud_last_error
agentcloud_node_status
```

Do not expose CAT or principal credential details.

---

## 16. Implementation map

### 16.1 Omnigent

New focused modules:

- `omnigent/agentcloud_auth.py`: CAT mint/cache and TLS configuration.
- `omnigent/agentcloud_client.py`: typed C5 envelopes, connect/create/fleet/
  attach/page/input/interrupt/attach-node.
- `omnigent/agentcloud_protocol.py`: strict JSON decoding and additive-event
  handling, protocol version constant.
- `omnigent/agentcloud_bridge.py`: per-session state machine, replay, command
  serialization, outbound reconciliation.
- `omnigent/agentcloud_forwarder.py`: durable/live translation and external
  frame batches.
- `omnigent/host/agentcloud_sync.py`: phone-first Fleet reconciliation.
- `omnigent/runtime/harnesses/agentcloud_native.py`: built-in thin harness app
  and input/interrupt bridge.

Existing areas to modify:

- `omnigent/harness_capabilities.py`: `REMOTE_SESSION` and
  `external_transcript_owner`.
- `omnigent/harness_plugins.py`: built-in registration and metadata.
- `omnigent/host/connect.py`: Fleet reconciler lifecycle.
- `omnigent/runner/_entry.py`: bridge manager lifecycle and active-work signal.
- `omnigent/runner/app.py`: initialize/stop the per-session bridge; forward the
  pending ID with message/interrupt.
- `omnigent/server/routes/sessions.py`: binding APIs, ordered external-frame
  ingestion, external lifecycle events, response IDs on deltas, generalized
  transcript-owner dispatch, and response/pending identity on
  `session.input.consumed`.
- conversation DB models/store/migrations: the three tables above and atomic
  ingest methods.
- session schemas/snapshots: bridge health and response-origin fields.

Do not implement reliable ingestion as a series of calls to the current
`external_*` route. The item append and cursor advance must share a database
transaction.

### 16.2 CodeCompanion

Modify:

- `lua/codecompanion/omnigent/session.lua`: generate/pass client request ID,
  response-owner maps, response-scoped routing.
- `lua/codecompanion/omnigent/events.lua`: response-scoped accumulators and
  explicit delta response IDs; retain full input-consumed identity/content.
- `lua/codecompanion/interactions/chat/omnigent/handler.lua`: own only the
  response matching its pending ID.
- `lua/codecompanion/interactions/chat/omnigent/observer.lua`: support unrelated
  response IDs while a foreground handler exists.
- `lua/codecompanion/init.lua`: forward `omnigent_session_id` through the public
  chat API.
- AgentCloud adapter defaults: `background_updates=true`.

---

## 17. Test strategy

### 17.1 Protocol/client unit tests

Use a fake WebSocket server with captured production-shaped fixtures:

- envelope encoding/strict required fields/additive unknown fields;
- create/created, attach/hello, page, input, interrupt, attach-node;
- version mismatch, auth error, malformed frame, close during command;
- ping/pong and reconnect backoff;
- one attached session per connection;
- Fleet snapshot replacement semantics.

### 17.2 Translator tests

Fixture every supported durable event and outcome:

- user input and pending correlation;
- input-applied/run association;
- multi-block text and thinking;
- tool block + intent dedup and tool-result pairing;
- success/failure/cancelled tool results;
- usage accumulation over multiple model calls;
- completed/interrupted/failed runs;
- unknown events advance cursor without visible output.

For live frames test value replacement, delta folding, retract, durable eviction,
reconnect ephemerals, prefix divergence, and no duplicate final text.

### 17.3 Persistence/recovery tests

- crash after item append but before HTTP response: retry is a no-op;
- crash before commit: retry appends once;
- duplicate sequence with same/different hash;
- runner restart with a partially mirrored run;
- pages larger than 500 records;
- live frames arriving while history is paging;
- input socket loss before/after durable echo;
- lost create reply with zero/one/multiple Fleet matches;
- simultaneous Fleet reconcilers and binding uniqueness;
- host restart and session deletion behavior.

### 17.4 CodeCompanion tests

Extend the existing Omnigent handler, observer, and reconnect suites:

- editor turn owns only the response with its pending ID;
- phone run completes while an editor input is queued;
- editor run completes while phone input is queued;
- phone user item renders while a foreground handler exists;
- matched editor input-consumed clears the exact optimistic bubble once;
- interleaved response IDs do not mix text accumulators;
- only the owned terminal fires `RequestFinished`;
- stream reconnect hydrates each durable item once;
- interrupt is presented/handled as session-wide;
- public `CodeCompanion.chat({omnigent_session_id=...})` resumes directly.

### 17.5 Local AgentCloud rig

Use the existing local rig:

```bash
python3 fbcode/agentcloud/scripts/ac_dev.py up \
  -n omnigent-bridge --node omnigent-node --real-model
python3 fbcode/agentcloud/scripts/ac_dev.py env -n omnigent-bridge
```

Test with AgentCloud REPL/CLI as a phone surrogate:

1. Create from CodeCompanion, observe from the second client.
2. Send from CodeCompanion and from the second client.
3. Queue input from both while a run is active.
4. Interrupt from each side.
5. Drop the bridge socket during text, tool use, and terminal delivery.
6. Restart runner, host, and Omnigent server independently.
7. Delay and conflict node attachment.
8. Slow Omnigent ingestion until C5 disconnects; verify page recovery.
9. Create from the second client and verify Fleet discovery/resume.

### 17.6 Production gates

Do not enable automatic discovery until these gates pass in order:

1. **Transport spike:** CAT plus direct mTLS reaches production from the target
   devserver and can Fleet/attach without logging credentials.
2. **Read-only mirror:** attach an existing disposable session and reproduce its
   durable history exactly once in a test sink.
3. **Editor input:** submit one input, reconcile a forced lost echo, and verify it
   executes once.
4. **Multi-client:** phone surrogate and CodeCompanion turns remain correctly
   owned.
5. **Restart soak:** repeated runner/host restarts produce no duplicates or split
   sessions.
6. **Opt-in discovery:** attached-node policy only.
7. **Broader discovery:** workspace policy only after conflict/claim telemetry is
   clean.

---

## 18. Rollout plan

### Phase 0: de-risk transport and C5 decoding

Build the auth/client modules and a read-only diagnostic command. Validate
production CAT verifier, x509, Fleet, attach, Hello, paging, and unknown-event
handling. This phase creates no Omnigent sessions.

### Phase 1: durable editor-first bridge

Add binding/outbound/receipt persistence, ordered durable ingestion, explicit
response lifecycle, CodeCompanion ownership routing, create/reconcile, input,
interrupt, and restart recovery. Validate using complete durable blocks first.

### Phase 2: live streaming

Fold C5 Value/Delta/Retract and emit response-ID-bearing previews. Gate on
durable-final repair tests and reconnect soak.

### Phase 3: phone-first discovery

Enable the host Fleet reconciler with attached-node policy, then optionally
workspace-root policy.

### Phase 4: Codex and child topology

Enable Codex after its event fixtures pass the same contract. Mirror Fleet
parent links into Omnigent sub-agent rows only after root-session correctness is
stable.

---

## 19. Known limitations

- C5 cannot perfectly identify concurrently submitted identical inputs from the
  same principal.
- An AgentCloud interrupt is shared-session, not editor-response scoped.
- AgentCloud tool execution bypasses Omnigent's pre-tool policy enforcement.
- Model/effort mutation after AgentCloud session creation is not supported by
  the current client protocol.
- AgentCloud session deletion is not exposed through C5.
- First release is restricted to direct-mTLS devservers.
- Child-session tree mirroring is deferred.

These limitations should be represented honestly in status and UI. None
requires an AgentCloud server change for the core shared-session workflow.

---

## 20. References

Omnigent:

- `omnigent/native_server_harness.py`
- `omnigent/native_server_transport.py`
- `omnigent/harness_capabilities.py`
- `omnigent/harness_plugins.py`
- `omnigent/runtime/pending_inputs.py`
- `omnigent/server/routes/sessions.py`
- `omnigent/runner/_entry.py`
- `omnigent/host/connect.py`
- `omnigent/_native_post_delivery.py`

CodeCompanion:

- `$CODECOMPANION/lua/codecompanion/omnigent/session.lua`
- `$CODECOMPANION/lua/codecompanion/omnigent/events.lua`
- `$CODECOMPANION/lua/codecompanion/interactions/chat/omnigent/handler.lua`
- `$CODECOMPANION/lua/codecompanion/interactions/chat/omnigent/observer.lua`

AgentCloud source consulted for the verified contract:

- `$FBSOURCE/fbcode/agentcloud/proto/src/lib.rs`
- `$FBSOURCE/fbcode/agentcloud/session/src/event.rs`
- `$FBSOURCE/fbcode/agentcloud/orchestrator/src/chat.rs`
- `$FBSOURCE/fbcode/agentcloud/orchestrator/src/wire.rs`
- `$FBSOURCE/fbcode/agentcloud/harness/src/node_hosted.rs`
- `$FBSOURCE/fbcode/agentcloud/repl/src/wire.rs`
- `$FBSOURCE/fbcode/agentcloud/repl/src/auth.rs`
- `$FBSOURCE/fbcode/agentcloud/cat/lib.rs`
- `$FBSOURCE/fbcode/agentcloud/client-ts/src/client.ts`
- `$FBSOURCE/fbcode/agentcloud/client-ts/src/auth.ts`
- `$FBSOURCE/fbcode/agentcloud/scripts/ac_dev.py`
