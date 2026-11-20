-- Reloading a live file from disk re-runs its FileType handlers, which map
-- LazyVim's treesitter ]f/[f in the buffer again. The diff split's keys still
-- win in its panes, and the buffer's own ]f still works outside them.
local H = dofile(os.getenv("NVIM_META_HG_LIB"))

H.run(function()
	local root = H.repo("diff")
	vim.cmd.edit(root .. "/bar.py")
	local bar = vim.api.nvim_get_current_buf()
	local origin = vim.api.nvim_get_current_win()
	vim.cmd("HgDiffSplitWorkingSet")
	local s = H.session()
	local pair, i = H.pair(s, "bar.py")
	require("lib.diff-session").show_pair(s, i)
	H.check(pair.is_live and pair.new_buf == bar, "the split shows the live bar.py buffer")

	H.write(root .. "/bar.py", "def a():\n    pass\n\n\ndef b():\n    pass\n")
	vim.cmd("checktime")
	vim.wait(200)
	H.eq(vim.api.nvim_buf_get_lines(bar, 0, 1, false), { "def a():" }, "bar.py was reloaded")

	vim.api.nvim_set_current_win(s.left_win)
	vim.fn.maparg("]f", "n", false, true).callback()
	H.eq(s.index, i % #s.pairs + 1, "]f moves to the next file after the reload")

	vim.api.nvim_set_current_win(origin)
	vim.api.nvim_win_set_cursor(0, { 1, 0 })
	vim.fn.maparg("]f", "n", false, true).callback()
	vim.wait(100)
	H.eq(vim.fn.line("."), 5, "]f outside the panes runs the buffer's own map")

	require("lib.diff-session").close(s)
	H.eq(vim.fn.maparg("]f", "n", false, true).desc, "Next Function Start", "the buffer's own ]f is back after close")
end)
