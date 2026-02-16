return {
	-- Disable markdown concealment
	{
		"nvim-treesitter/nvim-treesitter",
		opts = {
			highlight = {
				enable = true,
				disable = function(lang, buf)
					-- Keep treesitter enabled but disable concealment effects
					return false
				end,
			},
		},
	},

	-- Keep mini.pairs but disable backtick auto-pairing
	{
		"nvim-mini/mini.pairs",
		opts = function(_, opts)
			-- Disable backtick pairing by setting it to nil
			opts.mappings = opts.mappings or {}
			opts.mappings["`"] = false
			return opts
		end,
	},
}
