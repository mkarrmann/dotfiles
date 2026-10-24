-- Only lib.meta-hg's hg integration runs: meta.nvim's bundled meta.hg must not
-- register a second set of sign autocmds on the shared namespace.
local H = dofile(os.getenv("NVIM_META_HG_LIB"))

H.run(function()
	H.check(package.loaded["meta.hg"] == require("lib.meta-hg"), "meta.hg resolves to lib.meta-hg")
	local sign_autocmds = vim.tbl_filter(function(a)
		return a.desc == "Update hg column signs for any buffer"
	end, vim.api.nvim_get_autocmds({ event = "BufEnter" }))
	H.eq(#sign_autocmds, 1, "one sign BufEnter autocmd")

	-- cwd is outside the repo; signs for a file inside it must still settle.
	local root = H.repo("plain")
	H.write(root .. "/lines.txt", H.read(root .. "/lines.txt"):gsub("line 5\n", "five\n"):gsub("line 9\n", "nine\n"))
	vim.cmd.edit(root .. "/lines.txt")
	local ns = vim.api.nvim_create_namespace("hg_signcolumn")
	local counts = {}
	for _ = 1, 6 do
		vim.api.nvim_exec_autocmds("BufEnter", { buffer = 0 })
		vim.wait(800)
		table.insert(counts, #vim.api.nvim_buf_get_extmarks(0, ns, 0, -1, {}))
	end
	-- A modified line gets a delete mark above it and an add mark on it.
	H.eq(counts, { 4, 4, 4, 4, 4, 4 }, "sign count stays at 4 across refreshes")
end)
