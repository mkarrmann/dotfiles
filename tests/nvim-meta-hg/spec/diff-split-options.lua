-- Diff panes set their window options locally.
local H = dofile(os.getenv("NVIM_META_HG_LIB"))

H.run(function()
	local root = H.repo("diff")
	local ds = require("lib.diff-session")
	vim.cmd.edit(root .. "/bar.py")
	local before = { fen = vim.go.foldenable, rnu = vim.go.relativenumber }

	vim.cmd("HgDiffSplitWorkingSet")
	local s = H.session()
	ds.show_pair(s, 2)
	ds.show_pair(s, 1)
	H.check(not vim.wo[s.left_win].foldenable and vim.wo[s.left_win].relativenumber, "diff panes: nofoldenable, relativenumber")
	H.eq({ vim.go.foldenable, vim.go.relativenumber }, { before.fen, before.rnu }, "global option values unchanged")
	vim.cmd("tabnew")
	H.eq({ vim.wo.foldenable, vim.wo.relativenumber }, { before.fen, before.rnu }, "a new tab's window doesn't inherit diff options")
end)
