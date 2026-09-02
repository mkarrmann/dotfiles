local M = {}

local PAD_SUBDIR = "Pad"
local ARCHIVE_SUBDIR = "Archive/Pad"
local LEGACY_ARCHIVE_SUBDIR = "Pad/Archive"
local NOTE_ID_PATTERN = "^note_[0-9a-f]+$"

---@class PadState
---@field win integer?
---@field last_file string?

---@type PadState
local state = {}

local function vault_dir()
	return require("lib.agent-session").resolve_vault_root()
end

local function pad_dir()
	return vault_dir() .. "/" .. PAD_SUBDIR
end

local function archive_dir()
	return vault_dir() .. "/" .. ARCHIVE_SUBDIR
end

local function ensure_pad_dir()
	vim.fn.mkdir(pad_dir(), "p")
end

local function ensure_archive_dir()
	vim.fn.mkdir(archive_dir(), "p")
end

local function notify_error(message)
	vim.notify(message, vim.log.levels.ERROR)
end

local function integration_url()
	local value = vim.env.ORCHEST_INTEGRATION_URL
	if not value or value == "" then
		return nil, "ORCHEST_INTEGRATION_URL is not configured"
	end
	return value:gsub("/+$", "")
end

local function editor_session_name()
	local value = vim.env.NVS_SESSION_NAME or vim.env.ORCHEST_NVIM_CONTEXT
	if not value or value == "" then
		return nil, "This Neovim instance has no configured Orchest editor context"
	end
	return value
end

local function post_json(path, body, callback)
	local base, base_error = integration_url()
	if not base then
		vim.schedule(function() callback(nil, base_error) end)
		return
	end
	vim.system({
		"curl",
		"--silent",
		"--show-error",
		"--noproxy",
		"*",
		"--connect-timeout",
		"1",
		"--max-time",
		"5",
		"-H",
		"Content-Type: application/json",
		"--data-binary",
		vim.json.encode(body),
		base .. path,
	}, { text = true }, function(result)
		vim.schedule(function()
			if result.code ~= 0 then
				callback(nil, (result.stderr or ""):gsub("%s+$", ""))
				return
			end
			local ok, response = pcall(vim.json.decode, result.stdout or "")
			if not ok or type(response) ~= "table" then
				callback(nil, "Orchest returned an invalid JSON response")
				return
			end
			if response.ok ~= true then
				local message = response.error and response.error.message or "Orchest rejected the request"
				callback(nil, message, response)
				return
			end
			callback(response, nil)
		end)
	end)
end

local function generate_note_id()
	local bytes, err = vim.uv.random(16)
	if not bytes then
		return nil, "Could not generate note ID: " .. tostring(err)
	end
	return "note_" .. (bytes:gsub(".", function(char) return string.format("%02x", string.byte(char)) end))
end

local function valid_note_id(value)
	return type(value) == "string" and #value == 37 and value:match(NOTE_ID_PATTERN) ~= nil
end

local function read_note_id(file)
	if vim.fn.filereadable(file) ~= 1 then
		return nil, "Note does not exist"
	end
	local lines = vim.fn.readfile(file)
	if lines[1] ~= "---" then
		return nil
	end
	local closing = nil
	local ids = {}
	for i = 2, #lines do
		if lines[i] == "---" then
			closing = i
			break
		end
		local value = lines[i]:match("^orchest_note_id:%s*(%S+)%s*$")
		if value then table.insert(ids, value) end
	end
	if not closing then
		return nil, "Malformed YAML frontmatter: missing closing ---"
	end
	if #ids > 1 then
		return nil, "Malformed YAML frontmatter: duplicate orchest_note_id"
	end
	if #ids == 1 and not valid_note_id(ids[1]) then
		return nil, "Malformed orchest_note_id"
	end
	return ids[1]
end

local function ensure_note_id(file)
	local existing, read_error = read_note_id(file)
	if read_error then return nil, read_error end
	if existing then return existing end
	local note_id, id_error = generate_note_id()
	if not note_id then return nil, id_error end
	local lines = vim.fn.readfile(file)
	if lines[1] == "---" then
		table.insert(lines, 2, "orchest_note_id: " .. note_id)
	else
		local with_frontmatter = { "---", "orchest_note_id: " .. note_id, "---", "" }
		vim.list_extend(with_frontmatter, lines)
		lines = with_frontmatter
	end
	if vim.fn.writefile(lines, file) ~= 0 then
		return nil, "Could not write note frontmatter"
	end
	return note_id
end

local function relative_note_path(file)
	local vault = vim.uv.fs_realpath(vault_dir()) or vim.fs.normalize(vault_dir())
	local target = vim.uv.fs_realpath(file) or vim.fs.normalize(file)
	local relative = vim.fs.relpath(vault, target)
	if not relative or relative == "" or relative == ".." or relative:match("^%.%./") then
		return nil, "Note is outside the configured vault"
	end
	return relative
end

local function list_note_files()
	local roots = { pad_dir(), archive_dir(), vault_dir() .. "/" .. LEGACY_ARCHIVE_SUBDIR }
	local seen = {}
	local files = {}
	for _, root in ipairs(roots) do
		for _, file in ipairs(vim.fn.glob(root .. "/**/*.md", false, true)) do
			if not seen[file] then
				seen[file] = true
				table.insert(files, file)
			end
		end
	end
	return files
end

local function find_note_by_id(note_id)
	if not valid_note_id(note_id) then return nil, "Invalid note ID" end
	local matches = {}
	for _, file in ipairs(list_note_files()) do
		if relative_note_path(file) then
			local candidate, err = read_note_id(file)
			if err then return nil, file .. ": " .. err end
			if candidate == note_id then table.insert(matches, file) end
		end
	end
	if #matches == 0 then return nil, "No note found for " .. note_id end
	if #matches > 1 then return nil, "Multiple notes have ID " .. note_id end
	return matches[1]
end

local function current_note_file()
	local file = vim.api.nvim_buf_get_name(0)
	if file == "" then return nil, "Current buffer is not a file" end
	local _, relative_error = relative_note_path(file)
	if relative_error then return nil, "Current buffer is outside the configured vault" end
	return file
end

local function update_note_location(file)
	local note_id, id_error = read_note_id(file)
	if id_error then
		notify_error(id_error)
		return
	end
	if not note_id then return end
	local relative, relative_error = relative_note_path(file)
	if not relative then
		notify_error(relative_error)
		return
	end
	post_json("/api/plugins/obsidian-notes/update", { noteId = note_id, relativePath = relative }, function(_, err)
		if err then notify_error("Note moved, but Orchest was not updated: " .. err) end
	end)
end

local function normalize_name(name)
	local normalized = name:lower():gsub("%s+", "-"):gsub("[^%w%-_]", "")
	if not normalized:match("%.md$") then
		normalized = normalized .. ".md"
	end
	return normalized
end

local function setup_pad_win(win)
	vim.wo[win].winfixwidth = true
	vim.wo[win].wrap = true
	vim.wo[win].linebreak = true
	vim.wo[win].spell = true
	vim.wo[win].number = false
	vim.wo[win].relativenumber = false
	vim.wo[win].signcolumn = "no"
end

local function open_panel(file)
	vim.cmd("topleft vsplit " .. vim.fn.fnameescape(file))
	state.win = vim.api.nvim_get_current_win()
	vim.api.nvim_win_set_width(state.win, math.floor(vim.o.columns * 0.30))
	setup_pad_win(state.win)
	state.last_file = file
end

---@return string[] Active pad note paths (excludes archive/)
local function list_active_pads()
	local dir = pad_dir()
	local files = vim.fn.glob(dir .. "/*.md", false, true)
	table.sort(files, function(a, b)
		return vim.fn.getftime(a) > vim.fn.getftime(b)
	end)
	return files
end

---@param file string
local function archive_file(file)
	ensure_archive_dir()
	local basename = vim.fn.fnamemodify(file, ":t")
	local dest = archive_dir() .. "/" .. basename
	if vim.fn.filereadable(dest) == 1 then
		local stem = vim.fn.fnamemodify(basename, ":r")
		local timestamp = os.date("%Y%m%d-%H%M%S")
		dest = archive_dir() .. "/" .. stem .. "-" .. timestamp .. ".md"
	end
	if vim.fn.rename(file, dest) ~= 0 then
		return nil, "Could not archive " .. file
	end
	return dest
end

function M.open_in_panel(file)
	if state.win and vim.api.nvim_win_is_valid(state.win) then
		vim.api.nvim_set_current_win(state.win)
		vim.cmd("edit " .. vim.fn.fnameescape(file))
		state.last_file = file
	else
		open_panel(file)
	end
end

function M.toggle()
	if state.win and vim.api.nvim_win_is_valid(state.win) then
		vim.api.nvim_win_close(state.win, false)
		state.win = nil
		return
	end
	-- If a Claude session is active, open its pad note
	local agent = require("lib.agent-session").get_current_agent()
	if agent and agent.name ~= "" then
		local name = agent.name:gsub("^qq:%s*", "")
		local file = pad_dir() .. "/" .. normalize_name(name)
		if vim.fn.filereadable(file) == 1 then
			M.open_in_panel(file)
		else
			vim.ui.input({ prompt = "New pad note: ", default = name }, function(input)
				if input and input ~= "" then
					M.open(input)
				end
			end)
		end
		return
	end
	local file = state.last_file
	if not file then
		M.open()
		return
	end
	open_panel(file)
end

function M.open(name)
	if not name or name == "" then
		vim.ui.input({ prompt = "Pad note: " }, function(input)
			if input and input ~= "" then
				M.open(input)
			end
		end)
		return
	end
	ensure_pad_dir()
	local filename = normalize_name(name)
	local file = pad_dir() .. "/" .. filename
	if vim.fn.filereadable(file) == 0 then
		vim.fn.writefile({ "# " .. name, "", "" }, file)
	end
	M.open_in_panel(file)
end

function M.open_by_id(note_id)
	local file, err = find_note_by_id(note_id)
	if not file then
		notify_error(err)
		return false
	end
	M.open_in_panel(file)
	return true
end

local function create_or_open_attached_note(name, token)
	ensure_pad_dir()
	local file = pad_dir() .. "/" .. normalize_name(name)
	if vim.fn.filereadable(file) == 0 then
		if vim.fn.writefile({ "# " .. name, "", "" }, file) ~= 0 then
			notify_error("Could not create " .. file)
			return
		end
	end
	local note_id, id_error = ensure_note_id(file)
	if not note_id then
		notify_error("Created/opened note, but could not attach it: " .. id_error)
		M.open_in_panel(file)
		return
	end
	local relative, relative_error = relative_note_path(file)
	if not relative then
		notify_error("Created/opened note, but could not attach it: " .. relative_error)
		M.open_in_panel(file)
		return
	end
	M.open_in_panel(file)

	local payload = { token = token, noteId = note_id, relativePath = relative }
	local function commit()
		post_json("/api/plugins/obsidian-notes/commit", payload, function(response, err)
			if response then
				vim.notify(string.format("Attached %s to “%s”", relative, response.taskTitle))
				return
			end
			vim.ui.select({ "Keep unattached", "Retry attachment" }, {
				prompt = "Created " .. relative .. ", but did not attach it: " .. tostring(err),
			}, function(choice)
				if choice == "Retry attachment" then commit() end
			end)
		end)
	end
	commit()
end

function M.open_for_active_task()
	local session_name, context_error = editor_session_name()
	if not session_name then
		notify_error(context_error)
		return
	end
	post_json(
		"/api/plugins/obsidian-notes/prepare",
		{ editorContext = { kind = "nvs", sessionName = session_name } },
		function(response, err)
			if not response then
				vim.ui.select({ "Cancel", "Create unattached note" }, {
					prompt = "Could not associate a note with Orchest: " .. tostring(err),
				}, function(choice)
					if choice == "Create unattached note" then M.open() end
				end)
				return
			end
			if response.kind == "existing" then
				if response.nvimSessionName == session_name then
					M.open_by_id(response.noteId)
					return
				end
				post_json("/api/commands", {
					type = "invokePluginAction",
					pluginId = "obsidian-notes",
					actionId = "open-note",
					taskId = response.taskId,
				}, function(_, open_error)
					if open_error then notify_error("Could not open associated note: " .. open_error) end
				end)
				return
			end
			if response.kind ~= "create" or type(response.token) ~= "string" then
				notify_error("Orchest returned an invalid prepare response")
				return
			end
			vim.ui.input({ prompt = "Pad note for “" .. response.taskTitle .. "”: ", default = response.taskTitle }, function(input)
				if input and input ~= "" then create_or_open_attached_note(input, response.token) end
			end)
		end
	)
end

function M.focus_linked_task()
	local file, file_error = current_note_file()
	if not file then
		notify_error(file_error)
		return
	end
	local note_id, id_error = read_note_id(file)
	if not note_id then
		notify_error(id_error or "Current note is not associated with Orchest")
		return
	end
	post_json("/api/plugins/obsidian-notes/lookup-task", { noteId = note_id }, function(response, err)
		if not response then
			notify_error("Could not find linked task: " .. tostring(err))
			return
		end
		post_json("/api/commands", { type = "focusTask", taskId = response.taskId }, function(_, focus_error)
			if focus_error then
				notify_error("Could not focus linked task: " .. focus_error)
			else
				vim.notify("Focused Orchest task “" .. response.taskTitle .. "”")
			end
		end)
	end)
end

function M.unlink_current()
	local file, file_error = current_note_file()
	if not file then
		notify_error(file_error)
		return
	end
	local note_id, id_error = read_note_id(file)
	if not note_id then
		notify_error(id_error or "Current note is not associated with Orchest")
		return
	end
	post_json("/api/plugins/obsidian-notes/unlink", { noteId = note_id }, function(response, err)
		if not response then
			notify_error("Could not unlink note: " .. tostring(err))
			return
		end
		vim.notify("Unlinked note from Orchest task “" .. response.taskTitle .. "”")
	end)
end

function M.sync_current()
	local file, file_error = current_note_file()
	if not file then
		notify_error(file_error)
		return
	end
	update_note_location(file)
end

M.read_note_id = read_note_id
M.ensure_note_id = ensure_note_id
M.find_note_by_id = find_note_by_id
M.relative_note_path = relative_note_path

function M.open_for_session(session_name)
	if not session_name or session_name == "" then
		M.toggle()
		return
	end
	local name = session_name:gsub("^qq:%s*", "")
	M.open(name)
end

function M.rename_pad(old_name, new_name)
	if not old_name or old_name == "" or not new_name or new_name == "" then
		return
	end
	local old_file = pad_dir() .. "/" .. normalize_name(old_name)
	if vim.fn.filereadable(old_file) == 0 then
		return
	end
	ensure_pad_dir()
	local new_file = pad_dir() .. "/" .. normalize_name(new_name)
	if vim.fn.rename(old_file, new_file) ~= 0 then
		notify_error("Could not rename " .. old_file)
		return
	end
	-- Update panel if the old file was displayed
	if state.last_file == old_file then
		state.last_file = new_file
		if state.win and vim.api.nvim_win_is_valid(state.win) then
			local buf = vim.api.nvim_win_get_buf(state.win)
			if vim.api.nvim_buf_get_name(buf) == old_file then
				vim.api.nvim_set_current_win(state.win)
				vim.cmd("edit " .. vim.fn.fnameescape(new_file))
				vim.api.nvim_buf_delete(buf, { force = true })
			end
		end
	end
	update_note_location(new_file)
end

function M.find()
	local ok, builtin = pcall(require, "telescope.builtin")
	if not ok then
		vim.notify("Telescope is required", vim.log.levels.ERROR)
		return
	end
	ensure_pad_dir()
	builtin.find_files({
		prompt_title = "Pad Notes",
		cwd = pad_dir(),
		search_dirs = { pad_dir() },
		find_command = { "find", pad_dir(), "-maxdepth", "1", "-name", "*.md", "-type", "f" },
		attach_mappings = function(_, _)
			local actions = require("telescope.actions")
			local action_state = require("telescope.actions.state")
			actions.select_default:replace(function(prompt_bufnr)
				actions.close(prompt_bufnr)
				local entry = action_state.get_selected_entry()
				if entry then
					local path = entry.path or (pad_dir() .. "/" .. entry[1])
					M.open_in_panel(path)
				end
			end)
			return true
		end,
	})
end

function M.archive_current()
	if not (state.win and vim.api.nvim_win_is_valid(state.win)) then
		vim.notify("No pad panel open", vim.log.levels.WARN)
		return
	end
	local buf = vim.api.nvim_win_get_buf(state.win)
	local file = vim.api.nvim_buf_get_name(buf)
	if file == "" or not file:find(pad_dir(), 1, true) or file:find(archive_dir(), 1, true) then
		vim.notify("Current buffer is not an active pad note", vim.log.levels.WARN)
		return
	end
	local dest, archive_error = archive_file(file)
	if not dest then
		notify_error(archive_error)
		return
	end
	vim.notify("Archived → " .. vim.fn.fnamemodify(dest, ":t"))
	update_note_location(dest)
	-- Switch panel to next note before wiping, so the window stays alive
	local remaining = list_active_pads()
	if #remaining > 0 then
		vim.api.nvim_set_current_win(state.win)
		vim.cmd("edit " .. vim.fn.fnameescape(remaining[1]))
		state.last_file = remaining[1]
	else
		state.last_file = nil
		vim.api.nvim_win_close(state.win, true)
		state.win = nil
	end
	vim.api.nvim_buf_delete(buf, { force = true })
end

function M.archive_bulk()
	local ok, pickers = pcall(require, "telescope.pickers")
	if not ok then
		vim.notify("Telescope is required", vim.log.levels.ERROR)
		return
	end
	local finders = require("telescope.finders")
	local conf = require("telescope.config").values
	local actions = require("telescope.actions")
	local action_state = require("telescope.actions.state")

	local files = list_active_pads()
	if #files == 0 then
		vim.notify("No active pad notes to archive", vim.log.levels.INFO)
		return
	end

	local entries = {}
	for _, f in ipairs(files) do
		table.insert(entries, { display = vim.fn.fnamemodify(f, ":t:r"), path = f })
	end

	pickers.new({}, {
		prompt_title = "Archive Pad Notes (Tab to select, Enter to confirm)",
		finder = finders.new_table({
			results = entries,
			entry_maker = function(entry)
				return {
					value = entry,
					display = entry.display,
					ordinal = entry.display,
					path = entry.path,
				}
			end,
		}),
		sorter = conf.generic_sorter({}),
		attach_mappings = function(prompt_bufnr, map)
			actions.select_default:replace(function()
				local picker = action_state.get_current_picker(prompt_bufnr)
				local selections = picker:get_multi_selection()
				if #selections == 0 then
					local entry = action_state.get_selected_entry()
					if entry then
						selections = { entry }
					end
				end
				actions.close(prompt_bufnr)
				if #selections == 0 then
					return
				end
				local current_buf_name = ""
				if state.win and vim.api.nvim_win_is_valid(state.win) then
					current_buf_name = vim.api.nvim_buf_get_name(vim.api.nvim_win_get_buf(state.win))
				end
				local archived = 0
				for _, sel in ipairs(selections) do
					local file = sel.value.path
					if vim.fn.filereadable(file) == 1 then
						local bufnr = vim.fn.bufnr(file)
						if bufnr ~= -1 then
							vim.api.nvim_buf_delete(bufnr, { force = true })
						end
						local dest, archive_error = archive_file(file)
						if dest then
							archived = archived + 1
							update_note_location(dest)
							if file == current_buf_name then state.last_file = nil end
						else
							notify_error(archive_error)
						end
					end
				end
				vim.notify("Archived " .. archived .. " note" .. (archived == 1 and "" or "s"))
				if state.win and vim.api.nvim_win_is_valid(state.win) and state.last_file == nil then
					local remaining = list_active_pads()
					if #remaining > 0 then
						M.open_in_panel(remaining[1])
					end
				end
			end)
			return true
		end,
	}):find()
end

function M.unarchive()
	local ok, pickers = pcall(require, "telescope.pickers")
	if not ok then
		vim.notify("Telescope is required", vim.log.levels.ERROR)
		return
	end
	local finders = require("telescope.finders")
	local conf = require("telescope.config").values
	local actions = require("telescope.actions")
	local action_state = require("telescope.actions.state")

	ensure_archive_dir()
	local files = vim.fn.glob(archive_dir() .. "/*.md", false, true)
	if #files == 0 then
		vim.notify("No archived pad notes", vim.log.levels.INFO)
		return
	end
	table.sort(files, function(a, b)
		return vim.fn.getftime(a) > vim.fn.getftime(b)
	end)

	local entries = {}
	for _, f in ipairs(files) do
		table.insert(entries, { display = vim.fn.fnamemodify(f, ":t:r"), path = f })
	end

	pickers.new({}, {
		prompt_title = "Unarchive Pad Notes (Tab to select, Enter to confirm)",
		finder = finders.new_table({
			results = entries,
			entry_maker = function(entry)
				return {
					value = entry,
					display = entry.display,
					ordinal = entry.display,
					path = entry.path,
				}
			end,
		}),
		sorter = conf.generic_sorter({}),
		attach_mappings = function(prompt_bufnr, map)
			actions.select_default:replace(function()
				local picker = action_state.get_current_picker(prompt_bufnr)
				local selections = picker:get_multi_selection()
				if #selections == 0 then
					local entry = action_state.get_selected_entry()
					if entry then
						selections = { entry }
					end
				end
				actions.close(prompt_bufnr)
				if #selections == 0 then
					return
				end
				ensure_pad_dir()
				local restored = 0
				local last_restored = nil
				for _, sel in ipairs(selections) do
					local file = sel.value.path
					if vim.fn.filereadable(file) == 1 then
						local basename = vim.fn.fnamemodify(file, ":t")
						local dest = pad_dir() .. "/" .. basename
						if vim.fn.filereadable(dest) == 1 then
							local stem = vim.fn.fnamemodify(basename, ":r")
							local timestamp = os.date("%Y%m%d-%H%M%S")
							dest = pad_dir() .. "/" .. stem .. "-" .. timestamp .. ".md"
						end
						if vim.fn.rename(file, dest) == 0 then
							restored = restored + 1
							last_restored = dest
							update_note_location(dest)
						else
							notify_error("Could not restore " .. file)
						end
					end
				end
				vim.notify("Restored " .. restored .. " note" .. (restored == 1 and "" or "s"))
				if restored == 1 and last_restored then
					M.open_in_panel(last_restored)
				end
			end)
			return true
		end,
	}):find()
end

vim.api.nvim_create_autocmd("WinClosed", {
	group = vim.api.nvim_create_augroup("obsidian_pad", { clear = true }),
	callback = function(ev)
		local closed = tonumber(ev.match)
		if state.win and closed == state.win then
			state.win = nil
		end
	end,
})

return M
