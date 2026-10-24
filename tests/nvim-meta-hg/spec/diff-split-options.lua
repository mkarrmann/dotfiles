-- Diff panes set their window options locally and leave your buffers' options alone.
local H = dofile(os.getenv("NVIM_META_HG_LIB"))

H.run(function()
	local root = H.repo("diff")
	local ds = require("lib.diff-session")
	vim.cmd.edit(root .. "/bar.py")
	local bar = vim.api.nvim_get_current_buf()
	local before = { fen = vim.go.foldenable, rnu = vim.go.relativenumber, swap = vim.bo[bar].swapfile }
	H.eq(before.swap, true, "bar.py has a swapfile to begin with")

	vim.cmd("HgDiffSplitWorkingSet")
	local s = H.session()
	ds.show_pair(s, 2)
	ds.show_pair(s, 1)
	H.check(not vim.wo[s.left_win].foldenable and vim.wo[s.left_win].relativenumber, "diff panes: nofoldenable, relativenumber")
	H.eq(vim.bo[bar].swapfile, true, "diffing an open file keeps its swapfile")
	H.eq({ vim.go.foldenable, vim.go.relativenumber }, { before.fen, before.rnu }, "global option values unchanged")
	-- Cycling the live file out of its pane doesn't leave it remembering
	-- nofoldenable for the next window that shows it.
	ds.show_pair(s, 2)
	ds.close(s)
	vim.cmd("vnew")
	vim.cmd.buffer(bar)
	H.eq(vim.wo.foldenable, before.fen, "a new window on the diffed file folds as before")
	vim.cmd("tabnew")
	H.eq({ vim.wo.foldenable, vim.wo.relativenumber }, { before.fen, before.rnu }, "a new tab's window doesn't inherit diff options")
end)
