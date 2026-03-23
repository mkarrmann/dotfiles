-- Per-tab terminal provider for claudecode.nvim.
-- Each Neovim tab gets its own independent Claude Code terminal,
-- replacing the upstream native provider's global singleton.

local M = {}

--- @type table<number, { bufnr: number?, winid: number?, jobid: number? }>
local tabs = {}

local config = {}

local function cleanup(t)
	tabs[t] = nil
end

local function get_state(t)
	return tabs[t]
end

local function ensure_state(t)
	if not tabs[t] then
		tabs[t] = {}
	end
	return tabs[t]
end

local function is_valid_for_tab(t)
	local s = tabs[t]
	if not s or not s.bufnr or not vim.api.nvim_buf_is_valid(s.bufnr) then
		cleanup(t)
		return false
	end
	if not s.winid or not vim.api.nvim_win_is_valid(s.winid) then
		for _, win in ipairs(vim.api.nvim_tabpage_list_wins(t)) do
			if vim.api.nvim_win_get_buf(win) == s.bufnr then
				s.winid = win
				return true
			end
		end
		s.winid = nil
		return true
	end
	return true
end

local function is_visible_in_tab(t)
	local s = tabs[t]
	if not s or not s.bufnr or not vim.api.nvim_buf_is_valid(s.bufnr) then
		return false
	end
	for _, win in ipairs(vim.api.nvim_tabpage_list_wins(t)) do
		if vim.api.nvim_win_is_valid(win) and vim.api.nvim_win_get_buf(win) == s.bufnr then
			s.winid = win
			return true
		end
	end
	s.winid = nil
	return false
end

local function create_split(effective_config)
	local original_win = vim.api.nvim_get_current_win()
	local width = math.floor(vim.o.columns * effective_config.split_width_percentage)
	local full_height = vim.o.lines
	local placement = effective_config.split_side == "left" and "topleft " or "botright "

	vim.cmd(placement .. width .. "vsplit")
	local new_winid = vim.api.nvim_get_current_win()
	vim.api.nvim_win_set_height(new_winid, full_height)

	return new_winid, original_win
end

local function open_terminal(cmd_string, env_table, effective_config, focus, t)
	if focus == nil then
		focus = true
	end
	local s = get_state(t)
	if s and s.bufnr and vim.api.nvim_buf_is_valid(s.bufnr) then
		if focus and s.winid and vim.api.nvim_win_is_valid(s.winid) then
			vim.api.nvim_set_current_win(s.winid)
			vim.cmd("startinsert")
		end
		return true
	end

	local new_winid, original_win = create_split(effective_config)
	vim.api.nvim_win_call(new_winid, function()
		vim.cmd("enew")
	end)

	local term_cmd_arg
	if cmd_string:find(" ", 1, true) then
		term_cmd_arg = vim.split(cmd_string, " ", { plain = true, trimempty = false })
	else
		term_cmd_arg = { cmd_string }
	end

	local new_state = ensure_state(t)

	vim.env.NVIM_TAB_HANDLE = tostring(t)

	new_state.jobid = vim.fn.termopen(term_cmd_arg, {
		env = env_table,
		cwd = effective_config.cwd,
		on_exit = function(job_id, _, _)
			vim.schedule(function()
				local st = tabs[t]
				if not st or st.jobid ~= job_id then
					return
				end
				local saved_winid = st.winid
				local saved_bufnr = st.bufnr
				cleanup(t)
				if not effective_config.auto_close then
					return
				end
				if saved_winid and vim.api.nvim_win_is_valid(saved_winid) then
					if
						saved_bufnr
						and vim.api.nvim_buf_is_valid(saved_bufnr)
						and vim.api.nvim_win_get_buf(saved_winid) == saved_bufnr
					then
						vim.api.nvim_win_close(saved_winid, true)
					else
						pcall(vim.api.nvim_win_close, saved_winid, true)
					end
				end
			end)
		end,
	})

	if not new_state.jobid or new_state.jobid == 0 then
		vim.notify("Failed to open Claude terminal.", vim.log.levels.ERROR)
		vim.api.nvim_win_close(new_winid, true)
		vim.api.nvim_set_current_win(original_win)
		cleanup(t)
		return false
	end

	new_state.winid = new_winid
	new_state.bufnr = vim.api.nvim_get_current_buf()
	vim.b[new_state.bufnr].claude_per_tab_terminal = t
	vim.bo[new_state.bufnr].bufhidden = "hide"

	if focus then
		vim.api.nvim_set_current_win(new_state.winid)
		vim.cmd("startinsert")
	else
		vim.api.nvim_set_current_win(original_win)
	end

	return true
end

local function show_hidden(effective_config, focus, t)
	local s = tabs[t]
	if not s or not s.bufnr or not vim.api.nvim_buf_is_valid(s.bufnr) then
		return false
	end
	if is_visible_in_tab(t) then
		if focus and s.winid then
			vim.api.nvim_set_current_win(s.winid)
			vim.cmd("startinsert")
		end
		return true
	end

	local new_winid, original_win = create_split(effective_config)
	vim.api.nvim_win_set_buf(new_winid, s.bufnr)
	s.winid = new_winid

	if focus then
		vim.api.nvim_set_current_win(s.winid)
		vim.cmd("startinsert")
	else
		vim.api.nvim_set_current_win(original_win)
	end

	return true
end

local function hide(t)
	local s = tabs[t]
	if s and s.bufnr and vim.api.nvim_buf_is_valid(s.bufnr) and s.winid and vim.api.nvim_win_is_valid(s.winid) then
		vim.api.nvim_win_close(s.winid, false)
		s.winid = nil
	end
end

local function find_existing_in_tab(t)
	-- Check visible buffers in windows first
	for _, win in ipairs(vim.api.nvim_tabpage_list_wins(t)) do
		local buf = vim.api.nvim_win_get_buf(win)
		if vim.api.nvim_buf_is_valid(buf) and vim.b[buf].claude_per_tab_terminal == t then
			return buf, win
		end
	end
	-- Check hidden buffers owned by this tab (no window, but buffer still alive)
	for _, buf in ipairs(vim.api.nvim_list_bufs()) do
		if vim.api.nvim_buf_is_valid(buf) and vim.b[buf].claude_per_tab_terminal == t then
			return buf, nil
		end
	end
	return nil, nil
end

local function recover_or_open(cmd_string, env_table, effective_config, focus, t)
	local existing_buf, existing_win = find_existing_in_tab(t)
	if existing_buf then
		tabs[t] = { bufnr = existing_buf, winid = existing_win }
		if existing_win then
			if focus then
				vim.api.nvim_set_current_win(existing_win)
				vim.cmd("startinsert")
			end
		else
			show_hidden(effective_config, focus, t)
		end
	else
		open_terminal(cmd_string, env_table, effective_config, focus, t)
	end
end

-- Provider interface --

function M.setup(term_config)
	config = term_config
end

function M.open(cmd_string, env_table, effective_config, focus)
	if focus == nil then
		focus = true
	end
	local t = vim.api.nvim_get_current_tabpage()
	if is_valid_for_tab(t) then
		local s = tabs[t]
		if not s.winid or not vim.api.nvim_win_is_valid(s.winid) then
			show_hidden(effective_config, focus, t)
		elseif focus then
			vim.api.nvim_set_current_win(s.winid)
			vim.cmd("startinsert")
		end
	else
		recover_or_open(cmd_string, env_table, effective_config, focus, t)
	end
end

function M.close()
	local t = vim.api.nvim_get_current_tabpage()
	local s = tabs[t]
	if s and s.winid and vim.api.nvim_win_is_valid(s.winid) then
		vim.api.nvim_win_close(s.winid, true)
	end
	cleanup(t)
end

function M.simple_toggle(cmd_string, env_table, effective_config)
	local t = vim.api.nvim_get_current_tabpage()
	local s = tabs[t]
	local has_buffer = s and s.bufnr and vim.api.nvim_buf_is_valid(s.bufnr)
	local visible = has_buffer and is_visible_in_tab(t)

	if visible then
		hide(t)
	elseif has_buffer then
		show_hidden(effective_config, true, t)
	else
		recover_or_open(cmd_string, env_table, effective_config, true, t)
	end
end

function M.focus_toggle(cmd_string, env_table, effective_config)
	local t = vim.api.nvim_get_current_tabpage()
	local s = tabs[t]
	local has_buffer = s and s.bufnr and vim.api.nvim_buf_is_valid(s.bufnr)
	local visible = has_buffer and is_visible_in_tab(t)

	if has_buffer then
		if visible then
			if s.winid == vim.api.nvim_get_current_win() then
				hide(t)
			else
				vim.api.nvim_set_current_win(s.winid)
				vim.cmd("startinsert")
			end
		else
			show_hidden(effective_config, true, t)
		end
	else
		local existing_buf, existing_win = find_existing_in_tab(t)
		if existing_buf then
			tabs[t] = { bufnr = existing_buf, winid = existing_win }
			if existing_win then
				if existing_win == vim.api.nvim_get_current_win() then
					hide(t)
				else
					vim.api.nvim_set_current_win(existing_win)
					vim.cmd("startinsert")
				end
			else
				show_hidden(effective_config, true, t)
			end
		else
			open_terminal(cmd_string, env_table, effective_config, true, t)
		end
	end
end

function M.get_active_bufnr()
	local t = vim.api.nvim_get_current_tabpage()
	local s = tabs[t]
	if s and s.bufnr and vim.api.nvim_buf_is_valid(s.bufnr) then
		return s.bufnr
	end
	return nil
end

function M.is_available()
	return true
end

vim.api.nvim_create_autocmd("TabClosed", {
	callback = function()
		local valid = {}
		for _, t in ipairs(vim.api.nvim_list_tabpages()) do
			valid[t] = true
		end
		for t in pairs(tabs) do
			if not valid[t] then
				tabs[t] = nil
			end
		end
	end,
})

return M
