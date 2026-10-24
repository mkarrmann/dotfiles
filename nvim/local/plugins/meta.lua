local META_NVIM_DIR = vim.fn.isdirectory("/usr/share/fb-editor-support/nvim") == 1
	and "/usr/share/fb-editor-support/nvim"
	or "/usr/local/share/fb-editor-support/nvim"

return {
	{
		dir = META_NVIM_DIR,
		name = "meta.nvim",
		cond = function() return vim.fn.isdirectory(META_NVIM_DIR) == 1 end,
		-- meta.nvim's init requires null-ls (meta/null-ls/init.lua), so none-ls
		-- has to be on the runtimepath first.
		dependencies = { "nvimtools/none-ls.nvim" },
		init = function()
			-- meta.nvim's init runs require("meta.hg").setup(). lib.meta-hg is our
			-- fork of that module; serve it instead, so only one implementation
			-- registers sign and blame autocmds.
			package.preload["meta.hg"] = function()
				return require("lib.meta-hg")
			end
		end,
		config = function()
			local meta = require("meta")
			meta.setup()

			require("null-ls").register({
				meta.null_ls.diagnostics.arclint,
				meta.null_ls.formatting.arclint,
			})

			-- arclint's formatter registers for every filetype but refuses to run
			-- in fbsource, which leaves none-ls holding LazyVim's primary format
			-- slot and doing nothing. Give linttool the slot where it attaches.
			require("lib.meta-format").setup()
		end,
	},
}
