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
-- dvsc-core-acp wrapper's `extractDvscSelection` (in `agent.ts`) then
-- sources mode/model/thinking_effort from `_meta.broker.client.metadata.dvsc`
-- and applies them to the per-session `CreateAgentRequest`.
local _dvsc = { pending = nil }

local DVSC_CACHE_PATH = vim.fn.stdpath("data") .. "/dvsc-acp-last.json"

local DVSC_MODES = { "native", "claude", "codex", "metacode" }

-- Per-mode model lists. Sample set culled from dm-core tests; the live list
-- lives in Configerator and is queryable via `GET /models` on a running
-- dvsc-core server. Replace with a one-shot fetch + disk cache when the
-- hardcoded list goes stale.
local DVSC_MODELS_BY_MODE = {
  native = {
    "claude-opus-4-7",
    "claude-sonnet-4.6",
    "gpt-5.1-codex-max",
    "gpt-5.1-codex",
    "avocado-taster",
    "gemini-3-pro",
  },
  claude = { "claude-opus-4-7", "claude-sonnet-4.6", "claude-opus-4-6" },
  codex = { "gpt-5.1-codex-max", "gpt-5.1-codex", "gpt-5-codex", "gpt-5.2", "gpt-5.3-codex" },
  metacode = { "avocado-taster" },
}

-- Mirrors dvsc-core's ReasoningEffort enum (lowercased on the wire); the
-- wrapper translates per-provider before sending to dvsc-core.
local DVSC_EFFORTS = { "high", "medium", "low", "xhigh", "minimal" }

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

local function _dvsc_launch(sel)
  -- Stash for the adapter function to consume on next spawn. Racy if you
  -- start two chats simultaneously; fine for single-user.
  _dvsc.pending = vim.fn.json_encode({ dvsc = sel })
  vim.cmd("CodeCompanionChat adapter=dvsc_core_broker")
end

local function dvsc_pick_and_launch(force)
  local cache = _dvsc_read_cache()
  if not force and cache.mode and cache.model and cache.thinking_effort then
    return _dvsc_launch(cache)
  end
  _dvsc_pick(DVSC_MODES, "Harness:", function(mode)
    _dvsc_pick(DVSC_MODELS_BY_MODE[mode] or {}, "Model:", function(model)
      _dvsc_pick(DVSC_EFFORTS, "Thinking effort:", function(effort)
        local sel = { mode = mode, model = model, thinking_effort = effort }
        _dvsc_write_cache(sel)
        _dvsc_launch(sel)
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
                description = "Resume a saved session from the persistence-server",
                callback = function(chat)
                  -- Asks the broker to materialize a *fresh* live session
                  -- loaded with the saved conversation history (calls
                  -- meta.broker.persistence.resume_saved_session). Returns
                  -- a new broker_session_id which the new chat connects
                  -- to via session/load. The Connection:_establish_session
                  -- patch below forces loadSession even when the agent
                  -- doesn't advertise the capability.
                  --
                  -- The broker's list_saved_sessions wire surface returns
                  -- only opaque IDs (no name/metadata), so paste-the-id
                  -- is the only useful UX here. Get the ID via:
                  --   sqlite3 ~/.local/share/acp-persistence-server/persistence.db \
                  --     "SELECT saved_session_id, json_extract(metadata,'$.nvim_session') \
                  --      FROM sessions ORDER BY started_at DESC LIMIT 20;"
                  vim.ui.input({
                    prompt = "saved_session_id: ",
                    default = "ss-",
                  }, function(saved_id)
                    if not saved_id or saved_id == "" or saved_id == "ss-" then return end
                    local conn = require("codecompanion.acp").new({ adapter = chat.adapter })
                    if not conn:connect_and_authenticate() then
                      return vim.notify("Failed to connect to broker", vim.log.levels.ERROR)
                    end
                    -- Default target_broker_id to the local broker (matches
                    -- what acp-broker-up sets ACP_BROKER_ID to via
                    -- `hostname -s`). User can override by editing the
                    -- input, but rarely needed for personal-use setup.
                    local local_broker = vim.fn.hostname():gsub("%..*", "")
                    local resp = conn:send_rpc_request(
                      "meta.broker.persistence.resume_saved_session",
                      { saved_session_id = saved_id, target_broker_id = local_broker }
                    )
                    pcall(function() conn:disconnect() end)
                    if not resp or not resp.broker_session_id then
                      return vim.notify(
                        "Resume failed; broker returned: " .. vim.inspect(resp),
                        vim.log.levels.ERROR
                      )
                    end
                    vim.notify(
                      "Resumed " .. saved_id .. " -> " .. resp.broker_session_id,
                      vim.log.levels.INFO
                    )
                    require("codecompanion.interactions.chat").new({
                      adapter = chat.adapter,
                      acp_session_id = resp.broker_session_id,
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

              -- Per-launch metadata sourced from env vars exported by
              -- ~/dotfiles/bin/nvs. Falls back to a minimal identity
              -- when run outside an nvs-managed session (ad-hoc nvims,
              -- local dev, etc.) so attribution still produces
              -- something queryable.
              local metadata_json = vim.json.encode({
                nvim_session = vim.env.NVS_SESSION_NAME or "ad-hoc",
                host         = vim.env.NVS_HOST or vim.fn.hostname(),
                cwd          = vim.env.NVS_WORKDIR or vim.fn.getcwd(),
                nvim_pid     = vim.fn.getpid(),
              })

              return require("codecompanion.adapters").extend("claude_code", {
                commands = {
                  default = { attach_bin },
                  yolo = { attach_bin },
                },
                env = {
                  CLAUDE_CODE_OAUTH_TOKEN = "CLAUDE_CODE_OAUTH_TOKEN",
                  ACP_BROKER_SOCKET = broker_socket,
                  ACP_BROKER_CLIENT_METADATA_JSON = metadata_json,
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
              local payload = _dvsc.pending
                  or vim.fn.json_encode({ dvsc = { mode = "native" } })
              _dvsc.pending = nil
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
                "ACP_BROKER_AGENT_NAME=%s ACP_BROKER_AGENT_CMD=%s exec %s 2>>%s",
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
          vim.schedule(function()
            require("lib.codecompanion-queue").on_chat_opened(args.data.bufnr)
          end)
        end,
      })

      vim.api.nvim_create_autocmd("User", {
        pattern = "CodeCompanionChatHidden",
        callback = function(args)
          vim.schedule(function()
            require("lib.codecompanion-queue").on_chat_hidden(args.data.bufnr)
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
      { "<leader>ah", "<cmd>CodeCompanionChat Toggle<cr>", mode = { "n", "v" }, desc = "CodeCompanion Chat" },
      { "<leader>av", "<cmd>CodeCompanion<cr>", mode = { "n", "v" }, desc = "CodeCompanion Inline" },
      { "<leader>aq", function() require("lib.codecompanion-queue").focus() end, desc = "Focus CodeCompanion Input" },
      { "<leader>ad", "<cmd>CodeCompanionDoctor<cr>", desc = "CodeCompanion Doctor" },
      { "<leader>aD", "<cmd>CodeCompanionChat adapter=devmate<cr>", desc = "CodeCompanion Chat (Devmate)" },
      { "<leader>aS", "<cmd>CodeCompanionChat adapter=dvsc_core<cr>", desc = "CodeCompanion Chat (Dvsc Core)" },
      { "<leader>ag", function() dvsc_pick_and_launch(false) end, desc = "Dvsc Chat via broker (last config)" },
      { "<leader>aG", function() dvsc_pick_and_launch(true)  end, desc = "Dvsc Chat via broker (pick config)" },
      { "<leader>aC", "<cmd>CodeCompanionChat adapter=claude_broker<cr>", desc = "Claude Chat via broker (direct)" },
    },
  },
}
