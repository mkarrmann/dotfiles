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

local function clear_colorcolumn()
	vim.b.virtcolumn_items = {}
	vim.w.virtcolumn_items = {}
	vim.opt_local.colorcolumn = ""
	local ns = vim.api.nvim_create_namespace("virtcolumn")
	vim.api.nvim_buf_clear_namespace(0, ns, 0, -1)
end

-- 'colorcolumn' rulers are opt-out by filetype rather than forced globally.
--
-- Why: virtcolumn.nvim re-derives its bars from the window-local 'cc' on a
-- dozen high-frequency events (WinScrolled, TextChanged*, WinEnter, ...). A
-- global 'cc' means every newly-created window (e.g. CodeCompanion's chat,
-- input, and status panes) inherits a non-empty local 'cc', so virtcolumn's
-- reseed clause (`local_cc ~= ''`) re-populates and redraws the bars no matter
-- how many times we clear them. Clearing once on FileType can't win that race.
-- Keeping the global empty and applying 'cc' only to wanted buffers removes the
-- source: excluded windows never receive a non-empty local 'cc', so there is
-- nothing for virtcolumn to reseed from.
local COLORCOLUMN = "79,80,88,100,120"
local NO_COLORCOLUMN = {
	codecompanion = true,
	codecompanion_input = true,
	codecompanion_queue_entry = true,
	codecompanion_cli = true,
}

vim.api.nvim_create_autocmd({ "FileType", "BufWinEnter" }, {
	group = vim.api.nvim_create_augroup("apply_colorcolumn", { clear = true }),
	callback = function()
		if NO_COLORCOLUMN[vim.bo.filetype] or vim.bo.buftype ~= "" then
			clear_colorcolumn()
		else
			vim.opt_local.colorcolumn = COLORCOLUMN
		end
	end,
})

vim.api.nvim_create_autocmd("TermOpen", {
	callback = clear_colorcolumn,
})

-- Force full redraw on focus gain or terminal re-entry. Needed because:
-- 1. Neovim doesn't fully repaint terminal buffers after overlapping
--    floats/splits close, e.g. Ctrl+g editor in Claude Code (TermEnter)
vim.api.nvim_create_autocmd({ "FocusGained", "TermEnter" }, {
	callback = function()
		vim.cmd("redraw!")
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
-- All our diff setups (lib/diff-opts.apply, lib/meta-hg.lua)
-- explicitly set scrollbind/cursorbind on their
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

-- New tabs open in the global cwd rather than inheriting the current tab's.
--
-- Problem: `:tabnew` copies the effective directory AND its locality, so a tab
-- spawned from a `:tcd`'d project tab is itself pinned to that project. With
-- scope.nvim isolating buffers per tab that reads as "every tab is its own
-- project", right up until one unrelated `:cd` collapses them all -- `:cd` is
-- global and additionally drops the current tab's local dir.
--
-- Clearing a local dir has no dedicated command; `:cd <current global>` is the
-- idiom. It re-sets the global to the value it already holds (a no-op) and
-- drops the local dir of the current tab/window ONLY -- other tabs keep their
-- `:tcd` pins.
--
-- The guard matters: without it every `:tabnew` would fire a redundant `:cd`,
-- emitting DirChanged to every plugin listening for it.
--
-- Explicit directory changes made after the tab exists still win, so
-- `lib/meta-hg.lua`'s `tabnew` + `lcd` terminal pattern is unaffected. Window
-- splits inherit as before; this only touches tab creation.
vim.api.nvim_create_autocmd("TabNewEntered", {
	group = vim.api.nvim_create_augroup("tab_cwd_reset", { clear = true }),
	desc = "Open new tabs in the global cwd instead of inheriting one",
	callback = function()
		if vim.fn.haslocaldir(0) == 0 and vim.fn.haslocaldir(-1, 0) == 0 then
			return -- already on the global cwd; nothing to clear
		end
		vim.cmd("cd " .. vim.fn.fnameescape(vim.fn.getcwd(-1, -1)))
	end,
})

require("lib.autosave").setup()

pcall(require, "config.local")
