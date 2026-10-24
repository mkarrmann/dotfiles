-- Scratch buffers in meta-hg diff splits: unique names, filetype detection.
local H = dofile(os.getenv("NVIM_META_HG_LIB"))

H.run(function()
	local root, revs = H.repo("diff")
	local hg = require("lib.meta-hg")
	local ds = require("lib.diff-session")
	hg.ssl_state.repo_root = root
	hg.ssl_commands.diff_split.action({ hash = revs.target, is_current = false })
	local s = H.session()
	local _, i_tsx = H.pair(s, "foo.tsx")
	local _, i_ts = H.pair(s, "foo.ts")
	ds.show_pair(s, i_tsx)
	ds.show_pair(s, i_ts)
	local ts, tsx = H.pair(s, "foo.ts"), H.pair(s, "foo.tsx")
	H.check(ts.new_buf ~= tsx.new_buf, "foo.ts and foo.tsx get distinct new buffers")
	H.check(ts.old_buf ~= tsx.old_buf, "foo.ts and foo.tsx get distinct old buffers")
	H.eq(H.lines(tsx.new_buf), "export const b = <span/>;", "foo.tsx new content intact")
	H.eq(H.lines(ts.new_buf), "export const a = 2;", "foo.ts new content")
	H.eq(vim.bo[ts.new_buf].filetype, "typescript", "foo.ts filetype")
	H.check(vim.treesitter.highlighter.active[ts.new_buf] ~= nil, "foo.ts scratch buffer has treesitter highlighting")

	-- Same commit in a second tab, then close it: the first tab's buffers must survive.
	vim.cmd("tabnew")
	hg.ssl_commands.diff_split.action({ hash = revs.target, is_current = false })
	local s2 = H.session()
	H.check(s2 ~= s and H.pair(s2, "a.py").new_buf ~= H.pair(s, "a.py").new_buf, "second tab gets its own buffers")
	local closed_buf = H.pair(s2, "a.py").new_buf
	vim.cmd("tabclose")
	vim.wait(100)
	H.check(vim.api.nvim_buf_is_valid(H.pair(s, "a.py").new_buf), "first tab's buffers survive closing the second")
	H.check(s2.closed and not vim.api.nvim_buf_is_valid(closed_buf), "closing the tab cleans up its split")
	H.check(not vim.tbl_contains(vim.tbl_values(ds.sessions), s2), "and forgets it")
end)
