-- :HgBrowse reports its errors instead of throwing, and yanks to the clipboard.
local H = dofile(os.getenv("NVIM_META_HG_LIB"))

H.run(function()
	local root = H.repo("plain")
	vim.cmd.edit(root .. "/small.txt")
	local ok, err = pcall(vim.cmd, "HgBrowseYank")
	H.check(ok, "no error without .hg/reponame: " .. tostring(err))
	H.check(vim.iter(H.notifications):any(function(n)
		return tostring(n.msg):find("repo name") and n.level == vim.log.levels.ERROR
	end), "reports the missing repo name")

	H.write(root .. "/.hg/reponame", "fbsource\n")
	vim.o.clipboard = ""
	vim.fn.setreg('"', "")
	vim.cmd("HgBrowseYank")
	H.eq(vim.fn.getreg('"'), "https://www.internalfb.com/code/fbsource/small.txt", "without a clipboard, yanks to the unnamed register")
end)
