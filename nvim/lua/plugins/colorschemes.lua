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
					local accent = require("lib.session-accent")
					local winbar_bg = accent.winbar_bg()
					-- Outline modified lines with colored underlines instead of
					-- solid background fills, keeping text readable.
					vim.api.nvim_set_hl(0, "DiffAdd", { sp = "#42be65", underline = true })
					vim.api.nvim_set_hl(0, "DiffDelete", { sp = "#fa4d56", underline = true })
					vim.api.nvim_set_hl(0, "DiffChange", { sp = "#d2a106", underline = true })
					vim.api.nvim_set_hl(0, "DiffText", { sp = "#d2a106", bg = "#302a0e" })
					vim.api.nvim_set_hl(0, "WinBar", { fg = "#888888", bg = winbar_bg })
					vim.api.nvim_set_hl(0, "WinBarNC", { fg = "#555555", bg = winbar_bg })
					-- Tint split separators and float borders with the per-instance
					-- accent so each nvs session is recognizable at a glance.
					vim.api.nvim_set_hl(0, "WinSeparator", { fg = accent.accent() })
					vim.api.nvim_set_hl(0, "FloatBorder", { fg = accent.accent() })
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
