-- :HgDiffSplit opens the shared diff split for the current file against .^,
-- beside your window, which it leaves alone.
local H = dofile(os.getenv("NVIM_META_HG_LIB"))

H.run(function()
	local root, revs = H.repo("diff")
	local ds = require("lib.diff-session")
	vim.cmd.edit(root .. "/bar.py")
	local origin = vim.api.nvim_get_current_win()
	local bufs_before = #vim.fn.getbufinfo({ buflisted = 1 })
	vim.cmd("HgDiffSplit")
	local s = H.session()
	H.check(s ~= nil, "diff split session opened")
	H.eq(s and H.files(s), "bar.py", "just the current file")
	local p = H.pair(s, "bar.py")
	H.check(p.is_live and H.lines(p.new_buf) == 'print("bar3")', "new side is the live file")
	H.eq(H.lines(p.old_buf), 'print("bar")', "old side from .^")
	H.eq(vim.wo[origin].diff, false, "your window isn't put in diff mode")
	local wb = vim.w[s.right_win].custom_winbar_text or ""
	H.check(wb:find(".^ " .. revs.many, 1, true) ~= nil, "winbar names the base: " .. wb)
	ds.close(s)
	H.eq(vim.api.nvim_get_current_win(), origin, "closing returns to your window")
	H.eq(#vim.fn.getbufinfo({ buflisted = 1 }), bufs_before, "no buffers left behind")

	-- Opening a second split in the tab replaces the first, and returns to
	-- the window it was opened from, not the first one's.
	vim.cmd.edit(root .. "/bar.py")
	vim.cmd("HgDiffSplit")
	vim.cmd.vsplit(root .. "/dos.txt")
	local second = vim.api.nvim_get_current_win()
	vim.cmd("HgDiffSplit")
	s = H.session()
	H.eq(H.files(s), "dos.txt", "second split replaced the first")
	ds.close(s)
	H.eq(vim.api.nvim_get_current_win(), second, "closing it returns to where it was opened")
	vim.cmd.only()

	-- A file renamed in the current commit diffs against its old name.
	H.sh({ "hg", "mv", "foo.ts", "renamed.ts" }, root)
	H.sh({ "hg", "commit", "-m", "rename", "foo.ts", "renamed.ts" }, root)
	vim.cmd.edit(root .. "/renamed.ts")
	vim.cmd("HgDiffSplit")
	s = H.session()
	H.eq(s and H.files(s), "renamed.ts<foo.ts", "renamed file pairs with its source")
end)
