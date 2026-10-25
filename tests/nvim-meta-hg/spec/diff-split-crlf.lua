-- A CRLF file diffs line by line against its live buffer, with or without a
-- newline at the end.
local H = dofile(os.getenv("NVIM_META_HG_LIB"))

H.run(function()
	local root = H.repo("diff")
	local ds = require("lib.diff-session")
	H.write(root .. "/noeol.txt", "a\r\nb\r\nc")
	H.sh({ "hg", "commit", "-A", "-m", "noeol", "noeol.txt" }, root)
	H.write(root .. "/noeol.txt", "a\r\nB\r\nc")

	vim.cmd.edit(root .. "/bar.py")
	vim.cmd("HgDiffSplitWorkingSet")
	local s = H.session()
	for _, file in ipairs({ "dos.txt", "noeol.txt" }) do
		local p, i = H.pair(s, file)
		ds.show_pair(s, i)
		H.eq(vim.bo[p.new_buf].fileformat, "dos", file .. ": live buffer loads as dos")
		H.eq(vim.api.nvim_buf_get_lines(p.old_buf, 0, -1, false), { "a", "b", "c" }, file .. ": old side without carriage returns")
		H.eq(vim.bo[p.old_buf].fileformat, "dos", file .. ": old side marked dos too")
		local changed = {}
		vim.api.nvim_win_call(s.left_win, function()
			for lnum = 1, 3 do
				if vim.fn.diff_hlID(lnum, 1) ~= 0 then
					table.insert(changed, lnum)
				end
			end
		end)
		H.eq(changed, { 2 }, file .. ": only line 2 differs")
	end
end)
