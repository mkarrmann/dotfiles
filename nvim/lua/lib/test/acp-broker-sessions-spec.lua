-- Headless unit tests for lib.acp-broker-sessions (pure logic, no I/O).
-- nvim --headless -u NONE --cmd "set rtp+=$HOME/dotfiles/nvim" -c "lua require('lib.test.acp-broker-sessions-spec').run()" -c "qa!"

local M = {}

local function assert_eq(actual, expected, label)
	if actual ~= expected then
		error(string.format("%s: expected %s, got %s", label, vim.inspect(expected), vim.inspect(actual)))
	end
end

local function assert_true(cond, label)
	if not cond then
		error(string.format("%s: expected truthy, got %s", label, vim.inspect(cond)))
	end
end

local function assert_list_eq(actual, expected, label)
	if type(actual) ~= "table" then
		error(string.format("%s: expected list, got %s", label, vim.inspect(actual)))
	end
	if #actual ~= #expected then
		error(string.format("%s: expected length %d, got %d (%s)", label, #expected, #actual, vim.inspect(actual)))
	end
	for i = 1, #expected do
		if actual[i] ~= expected[i] then
			error(string.format("%s[%d]: expected %s, got %s", label, i, vim.inspect(expected[i]), vim.inspect(actual[i])))
		end
	end
end

-- Real captured saved-sessions payload (trimmed to diverse rows, order = recency DESC
-- as the server returns it). Captured 2026-07-03 via
-- `acp-broker-cli history query saved-sessions --json`.
local FIXTURE = [[
{
  "saved_sessions": [
    {"saved_session_id":"bsid_a1d78873-835e-4348-befe-d5db0d59c96f","metadata":{"broker_client_metadata":{"nvim_session":"FTW-checkout2","host":"devvm36111","cwd":"/home/mkarrmann/checkout2/fbsource","nvim_pid":1282520,"dvsc":{"mode":"native","model":"claude-opus-4.8","llm_config":{"model_params":{"reasoning_config":{"anthropic_effort":{"effort":"high"}}}}},"nvim_tab_handle":1}}},
    {"saved_session_id":"bsid_af284dba-fb9c-4795-8d4e-ba97318a2349","metadata":{"broker_client_metadata":{"nvim_session":"CCO-checkout2","host":"devvm20365","cwd":"/home/mkarrmann/checkout2/fbsource","nvim_pid":1954882,"nvim_tab_handle":4,"dvsc":{"model":"claude-opus-4.8","mode":"native"}}}},
    {"saved_session_id":"bsid_606dc5ce-b6f2-46ca-8c5a-d0f84d36ebc5","metadata":{"broker_client_metadata":{"nvim_session":"FTW-checkout2","host":"devvm36111","cwd":"/home/mkarrmann/checkout2/fbsource/fbcode","nvim_pid":146861,"nvim_tab_handle":1}}},
    {"saved_session_id":"bsid_be2b31e1-c27c-4059-93e6-a980d4a42c1a","metadata":{"broker_client_metadata":{"nvim_pid":63590,"cwd":"/Users/mkarrmann","host":"MacBook-Pro.local","nvim_session":"ad-hoc"}}},
    {"saved_session_id":"bsid_7ccb45b7-e0e4-4526-b9cb-445e759502f0","metadata":{}}
  ]
}
]]

function M.run()
	local S = require("lib.acp-broker-sessions")

	-- ── parse_saved_sessions ────────────────────────────────────────────────
	local rows = S.parse_saved_sessions(FIXTURE)
	assert_eq(#rows, 5, "parse: row count")

	-- row 1: full local row
	assert_eq(rows[1].bsid, "bsid_a1d78873-835e-4348-befe-d5db0d59c96f", "parse: bsid")
	assert_eq(rows[1].broker_id, "devvm36111", "parse: broker_id from host")
	assert_eq(rows[1].cwd, "/home/mkarrmann/checkout2/fbsource", "parse: cwd")
	assert_eq(rows[1].model, "claude-opus-4.8", "parse: model")
	assert_eq(rows[1].mode, "native", "parse: mode")
	assert_eq(rows[1].effort, "high", "parse: effort")

	-- row 2: remote, dvsc present but no llm_config (effort absent)
	assert_eq(rows[2].broker_id, "devvm20365", "parse: remote broker_id")
	assert_eq(rows[2].model, "claude-opus-4.8", "parse: remote model")
	assert_eq(rows[2].effort, nil, "parse: absent effort is nil")

	-- row 3: no dvsc at all → model/mode/effort nil, cwd still present
	assert_eq(rows[3].model, nil, "parse: no-dvsc model nil")
	assert_eq(rows[3].cwd, "/home/mkarrmann/checkout2/fbsource/fbcode", "parse: no-dvsc cwd")

	-- row 4: mac
	assert_eq(rows[4].broker_id, "MacBook-Pro.local", "parse: mac host")
	assert_eq(rows[4].cwd, "/Users/mkarrmann", "parse: mac cwd")

	-- row 5: empty metadata {} → everything nil but bsid present, no crash
	assert_eq(rows[5].bsid, "bsid_7ccb45b7-e0e4-4526-b9cb-445e759502f0", "parse: empty-meta bsid")
	assert_eq(rows[5].broker_id, nil, "parse: empty-meta broker_id nil")
	assert_eq(rows[5].cwd, nil, "parse: empty-meta cwd nil")

	-- order preserved (recency DESC as returned)
	assert_eq(rows[1].bsid:sub(1, 13), "bsid_a1d78873", "parse: order[1]")
	assert_eq(rows[2].bsid:sub(1, 13), "bsid_af284dba", "parse: order[2]")

	-- malformed input → empty list, no crash
	assert_list_eq(S.parse_saved_sessions("not json"), {}, "parse: malformed → {}")
	assert_list_eq(S.parse_saved_sessions("{}"), {}, "parse: no saved_sessions key → {}")

	-- ── classify_origin ─────────────────────────────────────────────────────
	assert_eq(S.classify_origin(rows[1], "devvm36111"), "local", "origin: local")
	assert_eq(S.classify_origin(rows[2], "devvm36111"), "remote", "origin: remote devvm")
	assert_eq(S.classify_origin(rows[4], "devvm36111"), "remote", "origin: remote mac")
	assert_eq(S.classify_origin(rows[5], "devvm36111"), "remote", "origin: nil broker → remote")

	-- ── filter_by_cwd ───────────────────────────────────────────────────────
	local fb = "/home/mkarrmann/checkout2/fbsource"
	local filtered = S.filter_by_cwd(rows, fb)
	-- rows 1,2 have cwd == fbsource (exact). row 3 cwd == fbsource/fbcode is a CHILD
	-- of the query, not a prefix of it, so it does NOT match (design §3.1: a session
	-- matches when its cwd is a prefix of nvim's cwd — you're in the session's dir or
	-- a subdir of it). rows 4 (mac), 5 (nil) don't match.
	assert_eq(#filtered, 2, "filter: count for fbsource")
	assert_eq(filtered[1].bsid:sub(1, 13), "bsid_a1d78873", "filter: order preserved [1]")
	assert_eq(filtered[2].bsid:sub(1, 13), "bsid_af284dba", "filter: order preserved [2]")

	-- From a subdir, parent-cwd sessions (rows 1,2) match AND the exact subdir
	-- session (row 3) matches → 3 total.
	local sub = S.filter_by_cwd(rows, "/home/mkarrmann/checkout2/fbsource/fbcode")
	assert_eq(#sub, 3, "filter: subdir cwd matches parent-cwd sessions + exact")

	local none = S.filter_by_cwd(rows, "/nonexistent/path")
	assert_eq(#none, 0, "filter: no matches")

	-- ── route_for ───────────────────────────────────────────────────────────
	assert_eq(S.route_for({ origin = "local", live = true }), "resume", "route: local live")
	assert_eq(S.route_for({ origin = "local", live = false }), "resume_or_fork", "route: local dead")
	assert_eq(S.route_for({ origin = "remote", live = false }), "fork", "route: remote")
	assert_eq(S.route_for({ origin = "remote", live = true }), "fork", "route: remote ignores live")

	-- ── is_bsid ─────────────────────────────────────────────────────────────
	assert_true(S.is_bsid("bsid_a1d78873-835e-4348-befe-d5db0d59c96f"), "is_bsid: real")
	assert_true(S.is_bsid("bsid_a1d78873"), "is_bsid: short accepted")
	assert_eq(S.is_bsid("bsid_"), false, "is_bsid: bare prefix rejected")
	assert_eq(S.is_bsid("hello"), false, "is_bsid: arbitrary rejected")
	assert_eq(S.is_bsid(""), false, "is_bsid: empty rejected")
	assert_eq(S.is_bsid("  bsid_a1d78873  "), true, "is_bsid: trims whitespace")

	-- ── short_bsid ──────────────────────────────────────────────────────────
	assert_eq(S.short_bsid("bsid_a1d78873-835e-4348-befe-d5db0d59c96f"), "bsid_a1d78873", "short_bsid")
	assert_eq(S.short_bsid("bsid_deadbeef"), "bsid_deadbeef", "short_bsid: already short")

	-- ── relative_time ───────────────────────────────────────────────────────
	-- now = 2026-07-03T21:10:00Z (unix 1783113000)
	local now = 1783113000
	assert_eq(S.relative_time("2026-07-03T21:09:30Z", now), "30s ago", "reltime: seconds")
	assert_eq(S.relative_time("2026-07-03T21:05:00Z", now), "5m ago", "reltime: minutes")
	assert_eq(S.relative_time("2026-07-03T19:10:00Z", now), "2h ago", "reltime: hours")
	assert_eq(S.relative_time("2026-07-01T21:10:00Z", now), "2d ago", "reltime: days")
	assert_eq(S.relative_time("2026-06-19T21:10:00Z", now), "2w ago", "reltime: weeks")
	assert_eq(S.relative_time(nil, now), "?", "reltime: nil ts")

	-- ── render_label ────────────────────────────────────────────────────────
	-- Just assert structural content (glyph + short bsid + action arrow); exact
	-- spacing is cosmetic. Origin/live/action are inputs (computed elsewhere).
	local label = S.render_label(
		{ bsid = "bsid_a1d78873-835e-4348-befe-d5db0d59c96f", cwd = "/home/mkarrmann/checkout2/fbsource", model = "claude-opus-4.8", started_at = "2026-07-03T19:10:00Z" },
		{ origin = "local", live = true, action = "resume", now = now }
	)
	assert_true(label:find("●", 1, true) ~= nil, "label: local-live glyph")
	assert_true(label:find("bsid_a1d78873", 1, true) ~= nil, "label: short bsid")
	assert_true(label:find("resume", 1, true) ~= nil, "label: action")
	assert_true(label:find("2h ago", 1, true) ~= nil, "label: relative time")

	local remote_label = S.render_label(
		{ bsid = "bsid_af284dba-x", cwd = "/x", model = "m", started_at = "2026-07-03T19:10:00Z" },
		{ origin = "remote", live = false, action = "fork", now = now }
	)
	assert_true(remote_label:find("◆", 1, true) ~= nil, "label: remote glyph")
	assert_true(remote_label:find("fork", 1, true) ~= nil, "label: fork action")

	local dead_label = S.render_label(
		{ bsid = "bsid_dead-x", cwd = "/x", started_at = "2026-07-03T19:10:00Z" },
		{ origin = "local", live = false, action = "resume_or_fork", now = now }
	)
	assert_true(dead_label:find("○", 1, true) ~= nil, "label: local-dead glyph")

	-- unenriched list row (no started_at) → no time column, no crash
	local list_label = S.render_label(
		{ bsid = "bsid_a1d78873-x", cwd = "/home/x/fbsource", model = "opus" },
		{ origin = "local", live = false, action = "resume", now = now }
	)
	assert_true(list_label:find("bsid_a1d78873", 1, true) ~= nil, "label: unenriched has bsid")
	assert_true(list_label:find("?", 1, true) == nil, "label: unenriched omits '?' time col")

	print("acp-broker-sessions-spec: ALL PASSED")
end

return M
