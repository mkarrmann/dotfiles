# Design doc: fix `<leader>aB*` broker session continuation (cross-machine, no sharp edges)

**Status:** Implemented 2026-07-03 (Phases 0-5). Interactive UX verification pending.
**Author:** mkarrmann (with Claude)
**Date:** 2026-07-03
**Audience:** Future me, or any agent picking this up cold.

---

## TL;DR

The `<leader>aB*` broker mappings in `lua/plugins/codecompanion.lua` were built on a
resume-first mental model that quietly assumes **same broker, same machine**. In
reality my sessions are spread across brokers (`devvm36111`: 371, `devvm20365`: 132,
`MacBook-Pro-*`: ~28 — 531 total on the persistence-server), and:

- `aBc` / `aBl` read the **local WAL only**, so they structurally cannot see ~30% of
  my sessions (anything not captured by the current broker).
- `aBr` (resume by bsid) **silently starts a fresh session** when the bsid belongs to
  another broker — cross-broker `session/load` is rejected by design.
- Resuming a session whose agent has exited (e.g. right after closing a chat) throws
  `agent_not_found` (-32029) instead of recovering.

**The reframe:** `session/load` (resume) is a *same-broker* primitive by design.
`fork_saved_session` is the *only* cross-machine primitive — it reads history from the
**persistence-server** (all brokers) and spawns a fresh local agent.

**Proposal:**
1. Make the pickers **server-backed** (all machines), with graceful fallback to the
   local WAL when the server is unreachable.
2. Add a **routing engine** that classifies each session by `(broker, liveness)` and
   automatically resumes or forks — the user never pre-classifies.
3. **Collapse `aBr`/`aBc`/`aBl` into one smart `aBc` "continue"** command (pick from a
   list *or* paste a bsid), keeping `aBr`/`aBf` as explicit escape hatches.
4. Rework the **popups** so origin, liveness, and the chosen action (resume vs fork)
   are visible *before* selection; add preview + yank; surface auto-routing and
   degraded-mode via toasts.

---

## 1. Background

### 1.1 The broker topology (why local WAL is the wrong source)

The persistence stack is two processes, usually on two hosts:

```
persistence-server (on the Mac)              acp-broker (per devvm)
  • TCP 127.0.0.1:7847                          • UDS acp-broker.sock
  • canonical SQLite DB                         • local WAL mirror:
  • multi-broker: serves ALL brokers,             ~/.local/share/acp-broker/
    attributed by broker_id                       sqlite-persistence/wal.db
                     ▲                            • ships rows up to server,
                     └──── reverse tunnel ────────  reads also delegate to it
                          (nvs-tunnels, -r 7847:7847)
```

- The **server** has every session from every broker. The **local WAL** has only
  *this broker's* captures. Any cwd picker built on the WAL is machine-local by
  construction.
- **Cross-broker resume is not supported. Cross-broker fork is.**
  (`acp-broker/crates/acp-broker/src/canonical/persistence.rs:109`, `:394`.)

### 1.2 What the code does today (verified against `~/repos/acp-broker`)

**Resume ladder** — `client_link.rs:dispatch_load_session` (2006-2069):
1. `try_dispatch_live_join` (2077) — bsid live in registry → rebind + replay.
2. `try_dispatch_plugin_resume` (2136) — look up a `ResumeDescriptor` from the WAL;
   it points at the **original capturing agent** (`descriptor.agent_id`). Line 2157
   requires that agent to still be **live**; if it's gone →
   `BrokerError::AgentNotFound` → wire code **-32029** (`error.rs:378`, `:411`). **It
   does not respawn.**
3. `dispatch_load_forward_to_agent` — forward verbatim.

**Fork** — `sqlite/mod.rs:fork_saved_session` (787-870):
- `load_full_events_for_fork` (142-202) reads the **local WAL first**, then **falls
  back to the persistence-server** (line 190). This is the proof fork is
  cross-machine capable.
- Materializes a native history file for the target agent (`native_fork`), mints a
  new bsid, enqueues a `pending_sessions` row so a follow-up `session/load(new_bsid)`
  resolves, and returns `(new_bsid, resolved_cwd)`.
- `target_cwd` is required for cross-broker fork (source machine's cwd is meaningless
  locally) — `persistence.rs:424`.

### 1.3 The current nvim surface (`lua/plugins/codecompanion.lua`)

| Key | Fn | Source | Cross-machine? | Failure mode |
|---|---|---|---|---|
| `aBr` | `broker_resume_or_fork("resume")` (497) | bsid via `session/load` | ❌ | silently starts fresh |
| `aBf` | `broker_resume_or_fork("fork")` (497) | bsid via `fork_saved_session` | ✅ | ok |
| `aBc` | `broker_continue_cwd` (619) | **local WAL** | ❌ | invisible exclusion |
| `aBl` | `broker_resume_cwd_pick` (631) | **local WAL** | ❌ | invisible exclusion |

Supporting internals:
- `_broker_sessions_for_cwd` (563) — the `sqlite3 wal.db` cwd-prefix query. **The
  thing to replace.**
- `_broker_open_chat_with_session` (391) — opens a chat pre-loaded with a bsid via the
  patched `session/load` path. Reused as-is.
- `_broker_fork_saved_session` (474) — RPC wrapper for fork. Reused as-is.
- `_broker_adapter_for` (544) — picks `dvsc_core_broker` vs `codex_broker` from the
  live agent list.
- `_broker_read_last_bsid` / `_broker_write_last_bsid` (360/370) — last-bsid cache.

UI primitives: `vim.ui.select` / `vim.ui.input`, backed by **snacks.nvim** picker
(widened to width 0.7 in `lua/plugins/overrides.lua:203-211`; notifier timeout 10s at
`:194`).

---

## 2. Goals / Non-goals

### Goals
- `aBc` sees **all** my sessions across all machines (server-backed).
- Selecting any session **just works** — resume when possible, fork when necessary —
  with no silent-fresh and no `agent_not_found`.
- "I only know the bsid" is a first-class path (paste), routed the same way.
- Popups make **origin**, **liveness**, and **chosen action** visible before commit.
- Graceful, *visible* degradation when the persistence-server (Mac tunnel) is down.

### Non-goals
- Broker-side changes to `~/repos/acp-broker` (respawn-on-resume). Tracked as a
  stretch item (§7) but out of scope for v1 — the config-side fallback-to-fork covers
  the same user need without touching shared infra.
- Changing fork/resume wire semantics.
- Multi-select / bulk operations.

---

## 3. Design

### 3.1 Data source: server-first, WAL-fallback

New internal `_broker_list_sessions(opts)` returning a normalized row list, replacing
direct WAL reads in the pickers.

- **Primary (DECIDED — spike 2026-07-03):** `acp-broker-cli history query
  saved-sessions --json` over the broker UDS. Verified cross-broker (all 535 sessions:
  282 `devvm36111` + 120 `devvm20365` + 4 Mac), single call ~0.1s, clean JSON.
  - **Key spike finding:** the list is **already globally recency-ordered**
    (`started_at` DESC) *across brokers* — verified at list positions 0-9 and 40-48
    where local/remote interleave in perfect descending time order. So the picker
    needs **no per-row enrichment to rank** — list order *is* the ranking.
  - Each row provides: `saved_session_id`; `metadata.broker_client_metadata.host`
    (→ origin local/remote); `.cwd` (→ prefix filter); `.dvsc.model`/`.mode`/`.effort`
    (→ label). Sufficient for display + cwd filter + origin classification with zero
    extra calls.
  - **No rebuild needed.** The earlier `sqlite list-brokers` failure is just that one
    method being unimplemented in this binary; `saved-sessions`, `load`, and
    `agent list` all work.
  - Rejected: direct TCP JSON-RPC to `:7847`. Unnecessary — the CLI covers every read
    we need, and avoids a python/socket helper. (Kept only as a theoretical last
    resort if the CLI ever loses these methods.)
- **Fallback:** on CLI error/timeout (broker down / tunnel down), fall back to the
  existing WAL query and set `row.degraded = true` + a top-of-list notice.

**Normalized row schema (from `saved-sessions` alone — no enrichment):**
```
{
  bsid, broker_id (=host), cwd, model, mode, effort,
  origin,      -- "local" | "remote"  (host == THIS_BROKER_ID?)
  degraded,    -- came from WAL fallback
  label,       -- rendered display string (§3.3)
  -- NOT populated at list time (spike: per-row enrichment is too slow —
  -- serial 23s / parallel 13s for 56 cwd sessions):
  agent_id,    -- lazily filled for the PICKED row only (one `load|head -1`)
  live,        -- lazily computed for the PICKED local row only
  started_at,  -- unused for ranking (list is pre-sorted); available via load if ever needed
}
```

**Enrichment strategy (spike-driven):** the list is built from one
`saved-sessions --json` call and is already recency-ranked, so **no per-row
enrichment**. Liveness/agent_id — needed only to split `local·live` from `local·dead`
— is resolved **lazily for the single picked row** via one `history query load <bsid>
| head -1` (~0.7s) at Enter-time. Remote rows never need it (they always fork).

`THIS_BROKER_ID` is read once (from the local WAL `mirrored_sessions.broker_id`, or a
broker CLI/RPC identity call) and cached per session.

### 3.2 Routing engine (pure, unit-testable)

`_broker_route_for(row) -> action` — the heart of the change. Classifies from
`(origin, live)`. For **remote** rows the decision is immediate (no I/O). For **local**
rows it needs the picked row's liveness, which is enriched lazily at Enter-time (§3.1):

| origin | agent live? | action | why |
|---|---|---|---|
| local | yes | `resume` | live-join, cheapest, true continuation |
| local | no | `resume_or_fork` | try `session/load`; on -32029 fall back to fork |
| remote | (n/a) | `fork` | cross-broker resume rejected; fork reads from server |

`_broker_apply(row)` executes the action:
- `resume` → `_broker_open_chat_with_session(_broker_adapter_for(agent_id), bsid)`.
- `fork` → `_broker_fork_saved_session(adapter, bsid)` → open chat with the returned
  `(new_bsid, resolved_cwd)`; toast the fork (§3.4).
- `resume_or_fork` → attempt resume; if `_broker_open_chat_with_session`'s
  `load_session` reports `agent_not_found` (-32029), transparently fork and toast.
  - Requires threading the RPC error out of `try_load` in
    `_broker_open_chat_with_session` (391) — today it only `vim.notify`s. Add an
    `on_error(code)` callback so the router can catch -32029.

### 3.3 Unified `aBc` popup: pick-or-paste, with honest labels

Single `broker_continue` entry point:

1. Gather rows via `_broker_list_sessions{ cwd = getcwd() }` (cwd-prefix scoped by
   default; a keybind toggles to all-cwd — see below).
2. Open a `vim.ui.select` (snacks) with rich, **aligned** labels:

```
● local·live    2h ago   opus-4.8    fbsource            bsid_a1d78873   → resume
○ local·dead    yest     sonnet-4.6  fbsource/www        bsid_5d590f98   → fork
◆ devvm20365    3d ago   opus-4.8    fbsource            bsid_2af4c63b   → fork
◆ MacBook-Pro   5d ago   codex       fbsource            bsid_e8a1...    → fork
```

- **Glyph/origin column:** `●` local-live, `○` local-dead, `◆` remote(+broker name).
- **Relative time** (`2h ago`) — not raw RFC3339.
- **Short bsid** (`bsid_a1d78873`, first segment) — full id is in preview/yank.
- **Trailing `→ action`** so the routing decision is visible before Enter.
- Columns padded to align.

3. **Paste path (DECIDED — spike 2026-07-03):** a **dedicated `<C-x>` action**, not a
   `<CR>` overload. The custom action reads `picker.input.filter.pattern` (the live
   filter text — confirmed readable from a custom action), validates `^bsid_[%x-]+`,
   and routes that exact id (server lookup → classify → apply).
   - **Why not overload `<CR>`:** the spike proved that when the typed text matches no
     row, `confirm` still returns the **first list item** (not nil) — so a
     "filter-as-bsid on Enter" scheme would silently resume the wrong (top) session.
     A dedicated key is unambiguous.
   - Mechanism: `opts.snacks` (from `vim.ui.select`) merges a full picker config
     (`select.lua:73`), so `actions` + `win.input.keys` + `preview` inject cleanly
     without dropping `vim.ui.select`.
   - Also covers bsids for *other cwds* / from pastes/tasks that won't appear in the
     cwd-scoped list.

4. **Preview pane** (snacks preview for the select source): full bsid (copyable),
   `broker_id`, `agent_id`, `started_at`, model, cwd, and the session's **first user
   prompt** + last N turns, so 20 same-cwd sessions are distinguishable.

5. **Picker keybinds (collision-checked against snacks defaults, spike 2026-07-03):**
   - `<CR>` — route + open the highlighted row (resume/fork per label). Default confirm.
   - `<C-x>` — **paste-bsid**: route the typed filter text as an exact bsid. (free)
   - `<C-y>` — yank full bsid of the highlighted row to clipboard. (free)
   - `<A-c>` — toggle cwd-scoped ↔ all-cwd. (free)
   - **Force resume/fork:** use the top-level `aBr`/`aBf` escape-hatch keymaps rather
     than in-picker overrides — `<C-f>`/`<C-r>`/`<C-a>`/`<C-b>`/`<A-f>`/`<A-r>` all
     **collide** with snacks defaults (preview_scroll / select_all / register-insert /
     toggle_follow / toggle_regex), so keep the picker clean and lean on the existing
     top-level keys.

6. Dedicated snacks source layout (`picker.sources.acp_continue`) so it can be taller
   than the generic `select` override.

### 3.4 Toasts & banners (snacks.notifier, 10s)

- **Auto-route surfaced:** when a resume becomes a fork,
  `Forked bsid_2af4… from devvm20365 → bsid_9f… (history replayed)`. Prevents "I hit
  resume but got a fork" from feeling like a bug.
- **Degraded mode:** first list row / header:
  `⚠ persistence-server unreachable — showing THIS machine only (N sessions)`.
- **Empty state:** distinguish "none anywhere" from "none for this cwd" (offer
  `<C-a>` all-cwd or parent-cwd widen).

### 3.5 Keymap surface after the change

| Key | Role |
|---|---|
| **`aBc`** | **Smart continue** — server-backed pick *or* paste bsid; auto-routes. Everyday key. |
| `aBl` | Alias to `aBc` (kept for muscle memory) or removed. |
| `aBr` | Escape hatch: force **resume** by bsid (skip classification). |
| `aBf` | Escape hatch: force **fork** by bsid. |

`aBr`/`aBf` keep the plain `vim.ui.input` bsid entry but gain a **pre-flight confirm**
when the bsid's broker ≠ current (e.g. `aBr` on a remote bsid → "belongs to
devvm20365; resume will start fresh. Fork instead? [Y/n]").

---

## 4. Files touched

- `lua/plugins/codecompanion.lua`
  - New: `_broker_this_broker_id`, `_broker_list_sessions`, `_broker_route_for`,
    `_broker_apply`, `broker_continue`, label/relative-time/align helpers.
  - Modify: `_broker_open_chat_with_session` (add `on_error` for -32029),
    `broker_continue_cwd`/`broker_resume_cwd_pick` (reimplement on new engine or
    fold into `broker_continue`), `broker_resume_or_fork` (add broker-mismatch
    confirm), keymap block (2457-2472).
  - Keep: `_broker_fork_saved_session`, `_broker_adapter_for`, bsid cache.
- `lua/plugins/overrides.lua` — add `picker.sources.acp_continue` layout + preview.
- Possibly a small `lua/lib/acp-broker-sessions.lua` if the list/route/label logic
  grows enough to warrant its own module (keeps it unit-testable, mirrors `lib/`
  convention). **Preferred** — enables `lua/lib/test/` coverage.
- `lua/lib/test/acp-broker-route-spec.lua` — unit tests for `_broker_route_for` and
  label rendering (pure fns).

---

## 5. Testing

- **Unit (pure):** `_broker_route_for` truth table (§3.2); label alignment /
  relative-time / short-bsid rendering; bsid-paste regex.
- **Integration (manual, documented in the doc):**
  - local-live → `aBc` → resume (live-join), no toast.
  - local-dead (close a chat, then `aBc`) → resume_or_fork → fork + toast (the
    -32029 race).
  - remote (`devvm20365` bsid) → `aBc` paste → fork + toast; verify context replayed.
  - `aBr` on remote bsid → confirm prompt appears.
  - server down (drop tunnel) → degraded banner, WAL-only list still opens.
  - `<C-y>` yanks full bsid; preview shows first prompt.

---

## 6. Open questions / decisions

1. ~~**Transport for server queries.**~~ **DECIDED (spike 2026-07-03):**
   `acp-broker-cli history query saved-sessions --json`. Cross-broker, pre-sorted by
   recency, no rebuild needed. TCP fallback rejected as unnecessary. See §3.1.
2. ~~**Paste UX.**~~ **DECIDED (spike 2026-07-03):** dedicated `<C-x>` action reading
   `picker.input.filter.pattern`, not a `<CR>` overload (confirm returns the first row
   on no-match, which would silently resume the wrong session). See §3.3.
3. **Keep `aBl`?** Alias vs remove. *Alias to avoid breaking muscle memory.*
4. **`this_broker_id` source:** WAL row vs a broker identity RPC. *WAL is simplest and
   always local.*
5. **Rebuild `acp-broker-cli`?** The Jun-14 binary already fails `sqlite list-brokers`
   — independent of this work but worth doing.

---

## 7. Implementation notes & deviations (2026-07-03)

Two changes from the design emerged during implementation:

1. **`resume_or_fork` is pre-flight, not catch-on-error.** The doc (§3.2) planned to
   attempt `session/load` and catch `-32029` to fall back to fork. This is **not
   reliably implementable**: codecompanion's `_establish_session`
   (`acp/init.lua:386`) swallows a failed `session/load` by falling through to
   `session/new` and returning success — so a doomed resume silently starts fresh and
   never surfaces the error (the exact bug we're removing). Instead, local+dead is
   detected **before** attempting resume via the pre-flight liveness check in
   `_broker_enrich_pick` (the agent is absent from `agent list`), and we fork directly.
   `route_for` still returns `"resume_or_fork"` for local+dead; `_broker_apply` treats
   it as a direct fork with a toast. No dead `on_error` plumbing was added.

2. **Liveness glyph is lazy, not eager.** The list shows `● local` / `◆ remote` from
   host at list time; live-vs-dead (`●`/`○`) is resolved only for the picked row
   (during enrichment at Enter). Paying a bulk `agent list` up front to show live/dead
   for every local row would also require a per-row `load` to map bsid→agent_id, which
   reintroduces the slow path the transport spike ruled out (23s serial / 13s parallel
   for 56 rows). Lazy keeps the picker instant.

**Files delivered:**
- `lua/lib/acp-broker-sessions.lua` — pure core (parse/classify/filter/route/label).
- `lua/lib/test/acp-broker-sessions-spec.lua` — 40+ assertions, real fixtures.
- `lua/plugins/codecompanion.lua` — I/O adapters, `_broker_apply`,
  `_broker_enrich_and_apply`, `broker_continue`, `_broker_session_preview`, reworked
  `broker_resume_or_fork` (broker-mismatch confirm). Removed dead
  `broker_continue_cwd` / `broker_resume_cwd_pick` / `_broker_sessions_for_cwd`.
- `lua/plugins/overrides.lua` — `acp_continue` snacks source (wide + preview).

**Verified headless:** pure spec green; plugin + overrides load; full config loads
clean; routing decision matrix (local-live→resume, local-dead→fork, remote/mac/
nil-broker/codex→fork) passes against real broker data; labels render correctly.

**NOT verified (needs interactive testing via `<leader>aBc`):** live snacks picker
render, `<C-x>` paste, `<C-y>` yank, `<A-c>` cwd toggle, preview pane, and the actual
resume/fork chat-open + toasts. The snacks APIs used (`picker.input.filter.pattern`,
`picker:current()`, `picker:close()`, `ctx.preview:set_lines/highlight`, source
layout) are checked against the installed snacks source but not driven end-to-end.

## 8. Stretch: broker-side robustness (separate, likely upstream)

The *root* fix for the agent-death race is broker-side: in `try_dispatch_plugin_resume`
(`client_link.rs:2157`), when `descriptor.agent_id` is not live, **respawn/rehydrate**
the agent instead of returning `AgentNotFound`. That would make resume self-healing for
everyone, not just my config, and shrink the config-side `resume_or_fork` fallback to a
pure optimization. Out of scope for v1 (changes shared infra; needs its own review),
but the config-side fallback is designed to become redundant, not conflicting, if this
lands.
