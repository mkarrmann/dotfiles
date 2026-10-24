-- :HgBlame twice, then closing both blame windows, restores window options.
local H = dofile(os.getenv("NVIM_META_HG_LIB"))

H.run(function()
	local root = H.repo("plain")
	vim.cmd.edit(root .. "/lines.txt")
	local win = vim.api.nvim_get_current_win()
	vim.wo[win].wrap = true
	vim.wo[win].foldenable = true
	vim.wo[win].scrollbind = false
	vim.cmd("HgBlame")
	H.eq(vim.bo.filetype, "hgblame", "blame opened")
	vim.api.nvim_set_current_win(win)
	vim.cmd("HgBlame")
	for _ = 1, 2 do
		for _, w in ipairs(vim.api.nvim_tabpage_list_wins(0)) do
			if vim.bo[vim.api.nvim_win_get_buf(w)].filetype == "hgblame" then
				vim.api.nvim_win_close(w, true)
				vim.wait(100)
				break
			end
		end
	end
	H.eq({ vim.wo[win].wrap, vim.wo[win].foldenable, vim.wo[win].scrollbind }, { true, true, false }, "options restored")
end)
