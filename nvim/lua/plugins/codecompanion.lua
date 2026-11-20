-- CodeCompanion is kept only for the inline assistant (<leader>av) and its
-- action palette (<leader>ae), served by Claude over Meta's AI Gateway. Pinned
-- to a known-good upstream commit; update deliberately.
return {
  {
    "olimorris/codecompanion.nvim",
    commit = "9455f15e",
    dependencies = {
      "nvim-lua/plenary.nvim",
      "nvim-treesitter/nvim-treesitter",
    },
    opts = function()
      local has_snacks = pcall(require, "snacks")
      return {
        interactions = {
          -- An inline answer that needs prose opens a chat (Inline:to_chat);
          -- upstream's default chat adapter is Copilot, which isn't set up.
          chat = { adapter = "ai_gateway" },
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
                  -- Static, so the adapter doesn't fetch its model list from
                  -- api.anthropic.com, which rejects the gateway's token.
                  model = { default = model, choices = { [model] = { formatted_name = "Claude Opus 4.8", opts = {} } } },
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
        },
        display = {
          action_palette = {
            provider = has_snacks and "snacks" or "telescope",
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
          local bufnr = data.bufnr
          if not bufnr or not vim.api.nvim_buf_is_valid(bufnr) then return end
          if vim.bo[bufnr].filetype == "codecompanion" then return end

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
          clear(tostring(data.id))
        end,
      })
    end,

    cmd = { "CodeCompanion", "CodeCompanionCmd", "CodeCompanionActions" },
    keys = {
      { "<leader>ae", "<cmd>CodeCompanionActions<cr>", mode = { "n", "v" }, desc = "CodeCompanion Actions" },
      { "<leader>av", "<cmd>CodeCompanion<cr>", mode = { "n", "v" }, desc = "CodeCompanion Inline" },
    },
  },
}
