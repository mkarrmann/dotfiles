-- Inline comments look up the diff's latest version every time, and
-- :HgPublishDrafts publishes on the diff the comments were made on.
-- jf is faked: it logs each call's variables and answers with canned JSON.
local H = dofile(os.getenv("NVIM_META_HG_LIB"))

H.run(function()
	local log = H.tmp .. "/jf.log"
	local bin = H.tmp .. "/bin"
	vim.fn.mkdir(bin, "p")
	H.write(bin .. "/jf", table.concat({
		"#!/bin/sh",
		'printf "%s\\n" "$5" >> ' .. log,
		'case "$3" in',
		"*GetVersionID*) echo '{\"phabricator_diff_query\":[{\"results\":{\"nodes\":[{\"phabricator_versions\":{\"nodes\":[{\"id\":\"v\"}]}}]}}]}' ;;",
		"*CreateInlineDraft*) echo '{\"differential_inline_draft_create\":{\"comment\":{\"id\":\"c\"}}}' ;;",
		"*PublishDrafts*) echo '{\"update_phabricator_diff\":{\"phabricator_diff\":{\"number\":123,\"url\":\"u\"}}}' ;;",
		"esac",
		"",
	}, "\n"))
	vim.uv.fs_chmod(bin .. "/jf", 493)
	vim.env.PATH = bin .. ":" .. vim.env.PATH
	local function calls()
		return vim.uv.fs_stat(log) and vim.split(vim.trim(H.read(log)), "\n") or {}
	end
	-- one read: jf may append between two
	local function last_call()
		local all = calls()
		return all[#all] or ""
	end

	local root, revs = H.repo("diff")
	local hg = require("lib.meta-hg")
	hg.ssl_state.repo_root = root
	hg.ssl_commands.diff_split.action({ hash = revs.target, is_current = false, diff = "D123" })

	local function comment(text)
		vim.cmd("1HgInlineComment")
		vim.api.nvim_buf_set_lines(0, 0, -1, false, { text })
		vim.cmd.stopinsert()
		vim.api.nvim_win_close(0, true)
	end
	comment("first")
	vim.wait(3000, function()
		return #calls() >= 2
	end, 20)
	comment("second")
	vim.wait(3000, function()
		return #calls() >= 4
	end, 20)
	local version_lookups = vim.tbl_filter(function(c)
		return c:find("query_params", 1, true) ~= nil
	end, calls())
	H.eq(#version_lookups, 2, "each comment looks up the latest version")

	vim.cmd("HgPublishDrafts")
	vim.wait(3000, function()
		return last_call():find("attach_inlines", 1, true) ~= nil
	end, 20)
	local publish = vim.json.decode(last_call())
	H.eq(publish.data and publish.data.number, "123", "drafts published on the diff split's diff")

	-- Outside the split's panes, like :HgInlineComment, it goes by the file's commit.
	H.fake_hg()
	H.rewrite(root, "log", "s/^$/D456/")
	local session = H.session()
	for _, win in ipairs(H.wins()) do
		if win ~= session.left_win and win ~= session.right_win then
			vim.api.nvim_set_current_win(win)
		end
	end
	vim.cmd.edit(root .. "/bar.py")
	vim.cmd("HgPublishDrafts")
	vim.wait(3000, function()
		return last_call():find('"456"', 1, true) ~= nil
	end, 20)
	publish = vim.json.decode(last_call())
	H.eq(publish.data and publish.data.number, "456", "drafts published on the focused file's diff, not the split's")

	-- In a working-copy split (:HgDiffSplit) comments go on the current commit's diff.
	vim.cmd("HgDiffSplit")
	H.notifications = {}
	comment("third")
	vim.wait(3000, function()
		return last_call():find("third", 1, true) ~= nil
	end, 20)
	H.check(not vim.iter(H.notifications):any(function(n)
		return tostring(n.msg):find("No Phabricator diff", 1, true) ~= nil
	end), "not refused: " .. vim.inspect(H.notifications))
	H.check(last_call():find("third", 1, true) ~= nil, "draft created")
end)
