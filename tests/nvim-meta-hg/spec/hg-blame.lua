-- Closing :HgBlame's windows restores the blamed buffer's window options:
-- after the last of several blame windows, after showing another buffer in
-- the window meanwhile, and per buffer when two were blamed in one window.
local H = dofile(os.getenv("NVIM_META_HG_LIB"))

H.run(function()
	local root = H.repo("plain")
	vim.cmd.edit(root .. "/lines.txt")
	local lines_buf = vim.api.nvim_get_current_buf()
	local win = vim.api.nvim_get_current_win()
	local function opts()
		return { vim.wo[win].wrap, vim.wo[win].foldenable, vim.wo[win].scrollbind }
	end
	local function set_opts(wrap)
		vim.wo[win][0].wrap = wrap
		vim.wo[win][0].foldenable = true
		vim.wo[win][0].scrollbind = false
	end
	---Runs :HgBlame in `win` and returns the blame window.
	local function blame()
		vim.api.nvim_set_current_win(win)
		vim.cmd("HgBlame")
		local blame_win = vim.api.nvim_get_current_win()
		vim.api.nvim_set_current_win(win)
		return blame_win
	end
	local function close(w)
		vim.api.nvim_win_close(w, true)
		vim.wait(100)
	end

	set_opts(true)
	local first = blame()
	H.eq(vim.bo[vim.api.nvim_win_get_buf(first)].filetype, "hgblame", "blame opened")
	local second = blame()
	close(first)
	close(second)
	H.eq(opts(), { true, true, false }, "options restored after the last blame window")

	-- Closed while another buffer is in the window: restored when it's back.
	local first_blame = blame()
	vim.cmd.edit(root .. "/small.txt")
	local small_buf = vim.api.nvim_get_current_buf()
	close(first_blame)
	vim.api.nvim_win_set_buf(win, lines_buf)
	H.eq(opts(), { true, true, false }, "options restored when the blamed buffer comes back")

	-- Two buffers blamed in one window each get their own options back.
	local lines_blame = blame()
	vim.api.nvim_win_set_buf(win, small_buf)
	set_opts(false)
	local small_blame = blame()
	close(lines_blame)
	close(small_blame)
	H.eq(opts(), { false, true, false }, "small.txt's own options restored")
	vim.api.nvim_win_set_buf(win, lines_buf)
	H.eq(opts(), { true, true, false }, "lines.txt's own options restored")
end)
