-- Recover Pyrefly's type errors, which fbcode hides from the IDE.
--
-- `fbcode/pyrefly.toml` sets `disable-type-errors-in-ide = true` repo-wide, so
-- the language server publishes only unused-symbol diagnostics -- its own log
-- says `Published 0 diagnostics` for a file with three deliberate type errors.
-- Type errors, parse errors and unknown names all travel that same suppressed
-- channel, which is why neither bad types nor broken syntax show up while
-- editing. The switch is per-config and only a nearer checked-in pyrefly.toml
-- can flip it (fbcode/instagram-server does exactly that), so there is no
-- personal setting for it: PYREFLY_CONFIG is honoured by `pyrefly check` but
-- ignored by `pyrefly lsp`, and `lsp` has no --config flag.
--
-- The same binary reports everything happily on the command line, so run it
-- there after each write and publish the results into our own diagnostic
-- namespace. Independent of the LSP client, so nothing shared changes.
--
-- Cost: ~1s for a small file, ~8s for a 4000-line one, measured. It runs async
-- on BufWritePost, never on the write path, so saving is not slowed.
--
-- Expect a backlog rather than just your own mistakes -- a committed, properly
-- targeted file showed 14 findings -- and expect `missing-import` noise on
-- files outside a real Buck target. That noise is why fbcode muted the channel.
-- :PyreflyCheckToggle turns it off for the session.

local M = {}

M.NS = vim.api.nvim_create_namespace("pyrefly-check")
M.BINARY_REL = "/fbcode/scripts/__dotslash_builder__/pyrefly-fbcode/current/pyrefly-fbcode"
M.TIMEOUT_MS = 120000
M.enabled = true

local running = {}
local warned = false

---@param path string
---@return string? root, string? binary
function M.resolve(path)
	if path == nil or path == "" then
		return nil, nil
	end
	local root = vim.fs.root(path, ".hg")
	if not root then
		return nil, nil
	end
	local binary = root .. M.BINARY_REL
	if vim.fn.executable(binary) ~= 1 then
		return root, nil
	end
	return root, binary
end

local SEVERITY = {
	ERROR = vim.diagnostic.severity.ERROR,
	WARN = vim.diagnostic.severity.WARN,
	WARNING = vim.diagnostic.severity.WARN,
	INFO = vim.diagnostic.severity.INFO,
}

-- `ERROR fbcode/a/b.py:30:1-27: Cannot find module `x` [missing-import]`
---@param output string
---@param root string
---@param target string absolute path of the buffer being checked
---@return vim.Diagnostic[]
function M.parse(output, root, target)
	local out = {}
	for line in (output or ""):gmatch("[^\n]+") do
		local level, file, lnum, col_start, col_end, message =
			line:match("^(%u+)%s+(.-):(%d+):(%d+)%-(%d+):%s*(.+)$")
		if level and SEVERITY[level] then
			local abs = file:sub(1, 1) == "/" and file or (root .. "/" .. file)
			if vim.fs.normalize(abs) == vim.fs.normalize(target) then
				local rule = message:match("%[([%w%-]+)%]%s*$")
				out[#out + 1] = {
					lnum = math.max(0, tonumber(lnum) - 1),
					col = math.max(0, tonumber(col_start) - 1),
					end_lnum = math.max(0, tonumber(lnum) - 1),
					end_col = math.max(0, tonumber(col_end)),
					severity = SEVERITY[level],
					source = "pyrefly-check",
					code = rule,
					message = message,
				}
			end
		end
	end
	return out
end

---@param bufnr? integer
---@param opts? { notify?: boolean }
function M.check(bufnr, opts)
	opts = opts or {}
	bufnr = (bufnr == nil or bufnr == 0) and vim.api.nvim_get_current_buf() or bufnr
	if not vim.api.nvim_buf_is_valid(bufnr) or vim.bo[bufnr].buftype ~= "" then
		return
	end
	local path = vim.api.nvim_buf_get_name(bufnr)
	local root, binary = M.resolve(path)
	if not root then
		return
	end
	if not binary then
		if opts.notify and not warned then
			warned = true
			vim.notify("pyrefly-check: binary not found at " .. root .. M.BINARY_REL, vim.log.levels.WARN)
		end
		return
	end
	-- One in flight per buffer; a later write supersedes nothing, it just waits
	-- for the next save rather than piling up Buck queries.
	if running[bufnr] then
		return
	end
	running[bufnr] = true

	vim.system(
		{ binary, "check", path, "--output-format", "min-text" },
		{ cwd = root, text = true, timeout = M.TIMEOUT_MS },
		vim.schedule_wrap(function(res)
			running[bufnr] = nil
			if not vim.api.nvim_buf_is_valid(bufnr) then
				return
			end
			local combined = (res.stdout or "") .. "\n" .. (res.stderr or "")
			local diags = M.parse(combined, root, path)
			vim.diagnostic.set(M.NS, bufnr, diags)
			if opts.notify then
				vim.notify(string.format("pyrefly-check: %d finding(s)", #diags), vim.log.levels.INFO)
			end
		end)
	)
end

function M.setup()
	vim.api.nvim_create_user_command("PyreflyCheck", function()
		M.check(0, { notify = true })
	end, { desc = "Type-check this buffer with pyrefly (bypasses the IDE suppression)" })

	vim.api.nvim_create_user_command("PyreflyCheckToggle", function()
		M.enabled = not M.enabled
		if not M.enabled then
			for _, b in ipairs(vim.api.nvim_list_bufs()) do
				if vim.api.nvim_buf_is_valid(b) then
					vim.diagnostic.reset(M.NS, b)
				end
			end
		end
		vim.notify("pyrefly-check auto-run " .. (M.enabled and "ON" or "OFF"))
	end, { desc = "Toggle automatic pyrefly type checking on save" })

	vim.api.nvim_create_autocmd("BufWritePost", {
		group = vim.api.nvim_create_augroup("pyrefly_check", { clear = true }),
		pattern = "*.py",
		desc = "Type-check with pyrefly after writing (see lib.pyrefly-check)",
		callback = function(args)
			if M.enabled then
				M.check(args.buf)
			end
		end,
	})
end

return M
