-- Gutter signs line up with the change, including at the end of the file and
-- for pure deletions, and ]h/[h/]H visit every hunk.
local H = dofile(os.getenv("NVIM_META_HG_LIB"))

local ns = vim.api.nvim_create_namespace("hg_signcolumn")

local function signs()
	local marks = vim.api.nvim_buf_get_extmarks(0, ns, 0, -1, { details = true })
	table.sort(marks, function(a, b)
		return a[2] < b[2] or (a[2] == b[2] and a[4].sign_text < b[4].sign_text)
	end)
	return table.concat(
		vim.tbl_map(function(m)
			return (m[2] + 1) .. vim.trim(m[4].sign_text)
		end, marks),
		" "
	)
end

H.run(function()
	local root = H.repo("plain")
	local path = root .. "/lines.txt"
	local original = H.read(path)
	local function with_content(edit, label, expected, diff)
		local lines = vim.split(original:gsub("\n$", ""), "\n")
		edit(lines)
		H.write(path, table.concat(lines, "\n") .. "\n")
		vim.cmd.edit({ path, bang = true })
		vim.api.nvim_exec_autocmds("BufEnter", { buffer = 0 })
		vim.wait(2000, function()
			return signs() == expected
		end, 20)
		H.eq(signs(), expected, label)
		if diff ~= nil then
			H.eq(vim.b.hg_diff, diff, label .. ": b:hg_diff")
		end
	end

	with_content(function(l)
		l[5] = "five"
	end, "modified line 5", "4_ 5+", { added = 1, removed = 1 })
	with_content(function(l)
		l[20] = "twenty"
	end, "modified last line", "19_ 20+", { added = 1, removed = 1 })
	with_content(function(l)
		table.remove(l, 10)
	end, "deleted line 10", "9_", { added = 0, removed = 1 })
	with_content(function(l)
		table.remove(l, 1)
	end, "deleted line 1", "1_")
	with_content(function(l)
		table.remove(l, 20)
	end, "deleted last line", "19_")
	with_content(function(l)
		table.insert(l, "21")
		l[3] = "three"
	end, "appended at EOF", "2_ 3+ 21+")

	vim.api.nvim_win_set_cursor(0, { 1, 0 })
	vim.cmd("normal ]h")
	H.eq(vim.fn.line("."), 3, "]h to the first hunk")
	vim.cmd("normal ]h")
	H.eq(vim.fn.line("."), 21, "]h to the hunk at EOF")
	vim.cmd("normal [h")
	H.eq(vim.fn.line("."), 3, "[h back")
	vim.cmd("normal ]H")
	H.eq(vim.fn.line("."), 21, "]H to the last hunk")

	-- A slow refresh computed for older text doesn't replace a newer one.
	H.fake_hg()
	with_content(function(l)
		l[5] = "five"
	end, "before the race", "4_ 5+")
	H.slow(root, "cat", 1.5)
	vim.api.nvim_exec_autocmds("BufEnter", { buffer = 0 })
	vim.wait(300)
	vim.api.nvim_buf_set_lines(0, 9, 10, false, { "ten" })
	vim.api.nvim_exec_autocmds("BufEnter", { buffer = 0 })
	vim.wait(1000, function()
		return signs() == "4_ 5+ 9_ 10+"
	end, 20)
	vim.wait(2000)
	H.eq(signs(), "4_ 5+ 9_ 10+", "signs match the newest text")
end)
