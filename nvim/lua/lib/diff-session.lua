local M = {}

---@class DiffSession
---@field pairs DiffPair[]
---@field index integer
---@field left_win integer
---@field right_win integer
---@field update_winbar fun(session: DiffSession)
---@field on_close fun()?

---@class DiffPair
---@field file string
---@field old_buf integer?
---@field new_buf integer?
---@field is_live boolean? If true, new_buf is a real file buffer — don't delete on cleanup.
---@field load fun(pair: DiffPair)?

local KEYMAPS = { "]f", "[f", "]F", "[F", "gf", "gq" }

---@type table<integer, DiffSession>
M.sessions = {}

function M.create_pair_wins()
	vim.cmd("rightbelow vsplit")
	local left_win = vim.api.nvim_get_current_win()
	vim.cmd("rightbelow vsplit")
	local right_win = vim.api.nvim_get_current_win()
	return left_win, right_win
end

local function set_keymaps(session)
	---@type table<integer, boolean>
	local seen = {}
	for _, pair in ipairs(session.pairs) do
		if not pair.old_buf then
			goto continue
		end
		for _, b in ipairs({ pair.old_buf, pair.new_buf }) do
			if b and not seen[b] then
				seen[b] = true
				vim.keymap.set("n", "]f", function()
					M.cycle(session, 1)
				end, { buffer = b, desc = "Next diff file" })
				vim.keymap.set("n", "[f", function()
					M.cycle(session, -1)
				end, { buffer = b, desc = "Previous diff file" })
				vim.keymap.set("n", "]F", function()
					M.show_pair(session, #session.pairs)
				end, { buffer = b, desc = "Last diff file" })
				vim.keymap.set("n", "[F", function()
					M.show_pair(session, 1)
				end, { buffer = b, desc = "First diff file" })
				vim.keymap.set(
					"n",
					"gf",
					function()
						M.jump_to_file(session)
					end,
					{ buffer = b, desc = "Jump to diff file" }
				)
				vim.keymap.set("n", "gq", function()
					M.close(session)
				end, { buffer = b, desc = "Close diff" })
			end
		end
		::continue::
	end
end

---@param session DiffSession
---@param index integer
function M.show_pair(session, index)
	if
		not vim.api.nvim_win_is_valid(session.left_win)
		or not vim.api.nvim_win_is_valid(session.right_win)
	then
		vim.notify("Diff windows are no longer valid", vim.log.levels.WARN)
		M.close(session)
		return
	end

	local pair = session.pairs[index]
	if not pair.old_buf or not vim.api.nvim_buf_is_valid(pair.old_buf) then
		if pair.load then
			pair.load(pair)
		end
		if not pair.old_buf or not vim.api.nvim_buf_is_valid(pair.old_buf) then
			return
		end
	end

	session.index = index

	vim.api.nvim_win_call(session.left_win, function()
		vim.cmd("diffoff")
	end)
	vim.api.nvim_win_call(session.right_win, function()
		vim.cmd("diffoff")
	end)

	vim.api.nvim_win_set_buf(session.left_win, pair.new_buf)
	vim.api.nvim_win_set_buf(session.right_win, pair.old_buf)

	vim.api.nvim_win_call(session.left_win, function()
		vim.cmd("diffthis")
	end)
	vim.api.nvim_win_call(session.right_win, function()
		vim.cmd("diffthis")
	end)

	local diff_opts = require("lib.diff-opts")
	diff_opts.apply(session.left_win)
	diff_opts.apply(session.right_win)

	session.update_winbar(session)
	vim.cmd("syncbind")

	vim.api.nvim_win_call(session.left_win, function()
		vim.cmd("normal! gg")
	end)
	vim.api.nvim_win_call(session.right_win, function()
		vim.cmd("normal! gg")
	end)
	vim.api.nvim_set_current_win(session.left_win)
end

---@param session DiffSession
---@param direction 1|-1
function M.cycle(session, direction)
	if #session.pairs <= 1 then
		vim.notify("Only one file in this diff", vim.log.levels.INFO)
		return
	end
	local n = #session.pairs
	local new_index = ((session.index - 1 + direction) % n) + 1
	M.show_pair(session, new_index)
end

function M.jump_to_file(session)
	---@type {index: integer, file: string}[]
	local items = {}
	for i, pair in ipairs(session.pairs) do
		table.insert(items, { index = i, file = pair.file })
	end

	vim.ui.select(items, {
		prompt = "Jump to file:",
		format_item = function(item)
			local marker = item.index == session.index and " (current)" or ""
			return string.format("[%d/%d] %s%s", item.index, #session.pairs, item.file, marker)
		end,
	}, function(choice)
		if choice then
			M.show_pair(session, choice.index)
		end
	end)
end

---@param session DiffSession
function M.cleanup(session)
	for _, pair in ipairs(session.pairs) do
		if pair.old_buf and vim.api.nvim_buf_is_valid(pair.old_buf) then
			vim.api.nvim_buf_delete(pair.old_buf, { force = true })
		end
		if pair.new_buf and vim.api.nvim_buf_is_valid(pair.new_buf) then
			if pair.is_live then
				for _, key in ipairs(KEYMAPS) do
					pcall(vim.keymap.del, "n", key, { buffer = pair.new_buf })
				end
			else
				vim.api.nvim_buf_delete(pair.new_buf, { force = true })
			end
		end
	end
	for key, s in pairs(M.sessions) do
		if s == session then
			M.sessions[key] = nil
			break
		end
	end
end

function M.close(session)
	if session.on_close then
		session.on_close()
	else
		-- Default: close the tab containing the diff.
		local tab = vim.api.nvim_get_current_tabpage()
		if M.sessions[tab] == session then
			vim.cmd("tabclose")
		else
			M.cleanup(session)
		end
	end
end

---@param key any
---@param session DiffSession
function M.register(key, session)
	M.sessions[key] = session
	set_keymaps(session)
end

return M
