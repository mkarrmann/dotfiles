-- Inline blame: drawn in the right buffer at its own cursor, off when toggled
-- off, remapped through unsaved edits, and not recomputed needlessly.
-- hg blame is faked to attribute "line N" to diff DN.
local H = dofile(os.getenv("NVIM_META_HG_LIB"))

local ns = vim.api.nvim_create_namespace("hg_line_blame")
local TODAY = os.date("%Y-%m-%d")

local function marks(buf)
	return vim.tbl_map(function(m)
		return (m[2] + 1) .. ":" .. vim.trim(m[4].virt_text[1][1])
	end, vim.api.nvim_buf_get_extmarks(buf or 0, ns, 0, -1, { details = true }))
end

local function move_to(lnum)
	vim.api.nvim_win_set_cursor(0, { lnum, 0 })
	vim.api.nvim_exec_autocmds("CursorMoved", { buffer = 0 })
	vim.wait(500)
end

H.run(function()
	H.fake_hg()
	local root = H.repo("plain")
	H.rewrite(root, "blame", table.concat({
		[[s/^\( *\)test *\([0-9-]*\): line \([0-9]*\)$/\1test D\3 \2: line \3/]],
		-- without --user and --date
		[[s/^ *: line \([0-9]*\)$/D\1: line \1/]],
	}, "\n"))
	local blames = 0
	local system = vim.system
	vim.system = function(cmd, ...)
		if type(cmd) == "table" and cmd[1] == "hg" and cmd[2] == "blame" then
			blames = blames + 1
		end
		return system(cmd, ...)
	end

	-- A slow blame that lands after switching buffers.
	H.slow(root, "blame", 1)
	vim.cmd.edit(root .. "/lines.txt")
	local lines_buf = vim.api.nvim_get_current_buf()
	vim.wait(200)
	vim.cmd.edit(root .. "/small.txt")
	local small_buf = vim.api.nvim_get_current_buf()
	vim.v.errmsg = ""
	vim.wait(2000)
	H.eq(vim.v.errmsg, "", "no errors")
	H.eq(marks(lines_buf), {}, "nothing drawn in the buffer you left")
	H.eq(marks(small_buf), { "1:Not submitted yet" }, "the current buffer shows its own blame")

	vim.cmd.edit(root .. "/lines.txt")
	vim.wait(1000, function()
		return #marks() > 0
	end, 20)
	move_to(4)
	H.eq(marks(), { "4:test " .. TODAY .. " D4" }, "blame for the cursor line")

	-- Toggled off, moving the cursor shows nothing; toggled on, it's back.
	vim.cmd("HgLineBlameToggle")
	move_to(6)
	H.eq(marks(), {}, "nothing while toggled off")
	vim.cmd("HgLineBlameToggle")
	vim.wait(500)
	H.eq(marks(), { "6:test " .. TODAY .. " D6" }, "toggling on draws for the cursor line")

	-- Unsaved lines inserted above shift the blame with them.
	vim.api.nvim_buf_set_lines(0, 0, 0, false, { "new a", "new b", "new c" })
	move_to(5)
	H.eq(marks(), { "5:test " .. TODAY .. " D2" }, "line 5 is the old line 2")
	move_to(2)
	H.eq(marks(), {}, "an unsaved new line has no blame")
	-- and :HgLineBlameCopyDiff goes by the same lines
	local function copied()
		H.notifications = {}
		pcall(vim.cmd, "HgLineBlameCopyDiff")
		return H.notifications[#H.notifications] and H.notifications[#H.notifications].msg
	end
	H.eq(copied(), "No diff for this line (uncommitted or not tracked)", "no diff copied for an unsaved new line")
	move_to(5)
	H.eq(copied(), "Copied D2", "the old line 2's diff copied from line 5")
	vim.cmd("edit!")

	-- Re-entering an unchanged buffer reuses its blame; writing recomputes it.
	vim.wait(1000)
	local before = blames
	vim.cmd.edit(root .. "/small.txt")
	vim.cmd.edit(root .. "/lines.txt")
	vim.wait(300)
	H.eq(blames, before, "no hg blame when re-entering unchanged buffers")
	vim.api.nvim_buf_set_lines(0, 0, 1, false, { "line 1 edited" })
	vim.cmd("write")
	vim.wait(300)
	H.eq(blames, before + 1, "hg blame after a write")

	-- Switching away and back faster than hg blame takes still ends with blame.
	H.slow(root, "blame", 2, true)
	vim.api.nvim_buf_set_lines(0, 0, 1, false, { "line 1" })
	vim.cmd("write")
	local a = vim.api.nvim_get_current_buf()
	vim.api.nvim_win_set_cursor(0, { 1, 0 })
	-- line 1 was "line 1 edited" (not submitted) until this write
	local fresh = { "1:test " .. TODAY .. " D1" }
	local seen = false
	for _ = 1, 5 do
		seen = vim.wait(1200, function()
			return vim.deep_equal(marks(a), fresh)
		end, 20)
		if seen then
			break
		end
		vim.cmd.edit(root .. "/small.txt")
		vim.wait(100)
		vim.cmd.buffer(a)
	end
	H.check(seen, "blame shows despite switching buffers every 1.3 s (blame takes 2 s)")
end)
