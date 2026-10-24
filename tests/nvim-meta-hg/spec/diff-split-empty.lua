-- HgChanges `d` on a file that's no longer changed opens nothing.
local H = dofile(os.getenv("NVIM_META_HG_LIB"))

H.run(function()
	local root = H.repo("diff")
	vim.cmd.edit(root .. "/bar.py")
	vim.cmd("HgChanges")
	for i, l in ipairs(vim.api.nvim_buf_get_lines(0, 0, -1, false)) do
		if l:match("bar%.py$") then
			vim.api.nvim_win_set_cursor(0, { i, 0 })
		end
	end
	H.sh({ "hg", "revert", "--no-backup", "bar.py" }, root)
	local wins = #H.wins()
	local ok, err = pcall(vim.fn.maparg("d", "n", false, true).callback)
	H.check(ok, "no error: " .. tostring(err))
	H.eq(#H.wins(), wins, "no empty diff panes left behind")
	H.eq(H.session(), nil, "no diff session")
end)
