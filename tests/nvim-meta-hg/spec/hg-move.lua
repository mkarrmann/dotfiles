-- :HgMove resolves a relative destination against the repo root (nvim's cwd
-- is a workspace holding several repos), completes the same way, and
-- follows the file with the buffer.
local H = dofile(os.getenv("NVIM_META_HG_LIB"))

H.run(function()
	local root = H.repo("plain")
	vim.fn.mkdir(root .. "/sub", "p")
	H.write(root .. "/sub/s.txt", "s\n")
	H.sh({ "hg", "add", "sub/s.txt" }, root)
	H.sh({ "hg", "commit", "-m", "sub" }, root)

	vim.cmd.cd(vim.fs.dirname(root))
	vim.cmd.edit(root .. "/sub/s.txt")
	local buf = vim.api.nvim_get_current_buf()
	H.eq(vim.fn.getcompletion("HgMove su", "cmdline"), { "sub/" }, "completes relative to the repo root")
	vim.cmd("HgMove sub/moved.txt")
	H.check(vim.uv.fs_stat(root .. "/sub/moved.txt") ~= nil, "moved relative to the repo root")
	H.eq(vim.api.nvim_buf_get_name(buf), root .. "/sub/moved.txt", "buffer follows the file")
	H.eq(vim.bo[buf].modified, false, "buffer unmodified")
	H.check(H.sh({ "hg", "status" }, root):find("A sub/moved.txt", 1, true) ~= nil, "hg records the move")
end)
