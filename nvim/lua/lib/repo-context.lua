local M = {}

local TAB_CONTEXT_VAR = "active_meta_repo_context"

local function path_exists(path)
	return path and vim.uv.fs_stat(path) ~= nil
end

local function directory_for(path)
	if not path or path == "" then
		return nil
	end
	local stat = vim.uv.fs_stat(path)
	if stat and stat.type == "directory" then
		return path
	end
	return vim.fs.dirname(path)
end

---@param path string?
---@param marker string
---@return string?
local function root_for(path, marker)
	local directory = directory_for(path)
	if not directory then
		return nil
	end
	return vim.fs.root(directory, marker)
end

---@param bufnr integer?
---@return table?
local function buffer_context(bufnr)
	bufnr = bufnr or 0
	if not vim.api.nvim_buf_is_valid(bufnr) then
		return nil
	end
	if vim.bo[bufnr].buftype ~= "" then
		return nil
	end

	local path = vim.api.nvim_buf_get_name(bufnr)
	local repo_root = root_for(path, ".hg")
	if not repo_root then
		return nil
	end

	return {
		repo_root = repo_root,
		workdir = directory_for(path) or repo_root,
	}
end

---@param repo_root string
---@param workdir string?
---@param tabpage integer?
function M.remember(repo_root, workdir, tabpage)
	if not root_for(repo_root, ".hg") then
		return
	end
	tabpage = tabpage or vim.api.nvim_get_current_tabpage()
	if not workdir then
		local ok, previous = pcall(vim.api.nvim_tabpage_get_var, tabpage, TAB_CONTEXT_VAR)
		if ok and previous.repo_root == repo_root then
			workdir = previous.workdir
		end
	end
	pcall(
		vim.api.nvim_tabpage_set_var,
		tabpage,
		TAB_CONTEXT_VAR,
		{ repo_root = repo_root, workdir = workdir or repo_root }
	)
end

local function remembered_context()
	local ok, context = pcall(vim.api.nvim_tabpage_get_var, vim.api.nvim_get_current_tabpage(), TAB_CONTEXT_VAR)
	if
		not ok
		or type(context) ~= "table"
		or type(context.repo_root) ~= "string"
		or not path_exists(vim.fs.joinpath(context.repo_root, ".hg"))
	then
		return nil
	end
	if root_for(context.workdir, ".hg") ~= context.repo_root then
		context.workdir = context.repo_root
	end
	return context
end

---@param bufnr integer?
---@return table?
function M.current(bufnr)
	local context = buffer_context(bufnr)
	if context then
		M.remember(context.repo_root, context.workdir)
		return context
	end
	if bufnr ~= nil then
		return nil
	end

	context = remembered_context()
	if context then
		return context
	end

	for _, winid in ipairs(vim.api.nvim_tabpage_list_wins(0)) do
		context = buffer_context(vim.api.nvim_win_get_buf(winid))
		if context then
			M.remember(context.repo_root, context.workdir)
			return context
		end
	end

	local cwd = vim.uv.cwd() or vim.fn.getcwd()
	local repo_root = root_for(cwd, ".hg")
	if repo_root then
		context = { repo_root = repo_root, workdir = cwd }
		M.remember(repo_root, cwd)
		return context
	end

	return nil
end

local function candidate_parent()
	local cwd = vim.uv.cwd() or vim.fn.getcwd()
	local repo_root = root_for(cwd, ".hg")
	return repo_root and vim.fs.dirname(repo_root) or cwd
end

---@return string[]
function M.candidates()
	local parent = candidate_parent()
	local candidates = {}
	local iterator = vim.fs.dir(parent)
	if not iterator then
		return candidates
	end

	for name, entry_type in iterator do
		if entry_type == "directory" then
			local path = vim.fs.joinpath(parent, name)
			if path_exists(vim.fs.joinpath(path, ".hg")) then
				table.insert(candidates, path)
			end
		end
	end
	table.sort(candidates)
	return candidates
end

---@param callback fun(context: table)
---@param opts? { force: boolean? }
function M.with_context(callback, opts)
	opts = opts or {}
	local context = not opts.force and M.current() or nil
	if context then
		callback(context)
		return
	end

	local candidates = M.candidates()
	if #candidates == 0 then
		vim.notify("No Sapling repository found for this workspace", vim.log.levels.ERROR)
		return
	end
	if #candidates == 1 then
		M.remember(candidates[1])
		callback({ repo_root = candidates[1], workdir = candidates[1] })
		return
	end

	vim.ui.select(candidates, {
		prompt = "Select repository:",
		format_item = function(path)
			return vim.fs.basename(path)
		end,
	}, function(repo_root)
		if repo_root then
			M.remember(repo_root)
			callback({ repo_root = repo_root, workdir = repo_root })
		end
	end)
end

---@param path string?
---@return string?
function M.repo_root(path)
	if path then
		return root_for(path, ".hg")
	end
	local context = M.current()
	return context and context.repo_root or nil
end

---@param path string?
---@return string?
function M.buck_root(path)
	if path then
		return root_for(path, ".buckconfig")
	end
	local context = M.current()
	return context and root_for(context.workdir, ".buckconfig") or nil
end

function M.setup()
	local group = vim.api.nvim_create_augroup("meta_repo_context", { clear = true })
	vim.api.nvim_create_autocmd({ "BufEnter", "BufFilePost" }, {
		group = group,
		callback = function(args)
			local context = buffer_context(args.buf)
			if context then
				M.remember(context.repo_root, context.workdir)
			end
		end,
	})

	vim.api.nvim_create_user_command("MetaRepo", function()
		M.with_context(function(context)
			vim.notify("Active repository: " .. context.repo_root, vim.log.levels.INFO)
		end, { force = true })
	end, { desc = "Select the active Meta repository for this tab" })
end

return M
