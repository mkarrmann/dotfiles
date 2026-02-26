return {
	{
		"pittcat/codex.nvim",
		keys = {
			{ "<leader>ao", "<cmd>CodexToggle<cr>", desc = "Toggle Codex" },
			{ "<leader>aO", "<cmd>CodexOpen<cr>", desc = "Open Codex" },
			{ "<leader>aR", ":'<,'>CodexSendReference<cr>", mode = "v", desc = "Send reference to Codex" },
		},
		opts = {
			terminal = {
				provider = "auto",
				direction = "vertical",
				position = "right",
				size = 0.35,
			},
			terminal_bridge = {
				auto_attach = true,
				path_format = "rel",
				path_prefix = "@",
				selection_mode = "reference",
			},
			extra_args = {
				"--no-alt-screen",
			},
			env = {
				EDITOR = vim.fn.expand("~/bin/nvim-edit-in-tab"),
			},
		},
		init = function()
			vim.api.nvim_create_user_command("CodexAdd", function()
				vim.cmd("CodexSendPath")
			end, { desc = "Add current file path to Codex" })
			vim.api.nvim_create_user_command("CodexSend", function()
				vim.cmd("'<,'>CodexSendSelection")
			end, { range = true, desc = "Send visual selection to Codex" })
			vim.api.nvim_create_user_command("CodexTreeAdd", function()
				vim.cmd("CodexSendPath")
			end, { desc = "Add current tree file path to Codex" })
		end,
	},
}
