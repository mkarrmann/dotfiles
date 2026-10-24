-- A failed hg cat shows as an error in the pane, not as an empty (added or
-- deleted) file, and ]f still moves past it.
local H = dofile(os.getenv("NVIM_META_HG_LIB"))

H.run(function()
	H.fake_hg()
	local root, revs = H.repo("diff")
	local hg = require("lib.meta-hg")
	local ds = require("lib.diff-session")
	hg.ssl_state.repo_root = root
	H.fail(root, "cat", "abort: injected failure")
	hg.ssl_commands.diff_split.action({ hash = revs.target, is_current = false })
	local s = H.session()
	H.check(s ~= nil, "split opens even though the first file can't be read")
	local p = s.pairs[1]
	H.check(H.lines(p.old_buf):find("injected failure", 1, true) ~= nil, "old pane shows the error: " .. H.lines(p.old_buf))
	H.check(H.lines(p.new_buf):find("injected failure", 1, true) ~= nil, "new pane shows the error")
	vim.api.nvim_set_current_win(s.left_win)
	ds.cycle(s, 1)
	H.eq(s.index, 2, "]f moves on")
	ds.cycle(s, 1)
	H.eq(s.index, 3, "and on")
end)
