-- Smartlog actions report their failures.
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
end)
