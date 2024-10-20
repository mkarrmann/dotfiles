local vim = vim -- best to get the "undefined variable" warning just one place

local Plug = vim.fn['plug#']

vim.call('plug#begin')
Plug('smoka7/hop.nvim')
vim.call('plug#end')

local status_ok, hop = pcall(require, "hop")

if not status_ok then
        return
end

vim.opt.clipboard = "unnamedplus"
vim.g.mapleader = ','

-- TODO make more configurable (and confirm this even works as expected)
vim.api.nvim_create_autocmd("FileType", {
        pattern = "python",
        callback = function()
                vim.o.textwidth = 88
        end
})

if vim.g.vscode then
        -- See https://github.com/vscode-neovim/vscode-neovim/issues/1902#issuecomment-2151329542
        vim.api.nvim_set_keymap(
                'n',
                '<space>',
                [[<Cmd>lua require('vscode').call('vspacecode.space')<CR>]],
                { noremap = true, silent = true }
        )


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
