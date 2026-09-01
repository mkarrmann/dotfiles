-- Make linttool@meta the formatter for the buffers it serves.
--
-- meta.nvim's arclint null-ls formatter declines to run inside fbsource --
-- `if project_id == "fbsource" then return false end  -- let linttool IDE
-- handle this` (meta/null-ls/formatting/arclint.lua) -- and also declines on
-- any modified buffer, which is every buffer at BufWritePre. It nonetheless
-- registers for *all* filetypes (`filetypes = {}` means `_all` in null-ls),
-- and LazyVim's resolver inspects only registered sources, never
-- runtime_condition. So none-ls (primary, priority 200) claims the slot and
-- suppresses the LSP formatter (primary, priority 1) that would have worked.
--
-- Net effect in fbsource: <leader>cf and format-on-save silently do nothing,
-- for every filetype. none-ls registers its formatter at VeryLazy, which every
-- real session reaches, so this is consistent rather than intermittent.
--
-- Timing, for expectations: linttool takes ~2-3s, so an explicit :w pauses for
-- that long. Autosave (lib.autosave) does not pay it -- its autocmds are not
-- `nested`, so the `:update` it issues never fires BufWritePre and therefore
-- never formats. Editing stays cheap; formatting happens when you deliberately
-- save or press <leader>cf.
--
-- Registering linttool explicitly above none-ls makes the outcome
-- deterministic. Repos where linttool does not attach are untouched: in
-- configerator arclint legitimately runs (project_id ~= "fbsource"), no
-- linttool client attaches, sources() returns empty, and this formatter is
-- inert.

local M = {}

M.CLIENT = "linttool@meta"
-- Above none-ls (200) and conform (100); see lazyvim/plugins/extras/lsp/none-ls.lua.
M.PRIORITY = 300
M.TIMEOUT_MS = 10000

---@param buf integer
---@return string[]
function M.sources(buf)
	local names = {}
	for _, client in ipairs(vim.lsp.get_clients({ bufnr = buf, name = M.CLIENT })) do
		if client:supports_method("textDocument/formatting") then
			names[#names + 1] = client.name
		end
	end
	return names
end

---@return table
function M.formatter()
	return {
		name = M.CLIENT,
		primary = true,
		priority = M.PRIORITY,
		format = function(buf)
			vim.lsp.buf.format({ name = M.CLIENT, bufnr = buf, timeout_ms = M.TIMEOUT_MS })
		end,
		sources = M.sources,
	}
end

-- `register` is injectable so the spec can run without LazyVim loaded.
---@param register? fun(formatter: table)
---@return boolean registered
function M.setup(register)
	if type(register) ~= "function" then
		local lazyvim = rawget(_G, "LazyVim")
		register = lazyvim and lazyvim.format and lazyvim.format.register
	end
	if type(register) ~= "function" then
		return false
	end
	register(M.formatter())
	return true
end

return M
