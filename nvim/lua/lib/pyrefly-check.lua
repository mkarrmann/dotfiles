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
local pending = {}
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
	error = vim.diagnostic.severity.ERROR,
	warn = vim.diagnostic.severity.WARN,
	warning = vim.diagnostic.severity.WARN,
	info = vim.diagnostic.severity.INFO,
	hint = vim.diagnostic.severity.HINT,
}

-- `--output-format json` on stdout:
--   {"errors":[{"line","column","stop_line","stop_column","path","name",
--               "description","severity"}, ...]}
-- Positions are 1-based with an exclusive stop; Neovim wants 0-based.
---@param stdout string
---@param root string
---@param target string absolute path of the buffer being checked
---@return vim.Diagnostic[]
function M.parse(stdout, root, target)
	if stdout == nil or stdout == "" then
		return {}
	end
	local ok, decoded = pcall(vim.json.decode, stdout)
	if not ok or type(decoded) ~= "table" or type(decoded.errors) ~= "table" then
		return {}
	end

	local out = {}
	for _, e in ipairs(decoded.errors) do
		local path = e.path
		if type(path) == "string" then
			local abs = path:sub(1, 1) == "/" and path or (root .. "/" .. path)
			if vim.fs.normalize(abs) == vim.fs.normalize(target) then
				out[#out + 1] = {
					lnum = math.max(0, (tonumber(e.line) or 1) - 1),
					col = math.max(0, (tonumber(e.column) or 1) - 1),
					end_lnum = math.max(0, (tonumber(e.stop_line) or e.line or 1) - 1),
					end_col = math.max(0, (tonumber(e.stop_column) or 1) - 1),
					severity = SEVERITY[tostring(e.severity):lower()] or vim.diagnostic.severity.ERROR,
					source = "pyrefly-check",
					code = e.name,
					message = e.description or e.concise_description or "pyrefly error",
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
	-- One check in flight per buffer, so rapid saves don't pile up Buck queries.
	-- A save that arrives mid-check is remembered rather than dropped: dropping
	-- it would leave the previous run's diagnostics on screen with no indication
	-- they describe superseded content.
	if running[bufnr] then
		pending[bufnr] = true
		return
	end
	running[bufnr] = true

	vim.system(
		{ binary, "check", path, "--output-format", "json" },
		{ cwd = root, text = true, timeout = M.TIMEOUT_MS },
		vim.schedule_wrap(function(res)
			running[bufnr] = nil
			if not vim.api.nvim_buf_is_valid(bufnr) then
				pending[bufnr] = nil
				return
			end
			-- JSON goes to stdout; the binary's INFO chatter goes to stderr
			-- and must not be fed to the decoder.
			local diags = M.parse(res.stdout, root, path)
			vim.diagnostic.set(M.NS, bufnr, diags)
			if opts.notify then
				vim.notify(string.format("pyrefly-check: %d finding(s)", #diags), vim.log.levels.INFO)
			end
			if pending[bufnr] then
				pending[bufnr] = nil
				M.check(bufnr, opts)
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
