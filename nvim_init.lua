local vim = vim -- best to get the "undefined variable" warning just one place

-- Define data directory
local data_dir = vim.fn.stdpath('data') .. '/site'

-- Check if plug.vim exists, if not install it
if vim.fn.empty(vim.fn.glob(data_dir .. '/autoload/plug.vim')) == 1 then
  -- Download plug.vim using curl
  vim.fn.system({
    'curl',
    '-fLo',
    data_dir .. '/autoload/plug.vim',
    '--create-dirs',
    'https://raw.githubusercontent.com/junegunn/vim-plug/master/plug.vim'
  })

  -- Install plugins on VimEnter
  vim.api.nvim_create_autocmd('VimEnter', {
    callback = function()
      vim.cmd [[PlugInstall --sync]]
      vim.cmd [[source $MYVIMRC]]
    end
  })
end

local Plug = vim.fn['plug#']

local local_init_path = vim.fn.stdpath('config') .. '/local.lua'
if vim.fn.filereadable(local_init_path) == 0 then
	local_init_path = vim.env.HOME .. '/.config/nvim/local.lua'
end
local local_init = nil
if vim.fn.filereadable(local_init_path) == 1 then
	local_init = dofile(local_init_path)
end

vim.call('plug#begin')
Plug('smoka7/hop.nvim')
Plug('mbbill/undotree')
Plug('hrsh7th/nvim-cmp')
Plug('ThePrimeagen/99')
Plug('neovim/nvim-lspconfig')
Plug('nvim-lua/plenary.nvim')
Plug('nvim-telescope/telescope.nvim')
Plug('hrsh7th/cmp-nvim-lsp')
Plug('MunifTanjim/nui.nvim')
Plug('amitds1997/remote-nvim.nvim')
if local_init and local_init.plugins then
	local_init.plugins(Plug)
end
vim.call('plug#end')

vim.opt.clipboard = "unnamedplus"
vim.g.mapleader = ','

-- TODO make more configurable (and confirm this even works as expected)
vim.api.nvim_create_autocmd("FileType", {
	pattern = "python",
	callback = function()
		vim.o.textwidth = 88
	end
})

local status_ok, hop = pcall(require, "hop")

if vim.g.vscode then
	-- See https://github.com/vscode-neovim/vscode-neovim/issues/1902#issuecomment-2151329542
	-- Actually, now just let vscode totally handle this
	-- vim.api.nvim_set_keymap(
	--         'n',
	--         '<C-space>',
	--         [[<Cmd>lua require('vscode').call('vspacecode.space')<CR>]],
	--         { noremap = true, silent = true }
	-- )


	-- See https://github.com/vscode-neovim/vscode-neovim/issues/1902#issuecomment-2073831492

	-- Allows you distinguish whether an operator is pending using VSCode context keys

	vim.api.nvim_create_autocmd({ "VimEnter", "ModeChanged" }, {
		pattern = '*',
		callback = function()
			local fullMode = vim.api.nvim_eval('mode(1)')
			vim.fn["VSCodeCall"]('setContext', 'neovim.fullMode', fullMode)
		end,

	})

	-- Reverse of what extension sets, but what I'm used to
	vim.api.nvim_set_keymap('n', 'gD', "<Cmd>lua require('vscode').call('editor.action.revealDefinitionAside')<CR>",
		{ noremap = true, silent = true })

	vim.api.nvim_set_keymap('n', '<C-w>gd', "<Cmd>lua require('vscode').call('editor.action.peekDefinition')<CR>",
		{ noremap = true, silent = true })
end

-- easymotion plugin (hop) config
if status_ok then
	hop.setup {
		keys = 'etovxqpdygfblzhckisuran'
	}

	local opts = {
		silent = true,
		noremap = true,
		callback = nil,
		desc = nil,
	}

	local directions = require('hop.hint').HintDirection

	local bindings = {
		{
			mode = 'n',
			mapping = '<Leader>f',
			desc = '',
			func = function() hop.hint_char1({ direction = directions.AFTER_CURSOR, current_line_only = false }) end
		},
		{
			mode = 'n',
			mapping = '<Leader>F',
			desc = '',
			func = function() hop.hint_char1({ direction = directions.BEFORE_CURSOR, current_line_only = false }) end
		},
		{
			mode = 'n',
			mapping = '<Leader>t',
			desc = '',
			func = function() hop.hint_char1({ direction = directions.AFTER_CURSOR, current_line_only = false, hint_offset = -1 }) end
		},
		{
			mode = 'n',
			mapping = '<Leader>T',
			desc = '',
			func = function() hop.hint_char1({ direction = directions.BEFORE_CURSOR, current_line_only = false, hint_offset = 1 }) end
		},
	}

	for _, binding in pairs(bindings) do
		-- table.foreach(bindings, function(idx, binding)
		opts.callback = binding.func
		opts.desc = binding.desc
		vim.api.nvim_set_keymap(binding.mode, binding.mapping, '', opts)
	end
end

-- 99 (ThePrimeagen) config
-- TODO: 99 doesn't work in VSCode Neovim (floating windows unsupported)
if not vim.g.vscode then
	local nn_ok, _99 = pcall(require, "99")
	if nn_ok then
		local cwd = vim.uv.cwd()
		local basename = vim.fs.basename(cwd)
		_99.setup({
			provider = _99.Providers.ClaudeCodeProvider,
			logger = {
				level = _99.DEBUG,
				path = "/tmp/" .. basename .. ".99.debug",
				print_on_error = true,
			},
			completion = {
				source = "cmp",
			},
			md_files = {
				"AGENT.md",
			},
		})

		vim.keymap.set("v", "<leader>9v", function()
			_99.visual()
		end)

		vim.keymap.set("v", "<leader>9s", function()
			_99.stop_all_requests()
		end)
	end
end

if not vim.g.vscode then
	vim.api.nvim_create_autocmd("LspAttach", {
		callback = function(ev)
			local opts = { buffer = ev.buf }
			vim.keymap.set("n", "gd", vim.lsp.buf.definition, opts)
			vim.keymap.set("n", "gD", vim.lsp.buf.declaration, opts)
			vim.keymap.set("n", "gr", vim.lsp.buf.references, opts)
			vim.keymap.set("n", "gi", vim.lsp.buf.implementation, opts)
			vim.keymap.set("n", "K", vim.lsp.buf.hover, opts)
			vim.keymap.set("n", "<leader>rn", vim.lsp.buf.rename, opts)
			vim.keymap.set("n", "<leader>ca", vim.lsp.buf.code_action, opts)
			vim.keymap.set("n", "[d", vim.diagnostic.goto_prev, opts)
			vim.keymap.set("n", "]d", vim.diagnostic.goto_next, opts)
			vim.keymap.set("n", "<leader>e", vim.diagnostic.open_float, opts)
		end,
	})

	local cmp_ok, cmp = pcall(require, "cmp")
	local cmp_lsp_ok, cmp_nvim_lsp = pcall(require, "cmp_nvim_lsp")
	if cmp_ok and cmp_lsp_ok then
		cmp_nvim_lsp.default_capabilities()
		cmp.setup({
			sources = cmp.config.sources({
				{ name = "nvim_lsp" },
			}),
		})
	end

	local telescope_ok = pcall(require, "telescope")
	if telescope_ok then
		local builtin = require("telescope.builtin")
		vim.keymap.set("n", "<leader>sf", function()
			builtin.live_grep()
		end)
	end

	local remote_ok, remote_nvim = pcall(require, "remote-nvim")
	if remote_ok then
		remote_nvim.setup()
	end

	if local_init and local_init.setup then
		local_init.setup()
	end
end
