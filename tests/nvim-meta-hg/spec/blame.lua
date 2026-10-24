-- Inline blame is drawn in the right buffer at its own cursor, and not at
-- all while toggled off.
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
	H.rewrite(root, "blame", [[s/^\( *\)test *\([0-9-]*\): line \([0-9]*\)$/\1test D\3 \2: line \3/]])
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
end)
