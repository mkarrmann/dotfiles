-- The three entry points into the shared diff split: smartlog <CR>d,
-- :HgDiffSplitWorkingSet and the HgChanges picker's `d`.
local H = dofile(os.getenv("NVIM_META_HG_LIB"))

H.run(function()
	local root, revs = H.repo("diff")
	local hg = require("lib.meta-hg")
	local ds = require("lib.diff-session")

	hg.ssl_state.repo_root = root
	local origin = vim.api.nvim_get_current_win()
	hg.ssl_commands.diff_split.action({ hash = revs.target, is_current = false, diff = "D123" })
	local s = H.session()
	H.check(s ~= nil, "ssl: session opened")
	H.eq(H.files(s), "a.py,foo.ts,foo.tsx,new_name.py<old_name.py,runme", "ssl: files")
	H.check(H.lines(s.pairs[1].new_buf) == "a = 1" and H.lines(s.pairs[1].old_buf) == "", "ssl: a.py added")
	H.eq(vim.api.nvim_get_current_win(), s.left_win, "ssl: focus on left pane")
	H.check(s.commit.diff == "D123", "ssl: target carried")
	local wb = vim.w[s.left_win].custom_winbar_text or ""
	H.check(wb:find(revs.target, 1, true) and wb:find("[1/5]", 1, true), "ssl: winbar " .. wb)
	ds.close(s)
	H.check(H.session() == nil and vim.api.nvim_get_current_win() == origin, "ssl: close returns to origin window")

	vim.cmd.edit(root .. "/bar.py")
	local origin2 = vim.api.nvim_get_current_win()
	vim.cmd("HgDiffSplitWorkingSet")
	s = H.session()
	H.eq(s and H.files(s), "bar.py,dos.txt,wc_new.py<wc_old.py", "ws: files")
	local bar = H.pair(s, "bar.py")
	H.check(bar.is_live and H.lines(bar.new_buf) == 'print("bar3")', "ws: bar.py new side is the live file")
	H.eq(H.lines(bar.old_buf), 'print("bar2")', "ws: bar.py old side from .")
	ds.close(s)
	H.eq(vim.api.nvim_get_current_win(), origin2, "ws: close returns to origin window")

	hg.ssl_commands.changes.action()
	local picker = vim.api.nvim_get_current_win()
	for i, l in ipairs(vim.api.nvim_buf_get_lines(0, 0, -1, false)) do
		if l:match("bar%.py$") then
			vim.api.nvim_win_set_cursor(0, { i, 0 })
		end
	end
	vim.fn.maparg("d", "n", false, true).callback()
	s = H.session()
	H.eq(s and H.files(s), "bar.py", "changes: files")
	H.check(s and H.pair(s, "bar.py").is_live, "changes: bar.py live")
	ds.close(s)
	H.eq(vim.api.nvim_get_current_win(), picker, "changes: close returns to picker")
end)
