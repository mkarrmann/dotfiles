return {
	{
		"dasupradyumna/midnight.nvim",
		name = "midnight",
		lazy = false,
		priority = 1000,
		init = function()
			vim.api.nvim_create_autocmd("ColorScheme", {
				pattern = "midnight",
				callback = function()
					-- Outline modified lines with colored underlines instead of
					-- solid background fills, keeping text readable.
					vim.api.nvim_set_hl(0, "DiffAdd", { sp = "#42be65", underline = true })
					vim.api.nvim_set_hl(0, "DiffDelete", { sp = "#fa4d56", underline = true })
					vim.api.nvim_set_hl(0, "DiffChange", { sp = "#d2a106", underline = true })
					vim.api.nvim_set_hl(0, "DiffText", { sp = "#d2a106", bg = "#302a0e" })
					vim.api.nvim_set_hl(0, "WinBar", { fg = "#888888", bg = "#1a1a2e" })
					vim.api.nvim_set_hl(0, "WinBarNC", { fg = "#555555", bg = "#1a1a2e" })
				end,
			})
		end,
	},

	{
		"LazyVim/LazyVim",
		opts = {
			colorscheme = "midnight",
		},
	},
}
