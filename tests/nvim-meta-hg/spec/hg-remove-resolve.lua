-- :HgRemove and :HgResolve keep the buffer when hg fails, and :HgResolve
-- confirms before marking a file that still has conflict markers.
local H = dofile(os.getenv("NVIM_META_HG_LIB"))

H.run(function()
	local root = H.repo("plain")

	-- hg remove refuses a modified file.
	H.write(root .. "/small.txt", "modified on disk\n")
	vim.cmd.edit(root .. "/small.txt")
	local buf = vim.api.nvim_get_current_buf()
	vim.cmd("HgRemove")
	H.check(vim.api.nvim_buf_is_valid(buf), "buffer kept when hg remove fails")
	H.check(vim.iter(H.notifications):any(function(n)
		return n.level == vim.log.levels.ERROR and tostring(n.msg):find("remove")
	end), "failure reported")

	vim.cmd.edit(root .. "/lines.txt")
	buf = vim.api.nvim_get_current_buf()
	vim.cmd("HgRemove")
	H.check(not vim.api.nvim_buf_is_valid(buf), "buffer closed after a successful remove")
	H.check(vim.uv.fs_stat(root .. "/lines.txt") == nil, "file removed")

	-- Conflict markers left in the buffer: declining keeps it unresolved.
	H.write(root .. "/conflict.txt", "<<<<<<< ours\na\n=======\nb\n>>>>>>> theirs\n")
	vim.cmd.edit(root .. "/conflict.txt")
	buf = vim.api.nvim_get_current_buf()
	local asked
	vim.ui.select = function(items, opts, on_choice)
		asked = opts.prompt
		on_choice("No")
	end
	vim.cmd("HgResolve")
	H.check(asked ~= nil and asked:find("conflict markers") ~= nil, "asked before marking: " .. tostring(asked))
	H.check(vim.api.nvim_buf_is_valid(buf), "buffer kept when declined")
end)
