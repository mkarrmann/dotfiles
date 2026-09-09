-- Reveal agent-referenced source locations in this Neovim instance.
--
-- bin/nvim-show resolves a target nvs session, writes a JSON payload, and calls
-- dispatch_file over RPC. Every calling agent owns exactly one tabpage, keyed by
-- its harness session id, so two agents showing code concurrently never fight
-- over the same view. For the same reason the list is a window-local location
-- list rather than the global quickfix list, and annotations are extmarks in a
-- per-agent namespace: one agent's output is never clobbered or cleared by
-- another's.

local M = {}

local AGENT_VAR = "show_in_nvim_agent"
local NS_PREFIX = "show-in-nvim:"
local VIRT_HL = "DiagnosticVirtualTextInfo"

local function fail(fmt, ...)
	return nil, string.format(fmt, ...)
end

local function agent_ns(agent)
	return vim.api.nvim_create_namespace(NS_PREFIX .. agent)
end

local function find_tab(agent)
	for _, tab in ipairs(vim.api.nvim_list_tabpages()) do
		local ok, value = pcall(vim.api.nvim_tabpage_get_var, tab, AGENT_VAR)
		if ok and value == agent then
			return tab
		end
	end
	return nil
end

-- The window in `tab` that source should land in — never the location-list
-- window, which `:lopen` adds to the same tabpage.
local function content_win(tab)
	for _, win in ipairs(vim.api.nvim_tabpage_list_wins(tab)) do
		local buftype = vim.bo[vim.api.nvim_win_get_buf(win)].buftype
		if buftype ~= "quickfix" and buftype ~= "prompt" then
			return win
		end
	end
	return vim.api.nvim_tabpage_get_win(tab)
end

local function ensure_tab(agent, title)
	local tab = find_tab(agent)
	if tab then
		return tab
	end
	vim.cmd("tabnew")
	tab = vim.api.nvim_get_current_tabpage()
	vim.api.nvim_tabpage_set_var(tab, AGENT_VAR, agent)
	vim.api.nvim_tabpage_set_var(tab, "tab_name", title)
	-- A new window inherits the location list of the one it was opened from, so
	-- without this a second agent starts out holding the first agent's results.
	vim.fn.setloclist(vim.api.nvim_tabpage_get_win(tab), {}, "f")
	return tab
end

local function normalize_items(items)
	if type(items) ~= "table" then
		return fail("payload.items must be a list")
	end
	local out = {}
	for index, item in ipairs(items) do
		if type(item) ~= "table" or type(item.file) ~= "string" or item.file == "" then
			return fail("item %d has no file", index)
		end
		local path = vim.fn.fnamemodify(item.file, ":p")
		if vim.fn.filereadable(path) ~= 1 then
			return fail("file does not exist: %s", path)
		end
		out[#out + 1] = {
			file = path,
			line = math.max(1, math.floor(tonumber(item.line) or 1)),
			col = math.max(1, math.floor(tonumber(item.col) or 1)),
			text = type(item.text) == "string" and item.text ~= "" and item.text or nil,
		}
	end
	if #out == 0 then
		return fail("payload.items is empty")
	end
	return out
end

local function clear_annotations(ns)
	for _, buf in ipairs(vim.api.nvim_list_bufs()) do
		if vim.api.nvim_buf_is_loaded(buf) then
			vim.api.nvim_buf_clear_namespace(buf, ns, 0, -1)
		end
	end
end

local function annotate(ns, item)
	if not item.text then
		return
	end
	local buf = vim.fn.bufadd(item.file)
	vim.fn.bufload(buf)
	local row = math.min(item.line, vim.api.nvim_buf_line_count(buf)) - 1
	vim.api.nvim_buf_set_extmark(buf, ns, row, 0, {
		virt_text = { { "▍ " .. item.text, VIRT_HL } },
		virt_text_pos = "eol",
		hl_mode = "combine",
	})
end

local function reveal(win, item)
	vim.api.nvim_win_call(win, function()
		vim.cmd.edit(vim.fn.fnameescape(item.file))
		local line = math.min(item.line, vim.api.nvim_buf_line_count(0))
		vim.api.nvim_win_set_cursor(win, { line, item.col - 1 })
		vim.cmd("normal! zz")
	end)
end

---Apply one nvim-show request.
---@param payload table {agent, action?, title?, jump?, items?}
---@return string result "ok", or "error: <reason>"
function M.dispatch(payload)
	if type(payload) ~= "table" then
		return "error: payload must be an object"
	end
	local agent = payload.agent
	if type(agent) ~= "string" or agent == "" then
		return "error: payload.agent is required"
	end

	local action = payload.action or "show"
	local ns = agent_ns(agent)
	local previous_tab = vim.api.nvim_get_current_tabpage()

	if action == "clear" then
		clear_annotations(ns)
		local tab = find_tab(agent)
		if tab and vim.api.nvim_tabpage_is_valid(tab) then
			local win = content_win(tab)
			vim.fn.setloclist(win, {}, "r")
			vim.api.nvim_win_call(win, function()
				vim.cmd("lclose")
			end)
		end
		return "ok"
	end

	if action ~= "show" and action ~= "list" then
		return "error: unknown action: " .. tostring(action)
	end

	local items, err = normalize_items(payload.items)
	if not items then
		return "error: " .. err
	end
	if action == "show" and #items ~= 1 then
		return "error: show takes exactly one item"
	end

	local title = payload.title
	if type(title) ~= "string" or title == "" then
		title = "show:" .. agent:sub(1, 8)
	end

	local tab = ensure_tab(agent, title)
	local win = content_win(tab)

	-- "list" is a complete result set, so it supersedes this agent's previous
	-- annotations as well as its previous list.
	if action == "list" then
		clear_annotations(ns)
	end

	local entries = {}
	for _, item in ipairs(items) do
		entries[#entries + 1] = {
			filename = item.file,
			lnum = item.line,
			col = item.col,
			text = item.text or "",
		}
		annotate(ns, item)
	end

	reveal(win, items[1])
	-- "show" appends so repeated single jumps accumulate into a history the
	-- location list can walk; "list" replaces, since it is a complete result set.
	vim.fn.setloclist(win, entries, action == "show" and "a" or "r")
	-- setloclist() rejects a list and a {what} in the same call, so the title is
	-- a second, entry-free append onto the list just written.
	vim.fn.setloclist(win, {}, "a", { title = title })

	-- A result set is only useful if its size is visible, so show the list
	-- window. A single jump does not: one location needs no index.
	if action == "list" then
		vim.api.nvim_win_call(win, function()
			vim.cmd("lopen " .. math.min(#entries, 10))
		end)
		vim.api.nvim_set_current_win(win)
	end

	if payload.jump == false then
		if vim.api.nvim_tabpage_is_valid(previous_tab) then
			vim.api.nvim_set_current_tabpage(previous_tab)
		end
	else
		vim.api.nvim_set_current_tabpage(tab)
		vim.api.nvim_set_current_win(win)
	end

	return "ok"
end

---RPC entry point. Reads a JSON payload from `path` and applies it.
---Never raises: bin/nvim-show reads the returned string to decide its exit code.
---@param path string
---@return string result "ok", or "error: <reason>"
function M.dispatch_file(path)
	local readable, contents = pcall(vim.fn.readfile, path)
	if not readable then
		return "error: cannot read payload: " .. tostring(path)
	end
	local decoded, payload = pcall(vim.json.decode, table.concat(contents, "\n"))
	if not decoded then
		return "error: payload is not valid JSON"
	end
	local called, result = pcall(M.dispatch, payload)
	if not called then
		return "error: " .. tostring(result)
	end
	return result
end

return M
