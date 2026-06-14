-- Monkey-patch codecompanion.nvim's ACP Connection to handle the UNSTABLE
-- `elicitation/create` agent-to-client request via `vim.ui.select` /
-- `vim.ui.input` pickers.
--
-- Background. ACP's `session/request_permission` is the only built-in
-- channel for an agent to ask the user for a synchronous answer, but
-- its options are constrained to the four `allow_once` / `allow_always`
-- / `reject_once` / `reject_always` permission kinds. dm-core's
-- `ask_user_question` tool needs to surface structured questions
-- ("REST or GraphQL?") with arbitrary option labels, so the upstream
-- ACP spec added `elicitation/create` (PRs #376, #769, #771, #792,
-- #966 — all merged into the `unstable` namespace). The dvsc-core-acp
-- wrapper (D106593967) sends `elicitation/create` when the client
-- advertises `clientCapabilities.elicitation.form`; this module
-- advertises that capability and handles the inbound request.
--
-- The patch is wholly client-side and only takes effect when the
-- connected agent emits an `elicitation/create` request — no
-- behaviour change for any other adapter or for an agent that never
-- elicits. If a future codecompanion.nvim release ships native
-- elicitation support, `patch()` detects the pre-existing dispatch
-- entry and no-ops.
--
-- Spec source: `schema/schema.v2.unstable.json` in
-- github.com/agentclientprotocol/agent-client-protocol, commit `e2fb677`.
-- Re-vet the wire shape on upstream changes; the wrapper-side companion
-- types live at users/mk/mkarrmann/dvsc-core-acp/packages/acp-wrapper/src/elicitation.ts.

local M = {}

local ELICITATION_METHOD = "elicitation/create"

-- Best-effort error logger that won't take down the agent stream if the
-- plugin's logger module isn't loaded yet. Mirrors the pcall-then-log
-- pattern used by lib/codecompanion-doctor.lua.
local function log_error(msg)
  local ok, log = pcall(require, "codecompanion.utils.log")
  if ok and log and log.error then
    log:error("[acp::elicitation] %s", msg)
  else
    vim.notify("[acp::elicitation] " .. tostring(msg), vim.log.levels.ERROR)
  end
end

-- ===========================================================================
-- Render layer (pure-ish: takes a property + callback, drives vim.ui).
-- ===========================================================================

-- Returns the human-readable label for a property. Falls back through
-- `title` → property name. The wrapper sets `title = question.question`
-- so this is the actual user-facing question text in practice.
local function prompt_for(name, prop)
  return prop.title or name
end

-- Sentinel labels for the free-form escape hatch (problem (a)). ACP/MCP
-- elicitation enums are *closed* sets, so a value the user types is, strictly,
-- outside the schema's `enum`. We rely on the dvsc-core-acp wrapper forwarding
-- the response `content` verbatim into dm-core's `ask_user_question` result —
-- where answers are free text — so the agent reads the typed string as the
-- answer just like a picked option.
-- HACK: if a future wrapper strict-validates `content` against `enum` before
-- forwarding, the typed value would be rejected. The spec-pure fix is
-- server-side (emit the field as a plain `string`, or add an explicit
-- "Other (specify)" affordance to the schema).
-- Verified (D106593967, acp-wrapper/src/elicitation.ts → elicitationResponseToDecision):
-- the wrapper passes response `content[key]` through `stringifyContent` into
-- `ToolCallDecision.answers` with NO enum validation, so an out-of-enum typed
-- value round-trips verbatim into dm-core's `ask_user_question` result today.
-- Re-vet if that function starts validating against the requested schema.
local CUSTOM_ONE_LABEL = "✎ Other (type a custom response)…"
local CUSTOM_MANY_LABEL = "✎ add a custom value…"
local DONE_LABEL = "[done — submit selections]"
local CANCEL_LABEL = "[cancel]"

local function ask_input(prompt, callback)
  vim.ui.input({ prompt = prompt .. ": " }, function(value)
    callback(value)
  end)
end

-- Wraps vim.ui.select with the cancellable convention: callback(nil)
-- on Esc/cancel, callback(value) on selection.
local function pick_one(prompt, choices, callback)
  if #choices == 0 then
    return callback(nil)
  end
  vim.ui.select(choices, { prompt = prompt }, function(choice)
    callback(choice)
  end)
end

-- Like pick_one, but appends a free-form escape hatch so the user is never
-- boxed into the enum. Picking the sentinel drops to a text input.
local function pick_one_or_input(prompt, choices, callback)
  local entries = vim.list_extend({}, choices)
  entries[#entries + 1] = CUSTOM_ONE_LABEL
  vim.ui.select(entries, { prompt = prompt }, function(choice)
    if choice == nil then
      return callback(nil)
    elseif choice == CUSTOM_ONE_LABEL then
      return ask_input(prompt, callback)
    end
    callback(choice)
  end)
end

-- Multi-select via a re-entrant picker. Each iteration shows the remaining
-- choices plus `[done]` / `[cancel]` sentinels and a free-form "add custom
-- value" entry; selecting `[done]` finalises with the accumulated picks
-- (enum selections in choice order, then any custom values in entry order).
-- Tracks enum selections by index to keep duplicate labels safe.
local function pick_many(prompt, choices, callback)
  local picked_idx = {}
  local custom = {}
  local function collect()
    local out = {}
    for i = 1, #choices do
      if picked_idx[i] then
        out[#out + 1] = choices[i]
      end
    end
    for _, v in ipairs(custom) do
      out[#out + 1] = v
    end
    return out
  end
  local function step()
    local entries = { DONE_LABEL, CANCEL_LABEL, CUSTOM_MANY_LABEL }
    local index_map = {}
    for i, label in ipairs(choices) do
      if not picked_idx[i] then
        entries[#entries + 1] = label
        index_map[#entries] = i
      end
    end
    local current = collect()
    local hint = #current == 0 and "(none yet)"
      or string.format("(picked: %s)", table.concat(current, ", "))
    vim.ui.select(entries, { prompt = prompt .. " " .. hint }, function(entry, idx)
      if entry == nil then
        return callback(nil) -- Esc on the picker itself
      elseif entry == DONE_LABEL then
        return callback(collect())
      elseif entry == CANCEL_LABEL then
        return callback(nil)
      elseif entry == CUSTOM_MANY_LABEL then
        return ask_input(prompt, function(value)
          if value ~= nil and value ~= "" then
            custom[#custom + 1] = value
          end
          step()
        end)
      else
        local choice_idx = index_map[idx]
        if choice_idx then
          picked_idx[choice_idx] = true
        end
        return step()
      end
    end)
  end
  step()
end

-- Drives a single property's picker/input and yields the answer (or
-- nil on cancel) via callback(value).
--
-- Coverage matches the wrapper's `buildElicitationRequest`
-- (acp-wrapper/src/elicitation.ts):
--   string + enum     → single-select picker + free-form escape hatch
--   array + items.enum → multi-select picker + free-form "add custom value"
--   string (free)     → text input
--   boolean           → yes/no picker
--   number / integer  → text input, parsed
-- Anything else fails fast (handled by the caller as a decline).
local function ask_property(name, prop, callback)
  local prompt = prompt_for(name, prop)
  local ptype = prop.type
  if ptype == "string" then
    if type(prop.enum) == "table" and #prop.enum > 0 then
      return pick_one_or_input(prompt, prop.enum, callback)
    end
    return ask_input(prompt, callback)
  elseif ptype == "array" then
    local items = prop.items or {}
    if type(items.enum) == "table" and #items.enum > 0 then
      return pick_many(prompt, items.enum, callback)
    end
    -- Free-form arrays aren't generated by the wrapper today.
    log_error("array property without items.enum is unsupported: " .. name)
    return callback(nil)
  elseif ptype == "boolean" then
    return pick_one(prompt, { "true", "false" }, function(choice)
      if choice == nil then
        callback(nil)
      else
        callback(choice == "true")
      end
    end)
  elseif ptype == "integer" or ptype == "number" then
    return ask_input(prompt, function(value)
      if value == nil or value == "" then
        return callback(nil)
      end
      local parsed = tonumber(value)
      if parsed == nil then
        log_error(string.format("property %s expected %s, got %q", name, ptype, value))
        return callback(nil)
      end
      if ptype == "integer" and parsed ~= math.floor(parsed) then
        log_error(string.format("property %s expected integer, got %s", name, value))
        return callback(nil)
      end
      callback(parsed)
    end)
  end
  log_error(string.format("unsupported property type for %s: %s", name, tostring(ptype)))
  return callback(nil)
end

-- Walks every property in `schema.properties` and yields a `content`
-- table with one entry per answered property. Returns nil via callback
-- if the user cancelled any prompt.
--
-- Order matters for UX: the wrapper iterates dm-core's
-- `questions[]` in source order, so iterating `schema.required` (also
-- in source order) preserves that. Properties not listed in `required`
-- are appended afterwards (defensive — the wrapper marks every
-- question required, so this branch shouldn't fire in practice).
local function ask_schema(schema, callback)
  local properties = (schema and schema.properties) or {}
  local order = {}
  local seen = {}
  if type(schema.required) == "table" then
    for _, name in ipairs(schema.required) do
      if properties[name] and not seen[name] then
        order[#order + 1] = name
        seen[name] = true
      end
    end
  end
  for name, _ in pairs(properties) do
    if not seen[name] then
      order[#order + 1] = name
      seen[name] = true
    end
  end

  local content = {}
  local i = 1
  local function step()
    if i > #order then
      return callback(content)
    end
    local name = order[i]
    i = i + 1
    ask_property(name, properties[name], function(value)
      if value == nil then
        return callback(nil)
      end
      content[name] = value
      step()
    end)
  end
  step()
end

-- ===========================================================================
-- Wire layer: receive request → render → send response.
-- ===========================================================================

-- Surfaces the elicitation's overall `message` to the user before any
-- per-property picker opens. Long preambles get an `:echo`; short ones
-- inline into the first picker prompt via the schema's `description`.
local function announce_preamble(message)
  if type(message) ~= "string" or #message == 0 then
    return
  end
  -- vim.notify is the most consistent surface across UIs.
  vim.notify(message, vim.log.levels.INFO, { title = "Devmate question" })
end

-- Plan-exit elicitations (the wrapper's `buildPlanExitElicitationRequest`)
-- carry the plan file path in `_meta["dvsc.planPath"]`. Open it in a split so
-- the user can read — and optionally edit — the plan before answering the
-- sign-off picker. Editable on purpose: the agent reads the plan file when it
-- proceeds, so last-minute tweaks take effect. Best-effort — a missing or
-- unreadable path is silently skipped (the elicitation still carries a
-- human-readable `message`).
local PLAN_PATH_META_KEY = "dvsc.planPath"

local function open_plan_file(meta)
  if type(meta) ~= "table" then
    return
  end
  local path = meta[PLAN_PATH_META_KEY]
  if type(path) ~= "string" or path == "" or vim.fn.filereadable(path) == 0 then
    return
  end
  -- Reuse an already-open window on this file if one exists, else vsplit.
  local bufnr = vim.fn.bufnr(path)
  if bufnr ~= -1 then
    local win = vim.fn.bufwinid(bufnr)
    if win ~= -1 then
      return vim.api.nvim_set_current_win(win)
    end
  end
  vim.cmd("vsplit " .. vim.fn.fnameescape(path))
end

-- Switch focus to the tab that owns the chat behind `conn` before any picker
-- or split opens (problem (c)): elicitation UI must land in the agent's tab,
-- not wherever the cursor happens to be when the request arrives. Relies on
-- the per-tab ownership stamp set by the `CodeCompanionChatOpened` autocmd
-- (`vim.t.codecompanion_chat_bufnr` in plugins/codecompanion.lua). The handler's
-- `self` *is* the chat's `acp_connection`, so identity match is reliable.
-- Best-effort: stays put if no owning tab is found (e.g. the chat is hidden).
local function focus_owning_tab(conn)
  local ok_cc, cc = pcall(require, "codecompanion")
  if not ok_cc or type(cc.buf_get_chat) ~= "function" then
    return
  end
  for _, tab in ipairs(vim.api.nvim_list_tabpages()) do
    local ok, bufnr = pcall(vim.api.nvim_tabpage_get_var, tab, "codecompanion_chat_bufnr")
    if ok and type(bufnr) == "number" and vim.api.nvim_buf_is_valid(bufnr) then
      local chat = cc.buf_get_chat(bufnr)
      if chat and chat.acp_connection == conn then
        pcall(vim.api.nvim_set_current_tabpage, tab)
        return
      end
    end
  end
end

-- Main entry: takes the raw `elicitation/create` request and replies
-- on the same Connection. `accept` carries the picked content;
-- `cancel` is the wrapper-friendly word for "user dismissed"; the
-- spec also has `decline` (semantic: "user explicitly says no") which
-- we don't surface separately — pressing Esc means cancel here.
function M._handle_elicitation_create(conn, msg)
  local id = msg.id
  local params = msg.params or {}
  -- The wrapper only ever sends `mode: "form"`. If someone hooks this
  -- up to a URL-mode agent later, this branch fails fast.
  if params.mode ~= "form" then
    return conn:send_error(
      id,
      "only form-mode elicitation is supported by this client",
      -32601 -- METHOD_NOT_FOUND-ish; closest match for "feature unsupported"
    )
  end
  local schema = params.requestedSchema
  if type(schema) ~= "table" or type(schema.properties) ~= "table" then
    return conn:send_error(id, "invalid requestedSchema", -32602) -- INVALID_PARAMS
  end

  vim.schedule(function()
    focus_owning_tab(conn)
    open_plan_file(params._meta)
    announce_preamble(params.message)
    ask_schema(schema, function(content)
      if content == nil then
        return conn:send_result(id, { action = "cancel" })
      end
      conn:send_result(id, { action = "accept", content = content })
    end)
  end)
end

-- ===========================================================================
-- Patch installation.
-- ===========================================================================

-- True iff the patch has already been installed. Idempotent so a
-- second `patch()` call (e.g. plugin re-setup, :PackerCompile reload)
-- doesn't stack wraps.
local _patched = false

function M.patch()
  if _patched then
    return
  end
  local ok, Connection = pcall(require, "codecompanion.acp")
  if not ok or type(Connection) ~= "table" then
    log_error("could not load codecompanion.acp; elicitation patch skipped")
    return
  end

  -- 1. Inbound dispatch.
  --    `Connection:handle_incoming_request_or_notification` looks up
  --    the method in an internal DISPATCH table at module scope (not
  --    accessible from outside). Wrap the method instead and short-
  --    circuit on `elicitation/create` before the original lookup.
  local orig_handle = Connection.handle_incoming_request_or_notification
  if type(orig_handle) ~= "function" then
    log_error("Connection.handle_incoming_request_or_notification missing; skipping")
    return
  end
  function Connection:handle_incoming_request_or_notification(notification)
    if
      type(notification) == "table"
      and notification.method == ELICITATION_METHOD
      and notification.id ~= nil
    then
      return M._handle_elicitation_create(self, notification)
    end
    return orig_handle(self, notification)
  end

  -- 2. Capability advertisement.
  --    `Connection:connect_and_authenticate` sends `initialize` with
  --    `self.adapter_modified.parameters` as the request body — but
  --    `adapter_modified` is set to `{}` by `Connection.new` and
  --    only populated later inside `start_agent_process` →
  --    `prepare_adapter` (a `vim.deepcopy(self.adapter)`). So we
  --    splice the cap into the adapter table that `prepare_adapter`
  --    *returns* — that's the table assigned to `adapter_modified` and
  --    read by INITIALIZE moments later in the same call.
  local orig_prepare = Connection.prepare_adapter
  if type(orig_prepare) == "function" then
    function Connection:prepare_adapter()
      local adapter = orig_prepare(self)
      if type(adapter) == "table" then
        adapter.parameters = adapter.parameters or {}
        adapter.parameters.clientCapabilities = adapter.parameters.clientCapabilities or {}
        local caps = adapter.parameters.clientCapabilities
        -- vim.empty_dict() serializes as `{}` (object) rather than `[]` (array)
        -- — important because the spec types `form` as an object.
        caps.elicitation = caps.elicitation or { form = vim.empty_dict() }
        if caps.elicitation.form == nil then
          caps.elicitation.form = vim.empty_dict()
        end
      end
      return adapter
    end
  else
    log_error("Connection.prepare_adapter missing; capability not advertised")
  end

  _patched = true
end

-- Expose internal helpers for the spec runner.
M._internal = {
  ask_property = ask_property,
  ask_schema = ask_schema,
  prompt_for = prompt_for,
  custom_one_label = CUSTOM_ONE_LABEL,
  custom_many_label = CUSTOM_MANY_LABEL,
}

return M
