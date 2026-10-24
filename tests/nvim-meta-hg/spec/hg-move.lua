-- :HgMove resolves the destination like :saveas (against nvim's cwd) and
-- follows the file with the buffer.
local H = dofile(os.getenv("NVIM_META_HG_LIB"))

H.run(function()
	local root = H.repo("plain")
	vim.fn.mkdir(root .. "/sub", "p")
	H.write(root .. "/sub/s.txt", "s\n")
	H.sh({ "hg", "add", "sub/s.txt" }, root)
	H.sh({ "hg", "commit", "-m", "sub" }, root)

	vim.cmd.cd(root .. "/sub")
	vim.cmd.edit(root .. "/sub/s.txt")
	local buf = vim.api.nvim_get_current_buf()
	vim.cmd("HgMove moved.txt")
	H.check(vim.uv.fs_stat(root .. "/sub/moved.txt") ~= nil, "moved relative to cwd")
	H.check(vim.uv.fs_stat(root .. "/moved.txt") == nil, "not to the repo root")
	H.eq(vim.api.nvim_buf_get_name(buf), root .. "/sub/moved.txt", "buffer follows the file")
	H.eq(vim.bo[buf].modified, false, "buffer unmodified")
	H.check(H.sh({ "hg", "status" }, root):find("A sub/moved.txt", 1, true) ~= nil, "hg records the move")
end)
