-- :HgPrev, :HgNext, :HgHistory and :HgWrite run hg in the file's repo,
-- without :! (no autowrite, no %/# expansion, failures reported).
local H = dofile(os.getenv("NVIM_META_HG_LIB"))

H.run(function()
	local other = H.repo("plain")
	local root, revs = H.repo("plain")
	H.write(root .. "/small.txt", "second\n")
	H.sh({ "hg", "commit", "-m", "second %s #1" }, root)
	vim.cmd.cd(other)
	vim.cmd.edit(root .. "/small.txt")
	local buf = vim.api.nvim_get_current_buf()

	vim.cmd("HgPrev")
	H.eq(vim.trim(H.sh({ "hg", "log", "-r", ".", "-T", "{node|short}" }, root)), revs.plain, "HgPrev moved to the parent")
	H.eq(H.lines(buf), "small", "buffer reloaded after HgPrev")
	vim.cmd("HgNext")
	H.eq(H.lines(buf), "second", "buffer reloaded after HgNext")

	vim.cmd("HgHistory")
	vim.wait(100)
	H.eq(vim.bo.filetype, "hghistory", "history opened")
	local history = H.lines(0)
	H.check(history:find("second %s #1", 1, true) and history:find("plain", 1, true), "history lists both commits: " .. history)
	vim.cmd.close()

	H.write(root .. "/new.txt", "")
	vim.cmd.edit(root .. "/new.txt")
	vim.api.nvim_buf_set_lines(0, 0, -1, false, { "new" })
	vim.cmd("HgWrite")
	H.check(H.sh({ "hg", "status" }, root):find("A new.txt", 1, true) ~= nil, "HgWrite added the file")
	H.eq(H.read(root .. "/new.txt"), "new\n", "and wrote it")
end)
