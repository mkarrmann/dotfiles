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

-- ── Omnigent resume ────────────────────────────────────────────────────────
--
-- Omnigent sessions are durable and server-owned, so there is no fork/resume
-- split and no cross-agent classification — a single REST list +
-- `chat:resume_omnigent(id)`
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
-- A cached last-choice reused on the normal launch, re-prompted on force. Two
-- notable traits, both because of what omnigent exposes: the catalog is fetched
-- live from the server (GET /v1/agents) rather than hardcoded -- there is no
-- drift-prone entitlement list to mirror -- and the pick is a triple
-- {agent, model, effort}: the agent is the session harness (IMMUTABLE after
-- create, so launch-time only), while model + effort are the initial overrides
-- passed at session create and stay switchable mid-session via <leader>ao.
-- SDK/streaming harnesses are offered along with the two built-in native agents
-- whose terminal output Omnigent normalizes onto the ordinary session stream.
-- Other native harnesses remain excluded until their chat forwarding is tested.
local OMNIGENT_AGENT_CACHE_PATH = vim.fn.stdpath("data") .. "/codecompanion-omnigent-agent.json"

-- The launch selection is a triple {agent, model?, effort?}. A nil model/effort
-- means "no override" -- the agent spec's own model/effort applies. Read back by the
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

-- Whether this exact agent/harness pair has a supported chat stream contract.
local function _omnigent_is_chat_agent(agent)
  if not (type(agent.harness) == "string" and agent.harness:match("%-native$")) then return true end
  return (agent.name == "claude-native-ui" and agent.harness == "claude-native")
    or (agent.name == "codex-native-ui" and agent.harness == "codex-native")
end

-- Live agent catalog, filtered to validated chat-capable harnesses:
-- { { id, name, harness, description }, ... } or nil, err.
local function _omnigent_pickable_agents()
  local agents, err = _omnigent_client():list_agents()
  if not agents then return nil, err end
  local out = {}
  for _, a in ipairs(agents) do
    if a.name and _omnigent_is_chat_agent(a) then
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
-- "gpt"/"codex"). There is NO models endpoint, so this is just a short curated
-- preset list per family, always paired with a free-text "custom…" escape and a
-- "default" (no override) option, so a missing/renamed id is never a dead end.
--
-- Reasoning-effort values are the per-provider families from
-- omnigent/reasoning_effort.py: claude has `max`, codex has `none`/`minimal`, and
-- all are lowercase. Listed in omnigent's canonical display order.
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
-- deployment is retired -> 404). Verified working end-to-end via omnigent:
-- gpt-5.5. gpt-5.6-sol is in daily use through the standalone codex CLI
-- (codex_config/config.template.toml), so the slug is routed, but its
-- omnigent app-server path has not been separately exercised.
--
-- Claude's large-context ids carry a trailing `[1m]` marker. Name every 1M-family
-- model in that spelling, always: it is the ONE lever that makes the window
-- correct on every surface at once, because omnigent's own resolver has a
-- GENERIC rule for it (any claude id ending in `[1m]` -> 1,000,000, no per-model
-- table) while it can size nothing else here — litellm is not installed and the
-- MLflow catalog is fetched from github, which a devserver cannot reach, so
-- every other id collapses to a 128k default. The gateway serves the same model
-- either way, so the suffix costs nothing and buys a correct ring in the
-- CodeCompanion meter, the terminal REPL (which reads the server's number, not
-- OMNIGENT_CONTEXT_WINDOWS), and the runner's compaction budget.
--
-- Keyed on FAMILY, not on model id, so a future opus-6 / sonnet-6 is covered by
-- adding it to CLAUDE_BASE_MODELS and nothing else. haiku is excluded: it is a
-- small-window model with no 1M variant (the gateway 400s on a `[1m]` id that
-- does not exist — verified with a nonsense suffix).
local CLAUDE_1M_SUFFIX = "[1m]"
local CLAUDE_1M_FAMILIES = { "opus", "sonnet" }

--- Return `id` in its 1M spelling when it names a 1M Claude family.
--- Idempotent, and a no-op for non-Claude and small-window ids.
local function claude_1m(id)
  if id:sub(-#CLAUDE_1M_SUFFIX) == CLAUDE_1M_SUFFIX then
    return id
  end
  for _, family in ipairs(CLAUDE_1M_FAMILIES) do
    if id:match("^claude%-" .. family .. "[%-%.]") then
      return id .. CLAUDE_1M_SUFFIX
    end
  end
  return id
end

-- Bare vendor ids; the picker offers whatever claude_1m() makes of them.
local CLAUDE_BASE_MODELS = {
  "claude-opus-5",
  "claude-opus-4-8",
  "claude-sonnet-5",
  "claude-haiku-4-5",
}

local OMNIGENT_MODELS = {
  claude = vim.tbl_map(claude_1m, CLAUDE_BASE_MODELS),
  codex  = { "gpt-5.5", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna" },
}

-- Per-family "default" model. Codex's app-server default per-turn model is NOT a
-- routed slug (-> 421), so "default" for codex must resolve to a routed model
-- rather than "no override". claude pins opus-5 by preference rather than
-- necessity: claude-sdk's own default would work, but it resolves provider-side
-- (gateway/key/subscription) and is not visible from here, so pinning makes the
-- launch model explicit and stable instead of silently provider-dependent.
local OMNIGENT_MODEL_DEFAULT = { claude = claude_1m("claude-opus-5"), codex = "gpt-5.6-sol" }

-- Per-model input context windows (vendor model id -> tokens), keyed exactly as
-- OMNIGENT_MODELS above. Wired into the omnigent adapter `opts.context_windows`
-- and consumed by the fork's chat/omnigent/render.lua:enrich_usage, where it
-- takes precedence over the server-reported window. WHY: omnigent's server
-- resolves the window from an external model catalog fetched over the internet;
-- on a devserver with no direct egress that fetch fails and every model
-- collapses to a conservative 128k default, mislabeling the context meter (e.g.
-- 229.9k/128.0k => 179%).
--
-- These are the vendors' true INPUT windows, which is deliberately NOT what the
-- server reports. Verified against the installed omnigent (0.6.0): litellm
-- resolves none of these ids, and the MLflow catalog entry for claude-opus-5
-- exists but has max_input_tokens / max_output_tokens / max_tokens all null, so
-- get_model_context_window falls through to _DEFAULT_CONTEXT_WINDOW = 128_000.
-- It is NOT the `max_input + max_output` summing path -- that needs non-null
-- fields. The only ids omnigent sizes correctly unaided are the `[1m]` forms,
-- via the Anthropic-beta rule in _registry_context_window.
-- Only ids the SERVER cannot size correctly are listed here; everything else is
-- deliberately absent so the server's number (now litellm-backed) flows through
-- and stays current without edits on our side. Vendor ids -- claude-opus-5,
-- claude-sonnet-5, claude-haiku-4-5, gpt-5.5, gpt-5.6-* -- are all resolved
-- correctly by litellm's bundled map, so listing them here would only be a
-- second copy to drift. (The gpt rows previously here claimed 1178000; litellm
-- says 1050000.)
--
-- What survives is dm-core's private vocabulary, which no public registry knows.
-- litellm does not merely miss these -- it fuzzy-matches `claude-opus-4.8` to
-- 200000, a confidently wrong answer -- so the override is load-bearing.
local OMNIGENT_CONTEXT_WINDOWS = {
  ["claude-opus-4.8"] = 1000000, -- dm-core dot spelling; litellm mis-resolves to 200k
  ["claude-haiku-4.5"] = 200000,
  ["gpt-5-5"] = 1050000, -- dm-core slug; the vendor id gpt-5.5 resolves fine
  ["gpt-5-6"] = 1050000,
  -- TODO: unverified -- no public registry knows this id and dm-core does not
  -- publish a window. Left at the conservative default rather than guessed.
  -- ["avocado-code-internal-2.0"] = ?,
}

-- dvsc (the `acp:dvsc-core` agent) runs Meta's dm-core, which speaks its OWN
-- model ids (dot-notation, e.g. `claude-opus-4.8`) — NOT omnigent's vendor ids.
-- Curated to this user's dm-core entitlement so the omnigent picker offers
-- exactly what dm-core can run. The picked id rides session/new `model` → the
-- wrapper's top-level-model fallback → dm-core. Mirrors Configerator
-- `devmate_vscode/model/model_config.cconf`; must only contain models the
-- running dm-core actually has in its loaded snapshot (an unknown id silently
-- falls back to dm-core's default). dvsc keeps no OMNIGENT_MODEL_DEFAULT entry:
-- its "default" means dm-core's own default model (nil override).
OMNIGENT_MODELS.dvsc = {
  "claude-opus-5",
  "claude-opus-4.8",
  "claude-sonnet-5",
  "claude-haiku-4.5",
  "gpt-5-6",
  "gpt-5-5",
  "avocado-code-internal-2.0",
}

-- No derived rows: with litellm installed, the server sizes every vendor id
-- correctly on its own, and a client-side copy could only drift or override a
-- correct value with a stale guess.

-- dvsc via the generic ACP harness has no reasoning-effort channel (the `acp`
-- harness is EffortFamily.NONE and omnigent forwards no effort to it), so the
-- picker offers only "default" — effort (and mode) come from dm-core's defaults.
OMNIGENT_EFFORTS.dvsc = {}

-- Classify a harness id into a model/effort family, mirroring the vendor-token
-- rules in omnigent/model_override.py (model_family_mismatch).
local function _omnigent_family_for_harness(harness)
  if type(harness) ~= "string" then return nil end
  local h = harness:lower()
  -- dvsc-core over the generic ACP harness (`acp:dvsc-core`). Checked first: its
  -- dm-core model ids (dot-notation, e.g. claude-opus-4.8) would otherwise be
  -- misread as the "claude" vendor family and offer the wrong catalog.
  if h:find("dvsc", 1, true) then return "dvsc" end
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
-- create. `cb` is invoked with no args once the selection is cached
-- (agent → model → effort).
local function _omnigent_select(force, cb)
  local agents, err = _omnigent_pickable_agents()
  if not agents then
    return vim.notify("omnigent: failed to list agents: " .. (err and err.message or "?"), vim.log.levels.ERROR)
  end
  if #agents == 0 then
    return vim.notify(
      "omnigent: no validated chat-capable agents are available",
      vim.log.levels.WARN
    )
  end
  -- Reuse the cached choice only if its exact agent remains chat-capable.
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
      if a.name == "codex-native-ui" then
        return a.name .. "  —  Codex native session (Goal support)"
      elseif a.name == "claude-native-ui" then
        return a.name .. "  —  Claude native session (terminal-backed, chat-rendered)"
      end
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
-- hydrate it (omnigent load is a synchronous REST round-trip).
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

-- Fork the current tab's omnigent session into a new, independently-runnable
-- session, then open it in a NEW tab (the source chat stays put). Mirrors the Web
-- UI's "Fork from here": POST /fork deep-copies the transcript, then a runner is
-- launched on the SOURCE's host in a fresh git worktree (isolating the fork's
-- working dir and, for a native harness, carrying the source's transcript, which
-- the host clones at boot -- hence the same-host requirement).
--
-- Tested path is the SDK harness (e.g. `polly`). A native fork (claude-native-ui
-- / codex-native-ui) forks server-side correctly, but its terminal-first
-- rendering inside a CC chat buffer is unproven -- treat it as experimental.
local function _omnigent_fork_current()
  local bufnr = vim.t.codecompanion_chat_bufnr
  local chat = bufnr
    and vim.api.nvim_buf_is_valid(bufnr)
    and require("codecompanion").buf_get_chat(bufnr)
  if not chat then
    return vim.notify("omnigent: no CodeCompanion chat in this tab to fork", vim.log.levels.WARN)
  end
  if not (chat.adapter and chat.adapter.type == "omnigent") then
    return vim.notify("omnigent: fork only works on an Omnigent chat", vim.log.levels.WARN)
  end
  local source_id = chat.omnigent_session_id
  if not source_id then
    return vim.notify(
      "omnigent: this chat has no durable session yet -- send a turn before forking",
      vim.log.levels.WARN
    )
  end

  local client = _omnigent_client()
  -- Authoritative host_id / workspace for the launch: the snapshot, not the live
  -- runtime (which may be nil before the first turn / ensure_session).
  local snap, gerr = client:get_session(source_id)
  if not snap then
    return vim.notify(
      "omnigent: could not load source session: " .. (gerr and gerr.message or "?"),
      vim.log.levels.ERROR
    )
  end

  local Session = require("codecompanion.omnigent.session")
  local branch = "cc-fork-" .. os.date("%m%d-%H%M%S")
  local fork, ferr = Session.fork(client, {
    session_id = source_id,
    host_id = snap.host_id,
    workspace = snap.workspace,
  }, { branch_name = branch }) -- base_branch nil => branch from the source's HEAD

  if not fork then
    local msg = "omnigent: fork failed: " .. (ferr and ferr.message or "?")
    if ferr and ferr.fork_session_id then
      -- The copy landed but the runner didn't; the session exists unbound.
      msg = msg .. " (created unbound session " .. ferr.fork_session_id .. "; resume it to retry a runner)"
    end
    return vim.notify(msg, vim.log.levels.ERROR)
  end

  -- Fork + launch succeeded: open it in a fresh tab so the source is untouched.
  vim.cmd("tabnew")
  if not _omnigent_open_chat_with_session(fork.id) then
    vim.cmd("tabclose") -- open_chat_with_session already notified; drop the empty tab
    return
  end
  vim.notify("omnigent: forked into " .. fork.id .. " (branch " .. branch .. ")", vim.log.levels.INFO)
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
      -- Append the full session id so the picker's fuzzy matcher can find a
      -- session by id; format_summary only surfaces the id for untitled ones.
      local label = sessions_lib.format_summary(s, { now = now })
      local sid = s.id or ""
      if sid ~= "" then
        label = label .. "  " .. sid
      end
      items[#items + 1] = { session = s, label = label }
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
    local scope_txt = is_scoped and ("this cwd (" .. cwd .. ")") or "ALL workspaces"
    local hint = "  ·  <A-c> (Alt+c) toggle scope"
    vim.ui.select(items, {
      prompt = "Resume Omnigent · " .. scope_txt .. hint,
      format_item = function(item) return item.label end,
      snacks = {
        source = "omnigent_continue",
        title = "Resume Omnigent · " .. scope_txt .. hint,
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

-- Agent-path choices for the top-level picker in `<leader>aG`.
local AGENT_PATHS = {
  { label = "Omnigent (server-owned session, pick agent+model+effort)", adapter = "omnigent" },
}

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
-- For `omnigent`, the picker runs through `_omnigent_select` (interactive
-- or cached based on `force_pick`) and caches the chosen agent+model+effort
-- before the swap so the new spawn picks it up.
--
-- If no chat exists in the tab, falls through to the normal launch
-- path (`tab_chat_open_or_toggle`).
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
    if adapter_name == "omnigent" then
      -- The chosen agent is cached and read back by the omnigent adapter function
      -- at spawn — the cache IS the source of truth for the selection.
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

  local function apply()
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
    chat:change_adapter(adapter_name)
  end

  if adapter_name == "omnigent" then
    return _omnigent_select(opts.force_pick or false, function() apply() end)
  end
  apply()
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
  -- Resolve the agent's harness family first. For a "special" harness like
  -- dvsc's ACP wrap it must win over the model-string guess: dm-core ids
  -- (claude-opus-4.8) would otherwise be misread as the "claude" vendor family
  -- and offer the wrong (omnigent vendor) catalog. For vendor harnesses the
  -- model token is the more specific signal, so it still takes precedence.
  local harness_fam
  if session.agent_id then
    local agents = _omnigent_pickable_agents()
    if agents then
      for _, a in ipairs(agents) do
        if a.id == session.agent_id then
          harness_fam = _omnigent_family_for_harness(a.harness)
          break
        end
      end
    end
  end
  if harness_fam == "dvsc" then return harness_fam end
  local fam = _omnigent_family_for_model(session.model_override or session.model)
  if fam then return fam end
  return harness_fam
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
-- Limitation: only options the agent actually exposes via `configOptions`
-- are settable live. A direct dvsc_core session launches with dm-core's own
-- defaults; model/mode are then switchable here if the wrapper re-exposes them
-- as SessionConfigOptions. `<leader>aZ` (close + reopen) restarts the session.
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
-- `compact` (e.g. devmate / codex forward their own `/compact`). This is
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
-- (devmate / claude_code, and codex if it exposes it). We submit
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
--   * omnigent → a `compact` control event on the durable session, which the
--     server dispatches by harness (terminal agents compact themselves; SDK
--     agents get server-side summarisation, where their spec allows it).
--   * dvsc (dvsc_core) → `dm-core/compact` ext RPC.
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
  -- Omnigent owns compaction end-to-end in the plugin: the session is durable and
  -- server-side, so unlike the ACP paths below there is nothing for the editor to
  -- orchestrate -- guards, the progress indicator, the boundary marker and the
  -- context-meter refresh all live in interactions/chat/omnigent/compaction.lua.
  -- The same entry point backs the in-chat `/omnigent_compact`.
  if chat.adapter and chat.adapter.type == "omnigent" then
    local ok, err = chat:compact_omnigent()
    if not ok then
      vim.notify("Cannot compact: " .. ((err and err.message) or "unknown error"), vim.log.levels.WARN)
    end
    return
  end
  if not chat.adapter or chat.adapter.type ~= "acp" or not chat.acp_connection then
    return vim.notify("Current chat has no live ACP connection.", vim.log.levels.WARN)
  end
  if chat.current_request then
    return vim.notify("Wait for the current request to finish before compacting.", vim.log.levels.WARN)
  end

  local adapter_name = chat.adapter.name
  if adapter_name == "dvsc_core" then
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

local function codecompanion_local_dir()
  local checkout = vim.env.CODECOMPANION_NVIM_REPO
    or vim.fn.expand("~/repos/codecompanion.nvim")
  local omnigent_adapter = checkout .. "/lua/codecompanion/adapters/omnigent/init.lua"
  if vim.fn.isdirectory(checkout .. "/.git") == 1
      and vim.fn.filereadable(omnigent_adapter) == 1 then
    return checkout
  end
  return nil
end

return {
  {
    "mkarrmann/codecompanion.nvim", -- fork: adds the native omnigent adapter (see <leader>aM)
    name = "codecompanion.nvim",
    dir = codecompanion_local_dir(),
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
            adapter = "omnigent",
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
            },
          },
          inline = { adapter = "ai_gateway" },
          cmd = { adapter = "ai_gateway" },
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
                    local labels = {
                      ["omnigent.google_chat.enabled"] = "true",
                      ["orchest.nvim_session"] = vim.env.NVS_SESSION_NAME or "ad-hoc",
                    }
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
                  -- Per-model context windows (see OMNIGENT_CONTEXT_WINDOWS):
                  -- render.lua:enrich_usage prefers these over the server's
                  -- catalog-default window so the meter is correct offline.
                  -- Keyed by the active model_override / model id.
                  context_windows = OMNIGENT_CONTEXT_WINDOWS,
                },
              })
            end,
          },
        },

        opts = {
          log_level = "DEBUG",
        },

        -- Every agent behind the omnigent adapter discovers the same rules
        -- natively: the Claude harness walks CLAUDE.md (resolving @-includes
        -- and merging user/project/local), Codex reads ~/.codex/AGENTS.md, and
        -- Metacode reads ~/.config/opencode/AGENTS.md -- all three symlinked
        -- from agent_config/global-development-preferences.md by sync.sh.
        -- Autoloading them here would send a second, lower-precedence copy as
        -- user-turn text. This gates rules autoload on every chat-construction
        -- path (CodeCompanion.chat, the chat buffer, Inline:to_chat) and
        -- nothing else: `/rules` calls rules.new():make() directly, and
        -- prompt-library items naming a group pass an explicit rules_name,
        -- so both bypass the gate at rules/helpers.lua:101.
        rules = {
          opts = {
            chat = {
              enabled = false,
            },
          },
        },

        extensions = {
          spinner = {},
        },

        display = {
          action_palette = {
            provider = has_snacks and "snacks" or "telescope",
          },
          chat = {
            -- Per-message timestamps on role headers (fork feature): virtual
            -- text only, never edits the buffer. See config.lua defaults +
            -- ui/init.lua:render_timestamps.
            show_timestamps = true,
            timestamp_format = "%H:%M:%S",
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
      local omnigent_tool_group = vim.api.nvim_create_augroup("codecompanion_omnigent_tool_output", { clear = true })
      vim.api.nvim_create_autocmd("User", {
        group = omnigent_tool_group,
        pattern = "CodeCompanionOmnigentToolCall",
        callback = function(args)
          local data = args.data or {}
          local item = data.item or {}
          tool_output.bind_call(data.bufnr, item.call_id, data.line_number)
        end,
      })
      vim.api.nvim_create_autocmd("User", {
        group = omnigent_tool_group,
        pattern = "CodeCompanionOmnigentToolOutput",
        callback = function(args)
          local data = args.data or {}
          if data.streaming then
            tool_output.mark_call_streamed(data.bufnr, data.call_id)
          else
            tool_output.set_call_output(data.bufnr, data.call_id, data.output)
          end
        end,
      })
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

      -- HACK: when the agent process dies, Connection:handle_process_exit nils
      -- session_id; the next turn silently mints a fresh session/new while the
      -- chat buffer keeps showing the old transcript — so you keep talking to
      -- what looks like the same agent, but it has none of the prior context.
      -- Surface the swap (toast + inline banner). The last established id is
      -- remembered on the long-lived connection object and is NOT cleared on
      -- exit, so we can detect replacement. A fresh connection has `previous`
      -- nil, so no warning fires on an intentional resume. Remove once upstream
      -- notifies on session replacement.
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

      local Connection = require("codecompanion.acp")

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
          local items = require("lib.codecompanion-queue").slash_commands()
          local kind = cmp.lsp.CompletionItemKind.Function
          vim.iter(items):map(function(item)
            item.kind = kind
            item.context = { bufnr = params.context.bufnr, cursor = params.context.cursor }
          end)
          callback({ items = items, isIncomplete = false })
        end
        function QueueSlash:execute(item, callback)
          require("lib.codecompanion-queue").execute_slash(item)
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

      -- Inline "Processing…" spinner. Anchored to the buffer the *event* names
      -- (data.bufnr), never to the focused buffer: RequestStarted is fired
      -- asynchronously w.r.t. the keypress, so focus may already have moved
      -- (inline's own set_current_buf, a chat window, a picker...). Keyed by
      -- request id so overlapping requests don't clobber each other and a
      -- RequestFinished only tears down its own indicator.
      local ns = vim.api.nvim_create_namespace("codecompanion_inline_indicator")
      local spinner_frames = { "⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏" }
      local indicators = {}

      local function clear(key)
        local indicator = indicators[key]
        if not indicator then return end
        indicators[key] = nil
        if indicator.timer then
          indicator.timer:stop()
          indicator.timer:close()
        end
        if indicator.extmark and vim.api.nvim_buf_is_valid(indicator.bufnr) then
          pcall(vim.api.nvim_buf_del_extmark, indicator.bufnr, ns, indicator.extmark)
        end
      end

      vim.api.nvim_create_autocmd("User", {
        pattern = "CodeCompanionRequestStarted",
        callback = function(args)
          local data = args.data or {}
          if data.bufnr then
            require("lib.codecompanion-queue").on_request_started(data.bufnr, data.id)
          end

          local bufnr = data.bufnr
          if not bufnr or not vim.api.nvim_buf_is_valid(bufnr) then return end
          if vim.bo[bufnr].filetype == "codecompanion" then return end
          if bufnr == require("lib.codecompanion-queue").bufnr() then return end

          local key = tostring(data.id)
          clear(key)

          -- buffer_context is 1-indexed and only present on inline requests.
          local ctx = data.buffer_context or {}
          local line = (ctx.start_line or (ctx.cursor_pos and ctx.cursor_pos[1]) or 1) - 1
          line = math.max(0, math.min(line, vim.api.nvim_buf_line_count(bufnr) - 1))

          local indicator = { bufnr = bufnr }
          indicators[key] = indicator

          local frame = 0
          indicator.timer = vim.uv.new_timer()
          indicator.timer:start(0, 80, function()
            vim.schedule(function()
              if indicators[key] ~= indicator then return end
              if not vim.api.nvim_buf_is_valid(bufnr) then
                clear(key)
                return
              end
              -- Reuse the extmark id so it tracks edits made underneath it
              -- (inline writes its output into this buffer as it streams).
              local ok, id = pcall(vim.api.nvim_buf_set_extmark, bufnr, ns, line, 0, {
                id = indicator.extmark,
                virt_text = { { spinner_frames[frame + 1] .. " Processing…", "Comment" } },
                virt_text_pos = "eol",
              })
              if ok then indicator.extmark = id end
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
          clear(tostring(data.id))
        end,
      })

      vim.api.nvim_create_user_command("CodeCompanionDoctor", function()
        require("lib.codecompanion-doctor").run()
      end, { desc = "Diagnose CodeCompanion / ACP state" })

      vim.api.nvim_create_user_command("CodeCompanionCompact", function()
        tab_chat_compact()
      end, { desc = "Compact the current CodeCompanion chat (Omnigent, or any compaction-capable ACP agent)" })

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
      { "<leader>aG", function() tab_chat_pick_agent_and_set({ clear = true, force_pick = true }) end, desc = "Pick agent (omnigent), fresh" },
      { "<leader>aM", function() tab_chat_set_adapter("omnigent",         { clear = true }) end, desc = "CodeCompanion Chat (Omnigent, remembered agent+model+effort)" },
      { "<leader>aA", function() tab_chat_set_adapter("omnigent",         { clear = true, force_pick = true }) end, desc = "CodeCompanion Chat (Omnigent, pick agent+model+effort)" },
      { "<leader>amc", function() omnigent_continue() end, desc = "Omnigent: resume durable session (cwd-scoped)" },
      { "<leader>amf", function() _omnigent_fork_current() end, desc = "Omnigent: fork this session into a new worktree + tab" },
      { "<leader>ak", tab_chat_compact, desc = "CodeCompanion: compact current chat (Omnigent session, dvsc RPC, or agent /compact)" },
      { "<leader>aZ", function() tab_chat_full_refresh() end, desc = "CodeCompanion: full refresh (close + reopen, pick agent + model + config)" },
      { "<leader>ao", tab_chat_pick_option, desc = "CodeCompanion: change live session option (ACP config, or Omnigent model/effort)" },
      { "<leader>aQ", function()
          local bufnr = vim.t.codecompanion_chat_bufnr
          local chat = bufnr
            and vim.api.nvim_buf_is_valid(bufnr)
            and require("codecompanion").buf_get_chat(bufnr)
          if chat then chat:close() end
        end, desc = "CodeCompanion: close current tab's chat" },
    },
  },
}
