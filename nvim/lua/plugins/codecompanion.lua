return {
  {
    "olimorris/codecompanion.nvim",
    dependencies = {
      "nvim-lua/plenary.nvim",
      "nvim-treesitter/nvim-treesitter",
    },
    opts = function()
      local has_snacks = pcall(require, "snacks")

      return {
        interactions = {
          chat = { adapter = "claude_code" },
          inline = { adapter = "claude_code" },
          cmd = { adapter = "claude_code" },
        },

        adapters = {
          acp = {
            claude_code = function()
              return require("codecompanion.adapters").extend("claude_code", {
                defaults = {
                  model = "claude-opus-4-6",
                },
                env = {},
                handlers = {
                  auth = function() return true end,
                },
              })
            end,
          },
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

      -- HACK: Upstream acp_commands.lua uses `vim.pesc(trigger)` which double-escapes
      -- the backslash, producing `\\w\+` instead of `\\\w\+`. This breaks cmp filtering.
      -- Patch is applied lazily on FileType since the cmp source isn't loaded at config time.
      vim.api.nvim_create_autocmd("FileType", {
        pattern = "codecompanion",
        once = true,
        callback = function()
          local ok, acp_src = pcall(require, "codecompanion.providers.completion.cmp.acp_commands")
          if not ok then return end
          local orig = acp_src.get_keyword_pattern
          acp_src.get_keyword_pattern = function(self)
            local pat = orig(self)
            if pat == [[\\w\+]] then return [[\\\w\+]] end
            vim.notify("[codecompanion] ACP keyword pattern changed upstream — review HACK in plugins/codecompanion.lua",
              vim.log.levels.WARN)
            return pat
          end
        end,
      })
    end,

    cmd = { "CodeCompanion", "CodeCompanionChat", "CodeCompanionActions" },
    keys = {
      { "<leader>ae", "<cmd>CodeCompanionActions<cr>", mode = { "n", "v" }, desc = "CodeCompanion Actions" },
      { "<leader>ah", "<cmd>CodeCompanionChat Toggle<cr>", mode = { "n", "v" }, desc = "CodeCompanion Chat" },
      { "<leader>av", "<cmd>CodeCompanion<cr>", mode = { "n", "v" }, desc = "CodeCompanion Inline" },
    },
  },
}
