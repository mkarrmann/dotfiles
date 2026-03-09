local M = {}

local PAD_SUBDIR = "Pad"
local ARCHIVE_SUBDIR = "Archive/Pad"

---@class PadState
---@field win integer?
---@field last_file string?

---@type PadState
local state = {}

local function vault_dir()
	return vim.g.obsidian_vault or vim.fn.expand("~/obsidian")
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
	vim.fn.rename(file, dest)
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
	local file = state.last_file
	if not file then
		ensure_pad_dir()
		file = pad_dir() .. "/pad.md"
		if vim.fn.filereadable(file) == 0 then
			vim.fn.writefile({ "# Pad", "", "" }, file)
		end
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
	local dest = archive_file(file)
	vim.notify("Archived → " .. vim.fn.fnamemodify(dest, ":t"))
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
						archive_file(file)
						archived = archived + 1
						if file == current_buf_name then
							state.last_file = nil
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
						vim.fn.rename(file, dest)
						restored = restored + 1
						last_restored = dest
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
