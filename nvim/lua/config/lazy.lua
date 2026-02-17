local spec
if vim.g.vscode then
	spec = {
		{ import = "plugins" },
	}
else
	spec = {
		{ "LazyVim/LazyVim", import = "lazyvim.plugins" },
		{ import = "lazyvim.plugins.extras.coding.nvim-cmp" },
		{ import = "lazyvim.plugins.extras.editor.telescope" },
		{ import = "lazyvim.plugins.extras.ui.indent-blankline" },
		{ import = "lazyvim.plugins.extras.lsp.none-ls" },
		{ import = "lazyvim.plugins.extras.ui.treesitter-context" },
		{ import = "lazyvim.plugins.extras.ai.claudecode" },
		{ import = "plugins" },
	}
end

require("lazy").setup({
	spec = spec,
	defaults = {
		lazy = false,
		version = false,
	},
	checker = { enabled = true },
})
