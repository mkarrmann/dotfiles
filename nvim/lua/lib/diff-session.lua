local M = {}

---@class DiffSession
---@field pairs DiffPair[]
---@field index integer
---@field left_win integer
---@field right_win integer
---@field update_winbar fun(session: DiffSession)
---@field on_close fun()?
---@field attached table<integer, true>? Buffers this session mapped keys in.

---@class DiffPair
---@field file string
---@field old_buf integer?
---@field new_buf integer?
---@field is_live boolean? If true, new_buf is a real file buffer — don't delete on cleanup.
---@field load fun(pair: DiffPair)?

---@type table<integer, DiffSession>
M.sessions = {}

function M.create_pair_wins()
	vim.cmd("rightbelow vsplit")
	local left_win = vim.api.nvim_get_current_win()
	vim.cmd("rightbelow vsplit")
	local right_win = vim.api.nvim_get_current_win()
	return left_win, right_win
end

---The session whose pane is the current window, if any.
---@return DiffSession?
local function current_session()
	local session = M.sessions[vim.api.nvim_get_current_tabpage()]
	local win = vim.api.nvim_get_current_win()
	if session and (win == session.left_win or win == session.right_win) then
		return session
	end
end

---@type table<string, {desc: string, run: fun(session: DiffSession)}>
local ACTIONS = {
	["]f"] = { desc = "Next diff file", run = function(s) M.cycle(s, 1) end },
	["[f"] = { desc = "Previous diff file", run = function(s) M.cycle(s, -1) end },
	["]F"] = { desc = "Last diff file", run = function(s) M.show_pair(s, #s.pairs) end },
	["[F"] = { desc = "First diff file", run = function(s) M.show_pair(s, 1) end },
	["gf"] = { desc = "Jump to diff file", run = function(s) M.jump_to_file(s) end },
	["gq"] = { desc = "Close diff", run = function(s) M.close(s) end },
}

---@type table<integer, integer> Buffer -> sessions that mapped keys in it.
local attach_count = {}

---Maps the session keys in `buf` for `session`. A live file buffer can be in
---several sessions (one per tab) and in ordinary windows, so the maps look
---the session up from the current window when pressed, and fall back to the
---key's usual meaning outside diff panes.
---@param session DiffSession
---@param buf integer?
local function attach(session, buf)
	session.attached = session.attached or {}
	if not buf or session.attached[buf] then
		return
	end
	session.attached[buf] = true
	attach_count[buf] = (attach_count[buf] or 0) + 1
	if attach_count[buf] > 1 then
		return
	end
	for key, action in pairs(ACTIONS) do
		vim.keymap.set("n", key, function()
			local session_here = current_session()
			if session_here then
				return action.run(session_here)
			end
			local count = vim.v.count > 0 and tostring(vim.v.count) or ""
			vim.api.nvim_feedkeys(count .. key, "n", false)
		end, { buffer = buf, desc = action.desc })
	end
end

---@param buf integer
local function detach(buf)
	attach_count[buf] = (attach_count[buf] or 1) - 1
	if attach_count[buf] > 0 then
		return
	end
	attach_count[buf] = nil
	if vim.api.nvim_buf_is_valid(buf) then
		for key in pairs(ACTIONS) do
			pcall(vim.keymap.del, "n", key, { buffer = buf })
		end
	end
end

---Maps the session keys in a pair's buffers; call once the pair has loaded.
---@param session DiffSession
---@param pair DiffPair
function M.attach_pair(session, pair)
	attach(session, pair.old_buf)
	attach(session, pair.new_buf)
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
	-- set even if the pair can't be shown, so ]f/[f move past it
	session.index = index
	if not pair.old_buf or not vim.api.nvim_buf_is_valid(pair.old_buf) then
		if pair.load then
			pair.load(pair)
		end
		if not pair.old_buf or not vim.api.nvim_buf_is_valid(pair.old_buf) then
			vim.notify("Can't show " .. pair.file, vim.log.levels.WARN)
			return
		end
	end

	require("lib.diff-opts").show(session.left_win, pair.new_buf, session.right_win, pair.old_buf)
	session.update_winbar(session)
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
	for buf in pairs(session.attached or {}) do
		detach(buf)
	end
	session.attached = {}
	for _, pair in ipairs(session.pairs) do
		if pair.old_buf and vim.api.nvim_buf_is_valid(pair.old_buf) then
			vim.api.nvim_buf_delete(pair.old_buf, { force = true })
		end
		if pair.new_buf and vim.api.nvim_buf_is_valid(pair.new_buf) and not pair.is_live then
			vim.api.nvim_buf_delete(pair.new_buf, { force = true })
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
		M.cleanup(session)
	end
end

---@param key any
---@param session DiffSession
function M.register(key, session)
	M.sessions[key] = session
	for _, pair in ipairs(session.pairs) do
		M.attach_pair(session, pair)
	end
end

return M
