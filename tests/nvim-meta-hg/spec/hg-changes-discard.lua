-- HgChanges `X` reloads only the discarded checkout's buffers.
local H = dofile(os.getenv("NVIM_META_HG_LIB"))

H.run(function()
	local other = H.repo("diff")
	local root = H.repo("diff")

	-- Unsaved edits to the same repo-relative path in another checkout, which is nvim's cwd.
	vim.cmd.cd(other)
	vim.cmd.edit(other .. "/bar.py")
	local other_buf = vim.api.nvim_get_current_buf()
	vim.api.nvim_buf_set_lines(other_buf, 0, -1, false, { "unsaved" })

	vim.cmd.split(root .. "/bar.py")
	local buf = vim.api.nvim_get_current_buf()
	vim.cmd("HgChanges")
	local picker_buf = vim.api.nvim_get_current_buf()
	local function select_line(pattern)
		for i, l in ipairs(vim.api.nvim_buf_get_lines(picker_buf, 0, -1, false)) do
			if l:match(pattern) then
				vim.api.nvim_win_set_cursor(0, { i, 0 })
				vim.fn.maparg("<Space>", "n", false, true).callback()
			end
		end
	end
	select_line("bar%.py$")
	vim.ui.select = function(_, _, on_choice)
		on_choice("Yes")
	end
	vim.fn.maparg("X", "n", false, true).callback()
	H.check(vim.wait(10000, function()
		return vim.iter(H.notifications):any(function(n)
			return n.msg:find("^Discard") ~= nil
		end)
	end, 50), "discard finished")

	H.eq(H.read(root .. "/bar.py"), 'print("bar2")\n', "bar.py reverted on disk")
	H.eq(H.lines(buf), 'print("bar2")', "bar.py buffer in the discarded checkout reloaded")
	H.eq(H.lines(other_buf), "unsaved", "other checkout's unsaved buffer untouched")
end)
