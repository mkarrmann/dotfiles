-- Headless test for lib.meta-format.
-- nvim --headless -u NONE --cmd "set rtp+=$HOME/dotfiles/nvim" -c "lua require('lib.test.meta-format-spec').run()" -c "qa!"

local M = {}

local function assert_eq(actual, expected, label)
	if actual ~= expected then
		error(string.format("%s: expected %s, got %s", label, vim.inspect(expected), vim.inspect(actual)))
	end
end

local function fake_client(name, can_format)
	return {
		name = name,
		supports_method = function(_, method)
			return can_format and method == "textDocument/formatting"
		end,
	}
end

-- Stub vim.lsp.get_clients, honouring the {bufnr, name} filter the real API applies.
local function with_clients(clients, fn)
	local original = vim.lsp.get_clients
	vim.lsp.get_clients = function(opts)
		opts = opts or {}
		local out = {}
		for _, c in ipairs(clients) do
			if not opts.name or opts.name == c.name then
				out[#out + 1] = c
			end
		end
		return out
	end
	local ok, err = pcall(fn)
	vim.lsp.get_clients = original
	if not ok then
		error(err)
	end
end

local function fresh()
	package.loaded["lib.meta-format"] = nil
	return require("lib.meta-format")
end

function M.run()
	local ok, err = xpcall(function()
		local mf = fresh()

		-- Active only when linttool is attached and can format.
		with_clients({ fake_client("linttool@meta", true), fake_client("pyrefly@meta", false) }, function()
			assert_eq(#mf.sources(0), 1, "linttool attached -> one source")
			assert_eq(mf.sources(0)[1], "linttool@meta", "source is linttool")
		end)

		-- Inert where linttool does not attach (configerator), so arclint still wins there.
		with_clients({ fake_client("null-ls", true), fake_client("pyrefly@meta", false) }, function()
			assert_eq(#mf.sources(0), 0, "no linttool -> inert")
		end)

		-- Attached but formatting unsupported is not a source.
		with_clients({ fake_client("linttool@meta", false) }, function()
			assert_eq(#mf.sources(0), 0, "linttool without formatting support -> inert")
		end)

		-- Outranks none-ls (200) and conform (100) so it actually gets the primary slot.
		local got
		assert_eq(mf.setup(function(f)
			got = f
		end), true, "setup registers")
		assert_eq(got.name, "linttool@meta", "formatter name")
		assert_eq(got.primary, true, "must be primary to displace none-ls")
		assert_eq(got.priority > 200, true, "priority outranks none-ls")
		assert_eq(type(got.format), "function", "format is callable")

		-- format() targets only linttool, so null-ls/arclint is not invoked alongside it.
		local captured
		local original = vim.lsp.buf.format
		vim.lsp.buf.format = function(o)
			captured = o
		end
		local ok_fmt, err_fmt = pcall(got.format, 7)
		vim.lsp.buf.format = original
		if not ok_fmt then
			error(err_fmt)
		end
		assert_eq(captured.name, "linttool@meta", "formats with linttool only")
		assert_eq(captured.bufnr, 7, "formats the requested buffer")

		-- Without LazyVim present, report failure rather than pretending.
		assert_eq(mf.setup(nil), false, "no registry -> false")
	end, debug.traceback)

	if not ok then
		error(err)
	end
	print("meta-format-spec: all checks passed")
end

return M
