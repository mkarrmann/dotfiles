-- :HgStatus: X reverts the file (after confirming), ? shows help, and the
-- command can be run again while its window is open.
local H = dofile(os.getenv("NVIM_META_HG_LIB"))

H.run(function()
	local root, revs = H.repo("diff")
	vim.cmd.edit(root .. "/bar.py")
	vim.cmd("HgStatus")
	local buf = vim.api.nvim_get_current_buf()
	for i, l in ipairs(vim.api.nvim_buf_get_lines(buf, 0, -1, false)) do
		if l:match("bar%.py$") then
			vim.api.nvim_win_set_cursor(0, { i, 0 })
		end
	end
	local asked
	vim.ui.select = function(_, opts, on_choice)
		asked = opts.prompt
		on_choice("Yes")
	end
	local ok, err = pcall(vim.fn.maparg("X", "n", false, true).callback)
	H.check(ok, "X runs: " .. tostring(err))
	H.check(asked ~= nil, "X asks first")
	H.eq(H.read(root .. "/bar.py"), 'print("bar2")\n', "X reverted bar.py")
	H.eq(vim.trim(H.sh({ "hg", "log", "-r", ".", "-T", "{node|short}" }, root)), revs.descendant, "working copy parent unchanged")
	H.check(not H.lines(buf):find("bar.py", 1, true), "status list refreshed")

	ok, err = pcall(vim.fn.maparg("?", "n", false, true).callback)
	H.check(ok, "? shows help: " .. tostring(err))

	ok, err = pcall(vim.cmd, "HgStatus")
	H.check(ok, "a second :HgStatus works: " .. tostring(err))
end)
