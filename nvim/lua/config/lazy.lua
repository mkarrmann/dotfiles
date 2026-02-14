require("lazy").setup({
	spec = {
		{ "LazyVim/LazyVim", import = "lazyvim.plugins" },
		{ import = "lazyvim.plugins.extras.coding.nvim-cmp" },
		{ import = "lazyvim.plugins.extras.editor.telescope" },
		{ import = "lazyvim.plugins.extras.ui.indent-blankline" },
		{ import = "lazyvim.plugins.extras.lsp.none-ls" },
		{ import = "lazyvim.plugins.extras.ui.treesitter-context" },
		{ import = "lazyvim.plugins.extras.ai.claudecode" },
		{ import = "plugins" },
	},
	defaults = {
		lazy = false,
		version = false,
	},
	checker = { enabled = true },
})
