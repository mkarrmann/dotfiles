-- Renamed and copied files diff against their source.
local H = dofile(os.getenv("NVIM_META_HG_LIB"))

H.run(function()
	local root, revs = H.repo("diff")
	local hg = require("lib.meta-hg")
	local ds = require("lib.diff-session")
	hg.ssl_state.repo_root = root

	hg.ssl_commands.diff_split.action({ hash = revs.target, is_current = false })
	local s = H.session()
	H.eq(H.files(s), "a.py,foo.ts,foo.tsx,new_name.py<old_name.py,runme", "commit: files")
	local p, i = H.pair(s, "new_name.py")
	ds.show_pair(s, i)
	H.check(H.lines(p.old_buf) == "def old():\n    return 1" and H.lines(p.new_buf) == "def old():\n    return 2", "commit: rename diffs old vs new content")
	H.check(vim.bo[p.old_buf].filetype == "python" and vim.api.nvim_buf_get_name(p.old_buf):find("/old_name.py#", 1, true), "commit: old buffer named/typed after old path")
	local wb = vim.w[s.right_win].custom_winbar_text or ""
	H.check(wb:find("old_name.py", 1, true) ~= nil, "commit: right winbar shows old path: " .. wb)
	ds.close(s)

	hg.ssl_commands.diff_split.action({ hash = revs.edge, is_current = false })
	s = H.session()
	p, i = H.pair(s, "a_copy.py")
	H.check(p and p.old_file == "a.py" and H.pair(s, "a.py") == nil, "copy: a_copy.py<a.py, a.py not listed: " .. H.files(s))
	ds.show_pair(s, i)
	H.check(H.lines(p.old_buf) == "a = 1" and H.lines(p.new_buf) == "a = 1", "copy: identical content both sides")
	ds.close(s)

	vim.cmd.edit(root .. "/bar.py")
	vim.cmd("HgDiffSplitWorkingSet")
	s = H.session()
	p, i = H.pair(s, "wc_new.py")
	ds.show_pair(s, i)
	H.check(p.is_live and H.lines(p.new_buf) == "x = 2" and H.lines(p.old_buf) == "x = 1", "ws: live new vs old source")
	ds.close(s)

	hg.ssl_commands.changes.action()
	for n, l in ipairs(vim.api.nvim_buf_get_lines(0, 0, -1, false)) do
		if l:match("^A wc_new%.py$") then
			vim.api.nvim_win_set_cursor(0, { n, 0 })
		end
	end
	vim.fn.maparg("d", "n", false, true).callback()
	s = H.session()
	H.eq(s and H.files(s), "wc_new.py<wc_old.py", "changes: files")
	H.check(s and H.lines(s.pairs[1].old_buf) == "x = 1", "changes: old side from source")
end)
