-- nvim --headless -u NONE --cmd "set rtp+=$HOME/dotfiles/nvim" -c "lua require('lib.test.lualine-tabline-spec').run()" -c "qa!"

local M = {}

local function eq(actual, expected, label)
	if not vim.deep_equal(actual, expected) then
		error(string.format("%s: expected %s, got %s", label, vim.inspect(expected), vim.inspect(actual)))
	end
end

function M.run()
	local old_accent = package.loaded["lib.session-accent"]
	local old_lualine = package.loaded.lualine
	package.loaded["lib.session-accent"] = {
		winbar_bg = function()
			return "#000000"
		end,
	}

	local path = assert(vim.api.nvim_get_runtime_file("lua/plugins/overrides.lua", false)[1])
	local specs = dofile(path)
	local lualine_spec
	for _, spec in ipairs(specs) do
		if spec[1] == "nvim-lualine/lualine.nvim" then
			lualine_spec = spec
			break
		end
	end
	assert(lualine_spec, "lualine override not found")

	local opts = {
		options = { disabled_filetypes = { statusline = {}, winbar = {} } },
		sections = { lualine_x = {} },
		inactive_sections = {},
		tabline = { lualine_a = { "tabs" } },
	}
	opts = lualine_spec.opts(nil, opts)
	eq(opts.tabline, {}, "lualine tabline sections are disabled")

	local configured
	package.loaded.lualine = {
		setup = function(received)
			configured = received
			vim.o.tabline = "%!v:lua.require'lualine'.tabline()"
		end,
	}
	lualine_spec.config(nil, opts)
	eq(configured.tabline, {}, "disabled tabline reaches lualine setup")
	eq(vim.o.tabline, "%!v:lua._tabline()", "custom tabline owns the option after lualine setup")
	eq(vim.o.showtabline, 2, "custom tabline is always visible")

	package.loaded["lib.session-accent"] = old_accent
	package.loaded.lualine = old_lualine
	print("lualine-tabline-spec: all checks passed")
end

return M
