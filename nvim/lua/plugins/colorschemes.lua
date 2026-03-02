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
					vim.api.nvim_set_hl(0, "DiffAdd", { bg = "#1f4a2d" })
					vim.api.nvim_set_hl(0, "DiffDelete", { bg = "#5c2530" })
					vim.api.nvim_set_hl(0, "DiffChange", { bg = "#4d4418" })
					vim.api.nvim_set_hl(0, "DiffText", { bg = "#6b5f20" })
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
