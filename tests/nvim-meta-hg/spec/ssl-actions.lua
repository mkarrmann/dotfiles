-- Smartlog actions report their failures and only act on commit lines.
local H = dofile(os.getenv("NVIM_META_HG_LIB"))

local function notified(pattern)
	return vim.iter(H.notifications):any(function(n)
		return tostring(n.msg):find(pattern) ~= nil
	end)
end

H.run(function()
	local hg = require("lib.meta-hg")
	local root, revs = H.repo("diff")
	hg.ssl_state.repo_root = root

	-- Rebasing onto a revision that doesn't exist.
	vim.ui.select = function(_, _, on_choice)
		on_choice("custom")
	end
	vim.ui.input = function(_, on_confirm)
		on_confirm("doesnotexist")
	end
	hg.ssl_commands.rebase.action({ hash = revs.target, is_current = false }, nil)
	H.check(vim.wait(10000, function()
		return notified("Rebase failed")
	end, 50), "rebase onto a bad revision reports the failure")
	H.check(notified("Rebase failed.*unknown revision"), "and includes hg's error")

	-- Message lines that happen to contain "o  " are not commits.
	H.write(root .. "/bar.py", "x\n")
	H.sh({ "hg", "commit", "-m", "second  fox  commit" }, root)
	H.write(root .. "/bar.py", "y\n")
	H.sh({ "hg", "commit", "-m", "jo  deadbeef00" }, root)
	vim.cmd.edit(root .. "/bar.py")
	vim.cmd("HgSsl")
	vim.wait(5000, function()
		return not hg.ssl_state.refreshing
	end, 20)
	local lines = vim.api.nvim_buf_get_lines(0, 0, -1, false)
	local labelled = {}
	for _, lnum in pairs(hg.ssl_state.hop_map) do
		labelled[#labelled + 1] = lines[lnum]
	end
	H.check(#labelled == 7 and vim.iter(labelled):all(function(l)
		return l:find("^[%s\128-\255]*[@ox]  %x+  ") ~= nil
	end), "only commit lines are parsed as commits: " .. vim.inspect(labelled))
end)
