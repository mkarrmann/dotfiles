-- ── dvsc-core-acp via acp-broker: per-launch picker state ─────────────────
--
-- The broker-fronted `dvsc_core_broker` adapter (defined alongside
-- `dvsc_core` below) reads `_dvsc.pending` when CodeCompanion spawns it,
-- bakes that JSON into `ACP_BROKER_CLIENT_METADATA_JSON`, and runs
-- `acp-broker-attach-select-tag` which forwards the metadata through
-- the broker via `_meta/broker/connection/set_metadata`. The broker
-- then stamps `_meta.broker.client.metadata` onto the next `session/new`
-- envelope (see `stamp_broker_client_metadata` in
-- acp-broker/crates/acp-broker/src/client_link.rs — `session/new` only,
-- which matches "selection is fixed at session creation"). The
-- dvsc-core-acp wrapper's `extractDvscSelection` (in `agent.ts`) sources
-- `mode`, `model`, and `llm_config` from `_meta.broker.client.metadata.dvsc`
-- and applies them to the per-session `CreateAgentRequest`. The wrapper is
-- a dumb pass-through for `llm_config` — provider-specific shaping of
-- `reasoning_config` happens here, in this picker.
--
-- `_dvsc.pending` and `_dvsc.launch_queue` remain global because the
-- adapter spawn that consumes them is synchronous in the same tab where
-- the picker ran. The per-tab one-chat invariant (see
-- `tab_chat_open_or_toggle` below) prevents a launch when the tab
-- already owns a chat, so the FIFO never accumulates stale entries.
local _dvsc = { pending = nil, launch_queue = {}, by_chat_bufnr = {} }

local DVSC_CACHE_PATH = vim.fn.stdpath("data") .. "/dvsc-acp-last-v2.json"

-- Per-launch state for the direct claude-agent-acp / codex-acp paths
-- (`<leader>aG` → "Claude direct" / "Codex direct"). Mirrors `_dvsc` but with
-- two channels because the direct agents take their knobs differently than the
-- dvsc-core wrapper (which reads everything from `_meta.broker.client.metadata.dvsc`):
--   * `pending_meta` is deep-merged into the next `session/new` `params._meta`.
--     claude-agent-acp reads its thinking budget from
--     `_meta.claudeCode.options.maxThinkingTokens` at session creation
--     (acp-agent.js) — thinking is NOT a live config option, so it can only be
--     set here. Consumed once by the patched `Connection:send_rpc_request`. The
--     broker preserves client `_meta` siblings when it stamps its own
--     `_meta.broker.client.metadata`, so the merged object reaches the agent.
--   * `pending_apply` carries `{ provider, model, effort? }` applied
--     post-establish via live config options (model for both agents; reasoning
--     effort for codex, which exposes it as a live config option).
-- Single-flight, same per-tab one-chat assumption as `_dvsc.pending`.
local _direct = { pending_meta = nil, pending_apply = nil }

local DIRECT_CACHE_PATH = vim.fn.stdpath("data") .. "/direct-acp-last.json"

-- Claude thinking effort → `maxThinkingTokens`. claude-agent-acp reads this
-- integer at session/new (acp-agent.js:922); there is no per-level enum. The
-- budgets mirror Claude Code's built-in think levels. Keyed lowercase to match
-- `EFFORT_OPTIONS_BY_KIND.anthropic_adaptive`.
local CLAUDE_EFFORT_TOKENS = {
  low = 4096,
  medium = 10000,
  high = 24000,
  xhigh = 31999,
}

-- Direct broker adapters that support the model/effort picker, mapped to the
-- DVSC_MODELS provider whose models they can run.
local DIRECT_ADAPTERS = {
  claude_broker = { provider = "anthropic" },
  codex_broker  = { provider = "openai" },
}

local DVSC_MODES = { "native", "claude", "codex", "metacode" }

-- Canonical model catalog. Mirrors Configerator
-- `devmate_vscode/model/model_config.cconf` (v25 as of 2026-06-04). Refresh
-- by re-reading `configerator/source/devmate_vscode/model/model_config.cconf`
-- or by querying `GET /models` on a running dvsc-core (note: the HTTP
-- endpoint strips `supports_adaptive_thinking` — only the .cconf has it).
--
-- Per-harness applicability and `reasoning_config` shape are derived from
-- `provider` and (for Anthropic) `adaptive`, mirroring `getModelsForAgent`
-- and `getDevmateLLMConfig` in dm-core. Each model is also gated by an
-- availability gatekeeper server-side; what's actually usable depends on
-- which gates this user is in.
--
-- IMPORTANT: this list must only contain models the running dm-core actually
-- has in its loaded snapshot. dm-core's `getDevmateLLMConfig` silently
-- *falls back to the default model* (claude-opus-4.6) when an unknown
-- modelId is requested (config.ts:108) — and the wrapper happily forwards
-- the picker's provider-shaped `reasoning_config` (e.g. OpenAI's
-- `{effort: "HIGH"}`) into that wrong-provider config, which the LLM
-- gateway returns empty for, surfacing as `stopReason="refusal"` after a
-- ~60s hang. Models removed from the configerator-source list because they
-- aren't in dm-core's loaded `Loaded model config version 25` snapshot for
-- this user (verify via `[INFO] Registered N dynamic GKs` in
-- `/tmp/dvsc-core-acp-*.log` — missing `devmate_<model>` GK = absent):
--   - gemini-3-flash  (no devmate_gemini_3_flash GK)
--   - metabrain-dogfooding
-- TODO: replace this hardcoded list with a one-time HTTP fetch of dm-core's
-- `GET /models` endpoint when the picker opens, so drift between this file
-- and the user's actual entitlement can't reintroduce the silent-refusal
-- failure mode.
local DVSC_MODELS = {
  -- Anthropic
  { id = "claude-opus-4.8",        provider = "anthropic", adaptive = true  },
  { id = "claude-opus-4.7-long",   provider = "anthropic", adaptive = true  },
  { id = "claude-opus-4.6",        provider = "anthropic", adaptive = true  },
  { id = "claude-opus-4.6-long",   provider = "anthropic", adaptive = true  },
  { id = "claude-sonnet-4.6",      provider = "anthropic", adaptive = true  },
  { id = "claude-sonnet-4.6-long", provider = "anthropic", adaptive = true  },
  { id = "claude-haiku-4.5",       provider = "anthropic", adaptive = false },
  { id = "claude-haiku-4.5-long",  provider = "anthropic", adaptive = false },
  -- OpenAI
  { id = "gpt-5-5",       provider = "openai" },
  { id = "gpt-5-4",       provider = "openai" },
  { id = "gpt-5-3-codex", provider = "openai" },
  -- Google (Native only — `getModelsForAgent` has no Google-specific harness)
  { id = "gemini-3-1-pro", provider = "google" },
  -- Meta
  { id = "avocado-tester", provider = "meta" },
}

local function _dvsc_lookup_model(model_id)
  for _, m in ipairs(DVSC_MODELS) do
    if m.id == model_id then return m end
  end
  return nil
end

-- Mirror of `getModelsForAgent` (xplat/vscode/modules/dm-core/src/shared/
-- types/agent-events.ts:117). Native gets all models; Claude gets Anthropic;
-- Codex gets OpenAI; MetaCode gets Meta.
local function _models_for_mode(mode)
  local out = {}
  for _, m in ipairs(DVSC_MODELS) do
    if mode == "native"
        or (mode == "claude"   and m.provider == "anthropic")
        or (mode == "codex"    and m.provider == "openai")
        or (mode == "metacode" and m.provider == "meta") then
      table.insert(out, m.id)
    end
  end
  return out
end

-- Models runnable by a direct broker agent, scoped to one provider. Used by the
-- direct (`claude_broker` / `codex_broker`) picker, which has no harness dimension.
local function _models_for_provider(provider)
  local out = {}
  for _, m in ipairs(DVSC_MODELS) do
    if m.provider == provider then
      table.insert(out, m.id)
    end
  end
  return out
end

-- `reasoning_config` is a discriminated union shaped by provider in dm-core
-- (xplat/vscode/modules/dm-core/src/shared/types/llm-types.ts). The picker
-- emits the right shape literally — the wrapper just forwards.
--
--   openai             → { effort: "HIGH" }                       (uppercase)
--   google             → { effort: "HIGH" }, server clamps XHIGH→HIGH
--   anthropic_adaptive → { anthropic_effort: { effort: "high" } } (lowercase)
--
-- Non-adaptive Anthropic (Haiku 4.5) and Meta models have no effort knob —
-- the picker skips the prompt and the wrapper sends no `llm_config`,
-- letting dm-core's resolved defaults (e.g. `thinking_budget_tokens=4096`
-- for Haiku) stand.
-- Order matters: `high` first (marked as default in the picker label), then
-- the remaining levels low → medium → xhigh.
local EFFORT_OPTIONS_BY_KIND = {
  openai = { "HIGH", "LOW", "MEDIUM", "XHIGH" },
  google = { "HIGH", "LOW", "MEDIUM" },
  anthropic_adaptive = { "high", "low", "medium", "xhigh" },
}

local function _dvsc_reasoning_kind(model)
  if not model then return nil end
  local entry = _dvsc_lookup_model(model)
  if entry then
    if entry.provider == "openai" then return "openai" end
    if entry.provider == "google" then return "google" end
    if entry.provider == "anthropic" and entry.adaptive then return "anthropic_adaptive" end
    return nil
  end
  -- Fallback: prefix-based detection for catalog entries this picker hasn't
  -- been refreshed with yet. Errs on the side of offering an effort prompt
  -- (worst case the server ignores or shallow-merge-clobbers); a missing
  -- catalog entry is a more pressing fix than a stale prompt.
  local lower = model:lower()
  if lower:match("^gpt%-")    then return "openai" end
  if lower:match("^gemini%-") then return "google" end
  return nil
end

local function _dvsc_build_reasoning_config(model, effort)
  local kind = _dvsc_reasoning_kind(model)
  if not kind or not effort then return nil end
  if kind == "anthropic_adaptive" then
    return { anthropic_effort = { effort = effort } }
  end
  return { effort = effort }
end

local function _dvsc_build_llm_config(model, effort)
  local rc = _dvsc_build_reasoning_config(model, effort)
  if not rc then return nil end
  return { model_params = { reasoning_config = rc } }
end

local function _dvsc_read_cache()
  local f = io.open(DVSC_CACHE_PATH, "r")
  if not f then return {} end
  local body = f:read("*a")
  f:close()
  local ok, t = pcall(vim.fn.json_decode, body)
  return (ok and type(t) == "table") and t or {}
end

local function _dvsc_write_cache(t)
  local f = io.open(DVSC_CACHE_PATH, "w")
  if not f then return end
  f:write(vim.fn.json_encode(t))
  f:close()
end

-- Direct-path cache is a dict keyed by provider ("anthropic" / "openai") so a
-- claude selection doesn't clobber a codex one. Each slot is `{ model, effort? }`.
local function _direct_read_cache()
  local f = io.open(DIRECT_CACHE_PATH, "r")
  if not f then return {} end
  local body = f:read("*a")
  f:close()
  local ok, t = pcall(vim.fn.json_decode, body)
  return (ok and type(t) == "table") and t or {}
end

local function _direct_write_cache(t)
  local f = io.open(DIRECT_CACHE_PATH, "w")
  if not f then return end
  f:write(vim.fn.json_encode(t))
  f:close()
end

local function _dvsc_pick(items, prompt, cb)
  vim.ui.select(items, { prompt = prompt }, function(choice)
    if choice ~= nil then cb(choice) end
  end)
end

-- One-chat-per-tab invariant. Mirrors `claude-per-tab-terminal.lua`:
-- each tabpage owns at most one CodeCompanion chat, stamped on creation
-- via the `CodeCompanionChatOpened` autocmd below as
-- `vim.t.codecompanion_chat_bufnr` and `vim.b[chat_bufnr].cc_tab_owner`.
-- The chat is created in the current tab and never moves.
-- Per-connection client identity stamped onto every broker session via
-- `_meta.broker.client.metadata` (see acp-broker docs/SPEC.md §13.2).
-- The persistence plugin captures the whole object verbatim into
-- `sessions.metadata.broker_client_metadata` (capture.rs:659), so adding
-- keys here is forward-compat — no broker change required to persist
-- additional fields. `extra` lets adapter-specific selectors (e.g. the
-- dvsc-core picker's `{ dvsc = sel }`) merge into the same object.
local function build_client_metadata(extra)
  local md = {
    nvim_session    = vim.env.NVS_SESSION_NAME or "ad-hoc",
    host            = vim.env.NVS_HOST or vim.fn.hostname(),
    cwd             = vim.env.NVS_WORKDIR or vim.fn.getcwd(),
    nvim_pid        = vim.fn.getpid(),
    nvim_tab_handle = vim.api.nvim_get_current_tabpage(),
    tab_name        = vim.t.tab_name,
  }
  if extra then
    for k, v in pairs(extra) do md[k] = v end
  end
  return vim.json.encode(md)
end

-- Keep CodeCompanion chat buffers non-modifiable at rest so prompts and
-- edits can only flow through the per-tab input queue (lib/codecompanion-queue).
-- CodeCompanion brackets its own streaming writes with unlock/lock, and the
-- queue does the same for its programmatic submit; every other (manual) edit
-- hits a read-only buffer. Call this at the points CC leaves the buffer
-- editable at rest: after `Chat:reset`, on chat open, and after session restore.
local function lock_chat_buf(bufnr)
  if bufnr and vim.api.nvim_buf_is_valid(bufnr) then
    vim.bo[bufnr].modified = false
    vim.bo[bufnr].modifiable = false
  end
end

local function tab_chat_open_or_toggle(opts)
  opts = opts or {}
  local existing = vim.t.codecompanion_chat_bufnr
  if existing and vim.api.nvim_buf_is_valid(existing) then
    local chat = require("codecompanion").buf_get_chat(existing)
    if not chat then
      vim.t.codecompanion_chat_bufnr = nil
    else
      if opts.adapter then
        vim.notify(
          "Tab already has a CodeCompanion chat; close it before switching adapters.",
          vim.log.levels.WARN
        )
      end
      if chat.ui:is_visible() then
        chat.ui:hide()
      else
        chat.ui:open({ toggled = true })
      end
      return false
    end
  end
  if opts.adapter then
    vim.cmd("CodeCompanionChat adapter=" .. opts.adapter)
  else
    vim.cmd("CodeCompanionChat")
  end
  return true
end

local function _dvsc_launch_with(mode, model, effort)
  -- Refuse if the tab already owns a chat — `tab_chat_open_or_toggle`
  -- would just toggle visibility and the dvsc selection would be lost.
  -- Skip the launch_queue/pending push so they don't accumulate stale
  -- entries the next legitimate launch would consume.
  local existing = vim.t.codecompanion_chat_bufnr
  if existing and vim.api.nvim_buf_is_valid(existing) then
    return tab_chat_open_or_toggle({ adapter = "dvsc_core_broker" })
  end
  local sel = { mode = mode, model = model }
  local llm_config = _dvsc_build_llm_config(model, effort)
  if llm_config then sel.llm_config = llm_config end
  table.insert(_dvsc.launch_queue, { mode = mode, model = model, effort = effort })
  -- Stash for the adapter function to consume on next spawn. Racy if you
  -- start two chats simultaneously; fine for single-user. Stored as a
  -- Lua table so `build_client_metadata` can merge it with per-launch
  -- nvim identity rather than overwriting it.
  _dvsc.pending = { dvsc = sel }
  tab_chat_open_or_toggle({ adapter = "dvsc_core_broker" })
end

_G.codecompanion_dvsc_selection_for_buf = function(bufnr)
  return _dvsc.by_chat_bufnr[bufnr]
end

-- ── Resume / fork against the acp-broker ──────────────────────────────────
--
-- Both flows take a `broker_session_id` (lookup via
--   sqlite3 ~/.local/share/acp-persistence-server/persistence.db \
--     "SELECT broker_session_id, broker_id,
--             json_extract(metadata,'$.broker_client_metadata.nvim_session')
--      FROM sessions ORDER BY started_at DESC LIMIT 20;"
-- ). The last bsid used is cached so the next prompt prefills it.
--
-- Resume:  client sends `session/load(bsid)`; the broker live-joins the
--          existing session (if alive) or replays via the persistence
--          plugin's `try_resume`. Cross-broker resume is rejected — the
--          broker that captured the session is the only one that can
--          resume it (`docs/PROPOSAL-unified-session-id.md` §4.6).
--
-- Fork:    client sends `meta.broker.persistence.fork_saved_session`,
--          which mints a fresh session whose history is replayed into
--          the local broker. Cross-broker capable. The new session's
--          persistence record carries `parent = source_bsid`.
local BROKER_BSID_CACHE_PATH = vim.fn.stdpath("data") .. "/acp-broker-last-bsid.json"

local function _broker_read_last_bsid()
  local f = io.open(BROKER_BSID_CACHE_PATH, "r")
  if not f then return nil end
  local body = f:read("*a")
  f:close()
  local ok, t = pcall(vim.fn.json_decode, body)
  if not (ok and type(t) == "table" and t.bsid) then return nil end
  return vim.trim(t.bsid)
end

local function _broker_write_last_bsid(bsid)
  local f = io.open(BROKER_BSID_CACHE_PATH, "w")
  if not f then return end
  f:write(vim.fn.json_encode({ bsid = vim.trim(bsid) }))
  f:close()
end

-- Open a chat in the current tab pre-loaded with an existing broker
-- session. Bypasses `tab_chat_open_or_toggle` because the
-- `:CodeCompanionChat` command path doesn't accept `acp_session_id` —
-- the patched `ACPHandler:ensure_connection` (see config below) wires
-- it through, and the patched `Connection:_establish_session` forces
-- `loadSession` capability so the broker actually receives
-- `session/load(bsid)`.
--
-- `cwd_override` (optional) forces the follow-up `session/load` RPC to
-- send a specific cwd instead of `vim.fn.getcwd()`. For broker forks
-- this is the cwd the broker materialized the JSONL under (echoed back
-- in `ForkSavedSessionResponse.cwd`) — see `_broker_fork_saved_session`
-- above. The override is stored on the Connection as `_cwd_override`
-- and consumed by the patched `Connection:_establish_session` below.
local function _broker_open_chat_with_session(adapter, acp_session_id, cwd_override)
  local existing = vim.t.codecompanion_chat_bufnr
  if existing and vim.api.nvim_buf_is_valid(existing) then
    vim.notify(
      "Tab already has a CodeCompanion chat; close it before resuming/forking another session.",
      vim.log.levels.WARN
    )
    return false
  end
  -- Chat.new() dereferences self.buffer_context unconditionally
  -- (chat/init.lua:504); it must be a real table, not nil. Mirror what
  -- the CodeCompanion top-level entry points do (init.lua:170-179) and
  -- build it from the current buffer via context_utils.
  local buffer_context = require("codecompanion.utils.context").get(vim.api.nvim_get_current_buf())
  local chat = require("codecompanion.interactions.chat").new({
    adapter = adapter,
    acp_session_id = acp_session_id,
    buffer_context = buffer_context,
  })

  -- The patched ACPHandler:ensure_connection (below) pre-sets `session_id`
  -- on the Connection so the broker treats this as a resume. That makes
  -- `ACPHandler:ensure_session` early-exit at handler.lua:103-105 — so the
  -- actual `session/load(bsid)` RPC is never sent and the persistence
  -- replay events have no consumer (acp/init.lua:631-635 silently drops
  -- SESSION_UPDATE notifications when `_loading_session` is unset and no
  -- `_active_prompt` exists). Trigger the proper load explicitly, mirroring
  -- upstream's /resume slash command at slash_commands/builtin/resume.lua:
  -- collect updates synchronously during load_session, then hand them to
  -- `acp.render.restore_session` to repaint the chat buffer.
  local function try_load(attempts)
    local conn = chat.acp_connection
    if conn and conn:is_ready() then
      if cwd_override then
        conn._cwd_override = cwd_override
      end
      local updates = {}
      local ok = conn:load_session(acp_session_id, {
        on_session_update = function(u) table.insert(updates, u) end,
      })
      if not ok then
        vim.notify("acp-broker: load_session failed for " .. acp_session_id, vim.log.levels.ERROR)
        return
      end
      require("codecompanion.interactions.chat.acp.commands").link_buffer_to_session(
        chat.bufnr, conn.session_id
      )
      pcall(function()
        require("lib.codecompanion-chatinfo").pin(chat.bufnr, conn.session_id)
      end)
      require("codecompanion.interactions.chat.acp.render").restore_session(chat, updates)
      lock_chat_buf(chat.bufnr)
      require("codecompanion.utils").fire("ACPChatRestored", {
        bufnr = chat.bufnr,
        id = chat.id,
        session_id = conn.session_id,
      })
      return
    end
    if attempts <= 0 then
      vim.notify("acp-broker: timed out waiting for ACP connection", vim.log.levels.ERROR)
      return
    end
    vim.defer_fn(function() try_load(attempts - 1) end, 100)
  end
  vim.defer_fn(function() try_load(100) end, 50)

  return true
end

-- Issue `meta.broker.persistence.fork_saved_session(bsid)` and return
-- `(new_bsid, resolved_cwd)` (or nil on failure, with a notification).
--
-- `target_cwd = vim.uv.cwd()` instructs the broker to materialize the
-- forked JSONL under the local cwd, so the agent's later
-- `session/load(new_bsid)` finds it. Without this, cross-broker forks
-- would write the JSONL under the source machine's cwd path (which
-- doesn't exist locally) and the resume would silently start fresh.
-- See ralph-loops/fork-cwd-fix/context.md §6 in the broker repo.
--
-- The broker echoes the resolved cwd back in `response.cwd`; callers
-- thread it into `_broker_open_chat_with_session` so the follow-up
-- `session/load` uses the same cwd the JSONL was written under.
local function _broker_fork_saved_session(adapter, source_bsid)
  local conn = require("codecompanion.acp").new({ adapter = adapter })
  if not conn:connect_and_authenticate() then
    vim.notify("acp-broker: connect failed", vim.log.levels.ERROR)
    return nil
  end
  local resp = conn:send_rpc_request(
    "meta.broker.persistence.fork_saved_session",
    { broker_session_id = source_bsid, target_cwd = vim.uv.cwd() }
  )
  pcall(function() conn:disconnect() end)
  if not resp or not resp.broker_session_id then
    vim.notify("fork failed: " .. vim.inspect(resp), vim.log.levels.ERROR)
    return nil
  end
  return resp.broker_session_id, resp.cwd
end

-- `broker_resume_or_fork` (the aBr/aBf escape hatches) is defined after the I/O
-- adapters below, since it depends on `_broker_this_broker_id`/
-- `_broker_list_sessions` for its pre-flight broker-mismatch check.

-- The broker's local WAL mirror. The server-backed picker
-- (`broker_continue`) does NOT read this for its session list — it queries the
-- persistence-server via acp-broker-cli so it sees all brokers. The WAL is used
-- only for (a) reading THIS broker's id (`_broker_this_broker_id`) and (b) the
-- degraded fallback in `_broker_list_sessions` when the CLI/server is
-- unreachable.
local BROKER_WAL_PATH =
  vim.fn.expand("~/.local/share/acp-broker/sqlite-persistence/wal.db")

-- Adapter for a saved session. dvsc/claude/devmate all speak the claude_code
-- wire shape and resume cleanly through `dvsc_core_broker`; only codex needs
-- `codex_broker`. The agent *kind* is not reliably recoverable for dead
-- sessions (the WAL keeps only `agent_id`; `agent list` names only live
-- agents; lifecycle retains few `agent_spawned` records), so we positively
-- identify codex from the live agent list when possible and otherwise fall
-- back to `dvsc_core_broker`.
local function _broker_adapter_for(agent_id)
  if not agent_id or agent_id == "" then return "dvsc_core_broker" end
  local cli = vim.fn.expand("~/repos/acp-broker/target/release/acp-broker-cli")
  if vim.fn.executable(cli) == 0 then return "dvsc_core_broker" end
  local out = vim.fn.systemlist({ cli, "agent", "list" })
  if vim.v.shell_error ~= 0 then return "dvsc_core_broker" end
  for _, line in ipairs(out) do
    local id, name = line:match("^(%S+)%s+%S+%s+%S+%s+(%S+)")
    if id == agent_id then
      if name == "codex" then return "codex_broker" end
      return "dvsc_core_broker"
    end
  end
  return "dvsc_core_broker"
end

-- ── I/O adapters for the server-backed continue picker ─────────────────────
--
-- These are the thin impure edges that feed `lib.acp-broker-sessions` (pure).
-- Design: docs/acp-broker-continue-refactor.md §3.1-3.2.

local _BROKER_CLI = vim.fn.expand("~/repos/acp-broker/target/release/acp-broker-cli")
local _broker_sessions = require("lib.acp-broker-sessions")

-- This broker's id, read once from the local WAL and cached. Used to classify
-- rows as local vs remote. Returns nil if the WAL is unavailable (⇒ every row
-- classifies as remote ⇒ fork, which is the safe cross-broker path).
local _this_broker_id_cache = nil
local _this_broker_id_read = false
local function _broker_this_broker_id()
  if _this_broker_id_read then return _this_broker_id_cache end
  _this_broker_id_read = true
  if vim.fn.executable("sqlite3") == 1 and vim.fn.filereadable(BROKER_WAL_PATH) == 1 then
    local out = vim.fn.systemlist({
      "sqlite3", BROKER_WAL_PATH,
      "SELECT broker_id FROM mirrored_sessions ORDER BY started_at DESC LIMIT 1;",
    })
    if vim.v.shell_error == 0 and out[1] and out[1] ~= "" then
      _this_broker_id_cache = vim.trim(out[1])
    end
  end
  return _this_broker_id_cache
end

-- Live SESSION bsid set on THIS broker, via `session list`. This is the correct
-- liveness signal: a session is resumable via live-join only if it's in the
-- broker's live session registry. Keying off `agent list` is WRONG for dvsc —
-- the dvsc-core agent is a shared long-lived process that multiplexes many
-- sessions, so it stays "alive" even after a specific session crashes, which
-- made crashed sessions look resumable and open blank buffers. Returns {} on
-- failure (⇒ nothing looks live ⇒ everything forks, the safe default).
local function _broker_live_session_ids()
  local set = {}
  if vim.fn.executable(_BROKER_CLI) == 0 then return set end
  local out = vim.fn.system({ _BROKER_CLI, "session", "list", "--json" })
  if vim.v.shell_error ~= 0 then return set end
  local ok, decoded = pcall(vim.fn.json_decode, out)
  if not ok or type(decoded) ~= "table" or type(decoded.sessions) ~= "table" then
    return set
  end
  for _, s in ipairs(decoded.sessions) do
    local sid = type(s) == "table" and s.session_id
    if type(sid) == "string" and sid ~= "" then set[sid] = true end
  end
  return set
end

-- List saved sessions across ALL brokers via the persistence-server (through the
-- broker UDS). Returns `(rows, degraded)`: on CLI failure, falls back to the
-- local WAL (this-broker-only) with `degraded = true`. Rows are normalized by
-- `parse_saved_sessions` and already recency-sorted by the server.
local function _broker_list_sessions()
  if vim.fn.executable(_BROKER_CLI) == 1 then
    local out = vim.fn.system({ _BROKER_CLI, "history", "query", "saved-sessions", "--json" })
    if vim.v.shell_error == 0 and out and out ~= "" then
      local rows = _broker_sessions.parse_saved_sessions(out)
      if #rows > 0 then return rows, false end
    end
  end
  -- Fallback: local WAL only.
  local rows = {}
  if vim.fn.executable("sqlite3") == 1 and vim.fn.filereadable(BROKER_WAL_PATH) == 1 then
    local this = _broker_this_broker_id()
    local out = vim.fn.systemlist({
      "sqlite3", "-cmd", ".mode json", BROKER_WAL_PATH,
      "SELECT broker_session_id, cwd, "
        .. "json_extract(metadata,'$.broker_client_metadata.host') AS host, "
        .. "json_extract(metadata,'$.broker_client_metadata.dvsc.model') AS model, "
        .. "json_extract(metadata,'$.broker_client_metadata.dvsc.mode') AS mode "
        .. "FROM mirrored_sessions ORDER BY started_at DESC;",
    })
    local ok, decoded = pcall(vim.fn.json_decode, table.concat(out, "\n"))
    if ok and type(decoded) == "table" then
      for _, r in ipairs(decoded) do
        rows[#rows + 1] = {
          bsid = r.broker_session_id,
          broker_id = r.host or this,
          cwd = r.cwd,
          model = r.model,
          mode = r.mode,
        }
      end
    end
  end
  return rows, true
end

-- Lazily enrich a single picked row (design §3.2: per-row enrichment is too slow,
-- so this runs only for the chosen row). Reads the session's seq-0 event via
-- `history query load` (cross-broker safe) to get agent_id + started_at.
-- Liveness is keyed off the session's OWN bsid being in the live session
-- registry (`live_sids`), NOT off the agent being alive — see
-- `_broker_live_session_ids`. Returns { agent_id, started_at, live }.
local function _broker_enrich_pick(bsid, live_sids)
  local result = { agent_id = nil, started_at = nil, live = false }
  if (live_sids or {})[bsid] then
    result.live = true
  end
  if vim.fn.executable(_BROKER_CLI) == 0 then return result end
  -- `load | head -1`: the first line is the seq-0 session/new event. SIGPIPE from
  -- the early close is fine (we ignore shell_error here since head closing the
  -- pipe can make the CLI exit non-zero).
  local out = vim.fn.system(
    string.format("%s history query load %s 2>/dev/null | head -1",
      vim.fn.shellescape(_BROKER_CLI), vim.fn.shellescape(bsid))
  )
  if not out or out == "" then return result end
  local ok, ev = pcall(vim.fn.json_decode, out)
  if not ok or type(ev) ~= "table" then return result end
  result.agent_id = ev.agent_id
  result.started_at = ev.ts
  return result
end

-- Top-level entry points for `<leader>aBr` / `<leader>aBf` (escape hatches; the
-- everyday path is `<leader>aBc` / broker_continue). `action` is `"resume"` or
-- `"fork"`. For `resume`, a pre-flight broker-mismatch check: cross-broker
-- `session/load` is rejected and silently starts fresh (acp/init.lua:386), so if
-- the bsid belongs to another broker we warn and offer to fork instead.
local function broker_resume_or_fork(action, adapter_name)
  adapter_name = adapter_name or "dvsc_core_broker"
  local prompt = (action == "fork" and "Fork bsid: ") or "Resume bsid: "
  local default = _broker_read_last_bsid() or "bsid_"

  local function do_fork(bsid)
    local adapter = require("codecompanion.adapters").resolve(adapter_name)
    local target_bsid, target_cwd = _broker_fork_saved_session(adapter, bsid)
    if not target_bsid then return end
    vim.notify("forked " .. bsid .. " -> " .. target_bsid, vim.log.levels.INFO)
    _broker_write_last_bsid(target_bsid)
    _broker_open_chat_with_session(adapter_name, target_bsid, target_cwd)
  end

  vim.ui.input({ prompt = prompt, default = default }, function(bsid)
    bsid = bsid and vim.trim(bsid)
    if not bsid or bsid == "" or bsid == "bsid_" then return end
    _broker_write_last_bsid(bsid)

    if action == "fork" then
      return do_fork(bsid)
    end

    -- action == "resume": pre-flight broker-mismatch check.
    local this = _broker_this_broker_id()
    local rows = _broker_list_sessions()
    local owner
    for _, row in ipairs(rows) do
      if row.bsid == bsid then
        owner = row.broker_id
        break
      end
    end
    if owner and this and owner ~= this then
      return vim.ui.select({ "Fork instead (recommended)", "Resume anyway (will start fresh)" }, {
        prompt = string.format("%s belongs to %s, not this broker (%s). Resume can't work.",
          _broker_sessions.short_bsid(bsid), owner, this),
      }, function(choice)
        if not choice then return end
        if choice:match("^Fork") then
          do_fork(bsid)
        else
          _broker_open_chat_with_session(adapter_name, bsid, nil)
        end
      end)
    end
    _broker_open_chat_with_session(adapter_name, bsid, nil)
  end)
end


-- ── Smart continue: server-backed, auto-routing resume/fork ────────────────
--
-- Design: docs/acp-broker-continue-refactor.md §3.2-3.3. One command that lists
-- all sessions across brokers, classifies each by (origin, liveness), and routes
-- to resume (local+live), fork (remote), or fork-because-dead (local+dead).
--
-- DESIGN DEVIATION (implementation-time): the doc's `resume_or_fork` action was
-- to attempt resume and catch -32029. That is NOT reliably implementable —
-- codecompanion's `_establish_session` (acp/init.lua:386) swallows a failed
-- `session/load` by falling through to `session/new`, so a doomed resume
-- silently starts fresh and returns success (the very bug we're removing). We
-- instead detect local+dead via the pre-flight liveness check in
-- `_broker_enrich_pick` (agent-not-in-live-set) and fork directly. `route_for`
-- still returns "resume_or_fork" for local+dead; `_broker_apply` treats it as a
-- direct fork with a toast.

-- Execute the routed action for an enriched picked row.
local function _broker_apply(row, enrich)
  local origin = _broker_sessions.classify_origin(row, _broker_this_broker_id())
  local action = _broker_sessions.route_for({ origin = origin, live = enrich.live })
  _broker_write_last_bsid(row.bsid)

  if action == "resume" then
    _broker_open_chat_with_session(_broker_adapter_for(enrich.agent_id), row.bsid)
    return
  end

  -- fork (remote) or resume_or_fork→fork (local+dead): materialize a fresh
  -- local session from the server-held history and open it.
  local adapter = (row.model and row.model:match("codex")) and "codex_broker" or "dvsc_core_broker"
  local why = (origin == "remote")
    and ("forking cross-broker session from " .. (row.broker_id or "?"))
    or "original agent has exited; forking to recover context"
  local target_bsid, target_cwd = _broker_fork_saved_session(
    require("codecompanion.adapters").resolve(adapter), row.bsid
  )
  if not target_bsid then return end
  vim.notify(
    string.format("%s\n%s → %s", why, _broker_sessions.short_bsid(row.bsid), _broker_sessions.short_bsid(target_bsid)),
    vim.log.levels.INFO
  )
  _broker_write_last_bsid(target_bsid)
  _broker_open_chat_with_session(adapter, target_bsid, target_cwd)
end

-- Enrich (if local) then apply. Remote rows skip enrichment (they always fork).
local function _broker_enrich_and_apply(row, live_sids)
  local origin = _broker_sessions.classify_origin(row, _broker_this_broker_id())
  local enrich = { agent_id = nil, started_at = row.started_at, live = false }
  if origin == "local" then
    enrich = _broker_enrich_pick(row.bsid, live_sids or _broker_live_session_ids())
  end
  _broker_apply(row, enrich)
end

-- Fetch a short preview for a session: full bsid, broker/agent, and the first
-- user prompt text. Cross-broker safe (uses `history query load`). Returns a
-- list of display lines. Best-effort; never errors.
local function _broker_session_preview(row, enrich)
  local lines = {
    "bsid:    " .. (row.bsid or "?"),
    "broker:  " .. (row.broker_id or "?"),
    "cwd:     " .. (row.cwd or "?"),
    "model:   " .. (row.model or "?") .. (row.effort and (" [" .. row.effort .. "]") or ""),
  }
  if enrich then
    lines[#lines + 1] = "agent:   " .. (enrich.agent_id or "?") .. (enrich.live and " (live)" or " (dead)")
    lines[#lines + 1] = "started: " .. (enrich.started_at or "?")
  end
  lines[#lines + 1] = ""
  -- First user prompt: scan the load stream for the first client→agent
  -- session/prompt event's last text block.
  if vim.fn.executable(_BROKER_CLI) == 1 and row.bsid then
    local out = vim.fn.system(
      string.format("%s history query load %s 2>/dev/null | head -40",
        vim.fn.shellescape(_BROKER_CLI), vim.fn.shellescape(row.bsid))
    )
    for _, l in ipairs(vim.split(out or "", "\n", { plain = true })) do
      local ok, ev = pcall(vim.fn.json_decode, l)
      if ok and type(ev) == "table" and ev.method == "session/prompt"
        and ev.direction == "client_to_agent" then
        local prompt = ev.payload and ev.payload.prompt
        if type(prompt) == "table" then
          local last_text
          for _, block in ipairs(prompt) do
            if type(block) == "table" and block.type == "text" and block.text then
              last_text = block.text
            end
          end
          if last_text then
            lines[#lines + 1] = "── first prompt ──"
            for _, pl in ipairs(vim.split(last_text, "\n", { plain = true })) do
              lines[#lines + 1] = pl
            end
          end
        end
        break
      end
    end
  end
  return lines
end

-- Smart continue entry point. Server-backed list, cwd-filtered by default (toggle
-- to all-cwd with <A-c>), rich auto-routing labels, pick-or-paste, preview + yank.
-- Design §3.3.
local function broker_continue()
  local all_rows, degraded = _broker_list_sessions()
  local this = _broker_this_broker_id()
  local live_sids = _broker_live_session_ids()
  local cwd = vim.fn.getcwd()

  -- Precompute origin + display label per row for the current scope.
  local function build_items(scoped)
    local rows = scoped and _broker_sessions.filter_by_cwd(all_rows, cwd) or all_rows
    local items = {}
    for _, row in ipairs(rows) do
      local origin = _broker_sessions.classify_origin(row, this)
      -- Liveness is lazy; at list time we only know origin. Show origin glyph;
      -- action is "resume" for local (optimistic — refined on pick), "fork" for
      -- remote (always correct).
      local action = (origin == "remote") and "fork" or "resume"
      local label = _broker_sessions.render_label(row, {
        origin = origin, live = (origin == "local"), action = action, now = os.time(),
      })
      items[#items + 1] = { row = row, origin = origin, label = label }
    end
    return items
  end

  local scoped = true

  local function open(is_scoped)
    local items = build_items(is_scoped)
    if vim.tbl_isempty(items) then
      return vim.notify(
        "No saved broker session for " .. cwd .. " (try <A-c> for all cwds, or <C-x> to paste a bsid)",
        vim.log.levels.WARN
      )
    end
    local title = degraded and "Continue [DEGRADED: this machine] " or "Continue session "
    local scope_txt = is_scoped and cwd or "ALL cwds"

    -- Route a chosen row (enrich if local, then apply).
    local function route(row)
      _broker_enrich_and_apply(row, live_sids)
    end

    -- Route a pasted bsid: classify from the full list; unknown ⇒ fork (safe).
    local function route_bsid(bsid)
      bsid = vim.trim(bsid)
      if not _broker_sessions.is_bsid(bsid) then
        return vim.notify("Not a valid bsid: " .. bsid, vim.log.levels.ERROR)
      end
      for _, row in ipairs(all_rows) do
        if row.bsid == bsid then return route(row) end
      end
      route({ bsid = bsid, broker_id = nil })
    end

    vim.ui.select(items, {
      prompt = title,
      format_item = function(item) return item.label end,
      snacks = {
        source = "acp_continue",
        title = title .. "· " .. scope_txt,
        win = { input = { keys = {
          ["<c-x>"] = { "paste_bsid", mode = { "i", "n" } },
          ["<c-y>"] = { "yank_bsid", mode = { "i", "n" } },
          ["<a-c>"] = { "toggle_cwd", mode = { "i", "n" } },
        } } },
        preview = function(ctx)
          local item = ctx.item and ctx.item.item
          if not item or not item.row then return false end
          local lines = _broker_session_preview(item.row, nil)
          ctx.preview:set_lines(lines)
          ctx.preview:highlight({ ft = "markdown" })
          return true
        end,
        actions = {
          paste_bsid = function(picker)
            local pat = picker.input.filter.pattern
            if _broker_sessions.is_bsid(pat) then
              picker:close()
              vim.schedule(function() route_bsid(pat) end)
            else
              vim.notify("Type a full bsid_… in the filter first, then <C-x>", vim.log.levels.WARN)
            end
          end,
          yank_bsid = function(picker)
            local cur = picker:current()
            local bsid = cur and cur.item and cur.item.row and cur.item.row.bsid
            if bsid then
              vim.fn.setreg("+", bsid)
              vim.notify("Yanked " .. bsid, vim.log.levels.INFO)
            end
          end,
          toggle_cwd = function(picker)
            picker:close()
            vim.schedule(function() open(not is_scoped) end)
          end,
        },
      },
    }, function(choice)
      if not choice then return end
      route(choice.row)
    end)
  end

  if degraded then
    vim.notify("acp-broker: persistence-server unreachable — showing THIS machine only",
      vim.log.levels.WARN)
  end
  open(scoped)
end

-- ── Omnigent resume ────────────────────────────────────────────────────────
--
-- The native-omnigent analog of `broker_continue`, but far simpler: omnigent
-- sessions are durable and server-owned, so there is no fork/resume split and no
-- cross-broker classification — a single REST list + `chat:resume_omnigent(id)`
-- (which loads the snapshot + durable items and hydrates the buffer without
-- posting) is the whole flow. The pure list helpers live in the plugin
-- (`interactions.chat.omnigent.sessions`) and are reused here so formatting stays
-- consistent with the in-chat `/omnigent_resume` picker.
local function _omnigent_client()
  return require("codecompanion.omnigent.client").new({
    url = vim.env.OMNIGENT_URL or "http://127.0.0.1:6767",
  })
end

-- ── Omnigent agent picker ──────────────────────────────────────────────────
--
-- Mirrors the dvsc/direct model pickers (`_dvsc_select` / `_direct_select`): a
-- cached last-choice reused on the normal launch, re-prompted on force. Two
-- differences, both because of what omnigent exposes: the catalog is fetched
-- live from the server (GET /v1/agents) rather than hardcoded -- there is no
-- drift-prone entitlement list to mirror -- and the pick is a triple
-- {agent, model, effort}: the agent is the session harness (IMMUTABLE after
-- create, so launch-time only), while model + effort are the initial overrides
-- passed at session create and stay switchable mid-session via <leader>ao.
-- Only SDK/streaming harnesses are offered. Native harnesses (`*-native`: Claude
-- Code = claude-native, Codex = codex-native, cursor, ...) boot a vendor TUI in a
-- tmux terminal on the runner and send their output THERE, not to the chat stream
-- (verified empirically: a claude-native-ui turn renders nothing in the buffer,
-- and codex-native-ui fails to start) -- they're the wrong abstraction for a chat
-- surface. Use a terminal-attach flow for native harnesses instead.
local OMNIGENT_AGENT_CACHE_PATH = vim.fn.stdpath("data") .. "/codecompanion-omnigent-agent.json"

-- The launch selection is a triple {agent, model?, effort?} (mirroring how the
-- dvsc picker caches {mode, model, effort}). A nil model/effort means "no
-- override" -- the agent spec's own model/effort applies. Read back by the
-- omnigent adapter at spawn and by <leader>aM's cached-reuse path.
local function _omnigent_read_selection()
  local f = io.open(OMNIGENT_AGENT_CACHE_PATH, "r")
  if not f then return {} end
  local body = f:read("*a")
  f:close()
  local ok, t = pcall(vim.fn.json_decode, body)
  return (ok and type(t) == "table") and t or {}
end

local function _omnigent_write_selection(sel)
  local f = io.open(OMNIGENT_AGENT_CACHE_PATH, "w")
  if not f then return end
  f:write(vim.fn.json_encode({ agent = sel.agent, model = sel.model, effort = sel.effort }))
  f:close()
end

-- A harness whose output streams into the chat buffer. SDK/subprocess harnesses
-- (claude-sdk, codex, pi, openai-agents, ...) run the vendor model directly and
-- emit response.output_text deltas; native harnesses (`*-native`) run a terminal
-- TUI and don't, so they're excluded from a chat picker.
local function _omnigent_is_chat_harness(harness)
  return not (type(harness) == "string" and harness:match("%-native$"))
end

-- Live agent catalog, filtered to chat-capable (SDK) harnesses:
-- { { id, name, harness, description }, ... } or nil, err.
local function _omnigent_pickable_agents()
  local agents, err = _omnigent_client():list_agents()
  if not agents then return nil, err end
  local out = {}
  for _, a in ipairs(agents) do
    if a.name and _omnigent_is_chat_harness(a.harness) then
      out[#out + 1] = { id = a.id, name = a.name, harness = a.harness, description = a.description }
    end
  end
  return out
end

-- ── Omnigent model + reasoning-effort catalog ──────────────────────────────
--
-- omnigent runs the REAL vendor CLIs, so a model override is the vendor's own
-- canonical id (bare `claude-opus-4-8` / `gpt-5-4`); the server mechanically
-- localizes it to the Databricks gateway or a vendor-direct provider
-- (omnigent/model_override.py) and validates only charset + family (a
-- claude-family harness demands an id containing "claude"; codex-family demands
-- "gpt"/"codex"). There is NO models endpoint, so -- unlike the drift-prone
-- hardcoded DVSC_MODELS mirror -- this is just a short curated preset list per
-- family, always paired with a free-text "custom…" escape and a "default" (no
-- override) option, so a missing/renamed id is never a dead end.
--
-- Reasoning-effort values are the per-provider families from
-- omnigent/reasoning_effort.py. They deliberately differ from the DVSC
-- EFFORT_OPTIONS_BY_KIND: claude has `max`, codex has `none`/`minimal`, and all
-- are lowercase. Listed in omnigent's canonical display order.
local OMNIGENT_EFFORTS = {
  claude            = { "low", "medium", "high", "xhigh", "max" },
  codex             = { "none", "minimal", "low", "medium", "high", "xhigh" },
  ["openai-agents"] = { "none", "minimal", "low", "medium", "high", "xhigh" },
  antigravity       = { "low", "medium", "high" },
  copilot           = { "low", "medium", "high", "xhigh" },
}
-- Fallback for a harness whose family we don't recognise: offer every level and
-- let the server reject an unsupported one with its own clear message.
local OMNIGENT_EFFORT_ORDER = { "none", "minimal", "low", "medium", "high", "xhigh", "max" }

-- Curated presets only; "default" + "custom…" are added by the picker. Families
-- without a preset list (pi, cursor, multi-model harnesses, ...) fall back to
-- custom-only, which is always valid.
--
-- Codex via omnigent runs `codex app-server`, whose per-turn model must be one of
-- codex's routed `MODEL_ROUTES` slugs or the AI gateway answers 421 ("no upstream
-- configured for this host"): the model→host rewrite (azure-codex ->
-- azure-codex-<model>) only fires for routed slugs. So these MUST be the exact
-- dotted route ids (NOT gateway-style `gpt-5-5`), and `gpt-5.4` is omitted (its
-- deployment is retired -> 404). Verified working end-to-end: gpt-5.5.
local OMNIGENT_MODELS = {
  claude = { "claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5", "claude-opus-4-7" },
  codex  = { "gpt-5.5", "gpt-5.3-codex", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna" },
}

-- Per-family "default" model. Codex's app-server default per-turn model is NOT a
-- routed slug (-> 421), so "default" for codex must resolve to a routed model
-- rather than "no override". claude-sdk's own default is fine, so it stays nil.
local OMNIGENT_MODEL_DEFAULT = { codex = "gpt-5.5" }

-- Classify a harness id into a model/effort family, mirroring the vendor-token
-- rules in omnigent/model_override.py (model_family_mismatch).
local function _omnigent_family_for_harness(harness)
  if type(harness) ~= "string" then return nil end
  local h = harness:lower()
  if h:find("claude", 1, true) then return "claude" end
  if h:find("codex", 1, true) then return "codex" end
  if h:find("openai-agents", 1, true) then return "openai-agents" end
  if h:find("antigravity", 1, true) or h:find("gemini", 1, true) or h == "agy" then return "antigravity" end
  if h:find("copilot", 1, true) then return "copilot" end
  return nil
end

-- Best-effort family from a model id (used mid-session, where the harness isn't
-- on the session object but the current model usually is).
local function _omnigent_family_for_model(model)
  if type(model) ~= "string" then return nil end
  local m = model:lower()
  if m:find("claude", 1, true) then return "claude" end
  if m:find("gpt", 1, true) or m:find("codex", 1, true) then return "codex" end
  return nil
end

-- Model picker for a family. `include_default` adds a "no override" choice.
-- Invokes cb(model) with a string id, or cb(nil) for the default choice; never
-- called on cancel.
local function _omnigent_pick_model(family, include_default, cb)
  local items = {}
  if include_default then
    local d = OMNIGENT_MODEL_DEFAULT[family]
    items[#items + 1] = {
      label = d and ("default (" .. d .. ")") or "default (agent's model)",
      is_default = true,
    }
  end
  for _, id in ipairs(OMNIGENT_MODELS[family] or {}) do
    items[#items + 1] = { label = id, model = id }
  end
  items[#items + 1] = { label = "custom…", is_custom = true }
  vim.ui.select(items, {
    prompt = "Model:",
    format_item = function(it) return it.label end,
  }, function(choice)
    if choice == nil then return end
    if choice.is_default then return cb(OMNIGENT_MODEL_DEFAULT[family]) end
    if choice.is_custom then
      return vim.ui.input({ prompt = "Model id: " }, function(input)
        input = input and vim.trim(input)
        if not input or input == "" then return end
        cb(input)
      end)
    end
    cb(choice.model)
  end)
end

-- Effort picker for a family. Same contract as _omnigent_pick_model.
local function _omnigent_pick_effort(family, include_default, cb)
  local values = OMNIGENT_EFFORTS[family] or OMNIGENT_EFFORT_ORDER
  local items = {}
  if include_default then
    items[#items + 1] = { label = "default (agent's effort)", is_default = true }
  end
  for _, e in ipairs(values) do
    items[#items + 1] = { label = e, effort = e }
  end
  vim.ui.select(items, {
    prompt = "Thinking effort:",
    format_item = function(it) return it.label end,
  }, function(choice)
    if choice == nil then return end
    if choice.is_default then return cb(nil) end
    cb(choice.effort)
  end)
end

-- Launch-time picker: agent → model → effort, caching the triple. `force=true`
-- (the pick keymaps) always re-prompts; otherwise a still-valid cached agent is
-- reused verbatim (the whole remembered selection, model + effort included). The
-- chosen model/effort are read back from the cache by the omnigent adapter at
-- spawn (defaults.model_override / .reasoning_effort) and applied at session
-- create. `cb` is invoked with no args once the selection is cached. Mirrors
-- `_dvsc_select` (harness → model → effort).
local function _omnigent_select(force, cb)
  local agents, err = _omnigent_pickable_agents()
  if not agents then
    return vim.notify("omnigent: failed to list agents: " .. (err and err.message or "?"), vim.log.levels.ERROR)
  end
  if #agents == 0 then
    return vim.notify(
      "omnigent: no streaming (SDK) agents available — register a claude-sdk / codex agent "
        .. "(native-ui agents can't render in a chat)",
      vim.log.levels.WARN
    )
  end
  -- Reuse the cached choice only if its agent is still a valid chat-capable
  -- agent: a previously-cached native agent must not silently relaunch into a
  -- dead end.
  if not force then
    local cached = _omnigent_read_selection()
    if cached.agent then
      for _, a in ipairs(agents) do
        if a.name == cached.agent then return cb() end
      end
    end
  end
  vim.ui.select(agents, {
    prompt = "Omnigent agent:",
    format_item = function(a)
      local detail = (a.description and a.description ~= "") and a.description or a.harness
      return detail and (a.name .. "  —  " .. detail) or a.name
    end,
  }, function(choice)
    if choice == nil then return end
    local family = _omnigent_family_for_harness(choice.harness)
    _omnigent_pick_model(family, true, function(model)
      _omnigent_pick_effort(family, true, function(effort)
        _omnigent_write_selection({ agent = choice.name, model = model, effort = effort })
        cb()
      end)
    end)
  end)
end

-- Open a fresh chat in the current tab bound to an existing omnigent session and
-- hydrate it. Mirrors `_broker_open_chat_with_session` but without the ACP
-- connection-readiness poll (omnigent load is a synchronous REST round-trip).
local function _omnigent_open_chat_with_session(session_id)
  local existing = vim.t.codecompanion_chat_bufnr
  if existing and vim.api.nvim_buf_is_valid(existing) then
    vim.notify(
      "Tab already has a CodeCompanion chat; close it before resuming another session.",
      vim.log.levels.WARN
    )
    return false
  end
  local buffer_context = require("codecompanion.utils.context").get(vim.api.nvim_get_current_buf())
  local chat = require("codecompanion.interactions.chat").new({
    adapter = "omnigent",
    omnigent_session_id = session_id,
    buffer_context = buffer_context,
  })
  if not chat then
    vim.notify("Failed to open omnigent chat", vim.log.levels.ERROR)
    return false
  end
  vim.schedule(function()
    local ok, err = chat:resume_omnigent()
    if not ok then
      vim.notify("omnigent resume failed: " .. (err and err.message or "?"), vim.log.levels.ERROR)
      return
    end
    lock_chat_buf(chat.bufnr)
    require("codecompanion.utils").fire("OmnigentChatRestored", {
      bufnr = chat.bufnr,
      id = chat.id,
      session_id = session_id,
    })
  end)
  return true
end

-- Server-backed omnigent session picker. cwd-scoped by default (<A-c> toggles to
-- all workspaces), recency-sorted, archived filtered out.
local function omnigent_continue()
  local sessions_lib = require("codecompanion.interactions.chat.omnigent.sessions")
  local client = _omnigent_client()
  local list, err = client:list_sessions({ limit = 200 })
  if not list then
    return vim.notify("omnigent: failed to list sessions: " .. (err and err.message or "?"), vim.log.levels.ERROR)
  end
  list = sessions_lib.by_recency(sessions_lib.active(list))
  if #list == 0 then
    return vim.notify("omnigent: no saved sessions", vim.log.levels.INFO)
  end
  local cwd = vim.fn.getcwd()
  local now = os.time()

  local function build_items(scoped)
    local rows = scoped and sessions_lib.filter_by_workspace(list, cwd) or list
    local items = {}
    for _, s in ipairs(rows) do
      items[#items + 1] = { session = s, label = sessions_lib.format_summary(s, { now = now }) }
    end
    return items
  end

  local function open(is_scoped)
    local items = build_items(is_scoped)
    if vim.tbl_isempty(items) then
      return vim.notify(
        "omnigent: no session for " .. cwd .. " (<A-c> for all workspaces)",
        vim.log.levels.WARN
      )
    end
    local scope_txt = is_scoped and cwd or "ALL workspaces"
    vim.ui.select(items, {
      prompt = "Resume Omnigent session · " .. scope_txt,
      format_item = function(item) return item.label end,
      snacks = {
        source = "omnigent_continue",
        title = "Resume Omnigent · " .. scope_txt,
        win = { input = { keys = {
          ["<a-c>"] = { "toggle_scope", mode = { "i", "n" } },
        } } },
        preview = function(ctx)
          local s = ctx.item and ctx.item.item and ctx.item.item.session
          if not s then return false end
          local lines = {
            "id:        " .. (s.id or "?"),
            "title:     " .. (s.title or "(untitled)"),
            "agent:     " .. (s.agent_name or s.agent_id or "?"),
            "status:    " .. (s.status or "?"),
            "workspace: " .. (s.workspace or "(none)"),
            "effort:    " .. (s.reasoning_effort or "?"),
            "pending:   " .. tostring(s.pending_elicitations_count or 0),
          }
          ctx.preview:set_lines(lines)
          ctx.preview:highlight({ ft = "yaml" })
          return true
        end,
        actions = {
          toggle_scope = function(picker)
            picker:close()
            vim.schedule(function() open(not is_scoped) end)
          end,
        },
      },
    }, function(choice)
      if not choice then return end
      _omnigent_open_chat_with_session(choice.session.id)
    end)
  end

  open(true)
end

-- Pick a dvsc selection (interactive or from cache), then invoke `cb`
-- with `{ mode, model, effort? }`. Extracted from the original
-- `dvsc_pick_and_launch` so the picker can drive both first-time launches
-- and in-place adapter swaps without duplicating the cache+prompt logic.
local function _dvsc_select(force, cb)
  local cache = _dvsc_read_cache()
  if not force and cache.mode and cache.model then
    local kind = _dvsc_reasoning_kind(cache.model)
    if kind == nil or cache.effort then
      return cb(cache)
    end
    -- Cache is for a model that needs an effort but lacks one; fall
    -- through to a fresh pick rather than proceeding with no effort.
  end
  _dvsc_pick(DVSC_MODES, "Harness:", function(mode)
    _dvsc_pick(_models_for_mode(mode), "Model:", function(model)
      local kind = _dvsc_reasoning_kind(model)
      if kind == nil then
        local sel = { mode = mode, model = model }
        _dvsc_write_cache(sel)
        return cb(sel)
      end
      vim.ui.select(EFFORT_OPTIONS_BY_KIND[kind], {
        prompt = "Thinking effort:",
        format_item = function(e)
          if e:lower() == "high" then return e .. " (default)" end
          return e
        end,
      }, function(effort)
        if effort == nil then return end
        local sel = { mode = mode, model = model, effort = effort }
        _dvsc_write_cache(sel)
        cb(sel)
      end)
    end)
  end)
end

-- Pick a model (+ thinking/reasoning effort when the model supports it) for a
-- direct broker agent, scoped to `provider`. Mirrors `_dvsc_select` minus the
-- harness dimension, with a per-provider cache slot. Invokes `cb({ model, effort? })`.
-- `force=true` (the `<leader>aG` path) always re-prompts.
local function _direct_select(provider, force, cb)
  local cache = _direct_read_cache()
  local slot = cache[provider]
  if not force and slot and slot.model then
    local kind = _dvsc_reasoning_kind(slot.model)
    if kind == nil or slot.effort then
      return cb(slot)
    end
  end
  _dvsc_pick(_models_for_provider(provider), "Model:", function(model)
    local kind = _dvsc_reasoning_kind(model)
    if kind == nil then
      local sel = { model = model }
      cache[provider] = sel
      _direct_write_cache(cache)
      return cb(sel)
    end
    vim.ui.select(EFFORT_OPTIONS_BY_KIND[kind], {
      prompt = "Thinking effort:",
      format_item = function(e)
        if e:lower() == "high" then return e .. " (default)" end
        return e
      end,
    }, function(effort)
      if effort == nil then return end
      local sel = { model = model, effort = effort }
      cache[provider] = sel
      _direct_write_cache(cache)
      cb(sel)
    end)
  end)
end

-- Translate a `_direct_select` result for `adapter_name` into the two pending
-- channels. claude thinking effort → `_meta.claudeCode.options.maxThinkingTokens`
-- (creation-time only); model is always applied post-establish via config option.
-- codex applies both model and effort post-establish.
local function _direct_prime(adapter_name, sel)
  local provider = DIRECT_ADAPTERS[adapter_name].provider
  _direct.pending_meta = nil
  _direct.pending_apply = { provider = provider, model = sel.model, effort = sel.effort }
  if provider == "anthropic" and sel.effort then
    local tokens = CLAUDE_EFFORT_TOKENS[sel.effort:lower()]
    if tokens then
      _direct.pending_meta = { claudeCode = { options = { maxThinkingTokens = tokens } } }
    end
  end
end

-- Apply `_direct.pending_apply` to a freshly launched chat via live config
-- options. Model is set for both agents (with a fuzzy fallback when the
-- hardcoded catalog id doesn't match the agent's advertised value id); reasoning
-- effort is set for codex only (claude thinking rides session/new `_meta`).
-- Polls for connection readiness + non-empty config options, mirroring the
-- `try_load` pattern in `_broker_open_chat_with_session`. The set calls run
-- inside `async_utils.sync` so `send_rpc_request` takes the coroutine-yielding
-- path rather than blocking the editor on `vim.wait`.
local function _direct_apply_pending(chat)
  local apply = _direct.pending_apply
  _direct.pending_apply = nil
  if not apply or not chat then return end

  local Connection = require("codecompanion.acp")
  local async_utils = require("codecompanion.utils.async")

  local function resolve_value(opt, wanted)
    local lw = tostring(wanted):lower()
    for _, v in ipairs(Connection.flatten_config_options(opt.options or {})) do
      local name = tostring(v.name or ""):lower()
      local val = tostring(v.value or ""):lower()
      if val == lw or name == lw or val:find(lw, 1, true) or name:find(lw, 1, true) then
        return v.value
      end
    end
    return nil
  end

  local function run()
    local conn = chat.acp_connection
    async_utils.sync(function()
      if apply.model then
        local model_opt = conn:_find_config_option("model")
        if model_opt then
          conn:set_config_option(model_opt.id, resolve_value(model_opt, apply.model) or apply.model)
        end
      end
      if apply.provider == "openai" and apply.effort then
        for _, o in ipairs(conn:get_config_options()) do
          if o.type == "select" and o.id ~= "model" then
            local hay = ((o.id or "") .. " " .. (o.name or "") .. " " .. (o.category or "")):lower()
            if hay:find("effort", 1, true) or hay:find("reason", 1, true) or hay:find("think", 1, true) then
              local v = resolve_value(o, apply.effort)
              if v then conn:set_config_option(o.id, v) end
              break
            end
          end
        end
      end
    end)()
    vim.schedule(function() pcall(function() chat:update_metadata() end) end)
  end

  local function poll(attempts)
    local conn = chat.acp_connection
    if conn and conn:is_ready() and #(conn:get_config_options() or {}) > 0 then
      return run()
    end
    if attempts <= 0 then return end
    vim.defer_fn(function() poll(attempts - 1) end, 100)
  end
  vim.defer_fn(function() poll(300) end, 50)
end

local function dvsc_pick_and_launch(force)
  _dvsc_select(force, function(sel)
    _dvsc_launch_with(sel.mode, sel.model, sel.effort)
  end)
end

-- Agent-path choices for the top-level picker in `<leader>aG`.
-- `dvsc-core` defers to the existing harness/model/effort flow inside
-- `tab_chat_set_adapter("dvsc_core_broker", …)`. The direct wrappers
-- (`claude_broker` / `codex_broker`) run the provider-scoped `_direct_select`
-- picker (model + effort): the model (both) and reasoning effort (codex) are
-- applied post-establish via live config options, and claude thinking effort is
-- baked into the session/new `_meta` (see `_direct_apply_pending` and the
-- `send_rpc_request` patch below).
local AGENT_PATHS = {
  { label = "dvsc-core (native / claude / codex / metacode)", adapter = "dvsc_core_broker" },
  { label = "Claude (direct via claude-agent-acp)",           adapter = "claude_broker" },
  { label = "Codex (direct via codex-acp)",                   adapter = "codex_broker" },
  { label = "Omnigent (server-owned session, pick agent+model+effort)", adapter = "omnigent" },
}

-- Prime per-adapter spawn state for adapters that read `_dvsc.pending`
-- or otherwise need bookkeeping aligned with a specific chat bufnr.
-- Called immediately before `Chat:change_adapter` so the new ACP
-- connection's spawn callback sees the right state.
local function _prime_adapter_state(bufnr, adapter_name, sel)
  if adapter_name == "dvsc_core_broker" then
    local pending = { mode = sel.mode, model = sel.model }
    local llm_config = _dvsc_build_llm_config(sel.model, sel.effort)
    if llm_config then pending.llm_config = llm_config end
    _dvsc.pending = { dvsc = pending }
    _dvsc.by_chat_bufnr[bufnr] = sel
  else
    _dvsc.by_chat_bufnr[bufnr] = nil
  end
end

-- Switch the current tab's chat to `adapter_name` in-place, reusing the
-- chat buffer and its sibling queue input/status windows.
--
-- Built on `Chat:change_adapter`, which (via
-- `helpers.create_acp_connection` → `async_utils.sync`) sets up the new
-- ACP connection inside a coroutine so `send_rpc_request` takes the
-- yielding path. That avoids the `vim.wait()` polling loop that blocks
-- the editor on first-message session establishment when a connection
-- is created lazily from `_submit_acp`'s sync call chain.
--
-- Workarounds for upstream behavior:
--   * `Chat:change_adapter` nils `acp_connection` without disconnecting
--     it, leaking the agent process. We disconnect explicitly first.
--   * `Chat:change_adapter` blocks swaps between adapters when there
--     are tool calls or reasoning blocks in history. Pass `clear=true`
--     to wipe history first so the swap is permitted (and to start the
--     new adapter from a blank chat — usually what you want when
--     switching agents).
--
-- For `dvsc_core_broker`, the picker runs through `_dvsc_select`
-- (interactive or cached based on `force_pick`) and primes
-- `_dvsc.pending` + `_dvsc.by_chat_bufnr[bufnr]` before the swap so the
-- new spawn picks up the right mode/model/effort without firing
-- `CodeCompanionChatOpened` (which is what normally consumes
-- `_dvsc.launch_queue`).
--
-- If no chat exists in the tab, falls through to the normal launch
-- path (`dvsc_pick_and_launch` for the broker, `tab_chat_open_or_toggle`
-- otherwise).
--
-- @param adapter_name string
-- @param opts? { clear?: boolean, force_pick?: boolean }
local function tab_chat_set_adapter(adapter_name, opts)
  opts = opts or {}
  local bufnr = vim.t.codecompanion_chat_bufnr
  local chat = bufnr
    and vim.api.nvim_buf_is_valid(bufnr)
    and require("codecompanion").buf_get_chat(bufnr)

  if not chat then
    if adapter_name == "dvsc_core_broker" then
      return dvsc_pick_and_launch(opts.force_pick or false)
    end
    if DIRECT_ADAPTERS[adapter_name] and opts.force_pick then
      return _direct_select(DIRECT_ADAPTERS[adapter_name].provider, opts.force_pick or false, function(sel)
        _direct_prime(adapter_name, sel)
        tab_chat_open_or_toggle({ adapter = adapter_name })
        vim.defer_fn(function()
          local b = vim.t.codecompanion_chat_bufnr
          local c = b and vim.api.nvim_buf_is_valid(b) and require("codecompanion").buf_get_chat(b)
          if c then _direct_apply_pending(c) end
        end, 50)
      end)
    end
    if adapter_name == "omnigent" then
      -- The chosen agent is cached and read back by the omnigent adapter function
      -- at spawn (like `_dvsc.pending`, but the cache IS the source of truth since
      -- the selection is just the agent name).
      return _omnigent_select(opts.force_pick or false, function()
        tab_chat_open_or_toggle({ adapter = "omnigent" })
      end)
    end
    return tab_chat_open_or_toggle({ adapter = adapter_name })
  end

  if chat.current_request then
    return vim.notify(
      "Chat has a request in progress; cancel before switching adapters.",
      vim.log.levels.WARN
    )
  end

  -- Pre-flight: when keeping history, change_adapter refuses if any
  -- message has reasoning/tools state and we're crossing adapter
  -- boundaries. Bail before disconnecting so the chat isn't left in a
  -- half-broken state.
  local current_name = chat.adapter and chat.adapter.name
  if not opts.clear and current_name and current_name ~= adapter_name then
    local has_state = vim.iter(chat.messages or {}):any(function(m)
      return m.reasoning ~= nil or (m.tools and m.tools.calls ~= nil)
    end)
    if has_state then
      return vim.notify(
        string.format(
          "Cannot switch from %s to %s after tool calls/reasoning. Pass { clear = true } to start fresh.",
          current_name, adapter_name
        ),
        vim.log.levels.WARN
      )
    end
  end

  local function apply(sel)
    if chat.adapter and chat.adapter.type == "acp" and chat.acp_connection then
      pcall(function() chat.acp_connection:disconnect() end)
    end
    -- Symmetric to the ACP disconnect: an in-place swap away from an omnigent
    -- chat must tear down the outgoing SSE subscription, or it leaks (the durable
    -- server session lives on, but this editor's stream job would keep running).
    if chat.adapter and chat.adapter.type == "omnigent" and chat.omnigent_session then
      pcall(function() chat.omnigent_session:stop_stream() end)
    end
    chat.acp_session_id = nil
    -- The current session is being torn down; unpin so the winbar re-pins
    -- to the new adapter's session on first establish.
    pcall(function() require("lib.codecompanion-chatinfo").reset(bufnr) end)
    if opts.clear then
      chat:clear()
      -- Transcript wiped: drop section timestamps so stale times don't
      -- bottom-align onto the now-empty chat.
      pcall(function() require("lib.codecompanion-timing").reset(bufnr) end)
    end
    _prime_adapter_state(bufnr, adapter_name, sel or {})
    chat:change_adapter(adapter_name)
  end

  if adapter_name == "dvsc_core_broker" then
    return _dvsc_select(opts.force_pick or false, apply)
  end
  if DIRECT_ADAPTERS[adapter_name] and opts.force_pick then
    return _direct_select(DIRECT_ADAPTERS[adapter_name].provider, opts.force_pick or false, function(sel)
      _direct_prime(adapter_name, sel)
      apply(nil)
      _direct_apply_pending(chat)
    end)
  end
  if adapter_name == "omnigent" then
    return _omnigent_select(opts.force_pick or false, function() apply(nil) end)
  end
  apply(nil)
end

local function tab_chat_pick_agent_and_set(opts)
  opts = opts or {}
  vim.ui.select(AGENT_PATHS, {
    prompt = "Agent:",
    format_item = function(item) return item.label end,
  }, function(choice)
    if choice == nil then return end
    tab_chat_set_adapter(choice.adapter, opts)
  end)
end

-- Full refresh for `<leader>aZ`: close the current tab's chat outright (tearing
-- down its buffer + queue panes), then open a brand-new one via the agent
-- picker with a model/config re-prompt. Unlike `<leader>aG`, which swaps in
-- place via `change_adapter` and reuses the buffer, this is a clean
-- close-and-reopen. The agent picker runs first so cancelling it leaves the
-- existing chat untouched; the close happens only once a choice is made.
-- `chat:close()` fires `CodeCompanionChatClosed` synchronously, which clears
-- `vim.t.codecompanion_chat_bufnr`, so the subsequent `tab_chat_set_adapter`
-- takes its no-chat fresh-launch path.
local function tab_chat_full_refresh()
  vim.ui.select(AGENT_PATHS, {
    prompt = "Agent:",
    format_item = function(item) return item.label end,
  }, function(choice)
    if choice == nil then return end
    local bufnr = vim.t.codecompanion_chat_bufnr
    local chat = bufnr
      and vim.api.nvim_buf_is_valid(bufnr)
      and require("codecompanion").buf_get_chat(bufnr)
    if chat then
      if chat.current_request then
        return vim.notify(
          "Chat has a request in progress; cancel before refreshing.",
          vim.log.levels.WARN
        )
      end
      chat:close()
    end
    tab_chat_set_adapter(choice.adapter, { clear = true, force_pick = true })
  end)
end

-- ── Omnigent live session: change model / effort mid-session ────────────────
--
-- The omnigent analog of `tab_chat_pick_option`'s ACP path. omnigent makes both
-- model and reasoning effort live-mutable via PATCH /v1/sessions/{id}
-- (Session:set_model / Session:set_config), so -- unlike the dvsc/direct claude
-- path, where thinking is baked at session/new and needs a full restart -- this
-- is an in-place change, no relaunch. Concrete values only; to reset to the
-- agent's default, relaunch via <leader>aA and pick "default".

-- Resolve the model/effort family for a live omnigent chat: prefer the current
-- model's vendor token, else the session agent's harness (looked up by id).
local function _omnigent_session_family(session)
  local fam = _omnigent_family_for_model(session.model_override or session.model)
  if fam then return fam end
  if session.agent_id then
    local agents = _omnigent_pickable_agents()
    if agents then
      for _, a in ipairs(agents) do
        if a.id == session.agent_id then
          return _omnigent_family_for_harness(a.harness)
        end
      end
    end
  end
  return nil
end

local function _omnigent_pick_live_option(chat)
  local session = chat.omnigent_session
  if not session or not session.session_id then
    return vim.notify("Omnigent chat has no live session yet.", vim.log.levels.WARN)
  end
  local family = _omnigent_session_family(session)
  local items = {
    { label = "Model  (current: " .. tostring(session.model_override or session.model or "default") .. ")", kind = "model" },
    { label = "Effort (current: " .. tostring(session.reasoning_effort or "default") .. ")", kind = "effort" },
  }
  vim.ui.select(items, {
    prompt = "Omnigent session:",
    format_item = function(it) return it.label end,
  }, function(choice)
    if choice == nil then return end
    if choice.kind == "model" then
      _omnigent_pick_model(family, false, function(model)
        local ok, perr = session:set_model(model)
        if ok then
          pcall(function() chat:update_metadata() end)
          vim.notify("omnigent model → " .. model, vim.log.levels.INFO)
        else
          vim.notify("omnigent: failed to set model: " .. (perr and perr.message or "?"), vim.log.levels.ERROR)
        end
      end)
    else
      _omnigent_pick_effort(family, false, function(effort)
        local ok, perr = session:set_config("reasoning_effort", effort)
        if ok then
          pcall(function() chat:update_metadata() end)
          vim.notify("omnigent effort → " .. effort, vim.log.levels.INFO)
        else
          vim.notify("omnigent: failed to set effort: " .. (perr and perr.message or "?"), vim.log.levels.ERROR)
        end
      end)
    end
  end)
end

-- Interactive picker for the current chat's live ACP session config
-- options. Reads `chat.acp_connection:get_config_options()` — the
-- discrete-choice settings the running agent advertises (for dvsc:
-- mode, model, and any other knobs the wrapper exposes via
-- `configOptions`; for claude-code: typically just model) — and
-- applies changes via `session/set_config_option` over the existing
-- session.
--
-- Wrapped in `async_utils.sync(...)()` so `send_rpc_request` takes the
-- coroutine-yielding path inside `Connection:set_config_option`,
-- matching the pattern in `helpers.create_acp_connection`. Without
-- this, the call goes through `vim.wait()` polling and freezes the
-- editor while the agent applies the change (e.g. dvsc-core reloading
-- its model snapshot when mode flips).
--
-- Limitation: only options the agent actually exposes via
-- `configOptions` are settable live. Anything the dvsc-core-acp
-- wrapper bakes into `_meta.broker.client.metadata.dvsc.llm_config` at
-- session creation (e.g. provider-shaped `reasoning_config` for
-- thinking effort, if not also re-exposed as a SessionConfigOption)
-- requires a session restart — `<leader>aG`/`<leader>aZ` (force-pick +
-- clear) do the full re-prompt, `<leader>ag` reuses the cached selection.
local function tab_chat_pick_option()
  local bufnr = vim.t.codecompanion_chat_bufnr
  local chat = bufnr
    and vim.api.nvim_buf_is_valid(bufnr)
    and require("codecompanion").buf_get_chat(bufnr)
  if not chat then
    return vim.notify("No CodeCompanion chat in this tab.", vim.log.levels.WARN)
  end
  if chat.adapter and chat.adapter.type == "omnigent" then
    return _omnigent_pick_live_option(chat)
  end
  if not chat.adapter or chat.adapter.type ~= "acp" or not chat.acp_connection then
    return vim.notify("Current chat has no live ACP connection.", vim.log.levels.WARN)
  end

  local Connection = require("codecompanion.acp")
  local async_utils = require("codecompanion.utils.async")

  local options = vim.tbl_filter(function(o)
    return o.type == "select"
  end, chat.acp_connection:get_config_options() or {})

  if #options == 0 then
    return vim.notify("Agent exposes no selectable config options.", vim.log.levels.WARN)
  end

  local function value_label(opt, value_id)
    for _, v in ipairs(Connection.flatten_config_options(opt.options or {})) do
      if v.value == value_id then
        return v.name or v.value
      end
    end
    return value_id or "<unset>"
  end

  vim.ui.select(options, {
    prompt = "Config option:",
    format_item = function(o)
      return string.format("%s: %s", o.name or o.category or o.id, value_label(o, o.currentValue))
    end,
  }, function(opt)
    if not opt then return end

    local values = Connection.flatten_config_options(opt.options or {})
    if #values == 0 then
      return vim.notify(
        string.format("Option `%s` has no available values.", opt.name or opt.id),
        vim.log.levels.WARN
      )
    end

    vim.ui.select(values, {
      prompt = string.format("%s:", opt.name or opt.category or opt.id),
      format_item = function(v)
        local label = v.name or v.value
        if v.group then label = string.format("[%s] %s", v.group, label) end
        if v.value == opt.currentValue then label = label .. "  (current)" end
        return label
      end,
    }, function(value)
      if not value or value.value == opt.currentValue then return end

      async_utils.sync(function()
        local ok = chat.acp_connection:set_config_option(opt.id, value.value)
        vim.schedule(function()
          if ok then
            chat:update_metadata()
            vim.notify(
              string.format("%s → %s", opt.name or opt.id, value.name or value.value),
              vim.log.levels.INFO
            )
          else
            vim.notify(
              string.format("Failed to set %s.", opt.name or opt.id),
              vim.log.levels.ERROR
            )
          end
        end)
      end)()
    end)
  end)
end

-- Animated "Compacting…" indicator pinned to the end of the chat buffer.
-- Returns a stop() that cancels the timer and clears the extmark. Used by both
-- compaction paths so the in-progress state is visible regardless of adapter.
local function start_compaction_spinner(bufnr)
  local ns = vim.api.nvim_create_namespace("codecompanion_compaction_spinner")
  local frames = { "⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏" }
  local timer = vim.uv.new_timer()
  local frame = 0
  local stopped = false
  timer:start(0, 80, function()
    vim.schedule(function()
      -- Guard against an in-flight tick repainting after stop() cleared us.
      if stopped or not vim.api.nvim_buf_is_valid(bufnr) then
        return
      end
      pcall(vim.api.nvim_buf_clear_namespace, bufnr, ns, 0, -1)
      local last = vim.api.nvim_buf_line_count(bufnr) - 1
      pcall(vim.api.nvim_buf_set_extmark, bufnr, ns, last, 0, {
        virt_text = { { frames[frame + 1] .. " Compacting context…", "Comment" } },
        virt_text_pos = "eol",
      })
      frame = (frame + 1) % #frames
    end)
  end)
  return function()
    stopped = true
    pcall(function() timer:stop() end)
    pcall(function() timer:close() end)
    if vim.api.nvim_buf_is_valid(bufnr) then
      pcall(vim.api.nvim_buf_clear_namespace, bufnr, ns, 0, -1)
    end
  end
end

-- True if the chat's live ACP session advertises a slash command named
-- `compact` (e.g. claude-agent-acp forwards Claude Code's `/compact`). This is
-- how we detect compaction support for non-dvsc agents.
local function acp_session_has_compact(chat)
  local conn = chat and chat.acp_connection
  if not conn or not conn.session_id then
    return false
  end
  local commands = require("codecompanion.interactions.chat.acp.commands")
    .get_commands_for_session(conn.session_id)
  for _, c in ipairs(commands) do
    if c.name == "compact" then
      return true
    end
  end
  return false
end

-- dvsc compaction via the wrapper's `dm-core/compact` ext RPC.
--
-- The RPC returns only `{ compacted = bool }` — never the summary text — so we
-- cannot show the model's actual post-compaction context. We therefore KEEP the
-- full local transcript (for the human's reference) and append a boundary
-- marker recording where the model's memory was condensed. Retaining history is
-- safe: `form_messages` (adapters/acp/helpers.lua) only re-sends user messages
-- with `not _meta.sent`, so old turns are never re-sent to the agent.
local function dvsc_compact(chat)
  local async_utils = require("codecompanion.utils.async")
  local cc_config = require("codecompanion.config")
  local parser = require("codecompanion.interactions.chat.parser")
  local tags = require("codecompanion.interactions.shared.tags")

  local stop_spinner = start_compaction_spinner(chat.bufnr)

  async_utils.sync(function()
    local resp = chat.acp_connection:send_rpc_request("dm-core/compact", {
      sessionId = chat.acp_connection.session_id,
      triggerType = "CommandButton",
    })

    vim.schedule(function()
      stop_spinner()
      if not resp or not resp.compacted then
        return vim.notify("ACP compact was not performed.", vim.log.levels.WARN)
      end

      -- Keep the full transcript; just record where compaction happened.
      chat:add_message({
        role = cc_config.constants.LLM_ROLE,
        content = "───────── context compacted ─────────\n"
          .. "The transcript above is retained for your reference, but is no "
          .. "longer in the model's context.",
      }, {
        _meta = { tag = tags.COMPACT_SUMMARY },
      })

      -- `UI:render` mutates the messages table it receives while stripping the
      -- final draft line, so render from a deep copy rather than `chat.messages`.
      chat.ui:render(vim.deepcopy(chat.buffer_context), vim.deepcopy(chat.messages), {
        stop_context_insertion = true,
      })
      chat:set_system_prompt()

      local header_line = parser.headers(chat, chat.chat_parser)
      chat.header_line = header_line and (header_line + 1) or 1
      chat._last_role = cc_config.constants.LLM_ROLE
      chat:ready_for_input()
      chat:checkpoint()
      lock_chat_buf(chat.bufnr)

      vim.notify("CodeCompanion chat compacted.", vim.log.levels.INFO)
    end)
  end)()
end

-- Compaction for any ACP agent that advertises a `compact` slash command
-- (claude_code / claude_broker, and codex if it exposes it). We submit
-- `\compact` through the normal pipeline; ACPHandler:transform_acp_commands
-- rewrites `\compact` → `/compact` on the wire, the agent runs compaction and
-- streams its own "Compacting…/Compacting completed." messages — which serve as
-- the in-history reference. History is never cleared on this path.
local function agent_command_compact(chat)
  local trigger = require("codecompanion.triggers").mappings.acp_slash_commands
  local stop_spinner = start_compaction_spinner(chat.bufnr)

  -- Stop the spinner once this compaction request completes.
  local grp = vim.api.nvim_create_augroup("cc_compact_" .. chat.bufnr, { clear = true })
  vim.api.nvim_create_autocmd("User", {
    group = grp,
    pattern = "CodeCompanionRequestFinished",
    callback = function(args)
      if args.data and args.data.bufnr == chat.bufnr then
        stop_spinner()
        pcall(vim.api.nvim_del_augroup_by_id, grp)
      end
    end,
  })

  chat:add_buf_message({
    role = require("codecompanion.config").constants.USER_ROLE,
    content = trigger .. "compact",
  })
  chat:submit()
end

-- Compact the current tab's chat for any agent that supports it.
--   * dvsc (dvsc_core / dvsc_core_broker) → `dm-core/compact` ext RPC.
--   * any other agent advertising a `compact` slash command → `/compact`.
-- The full transcript is always retained; the model's compacted context is
-- never returned to the client (the wrapper drops compaction_delta and the dvsc
-- RPC returns only a bool), so the local view intentionally holds more than the
-- model's actual context.
local function tab_chat_compact()
  local bufnr = vim.t.codecompanion_chat_bufnr
  local chat = bufnr
    and vim.api.nvim_buf_is_valid(bufnr)
    and require("codecompanion").buf_get_chat(bufnr)
  if not chat then
    return vim.notify("No CodeCompanion chat in this tab.", vim.log.levels.WARN)
  end
  if not chat.adapter or chat.adapter.type ~= "acp" or not chat.acp_connection then
    return vim.notify("Current chat has no live ACP connection.", vim.log.levels.WARN)
  end
  if chat.current_request then
    return vim.notify("Wait for the current request to finish before compacting.", vim.log.levels.WARN)
  end

  local adapter_name = chat.adapter.name
  if adapter_name == "dvsc_core" or adapter_name == "dvsc_core_broker" then
    return dvsc_compact(chat)
  end
  if acp_session_has_compact(chat) then
    return agent_command_compact(chat)
  end
  return vim.notify(
    string.format("Adapter `%s` does not support compaction.", adapter_name),
    vim.log.levels.WARN
  )
end

return {
  {
    "mkarrmann/codecompanion.nvim", -- fork: adds the native omnigent adapter (see <leader>aM)
    dependencies = {
      "nvim-lua/plenary.nvim",
      "nvim-treesitter/nvim-treesitter",
      "franco-ruggeri/codecompanion-spinner.nvim",
    },
    opts = function()
      local has_snacks = pcall(require, "snacks")

      local function discover_skills()
        local skills = {}
        local seen = {}

        local function add(name, path)
          local real = vim.uv.fs_realpath(path) or path
          if seen[real] then return end
          seen[real] = true
          skills[#skills + 1] = { name = name, path = real }
        end

        for _, dir in ipairs({
          vim.fn.expand("~/dotfiles/agent_config/skills"),
          vim.fn.expand("~/.claude/skills"),
        }) do
          for _, path in ipairs(vim.fn.globpath(dir, "*/SKILL.md", false, true)) do
            add(vim.fn.fnamemodify(path, ":h:t"), path)
          end
        end

        local manifest_path = vim.fn.expand("~/.claude/.claude-templates-manifest.json")
        if vim.fn.filereadable(manifest_path) == 1 then
          local ok, text = pcall(vim.fn.readfile, manifest_path)
          if ok then
            local manifest = vim.json.decode(table.concat(text, "\n"))
            local installed = (manifest.components or {}).skills or {}
            local base = "/opt/facebook/claude-templates-cli/components"
            for comp_name, _ in pairs(installed) do
              for _, subdir in ipairs({ "skills", "plugins" }) do
                local skill_path = base .. "/" .. subdir .. "/" .. comp_name .. "/SKILL.md"
                if vim.fn.filereadable(skill_path) == 1 then
                  add(comp_name, skill_path)
                end
              end
            end
          end
        end

        table.sort(skills, function(a, b) return a.name < b.name end)
        return skills
      end

      return {
        interactions = {
          chat = {
            adapter = "claude_code",
            slash_commands = {
              ["skill"] = {
                description = "Load a Claude Code skill",
                callback = function(chat)
                  local skills = discover_skills()
                  if #skills == 0 then
                    return vim.notify("No skills found", vim.log.levels.WARN)
                  end
                  vim.ui.select(skills, {
                    prompt = "Select skill:",
                    format_item = function(item) return item.name end,
                  }, function(choice)
                    if not choice then return end
                    local content = table.concat(vim.fn.readfile(choice.path), "\n")
                    local preamble = "Follow the instructions in this skill for all subsequent messages:\n\n"
                    chat:add_buf_message({ role = "user", content = preamble .. content })
                    chat:submit()
                    vim.notify("Loaded skill: " .. choice.name)
                  end)
                end,
              },
              ["join"] = {
                description = "Join an existing broker session",
                callback = function(chat)
                  local conn = require("codecompanion.acp").new({ adapter = chat.adapter })
                  if not conn:connect_and_authenticate() then
                    return vim.notify("Failed to connect to broker", vim.log.levels.ERROR)
                  end
                  local sessions = conn:session_list()
                  pcall(function() conn:disconnect() end)
                  if vim.tbl_isempty(sessions) then
                    return vim.notify("No active broker sessions", vim.log.levels.WARN)
                  end
                  vim.ui.select(sessions, {
                    prompt = "Join broker session:",
                    format_item = function(item) return item.sessionId or "<unknown>" end,
                  }, function(choice)
                    if not choice then return end
                    require("codecompanion.interactions.chat").new({
                      adapter = chat.adapter,
                      acp_session_id = choice.sessionId,
                    })
                  end)
                end,
              },
              ["resume"] = {
                description = "Resume a saved session via session/load(bsid)",
                callback = function(chat)
                  -- Sends `session/load(bsid)` to the broker. Same-broker
                  -- semantics: the broker live-joins the in-memory session
                  -- (if alive) or replays via the persistence plugin's
                  -- `try_resume`. Cross-broker resume is rejected — the
                  -- standalone `meta.broker.persistence.resume_saved_session`
                  -- wire method was deleted (commits 7db4fed/c458043).
                  -- Use `/fork` instead for cross-broker.
                  --
                  -- Look up bsids via:
                  --   sqlite3 ~/.local/share/acp-persistence-server/persistence.db \
                  --     "SELECT broker_session_id, broker_id,
                  --             json_extract(metadata,'$.broker_client_metadata.nvim_session')
                  --      FROM sessions ORDER BY started_at DESC LIMIT 20;"
                  local default = _broker_read_last_bsid() or "bsid_"
                  vim.ui.input({ prompt = "broker_session_id: ", default = default }, function(bsid)
                    bsid = bsid and vim.trim(bsid)
                    if not bsid or bsid == "" or bsid == "bsid_" then return end
                    _broker_write_last_bsid(bsid)
                    require("codecompanion.interactions.chat").new({
                      adapter = chat.adapter,
                      acp_session_id = bsid,
                    })
                  end)
                end,
              },
              ["fork"] = {
                description = "Fork a saved session onto the local broker",
                callback = function(chat)
                  -- Calls `meta.broker.persistence.fork_saved_session` —
                  -- mints a fresh session whose history is replayed into
                  -- the local broker. Cross-broker capable; the new
                  -- session's persistence record carries
                  -- `parent = source_bsid`.
                  local default = _broker_read_last_bsid() or "bsid_"
                  vim.ui.input({ prompt = "Fork bsid: ", default = default }, function(bsid)
                    bsid = bsid and vim.trim(bsid)
                    if not bsid or bsid == "" or bsid == "bsid_" then return end
                    _broker_write_last_bsid(bsid)
                    local new_bsid = _broker_fork_saved_session(chat.adapter, bsid)
                    if not new_bsid then return end
                    vim.notify("forked " .. bsid .. " -> " .. new_bsid, vim.log.levels.INFO)
                    require("codecompanion.interactions.chat").new({
                      adapter = chat.adapter,
                      acp_session_id = new_bsid,
                    })
                  end)
                end,
              },
            },
          },
          inline = { adapter = "ai_gateway" },
          cmd = { adapter = "claude_code" },
        },

        adapters = {
          http = {
            -- Claude Opus 4.8 over Meta's AI Gateway (Vertex upstream) — the
            -- same approved path Claude Code itself uses on this devserver.
            -- Used by the one-shot `inline` strategy, which requires an HTTP
            -- adapter (ACP adapters are session-based and rejected by inline:
            -- interactions/inline/init.lua "Only HTTP adapters are supported").
            --
            -- Auth needs no key-minting: mTLS client cert auto-rotated on disk
            -- at /var/facebook/credentials/$USER/agent_x509/, plus a short-lived
            -- bearer token from Claude Code's managed api-key-helper. The Vertex
            -- rawPredict endpoint returns a standard Anthropic Messages payload,
            -- so we extend the `anthropic` adapter and only adjust transport +
            -- the model-in-URL / anthropic_version-in-body Vertex conventions.
            ai_gateway = function()
              local user = vim.env.USER or vim.fn.expand("$USER")
              local cert = "/var/facebook/credentials/" .. user .. "/agent_x509/claude_code_" .. user .. ".pem"
              local ca = vim.env.CURL_CA_BUNDLE or "/etc/pki/tls/certs/fb_certs.pem"
              local host = "vertex.ai-gateway.fbinfra.net"
              local project = vim.env.ANTHROPIC_VERTEX_PROJECT_ID or "devai-mea-egeit"
              local region = vim.env.CLOUD_ML_REGION or "global"
              local model = "claude-opus-4-8"
              local url = string.format(
                "https://%s/v1/projects/%s/locations/%s/publishers/anthropic/models/%s:rawPredict",
                host,
                project,
                region,
                model
              )
              return require("codecompanion.adapters").extend("anthropic", {
                name = "ai_gateway",
                formatted_name = "AI Gateway (Opus 4.8)",
                url = url,
                env = {
                  -- Short-lived bearer token, re-minted per request (~30m TTL).
                  api_key = "cmd:/usr/local/bin/claude_code/api-key-helper",
                },
                headers = {
                  ["content-type"] = "application/json",
                  ["Authorization"] = "Bearer ${api_key}",
                  ["X-Meta-AI-Gateway-Calling-Product"] = "codecompanion:nvim",
                },
                -- mTLS client cert + CA, and bypass the fwdproxy set in
                -- local/config/meta.lua (the gateway is corpnet-direct).
                raw = { "--cert", cert, "--key", cert, "--cacert", ca, "--noproxy", host },
                schema = {
                  model = { default = model },
                  -- Anthropic requires max_tokens; pin it since model_choice
                  -- can't resolve this (non-catalog) model id to a default.
                  max_tokens = { default = 4096 },
                },
                handlers = {
                  -- Vertex uses Bearer auth + an anthropic_version body field,
                  -- not the Anthropic-direct x-api-key / anthropic-version header.
                  setup = function(self)
                    local base = require("codecompanion.adapters.http.anthropic").handlers.setup
                    local ok = base(self)
                    self.headers["x-api-key"] = nil
                    self.headers["anthropic-version"] = nil
                    return ok
                  end,
                  -- Vertex rawPredict: model is in the URL, not the body, and
                  -- the request must carry anthropic_version.
                  form_parameters = function(self, params, messages)
                    local base = require("codecompanion.adapters.http.anthropic").handlers.form_parameters
                    params = base(self, params, messages) or params
                    params.model = nil
                    params.anthropic_version = "vertex-2023-10-16"
                    return params
                  end,
                },
              })
            end,
          },
          acp = {
            claude_code = function()
              local broker_socket = vim.env.ACP_BROKER_SOCKET
                or ((vim.env.XDG_RUNTIME_DIR or "/tmp") .. "/acp-broker.sock")
              -- Use the -tag wrapper so the broker stamps per-launch
              -- identity onto every session/new envelope, attributing
              -- captured sessions to the right nvim/host/cwd in the
              -- central persistence-server. See acp-broker
              -- docs/RUNBOOK.md §3.3.
              local attach_bin = vim.fn.expand("~/.cargo/bin/acp-broker-attach-tag")

              return require("codecompanion.adapters").extend("claude_code", {
                commands = {
                  default = { attach_bin },
                  yolo = { attach_bin },
                },
                env = {
                  CLAUDE_CODE_OAUTH_TOKEN = "CLAUDE_CODE_OAUTH_TOKEN",
                  ACP_BROKER_SOCKET = broker_socket,
                  ACP_BROKER_CLIENT_METADATA_JSON = build_client_metadata(),
                },
                defaults = {
                  timeout = 120000,
                  mode = "bypassPermissions",
                },
                handlers = {
                  auth = function() return true end,
                },
              })
            end,
            codex = function()
              return require("codecompanion.adapters").extend("codex", {
                env = {},
                handlers = {
                  auth = function() return true end,
                },
              })
            end,
            devmate = function()
              local wrapper = vim.fn.expand("~/devmate-acp/packages/acp-wrapper/dist/index.js")
              local fbsource = vim.fn.expand("~/fbsource")
              -- The wrapper resolves the Devmate bridge binary relative to its
              -- own cwd. CodeCompanion spawns adapters with cwd = vim.fn.getcwd()
              -- and has no per-adapter cwd, so we force it here.
              -- CodeCompanion swallows the spawned process's stderr. Redirect it to a
              -- file so wrapper-side errors (failed bridge spawn, etc.) are visible.
              local stderr_log = vim.fn.expand("~/.local/state/nvim/devmate-acp.stderr.log")
              local launch = string.format("cd %s && exec node %s 2>>%s",
                vim.fn.shellescape(fbsource),
                vim.fn.shellescape(wrapper),
                vim.fn.shellescape(stderr_log))
              return require("codecompanion.adapters").extend("claude_code", {
                name = "devmate",
                formatted_name = "Devmate",
                commands = {
                  default = { "sh", "-c", launch },
                  yolo = { "sh", "-c", launch },
                },
                env = {},
                defaults = {
                  timeout = 120000,
                },
                handlers = {
                  auth = function() return true end,
                },
              })
            end,
            dvsc_core = function()
              local wrapper = vim.fn.expand(
                "~/fbsource/users/mk/mkarrmann/dvsc-core-acp/packages/acp-wrapper/dist/index.js"
              )
              local fbsource = vim.fn.expand("~/fbsource")
              local stderr_log = vim.fn.expand("~/.local/state/nvim/dvsc-core-acp.stderr.log")
              -- /usr/local/bin/node is Node 16, which lacks global `fetch`. The
              -- wrapper's HTTP client to dvsc-core requires Node 18+. fbsource
              -- ships a pinned Node toolchain we use instead.
              local node_bin = vim.fn.expand("~/fbsource/xplat/third-party/node/bin/node")
              local launch = string.format("cd %s && exec %s %s 2>>%s",
                vim.fn.shellescape(fbsource),
                vim.fn.shellescape(node_bin),
                vim.fn.shellescape(wrapper),
                vim.fn.shellescape(stderr_log))
              return require("codecompanion.adapters").extend("claude_code", {
                name = "dvsc_core",
                formatted_name = "Dvsc Core",
                commands = {
                  default = { "sh", "-c", launch },
                  yolo = { "sh", "-c", launch },
                },
                env = {},
                defaults = {
                  timeout = 120000,
                },
                handlers = {
                  auth = function() return true end,
                },
              })
            end,
            -- Broker-fronted variant. Spawns `acp-broker-attach-select-tag`,
            -- which (a) stamps the metadata JSON from
            -- `ACP_BROKER_CLIENT_METADATA_JSON` onto every envelope via
            -- `_meta/broker/connection/set_metadata`, and (b) selects the
            -- broker-registered agent named in `ACP_BROKER_AGENT_NAME` for
            -- this connection's sessions (falling back to spawning
            -- `ACP_BROKER_AGENT_CMD` via `_meta/broker/agent/spawn` if the
            -- name doesn't resolve and the broker has `--allow-agent-spawn`).
            -- The dvsc-core-acp wrapper picks up `mode`/`model`/`thinking_effort`
            -- from the stamped client metadata. Drive via
            -- `dvsc_pick_and_launch` (see `<leader>ag`/`<leader>aG`).
            dvsc_core_broker = function()
              local extra = _dvsc.pending or { dvsc = { mode = "native" } }
              _dvsc.pending = nil
              local payload = build_client_metadata(extra)
              local stderr_log = vim.fn.expand("~/.local/state/nvim/dvsc-core-acp.stderr.log")
              -- Installed into ~/.cargo/bin via `cargo install --path crates/acp-broker`
              -- in ~/repos/acp-broker.
              local attach_bin = vim.fn.expand("~/.cargo/bin/acp-broker-attach-select-tag")
              local agent_cmd = vim.fn.expand("~/bin/dvsc-core-acp")
              local launch = string.format(
                "ACP_BROKER_CLIENT_METADATA_JSON=%s ACP_BROKER_AGENT_NAME=%s ACP_BROKER_AGENT_CMD=%s exec %s 2>>%s",
                vim.fn.shellescape(payload),
                vim.fn.shellescape("dvsc-core"),
                vim.fn.shellescape(agent_cmd),
                vim.fn.shellescape(attach_bin),
                vim.fn.shellescape(stderr_log)
              )
              return require("codecompanion.adapters").extend("claude_code", {
                name = "dvsc_core_broker",
                formatted_name = "Dvsc Core (Broker)",
                commands = {
                  default = { "sh", "-c", launch },
                  yolo = { "sh", "-c", launch },
                },
                env = {},
                defaults = {
                  timeout = 120000,
                },
                handlers = {
                  auth = function() return true end,
                },
              })
            end,
            -- Broker-fronted direct claude-agent-acp variant. Same wrapper as
            -- `dvsc_core_broker`, but pinned to `ACP_BROKER_AGENT_NAME=claude`
            -- so the session is unconditionally routed to the broker's
            -- claude-agent-acp registration rather than dvsc-core-acp. If the
            -- claude-agent-acp registration is also the broker's configured
            -- default, this adapter is observationally identical to letting
            -- the broker pick — the explicit name selection just makes the
            -- routing intent legible regardless of which agent currently
            -- holds the `default = true` slot.
            --
            -- Model + thinking effort are chosen via `_direct_select` when
            -- launched through `<leader>aG`: the model is applied post-establish
            -- as a live config option, and the thinking budget rides session/new
            -- `_meta.claudeCode.options.maxThinkingTokens` (the send_rpc_request
            -- patch below). `<leader>aC` is the plain quick-launch (no picker,
            -- broker/agent default model + thinking).
            claude_broker = function()
              local stderr_log = vim.fn.expand("~/.local/state/nvim/claude-agent-acp.stderr.log")
              local attach_bin = vim.fn.expand("~/.cargo/bin/acp-broker-attach-select-tag")
              local agent_cmd = vim.fn.expand("~/bin/claude-agent-acp")
              local launch = string.format(
                "ACP_BROKER_CLIENT_METADATA_JSON=%s ACP_BROKER_AGENT_NAME=%s ACP_BROKER_AGENT_CMD=%s exec %s 2>>%s",
                vim.fn.shellescape(build_client_metadata()),
                vim.fn.shellescape("claude"),
                vim.fn.shellescape(agent_cmd),
                vim.fn.shellescape(attach_bin),
                vim.fn.shellescape(stderr_log)
              )
              return require("codecompanion.adapters").extend("claude_code", {
                name = "claude_broker",
                formatted_name = "Claude (Broker)",
                commands = {
                  default = { "sh", "-c", launch },
                  yolo = { "sh", "-c", launch },
                },
                env = {},
                defaults = {
                  timeout = 120000,
                },
                handlers = {
                  auth = function() return true end,
                },
              })
            end,
            -- Broker-fronted codex variant. Same wrapper pattern as
            -- `claude_broker`, pinned to `ACP_BROKER_AGENT_NAME=codex` so
            -- the session is routed to the broker's codex-acp registration
            -- (declared in ~/dotfiles/bin-macos/acp-broker-launch). Extends
            -- the upstream `codex` adapter so capability negotiation,
            -- prompt shape, and `auth_method` defaults match the codex
            -- ACP wire protocol; the broker just transports envelopes.
            -- Model + reasoning effort are chosen via `_direct_select` when
            -- launched through `<leader>aG` and applied post-establish as live
            -- config options (codex exposes both — cf. `gpt-5-codex[high]` via
            -- /acp_session_options). `<leader>aO` is the plain quick-launch
            -- (no picker, agent default model + effort).
            codex_broker = function()
              local stderr_log = vim.fn.expand("~/.local/state/nvim/codex-acp.stderr.log")
              local attach_bin = vim.fn.expand("~/.cargo/bin/acp-broker-attach-select-tag")
              local agent_cmd = vim.fn.expand("~/bin/codex-acp")
              local launch = string.format(
                "ACP_BROKER_CLIENT_METADATA_JSON=%s ACP_BROKER_AGENT_NAME=%s ACP_BROKER_AGENT_CMD=%s exec %s 2>>%s",
                vim.fn.shellescape(build_client_metadata()),
                vim.fn.shellescape("codex"),
                vim.fn.shellescape(agent_cmd),
                vim.fn.shellescape(attach_bin),
                vim.fn.shellescape(stderr_log)
              )
              return require("codecompanion.adapters").extend("codex", {
                name = "codex_broker",
                formatted_name = "Codex (Broker)",
                commands = {
                  default = { "sh", "-c", launch },
                  yolo = { "sh", "-c", launch },
                },
                env = {},
                defaults = {
                  timeout = 120000,
                },
                handlers = {
                  auth = function() return true end,
                },
              })
            end,
          },
          -- Native Omnigent (REST + SSE) sessions. Unlike acp/http this is a
          -- durable, server-owned session the editor observes -- the substrate
          -- for later resume/attach and background wakeups (see
          -- ~/repos/codecompanion.nvim/.codecompanion/omnigent-native-progress.md).
          -- Extend the family's builtin "default" via the family module directly;
          -- extending "omnigent" would recurse (the family key IS this function)
          -- and routing "default" through the top-level extend() misfires to http.
          omnigent = {
            omnigent = function()
              local sel = _omnigent_read_selection()
              return require("codecompanion.adapters.omnigent").extend("default", {
                name = "omnigent",
                formatted_name = "Omnigent",
                url = vim.env.OMNIGENT_URL or "http://127.0.0.1:6767",
                defaults = {
                  -- Agent + model + effort come from the launch-time picker
                  -- (<leader>aM reuses the remembered selection; <leader>aA /
                  -- <leader>aG re-pick), cached in OMNIGENT_AGENT_CACHE_PATH and
                  -- read back here at spawn. Agent falls back to `polly` (a
                  -- claude-sdk agent: streams output_text + surfaces
                  -- elicitations) before any pick; it is the session harness and
                  -- is immutable after create. model_override / reasoning_effort
                  -- are nil unless picked (=> the agent spec's own defaults
                  -- apply); Session:create forwards them at create, and both stay
                  -- switchable mid-session via <leader>ao.
                  agent = sel.agent or "polly",
                  model_override = sel.model,
                  reasoning_effort = sel.effort,
                  host = "auto", -- fail-closed FQDN match to this machine
                  workspace = "auto", -- cwd, only when the resolved host is local
                  -- Correlation identity for external mappers (the Orchest
                  -- omnigent-bridge). host_id + workspace already let Orchest
                  -- attribute a session to a checkout; nvim_session is the one
                  -- signal host+cwd can't derive (two chats in one checkout) --
                  -- mirroring why the acp-bridge keys on byNvimSession. A future
                  -- Orchest-minted workspace id drops in here without a reshape.
                  -- Evaluated at session-create time to capture the launching tab.
                  labels = function()
                    local labels = { ["orchest.nvim_session"] = vim.env.NVS_SESSION_NAME or "ad-hoc" }
                    local tab = vim.t.tab_name
                    if type(tab) == "string" and tab ~= "" then
                      labels["orchest.tab"] = tab
                    end
                    return labels
                  end,
                },
                opts = {
                  -- M4 has landed: keep the SSE stream open at attach so
                  -- externally-triggered background turns (wakeups, another
                  -- client) render while the chat is idle, and auto-reconnect a
                  -- dropped stream (the observer's content-dedup makes the
                  -- stream-first replay safe).
                  background_updates = true,
                  stream_heartbeat_timeout = 45000,
                },
              })
            end,
          },
        },

        opts = {
          log_level = "DEBUG",
        },

        extensions = {
          spinner = {},
        },

        display = {
          action_palette = {
            provider = has_snacks and "snacks" or "telescope",
          },
          chat = {
            window = {
              layout = "vertical",
              position = "right",
              width = 0.42,
              full_height = true,
              -- Make upstream's Chat lifecycle tab-aware. Without this,
              -- `Chat.new` calls `close_last_chat` which hides whatever
              -- chat is currently visible — including chats in *other*
              -- tabs. Setting pertab=true makes close_last_chat skip
              -- chats visible in non-current tabs (chat/init.lua:2025).
              -- Our `tab_chat_open_or_toggle` still enforces the
              -- one-chat-per-tab invariant on top.
              pertab = true,
            },
          },
        },
      }
    end,

    config = function(_, opts)
      require("codecompanion").setup(opts)

      -- Inject an extra system-role prompt into every inline invocation to
      -- steer placement decisions. Mirrors CodeCompanion.inline
      -- (init.lua:39-45) but adds `prompts` to Inline.new(), which
      -- make_ext_prompts forwards alongside the built-in system prompt
      -- (interactions/inline/init.lua:361-404). The addendum stacks on top of
      -- the baked-in SYSTEM_PROMPT (init.lua:52-73) rather than replacing it,
      -- since that one is a local CONSTANTS field and not reachable from
      -- config.
      do
        local api = vim.api
        local cc = require("codecompanion")
        local ctx = require("codecompanion.utils.context")
        local Inline = require("codecompanion.interactions.inline")

        local INLINE_SYSTEM_ADDENDUM = [[
Placement guidance (overrides the base prompt where they conflict):
- Terse directives ("use modern bash", "make it faster", "rename X to Y")
  are edits, not questions — pick replace/add/before/new. Default to
  "replace" when a visual selection exists, "add" at cursor otherwise.
- "chat" is appropriate ONLY for one of:
  (a) a literal question about code ("what does this do?", "why does this fail?"), or
  (b) a genuine issue, ambiguity, correctness concern, complexity, or hidden
      gotcha that the user is plausibly overlooking and that deserves to be
      surfaced before you produce code. In this case, briefly explain the
      concern in chat rather than silently guessing.
- Do NOT restate the user's prompt in code comments.
]]

        cc.inline = function(args)
          local context = ctx.get(api.nvim_get_current_buf(), args)
          local inline = Inline.new({
            buffer_context = context,
            prompts = {
              {
                role = "system",
                opts = { visible = false },
                content = INLINE_SYSTEM_ADDENDUM,
              },
            },
          })
          if inline then
            inline:prompt(args.args)
          end
        end
      end

      -- Full, untruncated single-line tool-call header.
      --
      -- Upstream's acp/formatters.tool_message caps the label at MAX_TITLE=60
      -- (formatters.lua) and shortens cwd-relative paths, so commands and paths
      -- get cut (e.g. "Execute: Running cd ~/checkout2/fbsource 2>/dev/null …").
      -- We reimplement the label to show the complete command / absolute path on
      -- one line. It stays single-line so CodeCompanion's in-place streaming
      -- update (handler.update_buf_line, which replaces exactly one line) keeps
      -- working. The tool *output* is shown separately, collapsibly, via
      -- lib.codecompanion-tool-output (see the process_tool_call wrap below).
      --
      -- Still handles the dvsc-core "other" tools whose JSON-ish titles
      -- (`skill {"path":...}`) upstream mis-parses into `Other: skill {"path"`.
      local acp_formatters = require("codecompanion.interactions.chat.acp.formatters")
      local orig_tool_message = acp_formatters.tool_message

      local function format_kind(kind)
        if not kind or kind == "" then return "Tool" end
        local s = tostring(kind):gsub("_", " ")
        return s:sub(1, 1):upper() .. s:sub(2)
      end

      local function full_tool_header(tool_call)
        local kind = format_kind(tool_call.kind)
        local target

        -- Prefer concrete file targets (full, absolute — not cwd-relative).
        local loc = tool_call.locations and tool_call.locations[1]
        if loc and type(loc.path) == "string" and loc.path ~= "" then
          target = loc.path
        elseif type(tool_call.content) == "table" then
          for _, c in ipairs(tool_call.content) do
            if c and c.type == "diff" and type(c.path) == "string" and c.path ~= "" then
              target = c.path
              break
            end
          end
        end

        -- dvsc-core "other" tools: pull the structured rawInput.path and the
        -- leading verb from the title rather than parsing the JSON-ish title.
        if not target and tool_call.kind == "other" then
          local raw = tool_call.rawInput
          local path = type(raw) == "table" and raw.path
          if type(path) == "string" and path ~= "" then
            kind = ((tool_call.title or ""):match("^(%S+)") or "Tool"):gsub("^%l", string.upper)
            target = path
          end
        end

        -- Fallback: the full title, collapsed to one line, untruncated.
        if not target then
          local title = tool_call.title or "Tool call"
          title = title:gsub("\r?\n", " "):gsub("%s+", " ")
          title = title:match("^%s*(.-)%s*$") or title
          title = title:gsub("^`(.+)`$", "%1")
          target = (title ~= "" and title) or "Tool call"
        end

        local s = (kind .. ": " .. target):gsub("`", ""):gsub("\r?\n", " ")
        return s
      end

      function acp_formatters.tool_message(tool_call, adapter)
        if type(tool_call) ~= "table" then
          return orig_tool_message(tool_call, adapter)
        end
        local ok, s = pcall(full_tool_header, tool_call)
        if ok and type(s) == "string" and s ~= "" then
          return s
        end
        return orig_tool_message(tool_call, adapter)
      end

      -- Collapsible full tool *output*, rendered as virtual lines beneath the
      -- header line (see lib.codecompanion-tool-output for the rationale). We
      -- wrap ACPHandler:process_tool_call: let upstream render/stream the
      -- single-line header as usual, then on completion attach the full output
      -- as a collapsed virt_lines block on the header's line. `merge_tool_call`
      -- is re-implemented from handler.lua (module-local there) so we can read
      -- the merged status/content before upstream clears self.tools[id].
      local tool_output = require("lib.codecompanion-tool-output")
      local ACPHandler = require("codecompanion.interactions.chat.acp.handler")

      local function merge_tool_call(existing, incoming)
        local out = vim.deepcopy(existing or {})
        for k, v in pairs(incoming or {}) do
          if v ~= vim.NIL then out[k] = v end
        end
        return out
      end

      local function raw_tool_output(tc)
        local parts = {}
        if type(tc.content) == "table" then
          for _, c in ipairs(tc.content) do
            if c and c.type == "content" and type(c.content) == "table" then
              local b = c.content
              if b.type == "text" and type(b.text) == "string" then
                parts[#parts + 1] = b.text
              elseif b.type == "resource" and b.resource and type(b.resource.text) == "string" then
                parts[#parts + 1] = b.resource.text
              elseif b.type == "resource_link" and type(b.uri) == "string" then
                parts[#parts + 1] = "[resource: " .. b.uri .. "]"
              end
            end
          end
        end
        return table.concat(parts, "\n")
      end

      local orig_process_tool_call = ACPHandler.process_tool_call
      function ACPHandler:process_tool_call(tool_call)
        local id = type(tool_call) == "table" and tool_call.toolCallId or nil
        -- Capture state before upstream may clear it on completion.
        local before = id and self.ui_state[id] or nil
        local merged = id and merge_tool_call(self.tools[id], tool_call) or nil

        orig_process_tool_call(self, tool_call)

        if not (id and merged and merged.status == "completed") then
          return
        end
        local st = self.ui_state[id] or before
        local line = st and st.line_number
        if not line then
          return
        end
        local text = raw_tool_output(merged)
        if text ~= "" then
          pcall(tool_output.set, self.chat.bufnr, line, text)
        end
      end

      -- HACK: Ctrl+C during a streaming response can wipe the chat buffer before
      -- the cancellation cleanup finishes. The call chain is:
      --   Chat:done() -> Chat:ready_for_input() -> Chat:add_buf_message()
      --     -> Builder:_write_to_buffer() -> UI:unlock_buf()
      -- By the time unlock_buf runs, the buffer id stored in self.chat_bufnr may
      -- already be invalid, causing "Invalid buffer id: N" from vim.bo[].
      --
      -- Upstream fix: lock_buf/unlock_buf in interactions/chat/ui/init.lua should
      -- guard with nvim_buf_is_valid before touching vim.bo[]. Remove this patch
      -- once that lands (check the unlock_buf function body after plugin updates).
      local UI = require("codecompanion.interactions.chat.ui")
      local orig_lock = UI.lock_buf
      local orig_unlock = UI.unlock_buf
      function UI:lock_buf()
        if vim.api.nvim_buf_is_valid(self.chat_bufnr) then
          orig_lock(self)
        end
      end
      function UI:unlock_buf()
        if vim.api.nvim_buf_is_valid(self.chat_bufnr) then
          orig_unlock(self)
        end
      end

      -- Re-apply per-message timestamp labels after CC repaints headers.
      -- render_headers runs after every full buffer render (compaction,
      -- session restore) and after each new role header during streaming;
      -- a full render deletes all extmarks, so our timing namespace must be
      -- rebuilt here. lib.codecompanion-timing.reapply re-derives header
      -- lines from the buffer, so it is correct regardless of how the
      -- transcript was rebuilt. See that module's header for the mapping.
      local orig_render_headers = UI.render_headers
      function UI:render_headers()
        orig_render_headers(self)
        pcall(function()
          require("lib.codecompanion-timing").reapply(self.chat_bufnr)
        end)
      end

      -- HACK: ACPHandler:ensure_connection doesn't pass Chat.acp_session_id to
      -- Connection.new, so /join (session loading) has no effect. Patch it through.
      local ACPHandler = require("codecompanion.interactions.chat.acp.handler")
      local orig_ensure_connection = ACPHandler.ensure_connection
      function ACPHandler:ensure_connection()
        if not self.chat.acp_connection and self.chat.acp_session_id then
          self.chat.acp_connection = require("codecompanion.acp").new({
            adapter = self.chat.adapter,
            session_id = self.chat.acp_session_id,
          })
        end
        return orig_ensure_connection(self)
      end

      -- HACK: when the agent process dies, Connection:handle_process_exit nils
      -- session_id; the next turn silently mints a fresh session/new while the
      -- chat buffer keeps showing the old transcript — so you keep talking to
      -- what looks like the same agent, but it has none of the prior context.
      -- Surface the swap (toast + inline banner). The last established id is
      -- remembered on the long-lived connection object and is NOT cleared on
      -- exit, so we can detect replacement. Intentional resume (/join, broker
      -- fork) uses a fresh connection, so `previous` is nil there and no warning
      -- fires. Remove once upstream notifies on session replacement.
      local orig_ensure_session = ACPHandler.ensure_session
      function ACPHandler:ensure_session()
        local conn = self.chat.acp_connection
        local previous = conn and conn._cc_established_session_id
        local ok = orig_ensure_session(self)
        if ok and conn and conn.session_id then
          if previous and conn.session_id ~= previous then
            local warning = ("Previous agent session ended; a new session (%s) was started. "):format(conn.session_id)
              .. "This agent does NOT have the earlier conversation context shown above."
            require("codecompanion.utils").notify(warning, vim.log.levels.WARN)
            pcall(function()
              self.chat:add_buf_message({
                role = require("codecompanion.config").constants.LLM_ROLE,
                content = "\n> [!WARNING] Session reset\n> " .. warning .. "\n",
              }, { type = self.chat.MESSAGE_TYPES.SYSTEM_MESSAGE })
            end)
          end
          conn._cc_established_session_id = conn.session_id
          -- Pin the first session id established for this chat so the
          -- winbar shows a stable handle even after involuntary reminting
          -- (first-write-wins; reset on close / adapter-swap-with-clear).
          pcall(function()
            require("lib.codecompanion-chatinfo").pin(self.chat.bufnr, conn.session_id)
          end)
        end
        return ok
      end

      -- HACK: Connection:_establish_session gates session/load on the agent
      -- advertising loadSession capability. The broker handles session/load
      -- directly for known sessions (the agent is never consulted), so the
      -- capability check is irrelevant. Patch it out.
      --
      -- Also: when `self._cwd_override` is set (broker fork resume — see
      -- `_broker_open_chat_with_session` above), force the orig's
      -- `vim.fn.getcwd()` call to return the override so the `session/load`
      -- params carry the cwd the broker materialized the JSONL under
      -- rather than the client's pwd. Without this, a fork issued from a
      -- client whose pwd differs from the source session's cwd resumes
      -- against a non-existent JSONL and silently starts fresh.
      local Connection = require("codecompanion.acp")
      local orig_establish = Connection._establish_session
      function Connection:_establish_session()
        if self.session_id and self._agent_info then
          self._agent_info.agentCapabilities = self._agent_info.agentCapabilities or {}
          self._agent_info.agentCapabilities.loadSession = true
        end
        local override = self._cwd_override
        if not override then
          return orig_establish(self)
        end
        local orig_getcwd = vim.fn.getcwd
        vim.fn.getcwd = function() return override end
        local ok, ret = pcall(orig_establish, self)
        vim.fn.getcwd = orig_getcwd
        if not ok then error(ret) end
        return ret
      end

      -- HACK: Connection:set_config_option updates self._config_options but
      -- never fires the autocmd that per-chat update_metadata listeners are
      -- bound to (chat/init.lua:316 listens for "CodeCompanionChatACPModeChanged"
      -- — a pattern upstream registers but never emits, so listeners are
      -- effectively dead until the next ready_for_input cycle). Fire it here
      -- so runtime model/effort changes (e.g. codex `gpt-5-codex[high]` via
      -- /acp_session_options) flow into _G.codecompanion_chat_metadata
      -- immediately rather than waiting for the next prompt turn.
      -- Upstream fix: emit this autocmd from _apply_config_options or
      -- set_config_option. Remove this patch once that lands.
      local orig_set_config_option = Connection.set_config_option
      function Connection:set_config_option(config_id, value)
        local ok = orig_set_config_option(self, config_id, value)
        if ok and self.session_id then
          vim.api.nvim_exec_autocmds("User", {
            pattern = "CodeCompanionChatACPModeChanged",
            data = { session_id = self.session_id, config_id = config_id, value = value },
          })
        end
        return ok
      end

      -- HACK: inject per-launch session metadata for the direct
      -- claude-agent-acp path. CodeCompanion's session/new sends only
      -- { cwd, mcpServers } (acp/init.lua), but claude-agent-acp reads its
      -- thinking budget from `_meta.claudeCode.options.maxThinkingTokens` at
      -- session creation (acp-agent.js) — thinking is NOT a live config option,
      -- so it can only be set here. The acp-broker preserves client `_meta`
      -- siblings when it stamps its own `_meta.broker.client.metadata`
      -- (envelope.rs: stamp_broker_client_metadata_preserves_existing_meta_siblings),
      -- so the merged object reaches the agent intact. Consumed once per launch.
      local METHODS = require("codecompanion.acp.methods")
      local orig_send_rpc_request = Connection.send_rpc_request
      function Connection:send_rpc_request(method, params)
        if method == METHODS.SESSION_NEW and _direct.pending_meta then
          local extra = _direct.pending_meta
          _direct.pending_meta = nil
          params = params or {}
          params._meta = vim.tbl_deep_extend("force", params._meta or {}, extra)
        end
        return orig_send_rpc_request(self, method, params)
      end

      -- ACP elicitation/create support. The wrapper at
      -- users/mk/mkarrmann/dvsc-core-acp ships dm-core's
      -- `ask_user_question` over `elicitation/create` (UNSTABLE) when
      -- the client advertises `clientCapabilities.elicitation.form`;
      -- without this patch, codecompanion.nvim has neither the
      -- capability advertisement nor a dispatcher for the inbound
      -- request, so the wrapper falls back to suppressing the tool.
      -- Removing this patch is fine if/when codecompanion.nvim ships
      -- native elicitation support — `patch()` is idempotent and
      -- a future native dispatcher would take precedence on its own.
      require("lib.codecompanion-elicitation").patch()

      -- HACK: PromptBuilder:handle_session_update only branches on the
      -- session/update kinds it knows how to render (agent_message_chunk,
      -- agent_thought_chunk, plan, tool_call, tool_call_update). Other
      -- discriminators — notably usage_update — fall through with no else
      -- branch and are silently dropped before any consumer can see them.
      -- Tap the method to fire a User autocmd with the raw payload first,
      -- then delegate. lib/codecompanion-stats consumes this for the
      -- lualine context-% display.
      -- Upstream fix: prompt_builder.lua should expose an extension point
      -- or fire an autocmd unconditionally. Remove this patch once that lands.
      local PromptBuilder = require("codecompanion.acp.prompt_builder")
      local orig_handle_su = PromptBuilder.handle_session_update
      function PromptBuilder:handle_session_update(update)
        vim.api.nvim_exec_autocmds("User", {
          pattern = "CodeCompanionACPSessionUpdate",
          data = {
            session_id = self.connection and self.connection.session_id or nil,
            update = update,
          },
        })
        return orig_handle_su(self, update)
      end

      -- HACK: Chat:_submit_acp runs the entire ACP submit chain
      -- (ensure_connection → connect_and_authenticate → ensure_session →
      -- _establish_session → create_and_send_prompt) on the main thread
      -- with no coroutine. Connection:send_rpc_request branches on
      -- coroutine.running(): with a coroutine it yields via async.wait;
      -- without one it falls back to wait_for_rpc_response, a
      -- vim.wait(10ms) polling loop that blocks the editor for the
      -- entire RPC round-trip. On the first message, that's three
      -- sequential RPCs (initialize, authenticate, session/new) and the
      -- session/new call is the slow one — for dvsc-core-acp it spans
      -- the whole dm-core boot (model snapshot load, GK registration,
      -- broker round-trip), regularly 5-30+ seconds.
      --
      -- The fix mirrors helpers.create_acp_connection (used by
      -- Chat:change_adapter), which wraps the same handler chain in
      -- async_utils.sync(fn)() so send_rpc_request takes the yielding
      -- path. Editor stays responsive; subsequent prompts in the same
      -- session were already async (acp_connection:is_ready() short-
      -- circuits ensure_connection and the prompt streaming uses
      -- on_stdout callbacks), so this only affects the first-message
      -- spin-up.
      --
      -- self.current_request is set synchronously to a sentinel before
      -- the coroutine runs to preserve the `current_request ~= nil`
      -- guard against double-submit at chat/init.lua:1245. The real
      -- request handle (with .cancel) replaces it once
      -- create_and_send_prompt returns. If the user cancels during the
      -- async window, current_request is already nil by the time the
      -- coroutine completes; we call handle.cancel() ourselves so the
      -- in-flight session/prompt doesn't orphan.
      --
      -- Upstream fix would be to either wrap _submit_acp in a coroutine
      -- or change wait_for_rpc_response to never run from the main
      -- thread for ACP. Remove this patch once that lands.
      local async_utils = require("codecompanion.utils.async")
      local ChatModule = require("codecompanion.interactions.chat")
      function ChatModule:_submit_acp(payload)
        local sentinel = { _placeholder = true, cancel = function() end }
        self.current_request = sentinel
        async_utils.sync(function()
          local acp_handler = require("codecompanion.interactions.chat.acp.handler").new(self)
          local handle = acp_handler:submit(payload)
          if self.current_request == sentinel then
            self.current_request = handle
          elseif handle and type(handle.cancel) == "function" then
            -- Cancel raced the connection setup; the prompt may already
            -- have been sent. Tell the agent to stop.
            handle.cancel()
          end
        end)()
      end

      -- HACK: CodeCompanion's chat submit path allows a blank user section
      -- after a previous user message. ACP adapters then filter the blank
      -- message out and send `prompt = {}` to the agent. Treat that as a
      -- no-op for normal ACP submits; tool auto-submit and regenerate keep
      -- their existing behavior.
      --
      -- Mirrors upstream's permissive condition at chat/init.lua:1264-1267:
      -- the buffer parser may legitimately return nil after a cancelled turn
      -- (header_line stale, Context-only `## Me` section) even though the
      -- in-memory `self.messages` carries an un-acked user message that's
      -- meant to be re-sent. Bail only when BOTH the buffer and the message
      -- history are empty of pending user input.
      --
      -- The "pending" qualifier matters: upstream's `helpers.has_user_messages`
      -- only checks `msg.role == USER_ROLE` and accepts any acked user message
      -- from a prior turn, which made this guard a no-op once the chat had any
      -- history. We need `_meta.sent == false` (the same flag `label_sent_items`
      -- toggles when an agent ack arrives) so the guard actually fires on a
      -- truly empty submit and only falls through for the cancel-resend case
      -- that motivated this branch.
      local Chat = require("codecompanion.interactions.chat")
      local cc_config = require("codecompanion.config")
      local orig_chat_submit = Chat.submit
      function Chat:submit(opts)
        opts = opts or {}
        if
          self.adapter
          and self.adapter.type == "acp"
          and not self.current_request
          and not opts.auto_submit
          and not opts.regenerate
        then
          local ok_parser, parser = pcall(require, "codecompanion.interactions.chat.parser")
          local ok_message, message_to_submit = false, nil
          if ok_parser then
            ok_message, message_to_submit = pcall(parser.messages, self, self.header_line)
          end
          local has_buf_text = ok_message and message_to_submit ~= nil
          local has_pending_user_msg = vim.iter(self.messages or {}):any(function(m)
            return m.role == cc_config.constants.USER_ROLE
              and m._meta and not m._meta.sent
              and type(m.content) == "string" and m.content ~= ""
          end)
          if not has_buf_text and not has_pending_user_msg then
            require("codecompanion.utils.log"):warn("[chat::submit] No ACP user message to submit")
            return
          end
        end
        return orig_chat_submit(self, opts)
      end

      -- Read-only chat buffer: prompts and edits go through the queue only.
      -- CodeCompanion leaves the chat buffer modifiable at rest (after
      -- `Chat:reset`, the last call in `ready_for_input`) so it can be typed
      -- into directly. We re-lock it there so the buffer is non-modifiable
      -- whenever a turn settles. Streaming writes are unaffected (the builder
      -- unlocks before each write), and the queue unlocks around its own
      -- programmatic submit (lib/codecompanion-queue).
      local orig_chat_reset = Chat.reset
      function Chat:reset()
        orig_chat_reset(self)
        lock_chat_buf(self.bufnr)
      end

      -- Block manual insert in the chat buffer with a useful hint instead of
      -- the raw "E21: 'modifiable' is off". Normal-mode edits are already
      -- blocked by the buffer being non-modifiable.
      vim.api.nvim_create_autocmd("FileType", {
        pattern = "codecompanion",
        callback = function(args)
          vim.api.nvim_create_autocmd("InsertEnter", {
            buffer = args.buf,
            callback = function()
              vim.cmd.stopinsert()
              vim.notify(
                "CodeCompanion chat is read-only. Use the input queue (<leader>aq) to send prompts.",
                vim.log.levels.WARN
              )
            end,
          })

          -- `za` expands/collapses the tool-call output under the cursor;
          -- falls through to the native fold toggle when not on a tool line.
          vim.keymap.set("n", "za", function()
            local line = vim.api.nvim_win_get_cursor(0)[1]
            local handled = require("lib.codecompanion-tool-output").toggle(args.buf, line)
            if not handled then
              pcall(vim.cmd, "normal! za")
            end
          end, { buffer = args.buf, silent = true, desc = "Toggle tool output / fold" })
        end,
      })

      local has_cmp, cmp = pcall(require, "cmp")
      if has_cmp then
        local QueueSlash = {}
        QueueSlash.new = function() return setmetatable({}, { __index = QueueSlash }) end
        function QueueSlash:is_available() return vim.bo.filetype == "codecompanion_input" end
        function QueueSlash:get_trigger_characters() return { "/" } end
        function QueueSlash:get_keyword_pattern() return [[/\%(\w\|-\)\+]] end
        function QueueSlash:complete(params, callback)
          local items = require("codecompanion.providers.completion").slash_commands("chat")
          local kind = cmp.lsp.CompletionItemKind.Function
          vim.iter(items):map(function(item)
            item.kind = kind
            item.context = { bufnr = params.context.bufnr, cursor = params.context.cursor }
          end)
          callback({ items = items, isIncomplete = false })
        end
        function QueueSlash:execute(item, callback)
          vim.api.nvim_set_current_line("")
          local chat_bufnr = require("lib.codecompanion-queue").chat_bufnr()
          local chat = chat_bufnr and require("codecompanion").buf_get_chat(chat_bufnr)
          if chat then
            require("codecompanion.interactions.chat.slash_commands").run(item, chat)
          end
          callback(item)
        end

        cmp.register_source("codecompanion_queue_slash", QueueSlash)

        -- ACP agent slash commands (\-triggered, e.g. \compact) for the queue
        -- input. The stock cmp source (providers/completion/cmp/acp_commands.lua)
        -- keys off the chat buffer, but our input is a separate
        -- `codecompanion_input` buffer that isn't session-linked, so we resolve
        -- the tab's chat session and build items from its advertised commands.
        -- On submit, ACPHandler:transform_acp_commands rewrites `\cmd` → `/cmd`
        -- on the wire, so completion only inserts text (no execution).
        local acp_trigger = require("codecompanion.triggers").mappings.acp_slash_commands
        local QueueAcp = {}
        QueueAcp.new = function() return setmetatable({}, { __index = QueueAcp }) end
        function QueueAcp:is_available()
          return vim.bo.filetype == "codecompanion_input"
            and require("codecompanion.config").interactions.chat.slash_commands.opts.acp.enabled
        end
        function QueueAcp:get_trigger_characters() return { acp_trigger } end
        function QueueAcp:get_keyword_pattern()
          return vim.fn.escape(acp_trigger, [[\]]) .. [[\%(\w\|-\)\+]]
        end
        function QueueAcp:complete(params, callback)
          local chat_bufnr = require("lib.codecompanion-queue").chat_bufnr()
          local chat = chat_bufnr and require("codecompanion").buf_get_chat(chat_bufnr)
          local conn = chat and chat.acp_connection
          if not conn or not conn.session_id then
            return callback({ items = {}, isIncomplete = false })
          end
          local commands = require("codecompanion.interactions.chat.acp.commands")
            .get_commands_for_session(conn.session_id)
          local kind = cmp.lsp.CompletionItemKind.Function
          local items = vim.iter(commands):map(function(cmd)
            local detail = cmd.description or ""
            if cmd.input and cmd.input ~= vim.NIL and type(cmd.input) == "table" and cmd.input.hint then
              detail = detail .. " " .. cmd.input.hint
            end
            return {
              label = acp_trigger .. cmd.name,
              detail = detail,
              command = cmd,
              kind = kind,
              context = { bufnr = params.context.bufnr, cursor = params.context.cursor },
            }
          end):totable()
          callback({ items = items, isIncomplete = true })
        end
        function QueueAcp:execute(item, callback)
          -- Insert "\<cmd>" (plus a trailing space if it takes args), replacing
          -- the partially typed trigger token. Idempotent: strips any existing
          -- "\cmd" token at the cursor first, so it is correct whether or not
          -- cmp already inserted the label. No auto-submit.
          local text = acp_trigger .. item.command.name
          if
            item.command.input
            and item.command.input ~= vim.NIL
            and type(item.command.input) == "table"
            and item.command.input.hint
          then
            text = text .. " "
          end
          local row, col = unpack(vim.api.nvim_win_get_cursor(0))
          local line = vim.api.nvim_get_current_line()
          local before = line:sub(1, col):gsub(vim.pesc(acp_trigger) .. "[-%w]*$", "")
          local after = line:sub(col + 1)
          vim.api.nvim_set_current_line(before .. text .. after)
          vim.api.nvim_win_set_cursor(0, { row, #before + #text })
          callback(item)
        end
        cmp.register_source("codecompanion_queue_acp", QueueAcp)

        local sources = vim.deepcopy(cmp.get_config().sources or {})
        table.insert(sources, { name = "codecompanion_queue_slash" })
        table.insert(sources, { name = "codecompanion_queue_acp" })
        cmp.setup({ sources = sources })
      end

      require("lib.codecompanion-timing").setup()
      -- Eager-load so the CodeCompanionACPSessionUpdate listener is registered
      -- before any chat opens. The module registers its autocmd at load time;
      -- if loaded lazily via lualine's cc_context (which only fires once a chat
      -- buffer has both filetype=codecompanion and an active acp session_id),
      -- the first prompt's usage_update notifications fire before the listener
      -- exists and are dropped.
      require("lib.codecompanion-stats")
      require("lib.codecompanion-diff").setup()
      require("lib.codecompanion-chatinfo").setup()
      -- Reap the ACP connection (broker agent + MCP fleet) on ANY chat close
      -- -- :tabclose/window-close/:bd, not just <C-c> or nvim exit. See
      -- lib/codecompanion-reap for the rationale (the chat buffer is hidden,
      -- not unloaded, on :tabclose, so the agent would otherwise leak).
      require("lib.codecompanion-reap").setup()

      local ns = vim.api.nvim_create_namespace("codecompanion_inline_indicator")
      local spinner_frames = { "⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏" }
      local indicator = {}

      local function clear()
        if indicator.timer then
          indicator.timer:stop()
          indicator.timer:close()
        end
        if indicator.bufnr and vim.api.nvim_buf_is_valid(indicator.bufnr) then
          vim.api.nvim_buf_clear_namespace(indicator.bufnr, ns, 0, -1)
        end
        indicator = {}
      end

      vim.api.nvim_create_autocmd("User", {
        pattern = "CodeCompanionRequestStarted",
        callback = function(args)
          local data = args.data or {}
          if data.bufnr then
            require("lib.codecompanion-queue").on_request_started(data.bufnr, data.id)
          end

          local bufnr = vim.api.nvim_get_current_buf()
          if vim.bo[bufnr].filetype == "codecompanion" then return end
          if bufnr == require("lib.codecompanion-queue").bufnr() then return end

          clear()
          local line = vim.api.nvim_win_get_cursor(0)[1] - 1
          indicator.bufnr = bufnr

          local frame = 0
          indicator.timer = vim.uv.new_timer()
          indicator.timer:start(0, 80, function()
            vim.schedule(function()
              if not vim.api.nvim_buf_is_valid(bufnr) then
                clear()
                return
              end
              pcall(vim.api.nvim_buf_clear_namespace, bufnr, ns, 0, -1)
              pcall(vim.api.nvim_buf_set_extmark, bufnr, ns, line, 0, {
                virt_text = { { spinner_frames[frame + 1] .. " Processing…", "Comment" } },
                virt_text_pos = "eol",
              })
              frame = (frame + 1) % #spinner_frames
            end)
          end)
        end,
      })

      vim.api.nvim_create_autocmd("User", {
        pattern = "CodeCompanionRequestFinished",
        callback = function(args)
          local data = args.data or {}
          if data.bufnr then
            require("lib.codecompanion-queue").on_request_finished(data.bufnr, data.id, data.status)
          end
          clear()
        end,
      })

      vim.api.nvim_create_user_command("CodeCompanionDoctor", function()
        require("lib.codecompanion-doctor").run()
      end, { desc = "Diagnose CodeCompanion / ACP state" })

      vim.api.nvim_create_user_command("CodeCompanionCompact", function()
        tab_chat_compact()
      end, { desc = "Compact the current CodeCompanion chat (any compaction-capable ACP agent)" })

      vim.api.nvim_create_autocmd("VimLeavePre", {
        callback = function()
          pcall(function() require("lib.codecompanion-doctor").cleanup_orphans() end)
        end,
      })

      vim.api.nvim_create_autocmd("User", {
        pattern = "CodeCompanionChatOpened",
        callback = function(args)
          local bufnr = args.data and args.data.bufnr
          if not bufnr then return end
          local tab = vim.api.nvim_get_current_tabpage()
          -- Stamp ownership synchronously so the queue lib's scheduled
          -- callback can resolve bufnr -> tab regardless of focus changes
          -- in between.
          pcall(vim.api.nvim_buf_set_var, bufnr, "cc_tab_owner", tab)
          pcall(vim.api.nvim_tabpage_set_var, tab, "codecompanion_chat_bufnr", bufnr)
          vim.schedule(function()
            local chat = require("codecompanion").buf_get_chat(bufnr)
            if chat and chat.adapter and chat.adapter.name == "dvsc_core_broker" then
              _dvsc.by_chat_bufnr[bufnr] = table.remove(_dvsc.launch_queue, 1)
            else
              _dvsc.by_chat_bufnr[bufnr] = nil
            end
            -- Chat.new's open->render leaves the buffer modifiable; re-lock so
            -- it can only be written through the queue (see lock_chat_buf).
            lock_chat_buf(bufnr)
            require("lib.codecompanion-queue").on_chat_opened(bufnr)
          end)
        end,
      })

      vim.api.nvim_create_autocmd("User", {
        pattern = "CodeCompanionChatHidden",
        callback = function(args)
          vim.schedule(function()
            local bufnr = args.data and args.data.bufnr
            if bufnr then
              _dvsc.by_chat_bufnr[bufnr] = nil
              require("lib.codecompanion-queue").on_chat_hidden(bufnr)
            end
          end)
        end,
      })

      vim.api.nvim_create_autocmd("User", {
        pattern = "CodeCompanionChatClosed",
        callback = function(args)
          local bufnr = args.data and args.data.bufnr
          if not bufnr then return end
          local ok, tab = pcall(function() return vim.b[bufnr].cc_tab_owner end)
          tab = (ok and tab and vim.api.nvim_tabpage_is_valid(tab)) and tab or nil
          _dvsc.by_chat_bufnr[bufnr] = nil
          pcall(function() require("lib.codecompanion-tool-output").clear(bufnr) end)
          -- Resolve the tab synchronously (above) — CodeCompanion deletes
          -- the chat buffer synchronously right after firing this event, so
          -- the `cc_tab_owner` stamp is gone by the next tick. Tear down now
          -- (not scheduled) so the whole UI comes down as a unit before any
          -- subsequent relaunch (e.g. <leader>aZ) can reopen into stale state.
          require("lib.codecompanion-queue").on_chat_closed(bufnr, tab)
        end,
      })

      vim.api.nvim_create_autocmd("User", {
        pattern = "CodeCompanionChatDone",
        callback = function(args)
          vim.schedule(function()
            require("lib.codecompanion-queue").on_chat_done(args.data.bufnr)
          end)
        end,
      })

      -- Omnigent M4: a background/wakeup turn arrived on an idle chat (the agent
      -- was driven from elsewhere). Toast it so the user notices activity in a
      -- chat they aren't looking at. Fired by the omnigent observer.
      vim.api.nvim_create_autocmd("User", {
        pattern = "CodeCompanionChatOmnigentWakeup",
        callback = function(args)
          local bufnr = args.data and args.data.bufnr
          local visible = bufnr and vim.fn.bufwinid(bufnr) ~= -1
          if not visible then
            vim.notify("Omnigent: background activity in a chat", vim.log.levels.INFO,
              { title = "CodeCompanion" })
          end
        end,
      })
    end,

    cmd = { "CodeCompanion", "CodeCompanionChat", "CodeCompanionActions" },
    keys = {
      { "<leader>ae", "<cmd>CodeCompanionActions<cr>", mode = { "n", "v" }, desc = "CodeCompanion Actions" },
      { "<leader>ah", function() tab_chat_open_or_toggle() end, mode = { "n", "v" }, desc = "CodeCompanion Chat (this tab)" },
      { "<leader>av", "<cmd>CodeCompanion<cr>", mode = { "n", "v" }, desc = "CodeCompanion Inline" },
      { "<leader>aw", function() require("lib.codecompanion-diff").toggle() end, desc = "Toggle CodeCompanion diff tab" },
      { "<leader>aq", function() require("lib.codecompanion-queue").focus() end, desc = "Focus CodeCompanion Input" },
      { "<leader>ad", "<cmd>CodeCompanionDoctor<cr>", desc = "CodeCompanion Doctor" },
      { "<leader>aD", function() tab_chat_set_adapter("devmate",          { clear = true }) end, desc = "CodeCompanion Chat (Devmate, fresh)" },
      { "<leader>aS", function() tab_chat_set_adapter("dvsc_core",        { clear = true }) end, desc = "CodeCompanion Chat (Dvsc Core, fresh)" },
      { "<leader>ag", function() tab_chat_set_adapter("dvsc_core_broker", { clear = true, force_pick = false }) end, desc = "Dvsc Chat via broker (last config, fresh)" },
      { "<leader>aG", function() tab_chat_pick_agent_and_set({ clear = true, force_pick = true }) end, desc = "Pick agent (dvsc / direct Claude / direct Codex / omnigent), fresh" },
      { "<leader>aC", function() tab_chat_set_adapter("claude_broker",    { clear = true }) end, desc = "Claude Chat via broker (direct, fresh)" },
      { "<leader>aO", function() tab_chat_set_adapter("codex_broker",     { clear = true }) end, desc = "Codex Chat via broker (fresh)" },
      { "<leader>aM", function() tab_chat_set_adapter("omnigent",         { clear = true }) end, desc = "CodeCompanion Chat (Omnigent, remembered agent+model+effort)" },
      { "<leader>aA", function() tab_chat_set_adapter("omnigent",         { clear = true, force_pick = true }) end, desc = "CodeCompanion Chat (Omnigent, pick agent+model+effort)" },
      { "<leader>amc", function() omnigent_continue() end, desc = "Omnigent: resume durable session (cwd-scoped)" },
      { "<leader>ak", tab_chat_compact, desc = "CodeCompanion: compact current chat (dvsc RPC or agent /compact)" },
      { "<leader>aZ", function() tab_chat_full_refresh() end, desc = "CodeCompanion: full refresh (close + reopen, pick agent + model + config)" },
      { "<leader>ao", tab_chat_pick_option, desc = "CodeCompanion: change live session option (ACP config, or Omnigent model/effort)" },
      { "<leader>aQ", function()
          local bufnr = vim.t.codecompanion_chat_bufnr
          local chat = bufnr
            and vim.api.nvim_buf_is_valid(bufnr)
            and require("codecompanion").buf_get_chat(bufnr)
          if chat then chat:close() end
        end, desc = "CodeCompanion: close current tab's chat" },
      -- ACP broker resume/fork by bsid. The `<leader>aB*` namespace (B
      -- for "broker") avoids collision with `<leader>ar`/`<leader>af`,
      -- which are owned by claude-agent-manager (Claude Code resume/fork),
      -- and leaves lowercase `<leader>ab` free (e.g. for "add buffer").
      -- Defaults to the `dvsc_core_broker` adapter — broker routes by bsid,
      -- not by adapter, so this works for any claude-code-shaped session
      -- (dvsc/claude/devmate). Use codex_broker by editing
      -- `broker_resume_or_fork` for codex sessions.
      { "<leader>aBr", function() broker_resume_or_fork("resume") end, desc = "ACP broker: resume saved session by bsid (escape hatch)" },
      { "<leader>aBf", function() broker_resume_or_fork("fork")   end, desc = "ACP broker: fork saved session by bsid (escape hatch)" },
      -- Smart continue (the everyday key). Server-backed cross-broker list,
      -- cwd-scoped by default (<A-c> toggles all-cwd), auto-routes each pick to
      -- resume (local+live) or fork (remote / local+dead). Pick from the list or
      -- type a full bsid + <C-x>. <C-y> yanks the highlighted bsid. See
      -- broker_continue and docs/acp-broker-continue-refactor.md.
      { "<leader>aBc", function() broker_continue() end, desc = "ACP broker: smart continue (list/paste, auto resume/fork)" },
      { "<leader>aBl", function() broker_continue() end, desc = "ACP broker: smart continue (alias of aBc)" },
    },
  },
}
