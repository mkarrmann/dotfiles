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
		return calls()[#calls()]:find("attach_inlines", 1, true) ~= nil
	end, 20)
	local publish = vim.json.decode(calls()[#calls()])
	H.eq(publish.data and publish.data.number, "123", "drafts published on the diff split's diff")
end)
