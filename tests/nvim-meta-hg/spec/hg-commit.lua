-- :HgCommit takes the message from the saved editor buffer, drops HG: lines,
-- and aborts when the template is left untouched.
local H = dofile(os.getenv("NVIM_META_HG_LIB"))

local function head_desc(root)
	return H.sh({ "hg", "log", "-r", ".", "-T", "{desc}" }, root)
end

local function wait_for(pred)
	return vim.wait(10000, pred, 50)
end

H.run(function()
	local root, revs = H.repo("plain")
	H.write(root .. "/small.txt", "changed\n")
	vim.cmd.edit(root .. "/small.txt")

	-- Closing the untouched template aborts.
	vim.cmd("HgCommit")
	H.eq(vim.bo.filetype, "hgcommit", "editor opened")
	vim.cmd("close")
	H.check(wait_for(function()
		return vim.iter(H.notifications):any(function(n)
			return n.msg == "Commit aborted"
		end)
	end), "untouched template aborts")
	H.eq(H.sh({ "hg", "log", "-r", ".", "-T", "{node|short}" }, root), revs.plain, "no commit made")

	-- A written message commits without the template's HG: lines.
	vim.cmd("HgCommit")
	local buf = vim.api.nvim_get_current_buf()
	local lines = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
	lines[1] = "Real title"
	vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
	vim.cmd("write")
	vim.cmd("close")
	H.check(wait_for(function()
		return head_desc(root):find("Real title", 1, true) ~= nil
	end), "commit made with the written title")
	local desc = head_desc(root)
	H.check(not desc:find("HG:", 1, true) and not desc:find("SL:", 1, true), "no template comment lines in message: " .. vim.inspect(desc))
end)
