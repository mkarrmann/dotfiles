#Requires AutoHotkey v2.0
#SingleInstance Force ; replace old instance immediately

; #NoTrayIcon



^!+8::  ; Press Ctrl+Alt+S to toggle all scripts
{
	OutputDebug("IDList" )
    SuspendAllScripts()
}

SuspendAllScripts() {
    old := DetectHiddenWindows(true)
	selfHwnd := WinExist("ahk_class AutoHotkey")
	OutputDebug("IDList" )
	For hwnd in WinGetList("ahk_class AutoHotkey")
	{
		if (hwnd = selfHwnd)
			continue
		OutputDebug(hwnd )
		; 0x111 = WM_COMMAND, 65404 = ID_FILE_SUSPEND
		PostMessage(0x111, 65305, , , hwnd) ; Suspend.
	}
	DetectHiddenWindows(old)
}
