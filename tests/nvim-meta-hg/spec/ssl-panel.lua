-- Smartlog panel rendering: status annotations, hidden bookmarks, :bdelete.
local H = dofile(os.getenv("NVIM_META_HG_LIB"))

H.run(function()
	local hg = require("lib.meta-hg")
	local root = H.repo("diff")
	local ns = vim.api.nvim_create_namespace("hg_ssl")
	local function refresh()
		hg.refresh_ssl()
		vim.wait(5000, function()
			return not hg.ssl_state.refreshing
		end, 20)
		vim.wait(1000, function()
			return #vim.api.nvim_buf_get_extmarks(hg.ssl_state.bufnr, ns, 0, -1, {}) > 0
		end, 20)
		vim.wait(200)
	end

	vim.cmd.edit(root .. "/bar.py")
	vim.cmd("HgSsl")
	refresh()
	refresh()
	refresh()
	H.eq(#vim.api.nvim_buf_get_extmarks(hg.ssl_state.bufnr, ns, 0, -1, {}), 1, "one status block after three refreshes")

	-- The current commit carries only a hidden bookmark (sl goto fbsource/stable).
	H.fake_hg()
	for _, sub in ipairs({ "sl", "ssl" }) do
		H.rewrite(root, sub, [[s|^@  \([0-9a-f]*\)  \(.* ago\)  test|@  \1  \2  remote/fbsource/stable|]])
	end
	H.notifications = {}
	vim.v.errmsg = ""
	refresh()
	local lines = vim.api.nvim_buf_get_lines(hg.ssl_state.bufnr, 0, -1, false)
	H.check(vim.iter(lines):any(function(l)
		return l:find("^@  ") ~= nil
	end), "current commit stays listed: " .. vim.inspect(lines))
	H.eq(vim.v.errmsg, "", "no errors")
	H.eq(#vim.api.nvim_buf_get_extmarks(hg.ssl_state.bufnr, ns, 0, -1, {}), 1, "status block still drawn")
end)
