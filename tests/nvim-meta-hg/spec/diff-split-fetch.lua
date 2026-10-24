-- Background prefetch of diff split contents: byte fidelity, batching,
-- cancellation on close, and the direct-fetch fallback.
local H = dofile(os.getenv("NVIM_META_HG_LIB"))

H.run(function()
	local root, revs = H.repo("diff")
	local hg = require("lib.meta-hg")
	local ds = require("lib.diff-session")
	hg.ssl_state.repo_root = root

	local cats = 0
	local orig_system = vim.system
	vim.system = function(cmd, ...)
		if type(cmd) == "table" and cmd[1] == "hg" and cmd[2] == "cat" then
			cats = cats + 1
		end
		return orig_system(cmd, ...)
	end

	-- Buffer lines must reproduce each file's bytes (unchanged in the working copy).
	hg.ssl_commands.diff_split.action({ hash = revs.edge, is_current = false })
	local s = H.session()
	for i, p in ipairs(s.pairs) do
		ds.show_pair(s, i)
		local raw = H.read(root .. "/" .. p.file):gsub("\n$", "")
		local got = table.concat(vim.api.nvim_buf_get_lines(p.new_buf, 0, -1, false), "\n")
		H.eq(got, raw, "content " .. p.file)
	end
	ds.close(s)

	-- Full prefetch of 120 files: 1 + 50 + 50 + 19 per side.
	cats = 0
	hg.ssl_commands.diff_split.action({ hash = revs.many, is_current = false })
	s = H.session()
	vim.wait(10000, function()
		return s.cache[s.commit.hash].contents["many/f120.txt"] ~= nil
			and s.cache[s.parent_rev].contents["many/f120.txt"] ~= nil
	end, 20)
	H.eq(cats, 8, "prefetch used 8 batched hg cat calls")
	cats = 0
	for i = 2, #s.pairs do
		ds.show_pair(s, i)
	end
	H.eq(cats, 0, "cycling all 120 files spawned no hg cat")
	H.check(H.lines(s.pairs[120].new_buf) == "v120" and H.lines(s.pairs[120].old_buf) == "", "f120 content")
	ds.close(s)

	-- Closing mid-prefetch stops further batches.
	cats = 0
	hg.ssl_commands.diff_split.action({ hash = revs.many, is_current = false })
	ds.close(H.session())
	vim.wait(3000)
	H.check(cats <= 4, "close stops prefetch: " .. cats .. " hg cat calls (full run is 8)")

	-- An uncached file falls back to a direct fetch.
	hg.ssl_commands.diff_split.action({ hash = revs.target, is_current = false })
	s = H.session()
	vim.wait(3000, function()
		for _, rev in ipairs({ s.commit.hash, s.parent_rev }) do
			local c = s.cache[rev]
			if c.pending["runme"] or c.contents["runme"] == nil then
				return false
			end
		end
		return true
	end, 20)
	local p, idx = H.pair(s, "runme")
	s.cache[s.commit.hash].contents["runme"] = nil
	s.cache[s.parent_rev].contents["runme"] = nil
	cats = 0
	ds.show_pair(s, idx)
	H.check(cats == 2 and H.lines(p.new_buf) == "#!/bin/bash\necho hi", "fallback fetched runme directly (" .. cats .. " calls)")
	ds.close(s)
end)
