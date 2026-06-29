-- Per-instance accent color, keyed by the nvs session name.
--
-- bin/nvs --launch exports NVS_SESSION_NAME (e.g. "FTW-checkout1") into each
-- headless server's environment, so every remote instance can derive a stable
-- visual identity. Colors are curated per known session for at-a-glance
-- recognition; unknown sessions fall back to a deterministic hash so new
-- checkouts still get a distinct (if untuned) color.

local M = {}

-- Curated accents. FTW = cool hues, CCO = warm hues; checkout number shifts
-- the hue within each family. Hand-tune freely.
M.accents = {
	["FTW-main1"] = "#06b6d4", -- cyan
	["FTW-checkout1"] = "#38bdf8", -- sky
	["FTW-checkout2"] = "#3b82f6", -- blue
	["FTW-checkout3"] = "#8b5cf6", -- violet
	["CCO-main1"] = "#eab308", -- yellow
	["CCO-checkout1"] = "#f59e0b", -- amber
	["CCO-checkout2"] = "#f97316", -- orange
	["CCO-checkout3"] = "#ef4444", -- red
	["CCO-checkout4"] = "#ec4899", -- pink
}

-- Accent for the local (non-nvs) instance, which has no session name.
M.default_accent = "#9ca3af"

local function hsl_to_hex(h, s, l)
	local c = (1 - math.abs(2 * l - 1)) * s
	local hp = h / 60
	local x = c * (1 - math.abs(hp % 2 - 1))
	local r, g, b = 0, 0, 0
	if hp < 1 then
		r, g, b = c, x, 0
	elseif hp < 2 then
		r, g, b = x, c, 0
	elseif hp < 3 then
		r, g, b = 0, c, x
	elseif hp < 4 then
		r, g, b = 0, x, c
	elseif hp < 5 then
		r, g, b = x, 0, c
	else
		r, g, b = c, 0, x
	end
	local m = l - c / 2
	return string.format(
		"#%02x%02x%02x",
		math.floor((r + m) * 255 + 0.5),
		math.floor((g + m) * 255 + 0.5),
		math.floor((b + m) * 255 + 0.5)
	)
end

-- Deterministic hue from an arbitrary name, fixed saturation/lightness.
local function hash_accent(name)
	local h = 0
	for i = 1, #name do
		h = (h * 31 + name:byte(i)) % 360
	end
	return hsl_to_hex(h, 0.55, 0.62)
end

local function darken(hex, factor)
	local r = tonumber(hex:sub(2, 3), 16)
	local g = tonumber(hex:sub(4, 5), 16)
	local b = tonumber(hex:sub(6, 7), 16)
	return string.format(
		"#%02x%02x%02x",
		math.floor(r * factor),
		math.floor(g * factor),
		math.floor(b * factor)
	)
end

-- The nvs session name for this instance, or nil for the local instance.
function M.session_name()
	local name = vim.env.NVS_SESSION_NAME
	if name == nil or name == "" then
		return nil
	end
	return name
end

-- The accent hex for this instance.
function M.accent()
	local name = M.session_name()
	if not name then
		return M.default_accent
	end
	return M.accents[name] or hash_accent(name)
end

-- A dark, accent-tinted background for the winbar/header.
function M.winbar_bg()
	return darken(M.accent(), 0.2)
end

return M
