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
	-- Disabled because plugins like codecompanion.nvim ship rockspecs that
	-- declare dependencies (e.g. plenary.nvim) not reliably available on
	-- luarocks for Lua 5.1. This is a known ecosystem-wide issue with no
	-- upstream fix planned. See: lazy.nvim#1570, plenary.nvim#615.
	-- Re-enable if you add a plugin that requires an actual luarock (e.g.
	-- magick, luaxml) — at that point you'll also need a working Lua 5.1
	-- toolchain (hererocks or system lua5.1 + luarocks).
	rocks = { enabled = false },
	checker = { enabled = true },
})
