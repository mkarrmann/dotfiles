# Workflow: implement `<leader>aB*` broker-continue refactor

**Companion to:** `docs/acp-broker-continue-refactor.md` (the design doc — read it first)
**Status:** Ready to execute
**Author:** mkarrmann (with Claude)
**Date:** 2026-07-03

This is the execution plan for the design doc. It is ordered so every phase is
independently testable and leaves the config in a working state. Each phase has an
explicit **gate** that must pass before moving on. TDD where the logic is pure.

---

## Conventions

**Test runner** (matches existing `lua/lib/test/*-spec.lua`): a module exposing
`M.run()` that `error()`s on failure, invoked headless. Canonical command:

```bash
nvim --headless -u NONE --cmd "set rtp+=$HOME/dotfiles/nvim" \
  -c "lua require('lib.test.acp-broker-sessions-spec').run()" -c "qa!"
```

Exit 0 + no error output = pass. Reuse the `assert_eq` / `assert_list_eq` helper style
from `diff-tab-spec.lua`.

**No commits unless I ask** (per global prefs). Each phase = one logical change I can
review; when I ask to commit, one commit per phase.

**Module layout** (design §4): pure logic in `lua/lib/acp-broker-sessions.lua`
(unit-testable), I/O + UI wiring stays in `lua/plugins/codecompanion.lua`.

**Golden rule:** never regress the existing `aBf` (fork by bsid) — it already works and
is the fallback everything else leans on.

---

## Phase 0 — Safety net & baseline (no behavior change)

**Goal:** lock in current behavior before touching anything.

1. `sl status` clean-check; note the current `codecompanion.lua` line numbers for the
   broker block (design §1.3 references may have drifted).
2. Manually exercise and record current behavior of all four keys (`aBr/aBf/aBc/aBl`)
   so we have a before/after reference: which succeed, which silently fresh, which
   error. Capture the `aBc`-after-close `agent_not_found` repro.
3. Confirm the spike commands still return live data (broker/tunnel up):
   `acp-broker-cli history query saved-sessions --json | jq '.saved_sessions|length'`.

**Gate:** baseline behaviors documented; `saved-sessions` returns >0 cross-broker rows.

---

## Phase 1 — Pure core module + tests (TDD, no wiring)

**Goal:** `lua/lib/acp-broker-sessions.lua` with the pure, I/O-free logic. Nothing calls
it yet. This is the highest-value, lowest-risk phase.

**Write tests first** in `lua/lib/test/acp-broker-sessions-spec.lua`, then implement to
green. Functions (all pure — take data, return data):

1. `M.parse_saved_sessions(json_str) -> rows[]` — parse the `saved-sessions --json`
   payload into normalized rows (design §3.1 schema: bsid, host/broker_id, cwd, model,
   mode, effort). Tolerate missing metadata keys (spike showed keys vary per row).
2. `M.classify_origin(row, this_broker_id) -> "local"|"remote"`.
3. `M.filter_by_cwd(rows, cwd) -> rows[]` — prefix match (cwd == row.cwd or cwd starts
   with row.cwd .. "/"), preserving input order (which is already recency-sorted —
   spike finding; add a test asserting order is preserved).
4. `M.route_for({origin=, live=}) -> "resume"|"resume_or_fork"|"fork"` — the §3.2 truth
   table. Pure; `live` is an input, not looked up here.
5. `M.render_label(row, {live=, action=}) -> string` — the §3.3 aligned label
   (glyph ●/○/◆, relative time, model, cwd, short bsid, `→ action`). Split
   `M.relative_time(ts, now)`, `M.short_bsid(bsid)`, `M.align(...)` as sub-helpers so
   each is independently tested.
6. `M.is_bsid(str) -> bool` — `^bsid_[%x-]+$` validator for the paste path.

**Test cases (minimum):**
- route_for truth table: all 3 rows.
- parse: real captured `saved-sessions` sample (paste a trimmed real payload as a
  fixture string), incl. a row missing `.dvsc.model` and a Mac `host`.
- filter_by_cwd: exact match, subdir match, non-match, order-preservation.
- relative_time: seconds/hours/days/weeks buckets against a fixed `now`.
- short_bsid: `bsid_a1d78873-...` → `bsid_a1d78873`.
- is_bsid: accept real bsid, reject `bsid_`, reject arbitrary text.

**Gate:** `acp-broker-sessions-spec` runs green headless. `codecompanion.lua` untouched
→ nvim still loads, all four keys behave exactly as Phase 0 baseline.

---

## Phase 2 — I/O adapters (thin, mockable)

**Goal:** the impure edges that feed the pure core. Keep them tiny so the untested
surface is minimal.

In `codecompanion.lua` (or a small `_io` section of the lib that takes injected
runners for testability):

1. `_broker_this_broker_id()` — read once from local WAL `mirrored_sessions.broker_id`,
   cached. (design §3.1)
2. `_broker_list_sessions()` — shell `acp-broker-cli history query saved-sessions
   --json`, parse via `M.parse_saved_sessions`. On non-zero exit / timeout → return
   `nil, err` (caller does WAL fallback + `degraded`).
3. `_broker_enrich_pick(bsid) -> {agent_id=, started_at=, live=}` — lazy, single-row:
   `acp-broker-cli history query load <bsid> | head -1`, parse seq-0 event; `live`
   via membership in `agent list` (reuse `_broker_adapter_for`'s agent-list call —
   consider caching it for the turn). Only called for the picked local row (spike:
   per-row enrichment is too slow; ~0.7s for one is fine).
4. WAL fallback path: keep the existing `_broker_sessions_for_cwd` query, mark rows
   `degraded=true`.

**Gate:** ad-hoc `:lua` smoke — `_broker_list_sessions()` returns cross-broker rows;
`_broker_enrich_pick(<local bsid>)` returns correct `live` for a known-live and a
known-dead agent. Fallback returns WAL rows when `acp-broker-cli` is forced to fail
(e.g. bogus `--socket`).

---

## Phase 3 — Router + apply (wire core to actions), behind a new command only

**Goal:** `broker_continue()` end-to-end, but bound to a **temporary throwaway key**
(e.g. `<leader>aBt`) so the existing keys stay untouched until Phase 5. De-risks the
routing before it becomes the default.

1. `_broker_apply(row, enrich)` — execute `M.route_for`:
   - `resume` → `_broker_open_chat_with_session(adapter, bsid)`.
   - `fork` → `_broker_fork_saved_session` → open with `(new_bsid, cwd)` + toast.
   - `resume_or_fork` → resume; on `-32029` fork + toast. **Requires** threading the
     RPC error out of `_broker_open_chat_with_session`'s `try_load` via an
     `on_error(code)` callback (design §3.2) — add it without changing existing
     callers' behavior (default nil callback = today's `vim.notify`).
2. `broker_continue()` — list → filter cwd → basic `vim.ui.select` (plain labels for
   now) → on pick: enrich (if local) → apply.
3. Bind temp key.

**Gate:** with temp key: local-live → resume (live-join, no toast); close-then-continue
→ resume_or_fork → fork + toast (the -32029 race is fixed); remote bsid via a
hard-coded test → fork + toast, context replayed. Existing keys still baseline.

---

## Phase 4 — Rich popup UI (labels, preview, paste, keybinds)

**Goal:** upgrade `broker_continue`'s picker to the design §3.3 UX.

1. Swap plain labels for `M.render_label` (glyph/origin/time/model/cwd/short-bsid/
   `→action`). Note: for **local** rows, liveness (`●` vs `○`) needs agent-list; do one
   **bulk `agent list`** up front (not per-row `load`) and mark local rows live by
   agent_id membership — but `saved-sessions` lacks agent_id. **Resolution:** show
   origin glyph from host at list time (`◆` remote, `●` local-unknown), and only
   resolve live/dead for the *picked* row (label can say `local` → refine to
   `local·live`/`local·dead` after enrich, which happens at Enter anyway). Confirm this
   is acceptable vs. paying one bulk agent-list; decide here.
2. `opts.snacks` injection (spike-verified path): custom `actions` + `win.input.keys`.
3. Paste action `<C-x>` → read `picker.input.filter.pattern` → `M.is_bsid` → route.
4. `<C-y>` yank full bsid; `<A-c>` toggle cwd/all. (all collision-checked free)
5. Preview pane: full bsid + broker/agent/started_at + first user prompt (via
   `history query load <bsid>` first prompt event; remote-safe per spike).
6. Dedicated `picker.sources.acp_continue` layout in `overrides.lua`.
7. Toasts (snacks.notifier): auto-route fork notice, degraded-mode banner, smart
   empty-state (design §3.4).

**Gate:** manual matrix — labels correct for local/remote/dead; `<C-x>` routes a pasted
foreign bsid to fork; `<C-y>` yanks; `<A-c>` toggles scope; preview shows first prompt
for both local and remote; degraded banner appears when tunnel dropped.

---

## Phase 5 — Cut over keymaps + escape hatches

**Goal:** make `aBc` the smart command; wire the confirms.

1. Rebind `<leader>aBc` → `broker_continue`. Remove temp `aBt`.
2. `aBl` → alias of `aBc` (muscle memory) — or delete (decide).
3. `aBr`/`aBf` keep bsid-input but add the **broker-mismatch pre-flight confirm**
   (design §3.5): `aBr` on a remote/foreign bsid → "belongs to devvmXXXXX; resume
   starts fresh. Fork instead? [Y/n]".
4. Update the keymap-block comments (2457-2472) to the new model.

**Gate:** full design §5 test matrix passes. Old silent-fresh and `agent_not_found`
paths are gone. `aBf` unchanged.

---

## Phase 6 — Docs, cleanup, decisions log

1. Flip design doc **Status: Proposed → Implemented (date)**; fold any
   implementation-time decisions back into §6 (e.g. the Phase-4 live-glyph tradeoff).
2. Remove any dead code (old `broker_continue_cwd`/`broker_resume_cwd_pick` if fully
   superseded, or keep as thin wrappers — decide).
3. Confirm no stray debug `vim.notify`s; run the spec once more.
4. README/keymap cheatsheet note if one exists.

**Gate:** spec green; nvim loads clean; design doc marked Implemented.

---

## Stretch (separate, not blocking) — design §7

Broker-side respawn-on-resume in `~/repos/acp-broker`
(`client_link.rs:try_dispatch_plugin_resume` ~2157): when `descriptor.agent_id` is
dead, respawn instead of `AgentNotFound`. Would make `resume_or_fork` a pure
optimization. Own review; likely upstream. Do **not** couple to this workflow.

---

## Risk register

| Risk | Mitigation |
|---|---|
| `acp-broker-cli` version drift breaks a subcommand | Phase 2 fallback to WAL + degraded banner; Phase 0 verifies commands live |
| snacks internal API (`input.filter.pattern`) changes | Spike-verified on current version; isolate in one action fn; pin behavior with a Phase-4 manual check |
| Tunnel/server down mid-use | Degraded mode is a first-class path, not an error |
| Enrichment latency creeps back in | Enforce "lazy, picked-row only"; no per-row `load` in the list path (Phase 4 §1) |
| Regressing `aBf` | Never edit `_broker_fork_saved_session`; golden-rule check each gate |
