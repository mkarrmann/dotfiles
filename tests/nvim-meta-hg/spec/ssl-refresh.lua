-- Smartlog refreshes that finish late never overwrite a newer one, a
-- refresh in flight doesn't lock out actions, and only one command that
-- changes the repo runs at a time.
local H = dofile(os.getenv("NVIM_META_HG_LIB"))

H.run(function()
	H.fake_hg()
	local hg = require("lib.meta-hg")
	local repo1, revs1 = H.repo("plain")
	local repo2, revs2 = H.repo("diff")
	local function panel()
		return H.lines(hg.ssl_state.bufnr)
	end
	-- the smartlog prints 10-character hashes
	for _, revs in ipairs({ revs1, revs2 }) do
		for name, hash in pairs(revs) do
			revs[name] = hash:sub(1, 10)
		end
	end

	-- Retarget while repo1's refresh is still running.
	H.slow(repo1, "ssl", 2)
	vim.cmd.edit(repo1 .. "/small.txt")
	vim.cmd("HgSsl")
	vim.wait(500)
	vim.cmd.edit(repo2 .. "/bar.py")
	vim.cmd("HgSsl")
	H.check(vim.wait(5000, function()
		return panel():find(revs2.descendant, 1, true) ~= nil
	end, 50), "panel shows repo2 after retargeting")
	vim.wait(2500)
	H.check(panel():find(revs2.descendant, 1, true) and not panel():find(revs1.plain, 1, true), "repo1's late refresh didn't overwrite repo2's panel")

	-- A slow refresh started before a commit finishes after the refresh that shows it.
	vim.cmd.edit(repo1 .. "/small.txt")
	vim.cmd("HgSsl")
	vim.wait(3000, function()
		return panel():find(revs1.plain, 1, true) ~= nil and not hg.ssl_state.refreshing
	end, 50)
	H.slow(repo1, "ssl", 2)
	hg.refresh_ssl()
	vim.wait(300)
	H.write(repo1 .. "/small.txt", "next\n")
	H.sh({ "hg", "commit", "-m", "newer" }, repo1)
	local newer = vim.trim(H.sh({ "hg", "log", "-r", ".", "-T", "{node|short}" }, repo1)):sub(1, 10)
	hg.refresh_ssl()
	H.check(vim.wait(3000, function()
		return panel():find(newer, 1, true) ~= nil
	end, 50), "a refresh started while another runs still lands")
	vim.wait(2500)
	H.check(panel():find(newer, 1, true) ~= nil, "the older slow refresh didn't overwrite it")

	-- Actions that change the repo run during a refresh, but not during another such action.
	H.slow(repo1, "ssl", 2)
	hg.refresh_ssl()
	vim.wait(200)
	for i, l in ipairs(vim.api.nvim_buf_get_lines(hg.ssl_state.bufnr, 0, -1, false)) do
		if l:find(revs1.plain, 1, true) then
			vim.api.nvim_win_set_cursor(0, { i, 0 })
		end
	end
	H.slow(repo1, "hide", 1)
	local hide = vim.fn.maparg("<CR>x", "n", false, true).callback
	H.notifications = {}
	hide()
	hide()
	local function notified(pattern)
		return vim.iter(H.notifications):any(function(n)
			return tostring(n.msg):find(pattern) ~= nil
		end)
	end
	H.check(not notified("SSL is updating"), "hide wasn't refused because a refresh was running")
	H.check(notified("still running"), "a second hide while the first runs is refused")
	H.check(vim.wait(5000, function()
		return notified("Commit has been hidden")
	end, 50), "hide ran")

	-- hg ssl doesn't wait for hg sl, and a late hg sl doesn't replace its output.
	vim.cmd.edit(repo2 .. "/bar.py")
	vim.cmd("HgSsl")
	vim.wait(5000, function()
		return not hg.ssl_state.refreshing
	end, 20)
	H.rewrite(repo2, "ssl", "s/  test$/  test  [ssl]/")
	H.slow(repo2, "sl", 2)
	local started = vim.uv.now()
	hg.refresh_ssl()
	vim.wait(5000, function()
		return panel():find("[ssl]", 1, true) ~= nil
	end, 20)
	local elapsed = vim.uv.now() - started
	H.check(elapsed < 1800, "full smartlog shown without waiting for hg sl (" .. elapsed .. " ms)")
	vim.wait(2500)
	H.check(panel():find("[ssl]", 1, true) ~= nil, "late hg sl output didn't replace it")

	-- If hg ssl fails, hg sl's output stays up.
	H.fail(repo2, "ssl", "abort: simulated ssl failure")
	hg.refresh_ssl()
	vim.wait(3000, function()
		return not hg.ssl_state.refreshing
	end, 20)
	vim.wait(500)
	H.check(panel():find(revs2.descendant, 1, true) ~= nil and not panel():find("[ssl]", 1, true), "hg sl output shown although hg ssl failed")
	vim.uv.fs_unlink(vim.uv.fs_realpath(repo2) .. "/.hg/fail-ssl")
	hg.refresh_ssl()
	vim.wait(5000, function()
		return not hg.ssl_state.refreshing
	end, 20)

	-- Re-entering the panel right after a refresh doesn't start another.
	local refreshes = hg.ssl_state.refresh_gen
	vim.cmd.wincmd("p")
	vim.cmd.wincmd("p")
	vim.api.nvim_exec_autocmds("BufEnter", { buffer = hg.ssl_state.bufnr })
	H.eq(hg.ssl_state.refresh_gen, refreshes, "no refresh on BufEnter right after one finished")

	-- Retargeting doesn't leave the old repo's commits up to act on in the new one.
	H.slow(repo1, "sl", 2)
	H.slow(repo1, "ssl", 2)
	vim.cmd.wincmd("p")
	vim.cmd.edit(repo1 .. "/small.txt")
	vim.cmd("HgSsl")
	H.check(not panel():find(revs2.descendant, 1, true), "repo2's commits are gone as soon as the panel retargets to repo1")
	H.notifications = {}
	vim.fn.maparg("<CR>x", "n", false, true).callback()
	H.check(notified("No commit under cursor"), "an action right after retargeting finds no commit")
	H.check(vim.wait(5000, function()
		return notified("ssl updated")
	end, 50), "repo1's smartlog arrives (its commits were all hidden above)")
end)
