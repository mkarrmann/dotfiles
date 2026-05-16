local M = {}

local Manager = {}
Manager.__index = Manager

--- Helpers ---

local function update_scratch_buf(buf, lines)
	vim.bo[buf].modifiable = true
	vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
	vim.bo[buf].modifiable = false
end

local function delete_buf(buf)
	if buf and vim.api.nvim_buf_is_valid(buf) then
		vim.api.nvim_buf_delete(buf, { force = true })
	end
end

local function get_file_list(state)
	return state.mode == "turn" and state.turn_files or state.files
end

local function get_before_buf(state, file_path)
	local data = state.file_data[file_path]
	if not data then
		return nil
	end
	return state.mode == "turn" and data.turn_buf or data.session_buf
end

--- Winbar ---

local function update_winbar(state)
	if not state.diff_tab then
		return
	end
	if not state.left_win or not vim.api.nvim_win_is_valid(state.left_win) then
		return
	end
	if not state.right_win or not vim.api.nvim_win_is_valid(state.right_win) then
		return
	end

	local file_list = get_file_list(state)
	local mode_label = state.mode

	if #file_list == 0 then
		vim.api.nvim_win_set_var(
			state.left_win,
			"custom_winbar_text",
			"%#Comment# [" .. mode_label .. "] no changes %*"
		)
		vim.api.nvim_win_set_var(
			state.right_win,
			"custom_winbar_text",
			"%#Comment# [" .. mode_label .. "] no changes %*"
		)
	else
		local idx = math.min(state.index, #file_list)
		local file_path = file_list[idx]
		local display = vim.fn.fnamemodify(file_path, ":.")
		local pos = string.format("[%d/%d]", idx, #file_list)
		vim.api.nvim_win_set_var(
			state.left_win,
			"custom_winbar_text",
			"%#DiagnosticOk# [" .. mode_label .. "] after " .. pos .. " %* " .. display
		)
		vim.api.nvim_win_set_var(
			state.right_win,
			"custom_winbar_text",
			"%#Comment# [" .. mode_label .. "] before " .. pos .. " %* " .. display
		)
	end

	pcall(function()
		require("lualine").refresh()
	end)
end

--- Display ---

local function show_pair(state, index)
	local file_list = get_file_list(state)
	if #file_list == 0 then
		update_winbar(state)
		return
	end

	index = math.max(1, math.min(index, #file_list))
	state.index = index

	local file_path = file_list[index]
	local data = state.file_data[file_path]
	if not data then
		return
	end

	local before_buf = get_before_buf(state, file_path)
	local after_buf = data.after_buf
	if not before_buf or not vim.api.nvim_buf_is_valid(before_buf) then
		return
	end
	if not after_buf or not vim.api.nvim_buf_is_valid(after_buf) then
		return
	end
	if not vim.api.nvim_win_is_valid(state.left_win) or not vim.api.nvim_win_is_valid(state.right_win) then
		return
	end

	local cur_left = vim.api.nvim_win_get_buf(state.left_win)
	local cur_right = vim.api.nvim_win_get_buf(state.right_win)

	if cur_left == after_buf and cur_right == before_buf then
		vim.api.nvim_win_call(state.left_win, function()
			vim.cmd("diffupdate")
		end)
		update_winbar(state)
		return
	end

	vim.api.nvim_win_call(state.left_win, function()
		vim.cmd("diffoff")
	end)
	vim.api.nvim_win_call(state.right_win, function()
		vim.cmd("diffoff")
	end)

	vim.api.nvim_win_set_buf(state.left_win, after_buf)
	vim.api.nvim_win_set_buf(state.right_win, before_buf)

	vim.api.nvim_win_call(state.left_win, function()
		vim.cmd("diffthis")
	end)
	vim.api.nvim_win_call(state.right_win, function()
		vim.cmd("diffthis")
	end)

	for _, win in ipairs({ state.left_win, state.right_win }) do
		vim.wo[win].scrollbind = true
		vim.wo[win].foldenable = false
		vim.wo[win].relativenumber = true
	end

	update_winbar(state)

	vim.api.nvim_win_call(state.left_win, function()
		vim.cmd("syncbind")
		vim.cmd("normal! gg")
	end)
	vim.api.nvim_win_call(state.right_win, function()
		vim.cmd("normal! gg")
	end)
end

--- Keymaps ---

local KEYMAPS = { "]f", "[f", "]F", "[F", "gf", "gq", "gm" }

local close_diff_tab

local function set_keymaps(buf, manager, session_id)
	if not buf or not vim.api.nvim_buf_is_valid(buf) then
		return
	end

	vim.keymap.set("n", "]f", function()
		local s = manager:get_state(session_id)
		local fl = get_file_list(s)
		if #fl <= 1 then
			return
		end
		show_pair(s, (s.index % #fl) + 1)
	end, { buffer = buf, desc = "Next diff file" })

	vim.keymap.set("n", "[f", function()
		local s = manager:get_state(session_id)
		local fl = get_file_list(s)
		if #fl <= 1 then
			return
		end
		show_pair(s, ((s.index - 2) % #fl) + 1)
	end, { buffer = buf, desc = "Previous diff file" })

	vim.keymap.set("n", "]F", function()
		local s = manager:get_state(session_id)
		show_pair(s, #get_file_list(s))
	end, { buffer = buf, desc = "Last diff file" })

	vim.keymap.set("n", "[F", function()
		local s = manager:get_state(session_id)
		show_pair(s, 1)
	end, { buffer = buf, desc = "First diff file" })

	vim.keymap.set("n", "gf", function()
		local s = manager:get_state(session_id)
		local fl = get_file_list(s)
		local items = {}
		for i, path in ipairs(fl) do
			table.insert(items, { index = i, file = path })
		end
		vim.ui.select(items, {
			prompt = "Jump to file:",
			format_item = function(item)
				local marker = item.index == s.index and " (current)" or ""
				return string.format(
					"[%d/%d] %s%s",
					item.index,
					#fl,
					vim.fn.fnamemodify(item.file, ":."),
					marker
				)
			end,
		}, function(choice)
			if choice then
				show_pair(s, choice.index)
			end
		end)
	end, { buffer = buf, desc = "Jump to diff file" })

	vim.keymap.set("n", "gq", function()
		close_diff_tab(manager:get_state(session_id))
	end, { buffer = buf, desc = "Close diff tab" })

	vim.keymap.set("n", "gm", function()
		local s = manager:get_state(session_id)
		s.mode = s.mode == "turn" and "session" or "turn"
		s.index = 1
		local fl = get_file_list(s)
		if #fl > 0 then
			show_pair(s, 1)
		else
			if vim.api.nvim_win_is_valid(s.left_win) then
				vim.api.nvim_win_call(s.left_win, function()
					vim.cmd("diffoff")
				end)
			end
			if vim.api.nvim_win_is_valid(s.right_win) then
				vim.api.nvim_win_call(s.right_win, function()
					vim.cmd("diffoff")
				end)
			end
			update_winbar(s)
		end
		vim.notify("Diff mode: " .. s.mode)
	end, { buffer = buf, desc = "Toggle turn/session mode" })
end

local function remove_keymaps_from_buf(buf)
	if not buf or not vim.api.nvim_buf_is_valid(buf) then
		return
	end
	for _, key in ipairs(KEYMAPS) do
		pcall(vim.keymap.del, "n", key, { buffer = buf })
	end
end

--- Tab management ---

close_diff_tab = function(state)
	if not state.diff_tab then
		return
	end

	local current_tab = vim.api.nvim_get_current_tabpage()
	if
		current_tab == state.diff_tab
		and state.work_tab
		and vim.api.nvim_tabpage_is_valid(state.work_tab)
	then
		vim.api.nvim_set_current_tabpage(state.work_tab)
	end

	for _, win in ipairs({ state.left_win, state.right_win }) do
		if win and vim.api.nvim_win_is_valid(win) then
			vim.api.nvim_win_call(win, function()
				vim.cmd("diffoff")
			end)
			pcall(vim.api.nvim_win_del_var, win, "custom_winbar_text")
		end
	end

	pcall(function()
		local tabs = vim.api.nvim_list_tabpages()
		for i, t in ipairs(tabs) do
			if t == state.diff_tab then
				vim.cmd(i .. "tabclose")
				break
			end
		end
	end)

	state.diff_tab = nil
	state.left_win = nil
	state.right_win = nil
	pcall(function()
		require("lualine").refresh()
	end)
end

local function setup_diff_tab(manager, state, session_id)
	state.work_tab = vim.api.nvim_get_current_tabpage()
	vim.cmd("tabnew")
	state.diff_tab = vim.api.nvim_get_current_tabpage()
	vim.t.tab_name = "diff"
	vim.t[manager.opts.diff_tab_var] = session_id

	state.left_win = vim.api.nvim_get_current_win()
	vim.cmd("vsplit")
	state.right_win = vim.api.nvim_get_current_win()

	local group_name = manager.opts.name .. "_" .. session_id
	local group = vim.api.nvim_create_augroup(group_name, { clear = true })
	vim.api.nvim_create_autocmd("TabClosed", {
		group = group,
		callback = function()
			if not state.diff_tab then
				return
			end
			local tabs = vim.api.nvim_list_tabpages()
			for _, t in ipairs(tabs) do
				if t == state.diff_tab then
					return
				end
			end
			state.diff_tab = nil
			state.left_win = nil
			state.right_win = nil
			pcall(vim.api.nvim_del_augroup_by_name, group_name)
		end,
	})

	vim.api.nvim_create_autocmd("BufWinEnter", {
		group = group,
		callback = function(args)
			if not state.diff_tab or vim.api.nvim_get_current_tabpage() ~= state.diff_tab then
				return
			end
			set_keymaps(args.buf, manager, session_id)
		end,
	})

	for _, win in ipairs({ state.left_win, state.right_win }) do
		set_keymaps(vim.api.nvim_win_get_buf(win), manager, session_id)
	end

	if #state.turn_files == 0 and #state.files > 0 then
		state.mode = "session"
	end

	local file_list = get_file_list(state)
	if #file_list > 0 then
		state.index = math.min(state.index, #file_list)
		show_pair(state, state.index)
	else
		update_winbar(state)
	end

	vim.api.nvim_set_current_win(state.left_win)
end

--- Manager ---

function M.new(opts)
	opts = opts or {}
	local self = setmetatable({}, Manager)
	self.opts = {
		name = opts.name,
		tab_var = opts.tab_var,
		diff_tab_var = opts.diff_tab_var or (opts.name .. "_session"),
	}
	self._sessions = {}
	self._counter = 0
	return self
end

function Manager:make_scratch_buf(lines, file_path, label)
	self._counter = self._counter + 1
	local buf = vim.api.nvim_create_buf(false, true)
	vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines or {})
	vim.bo[buf].modifiable = false
	vim.bo[buf].buftype = "nofile"
	vim.bo[buf].bufhidden = "hide"
	local ft = vim.filetype.match({ filename = file_path })
	if ft then
		vim.bo[buf].filetype = ft
	end
	pcall(vim.api.nvim_buf_set_name, buf, string.format("%s://%s#%d", label, file_path, self._counter))
	return buf
end

function Manager:get_state(session_id)
	if not session_id then
		return nil
	end
	if not self._sessions[session_id] then
		self._sessions[session_id] = {
			mode = "turn",
			diff_tab = nil,
			work_tab = nil,
			left_win = nil,
			right_win = nil,
			index = 1,
			files = {},
			turn_files = {},
			file_data = {},
		}
	end
	return self._sessions[session_id]
end

function Manager:add_file(session_id, file_path, opts)
	if not session_id or session_id == "" then
		return
	end
	opts = opts or {}
	local state = self:get_state(session_id)
	local data = state.file_data[file_path]
	local is_new_file = data == nil

	if not data then
		data = {}
		state.file_data[file_path] = data
	end

	local after_lines = opts.after_lines or {}
	if data.after_buf and vim.api.nvim_buf_is_valid(data.after_buf) then
		update_scratch_buf(data.after_buf, after_lines)
	else
		data.after_buf = self:make_scratch_buf(after_lines, file_path, "after")
		set_keymaps(data.after_buf, self, session_id)
	end

	if not data.turn_buf or not vim.api.nvim_buf_is_valid(data.turn_buf) then
		data.turn_buf = self:make_scratch_buf(opts.turn_before_lines or {}, file_path, "turn-before")
		set_keymaps(data.turn_buf, self, session_id)
	end

	if not data.session_buf or not vim.api.nvim_buf_is_valid(data.session_buf) then
		data.session_buf = self:make_scratch_buf(opts.session_before_lines or {}, file_path, "session-before")
		set_keymaps(data.session_buf, self, session_id)
	end

	if is_new_file then
		table.insert(state.files, file_path)
	end

	local in_turn = false
	for _, f in ipairs(state.turn_files) do
		if f == file_path then
			in_turn = true
			break
		end
	end
	if not in_turn then
		table.insert(state.turn_files, file_path)
	end

	if state.diff_tab and vim.api.nvim_tabpage_is_valid(state.diff_tab) then
		local file_list = get_file_list(state)
		local current_file = file_list[state.index]
		if current_file == file_path then
			show_pair(state, state.index)
		elseif #file_list == 1 then
			state.index = 1
			show_pair(state, 1)
		else
			update_winbar(state)
		end
	end
end

function Manager:refresh_after(session_id, file_path, lines)
	if not session_id or session_id == "" then
		return
	end
	local state = self._sessions[session_id]
	if not state then
		return
	end
	local data = state.file_data[file_path]
	if not data or not data.after_buf or not vim.api.nvim_buf_is_valid(data.after_buf) then
		return
	end
	update_scratch_buf(data.after_buf, lines or {})
	if state.diff_tab and vim.api.nvim_tabpage_is_valid(state.diff_tab) then
		local file_list = get_file_list(state)
		local current_file = file_list[state.index]
		if current_file == file_path then
			show_pair(state, state.index)
		end
	end
end

function Manager:new_turn(session_id)
	if not session_id or session_id == "" then
		return
	end

	vim.schedule(function()
		local state = self._sessions[session_id]
		if not state then
			return
		end

		for _, data in pairs(state.file_data) do
			delete_buf(data.turn_buf)
			data.turn_buf = nil
		end
		state.turn_files = {}

		if
			state.diff_tab
			and vim.api.nvim_tabpage_is_valid(state.diff_tab)
			and state.mode == "turn"
		then
			state.index = 1
			if state.left_win and vim.api.nvim_win_is_valid(state.left_win) then
				vim.api.nvim_win_call(state.left_win, function()
					vim.cmd("diffoff")
				end)
			end
			if state.right_win and vim.api.nvim_win_is_valid(state.right_win) then
				vim.api.nvim_win_call(state.right_win, function()
					vim.cmd("diffoff")
				end)
			end
			update_winbar(state)
		end
	end)
end

function Manager:cleanup(session_id)
	if not session_id or session_id == "" then
		return
	end

	vim.schedule(function()
		local state = self._sessions[session_id]
		if not state then
			return
		end

		if state.diff_tab and vim.api.nvim_tabpage_is_valid(state.diff_tab) then
			close_diff_tab(state)
		end

		for _, data in pairs(state.file_data) do
			delete_buf(data.after_buf)
			delete_buf(data.turn_buf)
			delete_buf(data.session_buf)
		end

		self._sessions[session_id] = nil
		pcall(vim.api.nvim_del_augroup_by_name, self.opts.name .. "_" .. session_id)
	end)
end

function Manager:toggle()
	local session_id = vim.t[self.opts.diff_tab_var] or vim.t[self.opts.tab_var]
	if not session_id then
		vim.notify("No " .. self.opts.name .. " session in this tab", vim.log.levels.WARN)
		return
	end
	local state = self:get_state(session_id)
	if state.diff_tab and vim.api.nvim_tabpage_is_valid(state.diff_tab) then
		local current_tab = vim.api.nvim_get_current_tabpage()
		if current_tab == state.diff_tab then
			if state.work_tab and vim.api.nvim_tabpage_is_valid(state.work_tab) then
				vim.api.nvim_set_current_tabpage(state.work_tab)
			end
		else
			vim.api.nvim_set_current_tabpage(state.diff_tab)
			if state.left_win and vim.api.nvim_win_is_valid(state.left_win) then
				vim.api.nvim_set_current_win(state.left_win)
			end
		end
	else
		setup_diff_tab(self, state, session_id)
	end
end

function Manager:debug()
	local tab_sid = vim.t[self.opts.tab_var]
	local lines = { self.opts.name .. " debug:" }
	table.insert(lines, "  vim.t." .. self.opts.tab_var .. " = " .. vim.inspect(tab_sid))
	table.insert(lines, "  sessions:")
	for sid, state in pairs(self._sessions) do
		local match = (sid == tab_sid) and " (MATCH)" or ""
		table.insert(
			lines,
			string.format(
				"    %s%s: %d files, %d turn_files",
				tostring(sid),
				match,
				#state.files,
				#state.turn_files
			)
		)
	end
	if not next(self._sessions) then
		table.insert(lines, "    (none — add_file was never called)")
	end
	vim.notify(table.concat(lines, "\n"), vim.log.levels.INFO)
end

return M
