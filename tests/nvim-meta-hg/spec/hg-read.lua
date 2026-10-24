-- :HgRead restores the buffer from its own repo whatever nvim's cwd is.
local H = dofile(os.getenv("NVIM_META_HG_LIB"))

H.run(function()
	local other = H.repo("plain")
	local root = H.repo("plain")
	vim.cmd.cd(other)
	vim.cmd.edit(root .. "/small.txt")
	vim.api.nvim_buf_set_lines(0, 0, -1, false, { "edited" })
	vim.cmd("HgRead")
	H.eq(H.lines(0), "small", "buffer restored from the file's repo")
end)
