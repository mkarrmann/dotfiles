local vim = vim

vim.api.nvim_create_autocmd("FileType", {
	pattern = "python",
	callback = function()
		vim.o.textwidth = 88
	end,
})

vim.api.nvim_create_autocmd("FileType", {
	pattern = { "python", "sql", "toml", "ini", "dockerfile", "sh" },
	callback = function()
		vim.bo.expandtab = true
		vim.bo.tabstop = 4
		vim.bo.shiftwidth = 4
	end,
})

vim.api.nvim_create_autocmd("TermOpen", {
	callback = function()
		vim.b.virtcolumn_items = {}
		vim.w.virtcolumn_items = {}
		vim.opt_local.colorcolumn = ""
		local ns = vim.api.nvim_create_namespace("virtcolumn")
		vim.api.nvim_buf_clear_namespace(0, ns, 0, -1)
	end,
})

-- Force full redraw on focus gain or terminal re-entry. Needed because:
-- 1. Neovim doesn't fully repaint terminal buffers after overlapping
--    floats/splits close, e.g. Ctrl+g editor in Claude Code (TermEnter)
vim.api.nvim_create_autocmd({ "FocusGained", "TermEnter" }, {
	callback = function()
		vim.cmd("redraw!")
	end,
})

-- Downgrade "✓" (Claude done, unread) → "~" (seen) when switching to the tab.
vim.api.nvim_create_autocmd("TabEnter", {
	callback = function()
		local ok, state = pcall(vim.api.nvim_tabpage_get_var, 0, "claude_state")
		if ok and state == "✓" then
			vim.api.nvim_tabpage_set_var(0, "claude_state", "~")
			vim.cmd("redrawtabline")
		end
	end,
})

-- Copy every yank to the local clipboard via OSC52 (copy-only).
local osc52 = require("vim.ui.clipboard.osc52")
vim.api.nvim_create_autocmd("TextYankPost", {
	callback = function()
		if vim.v.event.operator == "y" then
			osc52.copy("+")(vim.v.event.regcontents, vim.v.event.regtype)
		end
	end,
})

-- Open a file from a nested terminal in a vertical split instead of
-- launching a nested Neovim instance.
-- Called via `nvim --server $NVIM --remote-send` from the shell function.
function _G._open_from_terminal(path)
	vim.cmd.vsplit(vim.fn.fnameescape(path))
end

-- Strip inherited scrollbind/cursorbind/diff from newly-created windows.
--
-- Problem: Vim copies window-local options from the source window to the
-- new window on `:split`/`:vsplit`. When the source window is a diff pane
-- (or an hg-blame pane, or any other scrollbind-tagged window), every
-- subsequent split — terminals, CodeCompanion chats, file buffers — joins
-- the scrollbind group and scrolls along with the diff. Scrolling one
-- diff pane then yanks unrelated windows along with it.
--
-- All our diff setups (lib/diff-opts.apply, lib/claude-diff.lua,
-- lib/meta-hg.lua) explicitly set scrollbind/cursorbind on their
-- intended windows AFTER creating them, so stripping the inherited
-- values on WinNew does not break them. Vim's builtin `:diffsplit`
-- and `:diffthis` also re-enable these via `diff=true`'s implied
-- behavior, so they're unaffected.
--
-- Side effect: if you ever want to `:split` and have the new pane join
-- an existing scrollbind group by inheritance, you'll need to either
-- run the diff setup explicitly on the new window or call `:set
-- scrollbind cursorbind` after the split.
vim.api.nvim_create_autocmd("WinNew", {
	group = vim.api.nvim_create_augroup("strip_inherited_bind_options", { clear = true }),
	callback = function()
		local win = vim.api.nvim_get_current_win()
		vim.wo[win].scrollbind = false
		vim.wo[win].cursorbind = false
		vim.wo[win].diff = false
	end,
})

require("lib.autosave").setup()

pcall(require, "config.local")
