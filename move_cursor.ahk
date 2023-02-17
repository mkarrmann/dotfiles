; This is the only thing I've ever done with AutoHotKey, and I don't really know what I'm doing. I mostly just glued things together I found online (credit given below). I'm sure this could be cleaned up substantially.
; Credit for this section: Joe Winograd https://www.experts-exchange.com/articles/33932/Keyboard-shortcuts-hotkeys-to-move-mouse-in-multi-monitor-configuration-AutoHotkey-Script.html

#Warn,UseUnsetLocal ; warning on uninitialized variables
#NoEnv ; avoid checking empty variables to see if they are environment variables
#SingleInstance Force ; replace old instance immediately
SetBatchLines,-1 ; run at maximum speed

Gosub,InitializeVars ; initialize all variables
Gosub,ConfigureInitialTray ; configure initial system tray (notification area)
Return

InitializeVars:
SplitPath,A_ScriptName,,,,ProgramName ; ProgramName==>script name without path or extension
FileGetTime,ScriptDateTime,%A_ScriptName%,M ; modified time of script
FormatTime,ScriptDateTime,%ScriptDateTime%,yyyyMMdd-HHmmss

; *** begin variables to change ***
IdentifySeconds:=3 ; length of time in seconds for monitor identifier (its number or letter) to stay on screen
OffsetX:=-1 ; move mouse to this number of pixels from left edge of monitor (-1 means center)
OffsetY:=-1 ; move mouse to this number of pixels from top edge of monitor (-1 means center)
;OffsetX:=0 ; example - left edge
;OffsetY:=0 ; example - top edge
;OffsetX:=400 ; example - 400 pixels from left edge
;OffsetY:=300 ; example - 300 pixels from top edge
; *** end variables to change ***

IdentifyMilliseconds:=IdentifySeconds*1000 ; Sleep time is in milliseconds

SysGet,OrigNumMons,MonitorCount ; original number of monitors when script was run


ConfigureInitialTray:
Menu,Tray,NoStandard ; do not use standard AutoHotkey context menu
Menu,Tray,Add,Show &Monitor and Virtual Screen Information,ContextMenu
Menu,Tray,Add,&Identify Monitors,ContextMenu
Menu,Tray,Add,Start with &Windows (On/Off toggle),ContextMenu
StartupLink:=A_Startup . "\" . ProgramName . ".lnk"
If (FileExist(StartupLink))
  Menu,Tray,Check,Start with &Windows (On/Off toggle)
Else
  Menu,Tray,Uncheck,Start with &Windows (On/Off toggle)
Menu,Tray,Add,&Reload Script,ContextMenu
Menu,Tray,Add,&About,ContextMenu
Menu,Tray,Add,E&xit,ContextMenu
Menu,Tray,Default,Show &Monitor and Virtual Screen Information

TrayTip:=ProgramName . "`n" . HotkeyModifiersTip . "Number 0-9 or Letter a-z (zero always primary)`nRight-click for context menu"
Menu,Tray,Tip,%TrayTip%
Menu,Tray,Icon,%IconFile%
Return

PerformMove(MoveMonNum,OffX,OffY)
{
  global MoveX,MoveY
  Gosub,CheckNumMonsChanged ; before performing move, check if the number of monitors has changed
  RestoreDPI:=DllCall("SetThreadDpiAwarenessContext","ptr",-3,"ptr") ; enable per-monitor DPI awareness and save current value to restore it when done - thanks to lexikos for this
  SysGet,Coordinates%MoveMonNum%,Monitor,%MoveMonNum% ; get coordinates for this monitor
  Left:=Coordinates%MoveMonNum%Left
  Right:=Coordinates%MoveMonNum%Right
  Top:=Coordinates%MoveMonNum%Top
  Bottom:=Coordinates%MoveMonNum%Bottom
  If (OffX=-1)
    MoveX:=Left+(Floor(0.5*(Right-Left))) ; center
  Else
    MoveX:=Left+OffX
  If (OffY=-1)
    MoveY:=Top+(Floor(0.5*(Bottom-Top))) ; center
  Else
    MoveY:=Top+OffY
  DllCall("SetCursorPos","int",MoveX,"int",MoveY) ; first call to move it - usually works but not always
  Sleep,10 ; wait a few milliseconds for first call to settle
  DllCall("SetCursorPos","int",MoveX,"int",MoveY) ; second call sometimes needed
  DllCall("SetThreadDpiAwarenessContext","ptr",RestoreDPI,"ptr") ; restore previous DPI awareness - thanks to lexikos for this
  Return
}

CheckNumMonsChanged:
SysGet,CurrNumMons,MonitorCount ; current number of monitors
If (OrigNumMons!=CurrNumMons)
{
  MsgBox,4144,Warning,Number of monitors changed since script was run`nOriginal=%OrigNumMons%`nCurrent=%CurrNumMons%`n`nWill reload script when you click OK button
  ; since the number of monitors has changed, disable all hotkeys - the reload will enable the new/correct ones
  Loop,%OrigNumMons% ; process all current monitors
  {
    MonitorHotkey:=HotkeyModifiers . MonitorIDs[A_Index]
    Hotkey,%MonitorHotkey%,,Off ; disable hotkey
  }
  Reload
  Sleep,2000 ; give Reload two seconds to work during this Sleep - if Reload successful, will never get to code below
  MsgBox,4112,Error,Unable to reload script`nWill exit when you click OK button`nYou will have to re-run the script manually
  ExitApp
}
Return ; number of monitors has not changed

ContextMenu:
If (A_ThisMenuItem="Show &Monitor and Virtual Screen Information")
{
  Gosub,CheckNumMonsChanged ; before showing info, check if number of monitors has changed
  RestoreDPI:=DllCall("SetThreadDpiAwarenessContext","ptr",-3,"ptr") ; enable per-monitor DPI awareness and save current value to restore it when done - thanks to lexikos for this
  AllMons:=""
  Loop,%OrigNumMons% ; process all monitors
  {
    MonNum:=A_Index
    SysGet,MonName,MonitorName,%MonNum% ; get name of this monitor
    SysGet,Coordinates%MonNum%,Monitor,%MonNum% ; get coordinates for this monitor
    Left:=Coordinates%MonNum%Left
    Right:=Coordinates%MonNum%Right
    Top:=Coordinates%MonNum%Top
    Bottom:=Coordinates%MonNum%Bottom
    AllMons:=AllMons . MonitorIDs[A_Index] . ": " . MonName . " L=" . Left . " R=" . Right . " T=" . Top . " B=" . Bottom . "`n"
  }
  SysGet,VirtualX,76 ; coordinate for left side of virtual screen
  SysGet,VirtualY,77 ; coordinate for top of virtual screen
  SysGet,VirtualW,78 ; width of virtual screen
  SysGet,VirtualH,79 ; height of virtual screen
  SysGet,PrimaryMonNum,MonitorPrimary ; get number of Primary monitor (the Windows number, not the script identifier)
  MsgBox,4160,%ProgramName%,Monitor numbers/letters`, names`, coordinates:`n%AllMons%Primary monitor number/letter: %PrimaryMonNum%`n`nVirtual screen information:`nVirtualX=%VirtualX%`nVirtualY=%VirtualY%`nVirtualW=%VirtualW%`nVirtualH=%VirtualH%
  DllCall("SetThreadDpiAwarenessContext","ptr",RestoreDPI,"ptr") ; restore previous DPI awareness - thanks to lexikos for this
  Return
}

If (A_ThisMenuItem="Start with &Windows (On/Off toggle)")
{
  If (FileExist(StartupLink))
  {
    ; it's on, so this click turns it off
    Menu,Tray,Uncheck,Start with &Windows (On/Off toggle)
    FileDelete,%StartupLink%
    Return
  }
  Else
  {
    ; it's off, so this click turns it on
    Menu,Tray,Check,Start with &Windows (On/Off toggle)
    FileCreateShortcut,%A_ScriptFullPath%,%StartupLink%,%A_ScriptDir%,,%ProgramName%,%IconFile%
    {
      If (ErrorLevel!=0)
      {
        MsgBox,4112,Fatal Error,Error Level=%ErrorLevel% trying to create Startup shortcut:`n%StartupLink%
        ExitApp
      }
    }
    Return
  }
}

If (A_ThisMenuItem="&Reload Script")
{
  Reload
  Sleep,2000 ; give Reload two seconds to work during this Sleep - if Reload successful, will never get to code below
  MsgBox,4112,Error,Unable to reload script`nWill exit when you click OK button`nYou will have to reload the script manually
  ExitApp
}

If (A_ThisMenuItem="&About")
{
  MsgBox,4160,About %ProgramName%,%A_ScriptFullPath%`n`nVersion %Version%`n`nModified: %ScriptDateTime%
  Return
}

If (A_ThisMenuItem="E&xit")
{
  MsgBox,4388,%ProgramName% - Terminate?,Are you sure you want to quit and deactivate all hotkeys?
  IfMsgBox,No
    Return
  ExitApp
}

; Credit for this function: Maestr0 on autohotkey.com https://www.autohotkey.com/boards/viewtopic.php?t=54557
MWAGetMonitorMouseIsIn() ; we didn't actually need the "Monitor = 0"
{
	; get the mouse coordinates first
	Coordmode, Mouse, Screen	; use Screen, so we can compare the coords with the sysget information`
	MouseGetPos, Mx, My

	SysGet, MonitorCount, 80	; monitorcount, so we know how many monitors there are, and the number of loops we need to do
	Loop, %MonitorCount%
	{
		SysGet, mon%A_Index%, Monitor, %A_Index%	; "Monitor" will get the total desktop space of the monitor, including taskbars

		if ( Mx >= mon%A_Index%left ) && ( Mx < mon%A_Index%right ) && ( My >= mon%A_Index%top ) && ( My < mon%A_Index%bottom )
		{
			ActiveMon := A_Index
			break
		}
	}
	return ActiveMon
}

; What I wrote :)
; Assuming h is left, l is right a la vim, this assumes monitors are ordered left to right in numerical order. May need adjusting.
^l::
{
    MonID:=MWAGetMonitorMouseIsIn()
    MonID+=1
    SysGet, MonitorCount, 80
    if (MonID > MonitorCount) {
        PerformMove(1,OffsetX,OffsetY)
    } else {
        PerformMove(MonID,OffsetX,OffsetY)
    }
    return
}

^h::
{
    MonID:=MWAGetMonitorMouseIsIn()
    MonID-=1
    if (MonID <= 0) {
        SysGet, MonitorCount, 80
        PerformMove(MonitorCount,OffsetX,OffsetY)
    } else {
        PerformMove(MonID,OffsetX,OffsetY)
    }
    return
}