-- Diff split keys act on the split whose pane you're in. A live file buffer
-- shared by splits in two tabs keeps working in both, and behaves normally
-- in an ordinary window.
local H = dofile(os.getenv("NVIM_META_HG_LIB"))

H.run(function()
	local root = H.repo("diff")
	local ds = require("lib.diff-session")
	vim.cmd.edit(root .. "/bar.py")
	local bar = vim.api.nvim_get_current_buf()
	vim.cmd("HgDiffSplitWorkingSet")
	local s1 = H.session()
	local tab1 = vim.api.nvim_get_current_tabpage()

	vim.cmd.tabnew(root .. "/bar.py")
	vim.cmd("HgDiffSplitWorkingSet")
	local s2 = H.session()
	H.check(H.pair(s1, "bar.py").new_buf == bar and H.pair(s2, "bar.py").new_buf == bar, "both splits show the live bar.py buffer")

	-- gq in tab 1 closes tab 1's split, not tab 2's.
	vim.api.nvim_set_current_tabpage(tab1)
	vim.api.nvim_set_current_win(s1.left_win)
	vim.fn.maparg("gq", "n", false, true).callback()
	H.check(s1.closed and not s2.closed, "gq closed the split it was pressed in")

	-- tab 2's split still has working keys on the shared buffer.
	vim.cmd.tabnext()
	vim.api.nvim_set_current_win(s2.left_win)
	H.eq(vim.api.nvim_get_current_buf(), bar, "tab 2 shows bar.py")
	local map = vim.fn.maparg("]f", "n", false, true)
	H.check(map.buffer == 1, "]f still mapped on the shared buffer")
	map.callback()
	H.eq(s2.index, 2, "]f moved tab 2's split on")

	-- In an ordinary window on bar.py, gq is the format operator again.
	ds.close(s2)
	vim.cmd.edit(root .. "/bar.py")
	H.eq(vim.fn.maparg("gq", "n"), "", "no leftover mapping once no split shows the buffer")

	-- The buffer's own ]f (LazyVim's treesitter "next function") keeps working
	-- beside a split and comes back after it.
	local ts = vim.fn.maparg("]f", "n", false, true)
	H.check(ts.buffer == 1 and ts.desc ~= nil, "bar.py has its own ]f: " .. tostring(ts.desc))
	H.write(root .. "/bar.py", "def a():\n    pass\n\n\ndef b():\n    pass\n")
	vim.cmd("edit!")
	vim.wait(300)
	local origin = vim.api.nvim_get_current_win()
	vim.cmd("HgDiffSplitWorkingSet")
	local s3 = H.session()
	vim.api.nvim_set_current_win(origin)
	vim.api.nvim_win_set_cursor(0, { 1, 0 })
	vim.fn.maparg("]f", "n", false, true).callback()
	vim.wait(100)
	H.eq(vim.fn.line("."), 5, "]f outside the panes runs the buffer's own map")

	-- There gq still formats, with the rest of what was typed after it.
	vim.bo.textwidth = 10
	vim.bo.formatexpr = ""
	vim.api.nvim_buf_set_lines(0, 0, 1, false, { "one two three four" })
	vim.api.nvim_win_set_cursor(0, { 1, 0 })
	vim.api.nvim_feedkeys("gqq", "mx", false)
	H.eq(vim.api.nvim_buf_get_lines(0, 0, 2, false), { "one two", "three four" }, "gqq outside the panes formats the line")
	vim.cmd("silent! undo")
	ds.close(s3)
	H.eq(vim.fn.maparg("]f", "n", false, true).desc, ts.desc, "and it's restored when the split closes")
end)
