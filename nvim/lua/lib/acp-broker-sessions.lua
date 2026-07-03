-- Pure, I/O-free logic for the ACP broker session-continue picker.
--
-- This module is deliberately side-effect-free so it can be unit-tested headless
-- (see lib/test/acp-broker-sessions-spec.lua). All I/O (acp-broker-cli calls, WAL
-- reads, snacks UI) lives in plugins/codecompanion.lua and feeds normalized data in.
--
-- Design: docs/acp-broker-continue-refactor.md §3.1-3.3.

local M = {}

-- Parse the `acp-broker-cli history query saved-sessions --json` payload into a
-- list of normalized rows. The server returns rows already sorted by recency
-- (started_at DESC) across brokers; we preserve that order. Metadata keys vary
-- per row (some rows have only `{}` metadata), so every field access is
-- defensive. Returns {} on malformed input or a missing `saved_sessions` key.
---@param json_str string
---@return table[] rows
function M.parse_saved_sessions(json_str)
	local ok, decoded = pcall(vim.fn.json_decode, json_str)
	if not ok or type(decoded) ~= "table" then
		return {}
	end
	local list = decoded.saved_sessions
	if type(list) ~= "table" then
		return {}
	end
	local rows = {}
	for _, entry in ipairs(list) do
		local meta = (type(entry.metadata) == "table" and entry.metadata) or {}
		local bcm = (type(meta.broker_client_metadata) == "table" and meta.broker_client_metadata) or {}
		local dvsc = (type(bcm.dvsc) == "table" and bcm.dvsc) or {}
		-- effort lives deep under dvsc.llm_config.model_params.reasoning_config.anthropic_effort.effort
		local effort
		do
			local lc = dvsc.llm_config
			local mp = type(lc) == "table" and lc.model_params or nil
			local rc = type(mp) == "table" and mp.reasoning_config or nil
			local ae = type(rc) == "table" and rc.anthropic_effort or nil
			effort = type(ae) == "table" and ae.effort or nil
		end
		rows[#rows + 1] = {
			bsid = entry.saved_session_id,
			broker_id = bcm.host,
			cwd = bcm.cwd,
			model = dvsc.model,
			mode = dvsc.mode,
			effort = effort,
		}
	end
	return rows
end

-- "local" if the row's broker matches this broker, else "remote". A nil/absent
-- broker_id is treated as remote (we can't prove it's ours → the safe routing is
-- fork, which handles cross-broker).
---@param row table
---@param this_broker_id string
---@return "local"|"remote"
function M.classify_origin(row, this_broker_id)
	if row.broker_id ~= nil and row.broker_id == this_broker_id then
		return "local"
	end
	return "remote"
end

-- Keep rows whose stored cwd is a prefix of `cwd` (exact match or `cwd` is a
-- subdirectory of row.cwd). This means a session started in a parent directory
-- surfaces from a subdir. Input order is preserved (already recency-sorted).
---@param rows table[]
---@param cwd string
---@return table[]
function M.filter_by_cwd(rows, cwd)
	local out = {}
	for _, row in ipairs(rows) do
		local rc = row.cwd
		if type(rc) == "string" and rc ~= "" then
			if cwd == rc or cwd:sub(1, #rc + 1) == (rc .. "/") then
				out[#out + 1] = row
			end
		end
	end
	return out
end

-- The routing truth table (design §3.2). `live` is an input, resolved by the
-- caller (lazily, for the picked row only). Remote always forks — cross-broker
-- resume is rejected by the broker.
---@param opts { origin: "local"|"remote", live: boolean }
---@return "resume"|"resume_or_fork"|"fork"
function M.route_for(opts)
	if opts.origin == "remote" then
		return "fork"
	end
	if opts.live then
		return "resume"
	end
	return "resume_or_fork"
end

-- Validate a string as a broker session id. Trims surrounding whitespace. A bare
-- "bsid_" (no id body) is rejected so the paste path can't route an empty id.
---@param str string
---@return boolean
function M.is_bsid(str)
	if type(str) ~= "string" then
		return false
	end
	local trimmed = vim.trim(str)
	return trimmed:match("^bsid_[%x%-]+$") ~= nil
end

-- The first segment of a bsid (`bsid_<8hex>`), for compact display. The full id
-- lives in the preview / yank action.
---@param bsid string
---@return string
function M.short_bsid(bsid)
	if type(bsid) ~= "string" then
		return "?"
	end
	-- bsid_<uuid> → keep through the first hyphen group
	local head = bsid:match("^(bsid_[%x]+)")
	return head or bsid
end

-- Coarse "N{s,m,h,d,w} ago" from an RFC3339 ts relative to `now` (unix seconds).
-- Cosmetic; buckets by the largest unit that fits.
---@param ts string|nil
---@param now integer unix seconds
---@return string
function M.relative_time(ts, now)
	if type(ts) ~= "string" then
		return "?"
	end
	-- Parse RFC3339 (UTC, optional fractional seconds) → unix seconds.
	local y, mo, d, h, mi, s = ts:match("(%d+)%-(%d+)%-(%d+)T(%d+):(%d+):(%d+)")
	if not y then
		return "?"
	end
	local t = os.time({
		year = tonumber(y),
		month = tonumber(mo),
		day = tonumber(d),
		hour = tonumber(h),
		min = tonumber(mi),
		sec = tonumber(s),
		isdst = false,
	})
	-- os.time interprets the table as local time; correct to UTC by subtracting
	-- the local UTC offset so callers can pass a UTC `now`.
	local utc_offset = os.difftime(os.time(os.date("*t", 0)), os.time(os.date("!*t", 0)))
	t = t + utc_offset
	local delta = now - t
	if delta < 0 then
		delta = 0
	end
	if delta < 60 then
		return string.format("%ds ago", delta)
	elseif delta < 3600 then
		return string.format("%dm ago", math.floor(delta / 60))
	elseif delta < 86400 then
		return string.format("%dh ago", math.floor(delta / 3600))
	elseif delta < 604800 then
		return string.format("%dd ago", math.floor(delta / 86400))
	else
		return string.format("%dw ago", math.floor(delta / 604800))
	end
end

-- Origin/liveness glyph: ● local-live, ○ local-dead, ◆ remote.
---@param origin "local"|"remote"
---@param live boolean
---@return string
local function glyph(origin, live)
	if origin == "remote" then
		return "◆"
	end
	return live and "●" or "○"
end

-- Render a picker row label (design §3.3). `ctx` carries the computed
-- origin/live/action and `now` for relative time. Columns are space-padded for
-- rough alignment; exact spacing is cosmetic and not asserted in tests.
---@param row table
---@param ctx { origin: "local"|"remote", live: boolean, action: string, now: integer }
---@return string
function M.render_label(row, ctx)
	local g = glyph(ctx.origin, ctx.live)
	local origin_txt
	if ctx.origin == "remote" then
		origin_txt = row.broker_id or "remote"
	else
		origin_txt = ctx.live and "local·live" or "local·dead"
	end
	local when = M.relative_time(row.started_at, ctx.now)
	local model = row.model or "?"
	local cwd = row.cwd or "?"
	-- Compact the cwd to its last two path components for readability.
	local short_cwd = cwd:match("([^/]+/[^/]+)/?$") or cwd
	-- Time column only when a timestamp is available (list rows are unenriched
	-- and have none — position already conveys recency).
	local when_col = (when ~= "?") and (when .. "  ") or ""
	return string.format(
		"%s %-12s  %s%-16s  %-24s  %-13s  → %s",
		g,
		origin_txt,
		when_col,
		model,
		short_cwd,
		M.short_bsid(row.bsid or "?"),
		ctx.action
	)
end

return M
