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

local DVSC_MODES = { "native", "claude", "codex", "metacode" }

-- Canonical model catalog. Mirrors Configerator
-- `devmate_vscode/model/model_config.cconf` (v22 as of 2026-05-12). Refresh
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
-- aren't in dm-core's loaded `Loaded model config version 22` snapshot for
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
  { id = "claude-opus-4.7-long",   provider = "anthropic", adaptive = true  },
  { id = "claude-opus-4.6",        provider = "anthropic", adaptive = true  },
  { id = "claude-opus-4.6-long",   provider = "anthropic", adaptive = true  },
  { id = "claude-sonnet-4.6",      provider = "anthropic", adaptive = true  },
  { id = "claude-sonnet-4.6-long", provider = "anthropic", adaptive = true  },
  { id = "claude-haiku-4.5",       provider = "anthropic", adaptive = false },
  { id = "claude-haiku-4.5-long",  provider = "anthropic", adaptive = false },
  -- OpenAI
  { id = "gpt-5-3-codex", provider = "openai" },
  { id = "gpt-5-4",       provider = "openai" },
  { id = "gpt-5-5",       provider = "openai" },
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
local EFFORT_OPTIONS_BY_KIND = {
  openai = { "LOW", "MEDIUM", "HIGH", "XHIGH" },
  google = { "LOW", "MEDIUM", "HIGH" },
  anthropic_adaptive = { "low", "medium", "high", "xhigh" },
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
  return (ok and type(t) == "table") and t.bsid or nil
end

local function _broker_write_last_bsid(bsid)
  local f = io.open(BROKER_BSID_CACHE_PATH, "w")
  if not f then return end
  f:write(vim.fn.json_encode({ bsid = bsid }))
  f:close()
end

-- Open a chat in the current tab pre-loaded with an existing broker
-- session. Bypasses `tab_chat_open_or_toggle` because the
-- `:CodeCompanionChat` command path doesn't accept `acp_session_id` —
-- the patched `ACPHandler:ensure_connection` (see config below) wires
-- it through, and the patched `Connection:_establish_session` forces
-- `loadSession` capability so the broker actually receives
-- `session/load(bsid)`.
local function _broker_open_chat_with_session(adapter, acp_session_id)
  local existing = vim.t.codecompanion_chat_bufnr
  if existing and vim.api.nvim_buf_is_valid(existing) then
    vim.notify(
      "Tab already has a CodeCompanion chat; close it before resuming/forking another session.",
      vim.log.levels.WARN
    )
    return false
  end
  require("codecompanion.interactions.chat").new({
    adapter = adapter,
    acp_session_id = acp_session_id,
  })
  return true
end

-- Issue `meta.broker.persistence.fork_saved_session(bsid)` and return
-- the freshly-minted bsid (or nil on failure, with a notification).
local function _broker_fork_saved_session(adapter, source_bsid)
  local conn = require("codecompanion.acp").new({ adapter = adapter })
  if not conn:connect_and_authenticate() then
    vim.notify("acp-broker: connect failed", vim.log.levels.ERROR)
    return nil
  end
  local resp = conn:send_rpc_request(
    "meta.broker.persistence.fork_saved_session",
    { broker_session_id = source_bsid }
  )
  pcall(function() conn:disconnect() end)
  if not resp or not resp.broker_session_id then
    vim.notify("fork failed: " .. vim.inspect(resp), vim.log.levels.ERROR)
    return nil
  end
  return resp.broker_session_id
end

-- Top-level entry point used by `<leader>br` / `<leader>bf`. `action`
-- is `"resume"` or `"fork"`. The default adapter is `dvsc_core_broker`
-- because most broker-captured sessions are dvsc/claude (both use the
-- claude_code wire shape, so dvsc_core_broker resumes either cleanly).
-- For codex sessions, swap to `codex_broker` here.
local function broker_resume_or_fork(action, adapter_name)
  adapter_name = adapter_name or "dvsc_core_broker"
  local prompt = (action == "fork" and "Fork bsid: ") or "Resume bsid: "
  local default = _broker_read_last_bsid() or "bsid_"
  vim.ui.input({ prompt = prompt, default = default }, function(bsid)
    if not bsid or bsid == "" or bsid == "bsid_" then return end
    _broker_write_last_bsid(bsid)
    local target_bsid = bsid
    if action == "fork" then
      local adapter = require("codecompanion.adapters").resolve(adapter_name)
      target_bsid = _broker_fork_saved_session(adapter, bsid)
      if not target_bsid then return end
      vim.notify("forked " .. bsid .. " -> " .. target_bsid, vim.log.levels.INFO)
    end
    _broker_open_chat_with_session(adapter_name, target_bsid)
  end)
end

local function dvsc_pick_and_launch(force)
  local cache = _dvsc_read_cache()
  if not force and cache.mode and cache.model then
    local kind = _dvsc_reasoning_kind(cache.model)
    if kind == nil or cache.effort then
      return _dvsc_launch_with(cache.mode, cache.model, cache.effort)
    end
    -- Cache is for a model that needs an effort but lacks one; fall
    -- through to a fresh pick rather than launching with no effort.
  end
  _dvsc_pick(DVSC_MODES, "Harness:", function(mode)
    _dvsc_pick(_models_for_mode(mode), "Model:", function(model)
      local kind = _dvsc_reasoning_kind(model)
      if kind == nil then
        _dvsc_write_cache({ mode = mode, model = model })
        return _dvsc_launch_with(mode, model, nil)
      end
      _dvsc_pick(EFFORT_OPTIONS_BY_KIND[kind], "Thinking effort:", function(effort)
        _dvsc_write_cache({ mode = mode, model = model, effort = effort })
        _dvsc_launch_with(mode, model, effort)
      end)
    end)
  end)
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
            -- claude-agent-acp registration rather than dvsc-core-acp. No
            -- per-launch picker because claude-agent-acp has no
            -- harness/model/effort knobs the broker can pass through; if the
            -- claude-agent-acp registration is also the broker's configured
            -- default, this adapter is observationally identical to letting
            -- the broker pick — the explicit name selection just makes the
            -- routing intent legible regardless of which agent currently
            -- holds the `default = true` slot. Drive via `<leader>aC`.
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
            -- Drive via `<leader>aO`.
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

      -- HACK: Connection:_establish_session gates session/load on the agent
      -- advertising loadSession capability. The broker handles session/load
      -- directly for known sessions (the agent is never consulted), so the
      -- capability check is irrelevant. Patch it out.
      local Connection = require("codecompanion.acp")
      local orig_establish = Connection._establish_session
      function Connection:_establish_session()
        if self.session_id and self._agent_info then
          self._agent_info.agentCapabilities = self._agent_info.agentCapabilities or {}
          self._agent_info.agentCapabilities.loadSession = true
        end
        return orig_establish(self)
      end

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

      -- HACK: CodeCompanion's chat submit path allows a blank user section
      -- after a previous user message. ACP adapters then filter the blank
      -- message out and send `prompt = {}` to the agent. Treat that as a
      -- no-op for normal ACP submits; tool auto-submit and regenerate keep
      -- their existing behavior.
      local Chat = require("codecompanion.interactions.chat")
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
          local has_user_text = ok_message and message_to_submit ~= nil
          if not has_user_text then
            require("codecompanion.utils.log"):warn("[chat::submit] No ACP user message to submit")
            return
          end
        end
        return orig_chat_submit(self, opts)
      end

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
        local sources = vim.deepcopy(cmp.get_config().sources or {})
        table.insert(sources, { name = "codecompanion_queue_slash" })
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
            require("lib.codecompanion-queue").on_request_started(data.bufnr)
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
            require("lib.codecompanion-queue").on_request_finished(data.bufnr)
          end
          clear()
        end,
      })

      vim.api.nvim_create_user_command("CodeCompanionDoctor", function()
        require("lib.codecompanion-doctor").run()
      end, { desc = "Diagnose CodeCompanion / ACP state" })

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
      { "<leader>aq", function() require("lib.codecompanion-queue").focus() end, desc = "Focus CodeCompanion Input" },
      { "<leader>ad", "<cmd>CodeCompanionDoctor<cr>", desc = "CodeCompanion Doctor" },
      { "<leader>aD", function() tab_chat_open_or_toggle({ adapter = "devmate" }) end, desc = "CodeCompanion Chat (Devmate)" },
      { "<leader>aS", function() tab_chat_open_or_toggle({ adapter = "dvsc_core" }) end, desc = "CodeCompanion Chat (Dvsc Core)" },
      { "<leader>ag", function() dvsc_pick_and_launch(false) end, desc = "Dvsc Chat via broker (last config)" },
      { "<leader>aG", function() dvsc_pick_and_launch(true)  end, desc = "Dvsc Chat via broker (pick config)" },
      { "<leader>aC", function() tab_chat_open_or_toggle({ adapter = "claude_broker" }) end, desc = "Claude Chat via broker (direct)" },
      { "<leader>aO", function() tab_chat_open_or_toggle({ adapter = "codex_broker" }) end, desc = "Codex Chat via broker" },
      -- ACP broker resume/fork by bsid. Distinct `<leader>b*` namespace
      -- because `<leader>ar`/`<leader>af` are owned by claude-agent-manager
      -- (Claude Code resume/fork). Defaults to `dvsc_core_broker` adapter
      -- — broker routes by bsid, not by adapter, so this works for any
      -- claude-code-shaped session (dvsc/claude/devmate). Use codex_broker
      -- by editing `broker_resume_or_fork` for codex sessions.
      { "<leader>br", function() broker_resume_or_fork("resume") end, desc = "ACP broker: resume saved session by bsid" },
      { "<leader>bf", function() broker_resume_or_fork("fork")   end, desc = "ACP broker: fork saved session by bsid" },
    },
  },
}
