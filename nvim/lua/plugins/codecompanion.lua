return {
	{
		"olimorris/codecompanion.nvim",
		dependencies = {
			"nvim-lua/plenary.nvim",
			"nvim-treesitter/nvim-treesitter",
		},
		keys = {
			{ "<leader>ao", "<cmd>CodeCompanionChat adapter=codex<cr>", desc = "Open Codex Chat" },
			{ "<leader>aO", "<cmd>CodeCompanionChat Toggle<cr>", desc = "Toggle AI Chat" },
			{ "<leader>aP", "<cmd>CodeCompanionActions<cr>", mode = { "n", "v" }, desc = "AI Action Palette" },
			{ "<leader>aS", "<cmd>CodeCompanionChat Add<cr>", mode = "v", desc = "Send to AI Chat" },
		},
		opts = {
			adapters = {
				acp = {
					codex = function()
						return require("codecompanion.adapters").extend("codex", {
							defaults = {
								auth_method = "openai-api-key",
							},
							env = {
								OPENAI_API_KEY = "OPENAI_API_KEY",
							},
						})
					end,
				},
			},
			interactions = {
				chat = {
					adapter = "codex",
				},
			},
		},
	},
}
