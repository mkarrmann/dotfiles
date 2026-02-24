local api = vim.api
local util = require("satellite.util")

local handler = {
	name = "diff",
}

local config = {
	enable = true,
	overlap = false,
	priority = 25,
	signs = {
		add = "│",
		change = "│",
		delete = "-",
	},
}

local function setup_hl()
	for _, sfx in ipairs({ "Add", "Delete", "Change" }) do
		api.nvim_set_hl(0, "SatelliteDiff" .. sfx, {
			default = true,
			link = "Diff" .. sfx,
		})
	end
end

function handler.setup(config0, update)
	config = vim.tbl_deep_extend("force", config, config0)
	handler.config = config

	local group = api.nvim_create_augroup("satellite_diff", {})

	api.nvim_create_autocmd("ColorScheme", {
		group = group,
		callback = setup_hl,
	})

	setup_hl()

	api.nvim_create_autocmd("OptionSet", {
		group = group,
		pattern = "diff",
		callback = update,
	})
end

function handler.update(bufnr, winid)
	if not api.nvim_win_is_valid(winid) or not vim.wo[winid].diff then
		return {}
	end

	local total_lines = api.nvim_buf_line_count(bufnr)
	local diff_info = api.nvim_win_call(winid, function()
		local info = {}
		for lnum = 1, total_lines do
			local hl_id = vim.fn.diff_hlID(lnum, 1)
			if hl_id > 0 then
				info[#info + 1] = { lnum = lnum, type = vim.fn.synIDattr(hl_id, "name") }
			end
			if vim.fn.diff_filler(lnum) > 0 then
				info[#info + 1] = { lnum = lnum, type = "DiffDelete" }
			end
		end
		return info
	end)

	local marks = {}
	for _, entry in ipairs(diff_info) do
		local satellite_hl, symbol
		if entry.type == "DiffAdd" then
			satellite_hl = "SatelliteDiffAdd"
			symbol = config.signs.add
		elseif entry.type == "DiffChange" or entry.type == "DiffText" then
			satellite_hl = "SatelliteDiffChange"
			symbol = config.signs.change
		elseif entry.type == "DiffDelete" then
			satellite_hl = "SatelliteDiffDelete"
			symbol = config.signs.delete
		end

		if satellite_hl then
			local pos = util.row_to_barpos(winid, entry.lnum - 1)
			marks[#marks + 1] = {
				pos = pos,
				symbol = symbol,
				highlight = satellite_hl,
			}
		end
	end

	return marks
end

require("satellite.handlers").register(handler)
