-- Terminal commands close their own tab when they succeed, never the window
-- you moved to, and stay open when they fail.
local H = dofile(os.getenv("NVIM_META_HG_LIB"))

H.run(function()
	local root = H.repo("plain")
	H.write(root .. "/small.txt", "amend me\n")
	vim.cmd.edit(root .. "/small.txt")
	vim.cmd.split()
	local home_tab = vim.api.nvim_get_current_tabpage()

	vim.cmd("HgAmend")
	H.eq(#vim.api.nvim_list_tabpages(), 2, "amend opened a terminal tab")
	vim.api.nvim_set_current_tabpage(home_tab)
	H.check(vim.wait(15000, function()
		return #vim.api.nvim_list_tabpages() == 1
	end, 50), "terminal tab closed after success")
	H.eq(#H.wins(home_tab), 2, "the window you moved to survives")
	H.eq(vim.trim(H.sh({ "hg", "status" }, root)), "", "amend ran")

	-- A failing command leaves its output on screen.
	local bin = H.tmp .. "/bin"
	vim.fn.mkdir(bin, "p")
	H.write(bin .. "/jf", "#!/bin/sh\necho 'jf: failing on purpose'\nexit 3\n")
	vim.uv.fs_chmod(bin .. "/jf", 493)
	vim.env.PATH = bin .. ":" .. vim.env.PATH
	H.write(root .. "/small.txt", "suggest me\n")
	vim.cmd("HgSuggest -m 'two words'")
	local term_tab = vim.api.nvim_get_current_tabpage()
	local term_buf = vim.api.nvim_get_current_buf()
	vim.api.nvim_set_current_tabpage(home_tab)
	vim.wait(3000, function()
		return vim.fn.jobwait({ vim.bo[term_buf].channel }, 0)[1] ~= -1
	end, 50)
	vim.wait(300)
	H.check(vim.api.nvim_tabpage_is_valid(term_tab), "failed command's tab stays open")
	H.check(vim.api.nvim_buf_is_valid(term_buf) and H.lines(term_buf):find("failing on purpose", 1, true) ~= nil, "output visible")
	H.eq(#H.wins(home_tab), 2, "home tab untouched")
end)
