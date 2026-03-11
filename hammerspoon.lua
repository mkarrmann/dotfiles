require("hs.ipc")
hs.ipc.cliInstall("/opt/homebrew", true)

local spaces = require("hs.spaces")

local function userSpacesForScreen(screen)
	local all = spaces.spacesForScreen(screen)
	if not all then return nil end
	local result = {}
	for _, sid in ipairs(all) do
		if spaces.spaceType(sid) == "user" then
			result[#result + 1] = sid
		end
	end
	return result
end

function ghosttyEnsureSpaces(count)
	local screen = hs.screen.mainScreen()
	local userSpaces = userSpacesForScreen(screen)
	if not userSpaces then return -1 end

	local needed = count - #userSpaces
	if needed <= 0 then return 0 end

	for i = 1, needed do
		local closeMC = (i == needed)
		local ok, err = spaces.addSpaceToScreen(screen, closeMC)
		if not ok then
			return -1
		end
	end

	return needed
end

function ghosttyMoveToSpace(title, spaceIndex)
	local screen = hs.screen.mainScreen()
	local userSpaces = userSpacesForScreen(screen)
	if not userSpaces then return "ERROR: Failed to get spaces" end
	if spaceIndex > #userSpaces then
		return "ERROR: Space " .. spaceIndex .. " does not exist (" .. #userSpaces .. " user spaces)"
	end

	local app = hs.application.get("Ghostty")
	if not app then return "ERROR: Ghostty not running" end

	for _, win in ipairs(app:allWindows()) do
		if win:title() == title then
			local ok, err = spaces.moveWindowToSpace(win:id(), userSpaces[spaceIndex], true)
			if not ok then return "ERROR: " .. tostring(err) end
			return "ok"
		end
	end

	return "ERROR: Window '" .. title .. "' not found"
end

function ghosttyMoveNewestToSpace(knownIdStr, spaceIndex)
	local screen = hs.screen.mainScreen()
	local userSpaces = userSpacesForScreen(screen)
	if not userSpaces then return "ERROR: Failed to get spaces" end
	if spaceIndex > #userSpaces then return "ERROR: Space does not exist" end

	local app = hs.application.get("Ghostty")
	if not app then return "ERROR: Ghostty not running" end

	local known = {}
	for id in knownIdStr:gmatch("[^,]+") do
		known[tonumber(id) or -1] = true
	end

	for _, win in ipairs(app:allWindows()) do
		if not known[win:id()] then
			local ok, err = spaces.moveWindowToSpace(win:id(), userSpaces[spaceIndex], true)
			if not ok then return "ERROR: " .. tostring(err) end
			return "ok"
		end
	end

	return "ERROR: No new window found"
end

-- Screen navigation

function getNextScreen()
	local screens = hs.screen.allScreens()
	for i, s in ipairs(screens) do
		if s == hs.mouse.getCurrentScreen() then
			if i == #screens then
				return screens[1]
			else
				return screens[i + 1]
			end
		end
	end
	return screens[#screens]
end

function getWindowUnderMouse()
	local my_pos = hs.geometry.new(hs.mouse.absolutePosition())
	local my_screen = hs.mouse.getCurrentScreen()

	return hs.fnutils.find(
		-- Ideally use orderedWindows, but that seems to return empty list
		hs.window.allWindows(),
		function(w)
			return my_screen == w:screen() and my_pos:inside(w:frame())
		end
	)
end

function getPrevScreen()
	local screens = hs.screen.allScreens()
	for i, s in ipairs(screens) do
		if s == hs.mouse.getCurrentScreen() then
			if i == 1 then
				return screens[#screens]
			else
				return screens[i - 1]
			end
		end
	end
	return 1
end

hs.hotkey.bind(
	{"ctrl"},
	"k",
	function()
		local nextScreen = getNextScreen()
		local point = {}
		point.x = nextScreen:frame().w / 2
		point.y = nextScreen:frame().h / 2
		hs.mouse.setRelativePosition(point, nextScreen)
		local w = getWindowUnderMouse()
		w:focus()
	end
)

hs.hotkey.bind(
	{"ctrl"},
	"j",
	function()
		local prevScreen = getPrevScreen()
		local point = {}
		point.x = prevScreen:frame().w / 2
		point.y = prevScreen:frame().h / 2
		hs.mouse.setRelativePosition(point, prevScreen)
		-- There's a bug where this sometimes returns nil. Therefore, nothing
		-- is focused.
		local w = getWindowUnderMouse()
		w:focus()
	end
)
