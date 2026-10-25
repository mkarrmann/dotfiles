-- Inline comments go on the diff version submitted from the commit whose lines
-- they're on, and on the side of it those lines are from; without such a
-- version (or with unsaved or uncommitted lines) on the latest, with a
-- warning. :HgPublishDrafts publishes on the diff the comments were made on.
-- jf is faked: it logs each call's variables and answers with canned JSON.
local H = dofile(os.getenv("NVIM_META_HG_LIB"))

H.run(function()
	local log = H.tmp .. "/jf.log"
	local versions_file = H.tmp .. "/versions.json"
	local bin = H.tmp .. "/bin"
	vim.fn.mkdir(bin, "p")
	H.write(bin .. "/jf", table.concat({
		"#!/bin/sh",
		'printf "%s\\n" "$5" >> ' .. log,
		'case "$3" in',
		"*GetVersions*) cat " .. versions_file .. " ;;",
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

	---@param versions [string, string][] id and commit, oldest first
	local function answer_versions(versions)
		H.write(versions_file, vim.json.encode({
			phabricator_diff_query = { {
				results = { nodes = { {
					latest_phabricator_version = { id = versions[#versions][1] },
					author_relevant_phabricator_versions = vim.tbl_map(function(v)
						return { id = v[1], commit_hash_best_effort = v[2] }
					end, versions),
				} } },
			} },
		}))
	end

	local root, revs = H.repo("diff")
	local function node(rev)
		return vim.trim(H.sh({ "hg", "log", "-r", rev, "-T", "{node}" }, root))
	end
	local target, current = node(revs.target), node(".")
	-- target is D123, the current commit D456
	H.fake_hg()
	H.rewrite(root, "log", table.concat({
		"s/^" .. target .. " $/" .. target .. " D123/",
		[[s/^\([0-9a-f]\{40\}\) $/\1 D456/]],
		"s/^$/D456/",
	}, "\n"))

	local function comment(text)
		vim.cmd("1HgInlineComment")
		vim.api.nvim_buf_set_lines(0, 0, -1, false, { text })
		vim.cmd.stopinsert()
		vim.api.nvim_win_close(0, true)
	end
	---Comments `text` and returns the draft it created and the last notification.
	local function draft(text)
		H.notifications = {}
		comment(text)
		vim.wait(5000, function()
			return last_call():find(text, 1, true) ~= nil and #H.notifications > 0
		end, 20)
		local data = last_call():find(text, 1, true) and vim.json.decode(last_call()).data or {}
		local note = H.notifications[#H.notifications] or {}
		return data, note
	end
	local function pane(win)
		vim.api.nvim_set_current_win(win)
		vim.api.nvim_win_set_cursor(win, { 1, 0 })
	end

	-- A smartlog split of target: its own version, either side, no warning.
	answer_versions({ { "v-older", node(revs.base) }, { "v-target", target }, { "v-newer", current } })
	local hg = require("lib.meta-hg")
	hg.ssl_state.repo_root = root
	hg.ssl_commands.diff_split.action({ hash = revs.target, is_current = false, diff = "D123" })
	local split = H.session()
	pane(split.left_win)
	local d, note = draft("on target")
	H.eq({ d.phabricator_version_id, d.is_new_file }, { "v-target", true }, "left pane: target's version, new side")
	H.eq(note.level, vim.log.levels.INFO, "no warning: " .. tostring(note.msg))
	pane(split.right_win)
	d = draft("on target's parent")
	H.eq({ d.phabricator_version_id, d.is_new_file }, { "v-target", false }, "right pane: target's version, old side")

	-- No version from target (amended since): the latest one, with a warning.
	answer_versions({ { "v-older", node(revs.base) }, { "v-newer", current } })
	pane(split.left_win)
	d, note = draft("after an amend")
	H.eq(d.phabricator_version_id, "v-newer", "falls back to the latest version")
	H.check(note.level == vim.log.levels.WARN and tostring(note.msg):find("no version of D123 was submitted", 1, true) ~= nil,
		"warns that the line may be off: " .. tostring(note.msg))

	vim.cmd("HgPublishDrafts")
	vim.wait(3000, function()
		return last_call():find("attach_inlines", 1, true) ~= nil
	end, 20)
	local publish = vim.json.decode(last_call())
	H.eq(publish.data and publish.data.number, "123", "drafts published on the diff split's diff")

	-- Outside the split's panes, like :HgInlineComment, it goes by the file's commit.
	for _, win in ipairs(H.wins()) do
		if win ~= split.left_win and win ~= split.right_win then
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

	-- Working-copy splits comment on the current commit's diff. Its live pane
	-- has bar.py's uncommitted change, so that warns.
	answer_versions({ { "v-target", target }, { "v-current", current } })
	vim.cmd("HgDiffSplit")
	split = H.session()
	pane(split.left_win)
	d, note = draft("on the working copy")
	H.eq({ d.phabricator_version_id, d.is_new_file }, { "v-current", true }, "live pane: the current commit's version, new side")
	H.check(note.level == vim.log.levels.WARN and tostring(note.msg):find("uncommitted changes", 1, true) ~= nil,
		"warns about the uncommitted change: " .. tostring(note.msg))
	pane(split.right_win)
	d = draft("on .^")
	H.eq({ d.phabricator_version_id, d.is_new_file }, { "v-current", false }, "file vs .^: right pane is the old side")

	-- In "uncommitted vs .", the right pane is the current commit itself.
	vim.cmd.edit(root .. "/bar.py")
	vim.cmd("HgDiffSplitWorkingSet")
	split = H.session()
	pane(split.right_win)
	d, note = draft("on .")
	H.eq({ d.phabricator_version_id, d.is_new_file }, { "v-current", true }, "uncommitted vs .: right pane is the new side")
	H.eq(note.level, vim.log.levels.INFO, "no warning: " .. tostring(note.msg))
end)
