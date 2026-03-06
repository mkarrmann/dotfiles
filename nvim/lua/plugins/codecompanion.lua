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

    cmd = { "CodeCompanion", "CodeCompanionChat", "CodeCompanionActions" },
    keys = {
      { "<leader>ae", "<cmd>CodeCompanionActions<cr>", mode = { "n", "v" }, desc = "CodeCompanion Actions" },
      { "<leader>ah", "<cmd>CodeCompanionChat Toggle<cr>", mode = { "n", "v" }, desc = "CodeCompanion Chat" },
      { "<leader>av", "<cmd>CodeCompanion<cr>", mode = { "n", "v" }, desc = "CodeCompanion Inline" },
    },
  },
}
