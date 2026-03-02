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
