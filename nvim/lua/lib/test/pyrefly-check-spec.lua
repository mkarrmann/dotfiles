-- Headless test for lib.pyrefly-check.
-- nvim --headless -u NONE --cmd "set rtp+=$HOME/dotfiles/nvim" -c "lua require('lib.test.pyrefly-check-spec').run()" -c "qa!"

local M = {}

local function assert_eq(actual, expected, label)
	if actual ~= expected then
		error(string.format("%s: expected %s, got %s", label, vim.inspect(expected), vim.inspect(actual)))
	end
end

local ROOT = "/repo"
local TARGET = "/repo/fbcode/a/b.py"

-- Real `pyrefly check --output-format min-text` shape, including the INFO
-- chatter it writes to stderr and a finding for a different file.
local OUTPUT = table.concat({
	" INFO Loading new build system at /repo/fbcode: Buck(...)",
	" INFO Querying Buck for source DB",
	"ERROR fbcode/a/b.py:30:1-27: Cannot find module `docutils` [missing-import]",
	"ERROR fbcode/a/b.py:4:12-24: `Literal['x']` is not assignable to `int` [bad-assignment]",
	"WARN fbcode/a/b.py:7:1-5: something milder [some-rule]",
	"ERROR fbcode/other/c.py:1:1-2: not our file [bad-return]",
	"ERROR /repo/fbcode/a/b.py:9:2-6: absolute path form [unknown-name]",
	"random text that is not a finding",
}, "\n")

function M.run()
	local ok, err = xpcall(function()
		package.loaded["lib.pyrefly-check"] = nil
		local pc = require("lib.pyrefly-check")

		local d = pc.parse(OUTPUT, ROOT, TARGET)
		assert_eq(#d, 4, "only findings for the target file, both path forms")

		-- 1-indexed CLI columns/lines -> 0-indexed diagnostics.
		assert_eq(d[1].lnum, 29, "line is zero-indexed")
		assert_eq(d[1].col, 0, "col is zero-indexed")
		assert_eq(d[1].end_col, 27, "end col")
		assert_eq(d[1].severity, vim.diagnostic.severity.ERROR, "ERROR maps to ERROR")
		assert_eq(d[1].code, "missing-import", "rule extracted from trailing brackets")
		assert_eq(d[1].source, "pyrefly-check", "source tagged so it is distinguishable from the LSP")

		assert_eq(d[3].severity, vim.diagnostic.severity.WARN, "WARN maps to WARN")
		assert_eq(d[4].lnum, 8, "absolute-path finding is matched too")

		-- INFO chatter and prose must not become diagnostics.
		for _, x in ipairs(d) do
			if x.message:match("^Loading") or x.message:match("^random text") then
				error("parsed non-finding output as a diagnostic: " .. x.message)
			end
		end

		-- No .hg above the path -> nothing to run.
		local root, binary = pc.resolve("/definitely/not/a/repo/x.py")
		assert_eq(root, nil, "no repo root -> nil")
		assert_eq(binary, nil, "no repo root -> no binary")
		assert_eq(select(1, pc.resolve("")), nil, "empty path is handled")

		-- Namespace is private, so a reset never clears LSP diagnostics.
		assert_eq(type(pc.NS), "number", "has its own diagnostic namespace")
		local lsp_ns = vim.api.nvim_create_namespace("some.lsp.namespace")
		assert_eq(pc.NS ~= lsp_ns, true, "namespace is distinct")
	end, debug.traceback)

	if not ok then
		error(err)
	end
	print("pyrefly-check-spec: all checks passed")
end

return M
