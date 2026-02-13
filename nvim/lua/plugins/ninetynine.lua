return {
	{
		"ThePrimeagen/99",
		cond = not vim.g.vscode,
		dependencies = { "MunifTanjim/nui.nvim" },
		config = function()
			local _99 = require("99")
			local cwd = vim.uv.cwd()
			local basename = vim.fs.basename(cwd)
			_99.setup({
				provider = _99.Providers.ClaudeCodeProvider,
				logger = {
					level = _99.DEBUG,
					path = "/tmp/" .. basename .. ".99.debug",
					print_on_error = true,
				},
				completion = {
					source = "cmp",
				},
				md_files = {
					"AGENT.md",
				},
			})

			vim.keymap.set("v", "<leader>9v", function()
				_99.visual()
			end, { desc = "99 Visual" })

			vim.keymap.set("v", "<leader>9s", function()
				_99.stop_all_requests()
			end, { desc = "99 Stop All" })
		end,
	},
}
