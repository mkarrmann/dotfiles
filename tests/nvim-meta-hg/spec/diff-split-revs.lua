-- A working-copy diff split pins "." to a hash when it opens: files fetched
-- after a commit still come from the base it was opened against.
local H = dofile(os.getenv("NVIM_META_HG_LIB"))

H.run(function()
	local root, revs = H.repo("diff")
	local ds = require("lib.diff-session")
	vim.cmd.edit(root .. "/bar.py")
	vim.cmd("HgDiffSplitWorkingSet")
	local s = H.session()
	-- commit dos.txt's change before its pair is ever shown
	vim.wait(2000, function()
		return not vim.iter(s.cache):any(function(_, c)
			return next(c.pending) ~= nil
		end)
	end, 20)
	for _, c in pairs(s.cache) do
		c.contents["dos.txt"] = nil
	end
	H.sh({ "hg", "commit", "-m", "dos", "dos.txt" }, root)
	local p, i = H.pair(s, "dos.txt")
	ds.show_pair(s, i)
	H.eq(H.lines(p.old_buf), "a\nb\nc", "old side still from the original base")
	local wb = vim.w[s.right_win].custom_winbar_text or ""
	H.check(wb:find(". " .. revs.descendant:sub(1, 10), 1, true) ~= nil, "winbar names the base: " .. wb)
end)
