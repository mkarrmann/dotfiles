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

-- Verbatim shape of `pyrefly check --output-format json` on stdout.
local OUTPUT = vim.json.encode({
	errors = {
		{
			line = 30,
			column = 1,
			stop_line = 30,
			stop_column = 27,
			path = "fbcode/a/b.py",
			code = -2,
			name = "missing-import",
			description = "Cannot find module `docutils`",
			concise_description = "Cannot find module `docutils`",
			severity = "error",
		},
		{
			-- Multi-line span: a half-typed line produces stop_line > line.
			line = 12,
			column = 13,
			stop_line = 13,
			stop_column = 1,
			path = "fbcode/a/b.py",
			name = "parse-error",
			description = "Parse error: Expected `)`, found newline",
			severity = "error",
		},
		{
			line = 7,
			column = 1,
			stop_line = 7,
			stop_column = 5,
			path = "/repo/fbcode/a/b.py", -- absolute path form
			name = "some-rule",
			description = "something milder",
			severity = "warn",
		},
		{
			line = 1,
			column = 1,
			stop_line = 1,
			stop_column = 2,
			path = "fbcode/other/c.py", -- different file
			name = "bad-return",
			description = "not our file",
			severity = "error",
		},
	},
})

function M.run()
	local ok, err = xpcall(function()
		package.loaded["lib.pyrefly-check"] = nil
		local pc = require("lib.pyrefly-check")

		local d = pc.parse(OUTPUT, ROOT, TARGET)
		assert_eq(#d, 3, "only findings for the target file, both path forms")

		-- 1-based, exclusive stop -> 0-based.
		assert_eq(d[1].lnum, 29, "line is zero-indexed")
		assert_eq(d[1].col, 0, "column is zero-indexed")
		assert_eq(d[1].end_lnum, 29, "single-line span ends on the same line")
		assert_eq(d[1].end_col, 26, "exclusive stop_column is converted")
		assert_eq(d[1].severity, vim.diagnostic.severity.ERROR, "severity maps")
		assert_eq(d[1].code, "missing-import", "rule comes from `name`")
		assert_eq(d[1].message, "Cannot find module `docutils`", "message needs no cleanup")
		assert_eq(d[1].source, "pyrefly-check", "tagged so it is distinct from the LSP")

		-- The case the hand-rolled text parser got wrong.
		assert_eq(d[2].lnum, 11, "multi-line span start line")
		assert_eq(d[2].col, 12, "multi-line span start column")
		assert_eq(d[2].end_lnum, 12, "multi-line span end line")
		assert_eq(d[2].end_col, 0, "multi-line span end column")
		assert_eq(d[2].code, "parse-error", "parse errors are surfaced")

		assert_eq(d[3].severity, vim.diagnostic.severity.WARN, "warn maps to WARN")

		-- Junk must not throw or produce diagnostics.
		assert_eq(#pc.parse("", ROOT, TARGET), 0, "empty stdout")
		assert_eq(#pc.parse("not json at all", ROOT, TARGET), 0, "non-JSON stdout")
		assert_eq(#pc.parse(vim.json.encode({ other = 1 }), ROOT, TARGET), 0, "JSON without errors key")
		assert_eq(#pc.parse(vim.json.encode({ errors = {} }), ROOT, TARGET), 0, "no findings")

		-- No .hg above the path -> nothing to run.
		local root, binary = pc.resolve("/definitely/not/a/repo/x.py")
		assert_eq(root, nil, "no repo root -> nil")
		assert_eq(binary, nil, "no repo root -> no binary")
		assert_eq(select(1, pc.resolve("")), nil, "empty path is handled")

		-- Private namespace, so a reset never clears LSP diagnostics.
		assert_eq(type(pc.NS), "number", "has its own diagnostic namespace")
		assert_eq(pc.NS ~= vim.api.nvim_create_namespace("some.lsp.namespace"), true, "namespace is distinct")
	end, debug.traceback)

	if not ok then
		error(err)
	end
	print("pyrefly-check-spec: all checks passed")
end

return M
