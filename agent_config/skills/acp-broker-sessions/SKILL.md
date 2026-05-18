---
name: acp-broker-sessions
description: >-
  Use when fetching info or conversation history for an acp-broker session
  (an id starting with `bsid_`, e.g. `bsid_81326f8a-...`). Covers the
  multi-machine topology (broker on devvm ↔ persistence-server typically on
  the user's Mac, reverse-tunneled), the three ways to read session data
  (CLI, direct TCP JSON-RPC, on-disk broker WAL), and the event schema
  needed to reconstruct prompts and agent responses. Trigger keywords:
  bsid, broker session, conversation history, persistence-server,
  sqlite-persistence-plugin, ACP session events, session replay.
---

# acp-broker sessions

## What `bsid_*` is

A `bsid_<uuid-v4>` is a **`BrokerSessionId`** — minted by an `acp-broker`
when an ACP client opens a session against an agent. It is the durable
key for that session everywhere in the persistence layer.

## Topology — the data is probably not on this machine

The persistence stack is **two processes, often on two hosts**:

```
   ┌─────────────────────────────────────────┐
   │  persistence-server  (~/repos/acp-broker│
   │   crates/persistence-server)            │   ← typically on user's Mac
   │   • TCP listener (default 127.0.0.1:7847)│
   │   • Owns the canonical SQLite DB at     │
   │     $XDG_DATA_HOME/acp-persistence-     │
   │     server/persistence.db (Mac path)    │
   │   • Multi-broker: one DB serves many    │
   │     brokers, attributed by broker_id    │
   └────────────────┬────────────────────────┘
                    │  line-delimited JSON-RPC
                    │  (reverse SSH/ET tunnel:
                    │   devvm:7847 ← mac:7847)
                    │
   ┌────────────────┴────────────────────────┐
   │  acp-broker  + sqlite-persistence-plugin│  ← one per devvm
   │   • UDS at $XDG_RUNTIME_DIR/            │
   │     acp-broker.sock                     │
   │   • Local WAL mirror at                 │
   │     ~/.local/share/acp-broker/          │
   │     sqlite-persistence/wal.db           │
   │   • Ships rows to the server; reads     │
   │     also delegate to the server         │
   └─────────────────────────────────────────┘
```

**Critical implications:**

- A session with `broker_id = devvm36111` is captured by the broker on
  that devvm but its **authoritative copy lives in the persistence-server**
  on the Mac. Pulling data from the wrong devvm's WAL won't have it.
- The persistence-server may not be on `localhost` — it's reached via a
  reverse tunnel set up by the user's `nvs-tunnels` script (see
  `~/dotfiles/bin-macos/nvs-tunnels`, forwards `-r 7847:7847`).
- Cross-broker resume is **not** supported. Cross-broker **fork** is.

## Three ways to read a session — pick by environment

| Path | Source of truth? | Needs local broker? | Needs tunnel? | Use when |
|------|------------------|---------------------|---------------|----------|
| **A. `acp-broker-cli`** via broker UDS | Yes (server-backed) | Yes | Yes (broker uses it) | Default. Cleanest. See [[acp-broker-cli]]. |
| **B. Direct TCP JSON-RPC to port 7847** | Yes | No | Yes | No broker available, or scripting from a non-broker host. |
| **C. On-disk broker WAL** | No — mirror only | No | No | Daemon unreachable; only have THIS broker's captures. |

### Quick reachability check

```bash
# (A) Is there a local broker?
ls -l "${XDG_RUNTIME_DIR:-/tmp}/acp-broker.sock"

# (B) Is the persistence-server port reachable?
timeout 2 bash -c 'echo > /dev/tcp/127.0.0.1/7847' && echo "port 7847 open"

# (C) Is there a local WAL on this host?
ls -lh ~/.local/share/acp-broker/sqlite-persistence/wal.db
```

## Path A — `acp-broker-cli` (preferred)

See the [[acp-broker-cli]] skill for the full command surface. For a
session by `bsid_*`:

```bash
CLI=~/repos/acp-broker/target/release/acp-broker-cli
BSID=bsid_81326f8a-66b8-4c33-9fe6-c146b683705b

# Stream every recorded event for the session as one JSON object per line
$CLI history query session-events --session "$BSID" > /tmp/evs.jsonl

# Equivalent path (different broker code branch)
$CLI history query load "$BSID"

# Enumerate registered brokers known to the server
$CLI sqlite list-brokers
```

Known quirk: `--limit` on `session-events` may not be honored — expect
the full stream and slice with `head`/`jq` instead.

## Path B — Direct TCP JSON-RPC

The wire is **line-delimited JSON-RPC over a single full-duplex TCP
stream**, but the frame shape is **not standard JSON-RPC 2.0**: each
message is a JSON object discriminated by an explicit
`"kind": "request" | "response" | "notification" | "error_response"`
field, and `id` is a `u64`.

The first frame on every connection MUST be
`persistence_server.broker.register`.

```python
import socket, json, uuid
BSID = "bsid_81326f8a-66b8-4c33-9fe6-c146b683705b"
s = socket.create_connection(("127.0.0.1", 7847), timeout=15)
f = s.makefile("rwb", buffering=0)

def call(rid, method, params):
    f.write((json.dumps({"kind":"request","id":rid,"method":method,"params":params})+"\n").encode())
    return json.loads(f.readline().decode())

# Handshake (use a probe id, not the live broker's id)
call(1, "persistence_server.broker.register", {
    "broker_id": "b-cli-probe",
    "hostname":  "probe",
    "uuid":      str(uuid.uuid4()),
    "version":   "1",
})

# Session descriptor (filter clientside; SessionListRequest does not
# support filtering by broker_session_id directly in v1)
r = call(2, "persistence_server.session.list", {"filter": {}})
desc = next(x for x in r["result"]["sessions"] if x["broker_session_id"] == BSID)

# Full event log
events = call(3, "persistence_server.session.load",
              {"broker_session_id": BSID})["result"]["events"]
```

Other useful methods (full list:
`~/repos/acp-broker/crates/persistence-server-protocol/src/methods/`):

| Method | Purpose |
|--------|---------|
| `persistence_server.broker.list` | All brokers ever registered with the server |
| `persistence_server.session.list` | Sessions matching a `SessionFilter` (name, tags, time, broker, metadata key) |
| `persistence_server.session.load` | All events for one `bsid_*`, optional `seq_range` |
| `persistence_server.read_session_events` | Same data but scoped by `(broker_id, broker_session_id)` |
| `persistence_server.session.max_seq` | Highest persisted `seq` for a session |
| `persistence_server.lifecycle.read` | Agent lifecycle records (start/stop/etc.) |

## Path C — On-disk broker WAL (fallback)

`~/.local/share/acp-broker/sqlite-persistence/wal.db` is the broker's
local mirror. **Only contains sessions this broker captured.** Schema:

- `mirrored_sessions(saved_session_id, broker_id, broker_session_id,
  agent_id, name, started_at, ended_at, end_reason, metadata, cwd,
  agent_session_id, ...)`
- `mirrored_events(saved_session_id, seq, ts, direction, originator,
  frame_kind, method, request_id, payload BLOB)`

```bash
DB=~/.local/share/acp-broker/sqlite-persistence/wal.db
BSID=bsid_81326f8a-66b8-4c33-9fe6-c146b683705b

sqlite3 "$DB" "SELECT * FROM mirrored_sessions WHERE broker_session_id='$BSID';"
sqlite3 "$DB" "SELECT direction, frame_kind, method, COUNT(*)
               FROM mirrored_events WHERE saved_session_id='$BSID'
               GROUP BY 1,2,3 ORDER BY 4 DESC;"
```

Treat WAL data as **possibly stale or incomplete** if the server has
ever been unreachable.

## `PersistedEvent` schema (what you get back)

One JSON object per event, in capture order:

| Field | Notes |
|-------|-------|
| `kind` | `"session_event"` (vs lifecycle records) |
| `v` | Schema version |
| `seq` | Monotonic per-session sequence number |
| `ts` | RFC3339 timestamp |
| `agent_id`, `broker_id`, `broker_session_id` | Attribution |
| `direction` | `client_to_agent` or `agent_to_client` |
| `frame_kind` | `request` / `response` / `notification` |
| `method` | ACP method name (`session/new`, `session/prompt`, `session/update`, etc.) |
| `request_id` | JSON-RPC correlation id |
| `payload` | The full ACP frame (already parsed JSON, not a BLOB) |

## Reconstructing conversation history

A session's user-visible conversation is the interleaving of:

- `client_to_agent` `request` `session/prompt` — each user turn
- `agent_to_client` `notification` `session/update` — streaming agent
  output (one event per chunk: text, tool calls, thinking, etc.)
- `agent_to_client` `response` `session/prompt` — final per-turn reply

**User prompts** are arrays of typed content blocks in
`payload.prompt[]`. Each block has `type` ∈ {`text`, `resource_link`,
...}. The last `type=="text"` block is usually the actual user message
(earlier text blocks are commonly CLAUDE.md / attachment context):

```bash
# Breakdown
jq -r '"\(.direction)\t\(.frame_kind)\t\(.method)"' /tmp/evs.jsonl \
  | sort | uniq -c | sort -rn

# Prompts: seq, ts, last text block
jq -r 'select(.method=="session/prompt" and .direction=="client_to_agent")
       | "\(.seq)\t\(.ts)\t\(.payload.prompt | map(select(.type=="text")) | last.text)"' \
  /tmp/evs.jsonl
```

**Agent output** lives in `payload` of `session/update` notifications —
shape varies by update type (`agent_message_chunk`, `tool_call`,
`tool_call_update`, `plan`, ...). To reconstruct streamed text:

```bash
jq -r 'select(.method=="session/update")
       | .payload.update // .payload
       | select(.sessionUpdate=="agent_message_chunk")
       | .content.text // empty' /tmp/evs.jsonl
```

Exact field names live in
`~/repos/acp-broker/crates/acp-broker/` (ACP type defs) — grep there
when a field path is uncertain rather than guessing.

## Session counts can be huge

A single session commonly has **10k–20k+ events**, dominated by
streaming `session/update` notifications. Always filter before
materializing — don't dump 15k events into context.

## Gotchas

- Pick a **distinct `broker_id`** when handshaking directly (e.g.
  `b-cli-probe`). The broker_id is recorded in the `brokers` table;
  reusing the live broker's id won't break things but does pollute its
  `last_seen` timestamp.
- The CLI's `--limit` flag on `history query session-events` may emit
  the full stream regardless. Pipe through `head` if you need bounding.
- `session.list` in v1 has no `broker_session_id` filter — list and
  filter clientside.
- `cwd` on the descriptor reflects where the **session** was opened
  (the client's `session/new` payload), not where the broker was
  started.
