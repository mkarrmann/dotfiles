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
  { id = "claude-opus-4.8-long",   provider = "anthropic", adaptive = true  },
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

-- Top-level entry point used by `<leader>aBr` / `<leader>aBf`. `action`
-- is `"resume"` or `"fork"`. The default adapter is `dvsc_core_broker`
-- because most broker-captured sessions are dvsc/claude (both use the
-- claude_code wire shape, so dvsc_core_broker resumes either cleanly).
-- For codex sessions, swap to `codex_broker` here.
local function broker_resume_or_fork(action, adapter_name)
  adapter_name = adapter_name or "dvsc_core_broker"
  local prompt = (action == "fork" and "Fork bsid: ") or "Resume bsid: "
  local default = _broker_read_last_bsid() or "bsid_"
  vim.ui.input({ prompt = prompt, default = default }, function(bsid)
    bsid = bsid and vim.trim(bsid)
    if not bsid or bsid == "" or bsid == "bsid_" then return end
    _broker_write_last_bsid(bsid)
    local target_bsid = bsid
    local target_cwd = nil
    if action == "fork" then
      local adapter = require("codecompanion.adapters").resolve(adapter_name)
      target_bsid, target_cwd = _broker_fork_saved_session(adapter, bsid)
      if not target_bsid then return end
      vim.notify("forked " .. bsid .. " -> " .. target_bsid, vim.log.levels.INFO)
    end
    _broker_open_chat_with_session(adapter_name, target_bsid, target_cwd)
  end)
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
    "olimorris/codecompanion.nvim",
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
          inline = { adapter = "claude_code" },
          cmd = { adapter = "claude_code" },
        },

        adapters = {
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

      -- HACK: CodeCompanion parses JSON-looking ACP tool titles like
      -- `skill {"path":...}` as colon-delimited labels, producing
      -- `Other: skill {"path"`. Prefer structured rawInput paths for
      -- unknown dvsc-core tools instead of parsing the title string.
      local acp_formatters = require("codecompanion.interactions.chat.acp.formatters")
      local orig_tool_message = acp_formatters.tool_message
      function acp_formatters.tool_message(tool_call, adapter)
        if type(tool_call) == "table" and tool_call.kind == "other" then
          local raw = tool_call.rawInput
          local path = type(raw) == "table" and raw.path
          if type(path) == "string" and path ~= "" then
            local tool = (tool_call.title or ""):match("^(%S+)") or "Tool"
            return tool:gsub("^%l", string.upper) .. ": " .. vim.fn.fnamemodify(path, ":t")
          end
        end

        return orig_tool_message(tool_call, adapter)
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
          if ok and tab and vim.api.nvim_tabpage_is_valid(tab) then
            pcall(vim.api.nvim_tabpage_del_var, tab, "codecompanion_chat_bufnr")
          end
          _dvsc.by_chat_bufnr[bufnr] = nil
          vim.schedule(function()
            require("lib.codecompanion-queue").on_chat_closed(bufnr)
          end)
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
      { "<leader>aG", function() tab_chat_pick_agent_and_set({ clear = true, force_pick = true }) end, desc = "Pick agent (dvsc / direct Claude / direct Codex), fresh" },
      { "<leader>aC", function() tab_chat_set_adapter("claude_broker",    { clear = true }) end, desc = "Claude Chat via broker (direct, fresh)" },
      { "<leader>aO", function() tab_chat_set_adapter("codex_broker",     { clear = true }) end, desc = "Codex Chat via broker (fresh)" },
      { "<leader>ak", tab_chat_compact, desc = "CodeCompanion: compact current chat (dvsc RPC or agent /compact)" },
      { "<leader>aZ", function() tab_chat_full_refresh() end, desc = "CodeCompanion: full refresh (close + reopen, pick agent + model + config)" },
      { "<leader>ao", tab_chat_pick_option, desc = "CodeCompanion: change live ACP session config option" },
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
      { "<leader>aBr", function() broker_resume_or_fork("resume") end, desc = "ACP broker: resume saved session by bsid" },
      { "<leader>aBf", function() broker_resume_or_fork("fork")   end, desc = "ACP broker: fork saved session by bsid" },
    },
  },
}
