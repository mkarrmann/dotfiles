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

vim.call('plug#begin')
Plug('smoka7/hop.nvim')
Plug('mbbill/undotree')
Plug('hrsh7th/nvim-cmp')
Plug('ThePrimeagen/99')
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
