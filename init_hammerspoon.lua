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
