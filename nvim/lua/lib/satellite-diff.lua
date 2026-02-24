local api = vim.api
local util = require("satellite.util")

local handler = {
	name = "diff",
}

local config = {
	enable = true,
	overlap = true,
	priority = 25,
}

local function setup_hl()
	local function sign_fg(name, fallback)
		local hl = api.nvim_get_hl(0, { name = name, link = false })
		return hl.fg or fallback
	end

	api.nvim_set_hl(0, "SatelliteDiffAdd", { fg = sign_fg("GitSignsAdd", 0x2ea043) })
	api.nvim_set_hl(0, "SatelliteDiffChange", { fg = sign_fg("GitSignsChange", 0x0078d4) })
	api.nvim_set_hl(0, "SatelliteDiffDelete", { fg = sign_fg("GitSignsDelete", 0xf85149) })
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

	local other_bufnr
	for _, wid in ipairs(api.nvim_tabpage_list_wins(0)) do
		if wid ~= winid and api.nvim_win_is_valid(wid) and vim.wo[wid].diff then
			other_bufnr = api.nvim_win_get_buf(wid)
			break
		end
	end
	if not other_bufnr then
		return {}
	end

	local lines_a = table.concat(api.nvim_buf_get_lines(other_bufnr, 0, -1, false), "\n")
	local lines_b = table.concat(api.nvim_buf_get_lines(bufnr, 0, -1, false), "\n")
	local hunks = vim.diff(lines_a, lines_b, { result_type = "indices" })

	local marks = {}
	for _, hunk in ipairs(hunks) do
		local count_a = hunk[2]
		local start_b, count_b = hunk[3], hunk[4]

		if count_b > 0 then
			local hl = count_a == 0 and "SatelliteDiffAdd" or "SatelliteDiffChange"
			local first = util.row_to_barpos(winid, start_b - 1)
			local last = util.row_to_barpos(winid, start_b + count_b - 2)
			for pos = first, last do
				marks[#marks + 1] = {
					pos = pos,
					symbol = "│",
					highlight = hl,
				}
			end
		end

		if count_a > 0 and count_b == 0 then
			local pos = util.row_to_barpos(winid, math.max(0, start_b - 1))
			marks[#marks + 1] = {
				pos = pos,
				symbol = "─",
				highlight = "SatelliteDiffDelete",
			}
		end
	end

	return marks
end

require("satellite.handlers").register(handler)
