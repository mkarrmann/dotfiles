---
name: omnigent-sessions
description: >-
  Use when inspecting, listing, or debugging an Omnigent session — a
  conversation id starting with `conv_` (e.g.
  `conv_85209e85779b406aa9e5b78b2a0a43c2`) run by Matt's personal Omnigent
  server. Covers the single-hub topology, the localhost proxy gotcha, the
  three ways to read session data (REST API, `omnigent` CLI, on-disk SQLite),
  how to find recent sessions, the session/item schema, and how to diagnose a
  hung or failed turn. Trigger keywords: omnigent, conv_ id, omnigent session,
  recent omnigent sessions, session hung, session failed, idle watchdog,
  dvsc/codex/claude agent session, ~/.omnigent/chat.db, OMNIGENT_URL, 6767.
---

# Omnigent sessions

## What `conv_*` is

A `conv_<32hex>` is an **Omnigent conversation (session) id** — the durable
key for one agent session on Matt's personal Omnigent server. Sessions are
**server-owned and durable**: unlike acp-broker `bsid_*` sessions there is no
fork/resume split and no cross-broker classification to worry about. The whole
history (messages, tool outputs, resource events, errors) lives on the server
and is queryable by id. For the acp-broker `bsid_*` world (a *different*
system), see [[acp-broker-sessions]].

## Topology — one hub, dialed over 127.0.0.1:6767

There is exactly **one always-on Omnigent server** (the "hub"), run as a
systemd `--user` unit (`~/dotfiles/systemd/omnigent-server.service`):

```
omnigent server --host 127.0.0.1 --port 6767 \
    --database-uri sqlite:///~/.omnigent/chat.db \
    --artifact-location ~/.omnigent/artifacts \
    --config ~/dotfiles/omnigent_config/server.yaml
```

- **The DB (`~/.omnigent/chat.db`) exists ONLY on the hub devserver.** The hub
  is `OMNIGENT_PRIMARY_FQDN` in `~/dotfiles/omnigent_config/topology.env`
  (currently `devvm20365.cco0.facebook.com`; standby `devvm36111.ftw0`).
- Every other consumer — the Mac, other devservers, Orchest, CodeCompanion —
  reaches the hub at **`http://127.0.0.1:6767`** through an SSH/ET forward. So
  the REST API is the portable path: it works the same everywhere the forward
  is up, whether or not this is the hub.
- The canonical base URL is **`$OMNIGENT_URL`** (default
  `http://127.0.0.1:6767`). Prefer it over hardcoding the port.

### ⚠️ Localhost proxy gotcha (read this first)

In Matt's shells a corp HTTP proxy is set, and it **intercepts loopback and
returns `403 "127.0.0.1 is private"`**. Every `curl` to the server MUST bypass
it, or you'll misread a healthy server as down:

```bash
curl -s --noproxy '*' http://127.0.0.1:6767/health   # {"status":"ok"}
```

Use `--noproxy '*'` on every call (or `env -u http_proxy -u https_proxy …`).
All examples below assume it.

### Quick reachability check

```bash
BASE="${OMNIGENT_URL:-http://127.0.0.1:6767}"
curl -s --noproxy '*' -m 5 "$BASE/health"           # server up?
ls -lh ~/.omnigent/chat.db 2>/dev/null && echo "on the hub"  # DB local?
```

## Three ways to read a session — pick by environment

| Path | Source of truth? | Works off-hub? | Use when |
|------|------------------|----------------|----------|
| **A. REST API** (`$OMNIGENT_URL`) | Yes | Yes (via forward) | **Default.** Cleanest, portable, JSON. |
| **B. `omnigent` CLI** | Yes (calls REST) | Yes (`--server`) | Exporting a full transcript, or resuming. |
| **C. On-disk SQLite** | Yes | No — **hub only** | REST unreachable AND you're on the hub. |

## Path A — REST API (preferred)

Full surface: `~/repos/omnigent/openapi.json` (`GET /health`,
`GET /v1/agents`, `GET,POST /v1/sessions`, `…/{id}`, `…/{id}/items`,
`…/{id}/labels`, `…/{id}/resources`, `…/{id}/stream`, `…/fork`, etc.).

### Find recent sessions (the common case)

`GET /v1/sessions` returns a cursor-paginated `SessionList`
(`{object,data,first_id,last_id,has_more}`), newest first by default.

```bash
BASE="${OMNIGENT_URL:-http://127.0.0.1:6767}"

# 10 most recent sessions, compact.
curl -s --noproxy '*' "$BASE/v1/sessions?limit=10" \
  | jq -r '.data[] | "\(.id)  \(.status)  \(.agent_name)  \(.workspace // "")  \((.title // "")[0:50])"'
```

Useful query params (defaults in parens):

| Param | Purpose |
|-------|---------|
| `limit` (20) | Page size. |
| `order` (desc) / `sort_by` (created_at) | Sort direction / key. |
| `after` / `before` | Cursor pagination (pass a `conv_` id from `last_id`). |
| `agent_name` / `agent_id` | Filter by harness, e.g. `agent_name=dvsc`. |
| `search_query` | **Full-text over session items** (title + message/tool text). Returns a `search_snippet` per row. |
| `include_archived` (false) | Include archived sessions. |
| `project` | Filter by session project. |

A `SessionListItem` carries: `id`, `status`, `agent_name`/`agent_id`,
`workspace`, `git_branch`, `title`, `created_at`/`updated_at`,
`reasoning_effort`, `runner_online`/`host_online`,
`pending_elicitations_count`, `labels`, `search_snippet`.

```bash
# Find by remembered content across all sessions:
curl -s --noproxy '*' "$BASE/v1/sessions?search_query=presto%20deploy&limit=5"

# Most recent dvsc session id only:
curl -s --noproxy '*' "$BASE/v1/sessions?agent_name=dvsc&limit=1" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"][0]["id"])'
```

### Read one session

```bash
CONV=conv_85209e85779b406aa9e5b78b2a0a43c2

# Full session object, including all items inline:
curl -s --noproxy '*' "$BASE/v1/sessions/$CONV"

# Items only (paginated: {object,data,first_id,last_id,has_more}):
curl -s --noproxy '*' "$BASE/v1/sessions/$CONV/items?limit=100"

# Labels (see "Diagnosing" below):
curl -s --noproxy '*' "$BASE/v1/sessions/$CONV/labels"
```

## Path B — `omnigent` CLI

The published `~/.local/bin/omnigent` (NOT the `~/repos/omnigent` editable
build). Two commands matter for inspection:

```bash
# Export a portable transcript to JSONL (best for a full, offline read):
omnigent session export --id "$CONV" --output /tmp/$CONV.jsonl

# Resume (dispatches by runtime; native-ui sessions land in `omnigent claude`
# etc.). Off-hub, pass --server https://<hub-or-forward>:
omnigent resume "$CONV"
```

## Path C — On-disk SQLite (hub-only fallback)

Only when REST is down **and** you're on the hub. The DB is
`~/.omnigent/chat.db` (SQLAlchemy schema; enums stored as small ints, so it's
less legible than REST — prefer A/B).

- `conversations(id, created_at, updated_at, title, agent_id, workspace,
  git_branch, reasoning_effort, model_override, harness_override,
  parent_conversation_id, archived, session_state BLOB, …)` — note there is
  **no** `status`/`agent_name` column; the API computes those from
  `session_state`/`runner` and `agent_id`.
- `conversation_items(id, conversation_id, response_id, created_at, position,
  type SMALLINT, status SMALLINT, data TEXT /*JSON*/, search_text, created_by)`
- `conversation_labels(...)`; full-text search is backed by
  `conversation_items_fts`.

```bash
sqlite3 -header ~/.omnigent/chat.db \
  "SELECT id, datetime(created_at,'unixepoch') AS created, agent_id, workspace, title
   FROM conversations ORDER BY created_at DESC LIMIT 10;"
```

## Session / item schema (reconstructing a conversation)

A session's `items[]` (or `/items` `data[]`) is the conversation, in order.
Each item has `id`, `type`, `status`, `created_at`, `response_id`, and a
`data` object. Common `type`s:

| `type` | `data` shape | Meaning |
|--------|--------------|---------|
| `message` | `role` ∈ {user, assistant}; `content[]` blocks of `type` `input_text` (user) or `output_text` (assistant); assistant also has `model` | A conversational turn. Reasoning shows up inline in `output_text`. |
| `function_call` | `name`, `arguments`, `call_id` | An Omnigent-observed tool invocation. SDK-vendor-native tools may be absent unless disabled in that harness. |
| `function_call_output` | `call_id`, `output` (a JSON string: `{"data":…, "info":…}`) | Result of a tool call. `info` often carries human-readable status ("moved to background", "was cancelled", "Tool result too large → /tmp/…"). |
| `resource_event` | `event_type` (e.g. `session.resource.created`), `resource` | Terminal/file/env resource lifecycle. |
| `error` | `source`, `code`, `message` | A turn-level failure (see below). |

```bash
# Timeline of a session, one line per item:
curl -s --noproxy '*' "$BASE/v1/sessions/$CONV" | jq -r '
  .items[] | "\(.type)\t\(.status)\t" + (
    if   .type=="message" then .data.role + ": " + ((.data.content // [] | map(.text // "") | add)[0:80])
    elif .type=="error"   then "ERROR " + .data.code + ": " + .data.message
    else (.data | tostring)[0:90] end)'
```

## Diagnosing a hung or failed session

1. **Session-level signal:** check `status` from `/v1/sessions` (`idle`,
   `failed`, `running`) and the labels:

   ```bash
   curl -s --noproxy '*' "$BASE/v1/sessions/$CONV/labels"
   # omnigent.last_task_error_code / omnigent.last_task_error_message carry the
   # most recent turn error; omnigent.ui ∈ {terminal, …}; orchest.nvim_session
   # ties it back to the editor session that launched it.
   ```

2. **Turn-level signal:** scan items for `type == "error"`. The most common
   hang is the **harness idle watchdog**:

   > `RuntimeError: turn exceeded the 240s harness idle watchdog (run_turn
   > emitted no events for 240s; likely a wedged LLM or tool call)`

   This fires when the outer harness receives **no progress events** for the
   configured interval; it does not prove the model or tool is wedged. Check
   the timeline after the error. If SDK Codex keeps making native app-server
   tool calls or later produces an answer, those inner events were invisible
   to the watchdog. Matt's direct Codex agent disables native tools so calls
   travel through persisted Omnigent `function_call` / `function_call_output`
   items; `HARNESS_TURN_TIMEOUT_S` is only the fallback. A genuinely blocked
   background job is the other common case (for example, waiting for every
   long subagent at once); drain completed jobs incrementally.

3. **CWD signal:** a session's stored `workspace` proves what the client asked
   the host to launch, not what an inner SDK subprocess actually used. For
   direct Codex, confirm all three layers when diagnosing checkout drift:
   `OMNIGENT_RUNNER_WORKSPACE` in the runner environment, the Codex app-server
   process's `/proc/<pid>/cwd`, and a persisted `sys_os_shell` result's `cwd`.
   They should all name the same checkout. Do not infer the checkout from an
   agent's project notes when any of these disagree.

## Agents (harnesses)

`GET /v1/agents` lists the registered agents. Two families:

- **SDK / streaming** (`codex`, `claude`, `dvsc`, `debby`, `polly`, …): run the
  vendor model directly and stream output into the transcript. These render in
  the CodeCompanion chat surface and are fully reconstructable from `items`.
- **`*-native-ui`** (`claude-native-ui`, `codex-native-ui`, `cursor-native-ui`,
  …): boot a vendor TUI in a tmux terminal on the runner; their output goes to
  the **terminal resource**, not the message stream, so `items` for these are
  sparse (mostly `resource_event`s). To read those, attach the terminal, don't
  expect a message transcript.

## Gotchas

- **Always `--noproxy '*'`** on curl — the corp proxy 403s loopback.
- **`chat.db` is hub-only.** Off the hub, REST/CLI are your only options; a
  missing DB does not mean the session is gone.
- **Item count can be large** for long sessions — page `/items` with
  `limit`+`after`, or filter, rather than materializing everything.
- `workspace` on a session is where it was launched (the client's cwd), not
  where the hub runs.
- `search_query` searches item *content*, not just titles — good for "which
  session was I doing X in".

## Related

- [[acp-broker-sessions]] — the `bsid_*` acp-broker world (a different
  persistence system; don't confuse `conv_*` with `bsid_*`).
- [[personal-development-setup-and-env]] — how dotfiles wires up services like
  this one.
