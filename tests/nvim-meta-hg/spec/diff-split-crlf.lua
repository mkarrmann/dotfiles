-- A CRLF file diffs line by line against its live buffer.
local H = dofile(os.getenv("NVIM_META_HG_LIB"))

H.run(function()
	local root = H.repo("diff")
	local ds = require("lib.diff-session")
	vim.cmd.edit(root .. "/bar.py")
	vim.cmd("HgDiffSplitWorkingSet")
	local s = H.session()
	local p, i = H.pair(s, "dos.txt")
	ds.show_pair(s, i)
	H.eq(vim.bo[p.new_buf].fileformat, "dos", "live buffer loads as dos")
	H.eq(vim.api.nvim_buf_get_lines(p.old_buf, 0, -1, false), { "a", "b", "c" }, "old side without carriage returns")
	H.eq(vim.bo[p.old_buf].fileformat, "dos", "old side marked dos too")
	local changed = {}
	vim.api.nvim_win_call(s.left_win, function()
		for lnum = 1, 3 do
			if vim.fn.diff_hlID(lnum, 1) ~= 0 then
				table.insert(changed, lnum)
			end
		end
	end)
	H.eq(changed, { 2 }, "only line 2 differs")
end)
