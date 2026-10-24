-- The full config starts, reaches VeryLazy and opens a repo file without errors.
local H = dofile(os.getenv("NVIM_META_HG_LIB"))

H.run(function()
	H.eq(vim.v.errmsg, "", "no startup errors")
	local root = H.repo("plain")
	vim.cmd.edit(root .. "/lines.txt")
	vim.wait(1500)
	H.eq(vim.v.errmsg, "", "no errors opening a repo file")
	for _, cmd in ipairs({ "HgSsl", "HgDiffSplit", "HgDiffSplitWorkingSet", "HgBlame", "HgChanges", "SlPull" }) do
		H.eq(vim.fn.exists(":" .. cmd), 2, ":" .. cmd .. " defined")
	end
	H.check(vim.fn.maparg("<leader>hd", "n") ~= "", "<leader>hd mapped")
end)
