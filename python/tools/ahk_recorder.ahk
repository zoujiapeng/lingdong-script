; AHK v2 宏录制器
; 输出协议（简单管道分隔，Python 端解析）:
;   READY|pid
;   STATUS|recording|events|elapsed
;   EVENT|click|x|y|button|pressed
;   EVENT|key|keyname|pressed
;   EVENT|move|x|y
;   RESULT|success|name|events|duration|[json_encoded_events]

#Requires AutoHotkey v2.0

_events := []
_recording := false
_start_tick := 0
_include_mouse := true
_include_keys := true
_macro_name := "unnamed"
_last_x := 0
_last_y := 0

Emit(s) {
    stdout := FileOpen("*", "w", "utf-8")
    stdout.WriteLine(s)
    stdout.Read(0)  ; flush
}

AddEvent(type, data) {
    global _events
    now := Round((A_TickCount - _start_tick) / 1000, 3)
    ev := {type: type, time: now}
    for k, v in data.OwnProps()
        ev.%k% := v
    _events.Push(ev)
}

EscapeArg(v) {
    return StrReplace(v, "|", "\p")
}

; ── F6 热键 ──
F6:: {
    global _recording
    if _recording
        StopRecording(True)
    else
        StartRecording("recording_" A_Now, True, True)
}

; ── 鼠标事件（全局钩子）──
OnMessage(0x0201, OnClick)  ; WM_LBUTTONDOWN
OnMessage(0x0204, OnClick)  ; WM_RBUTTONDOWN
OnMessage(0x0207, OnClick)  ; WM_MBUTTONDOWN

OnClick(wParam, lParam, msg, hwnd) {
    if !_recording || !_include_mouse
        return
    DllCall("GetCursorPos", "ptr", &pt := Buffer(8, 0))
    sx := NumGet(pt, 0, "int"), sy := NumGet(pt, 4, "int")
    btn := msg = 0x0201 ? "Button.left" : msg = 0x0204 ? "Button.right" : "Button.middle"
    _last_x := sx, _last_y := sy
    AddEvent("click", {x: sx, y: sy, button: btn, pressed: true})
    Emit("EVENT|click|" sx "|" sy "|" btn "|true")
}

OnMessage(0x0200, OnMouseMove)  ; WM_MOUSEMOVE
OnMouseMove(wParam, lParam, msg, hwnd) {
    if !_recording || !_include_mouse
        return 0
    DllCall("GetCursorPos", "ptr", &pt := Buffer(8, 0))
    sx := NumGet(pt, 0, "int"), sy := NumGet(pt, 4, "int")
    if Abs(sx - _last_x) < 10 && Abs(sy - _last_y) < 10
        return 0
    _last_x := sx, _last_y := sy
    AddEvent("move", {x: sx, y: sy})
    Emit("EVENT|move|" sx "|" sy)
    return 0
}

; ── 键盘事件 ──
~*a::
~*b:::
~*c:::
~*d:::
~*e:::
~*f:::
~*g:::
~*h:::
~*i:::
~*j:::
~*k:::
~*l:::
~*m:::
~*n:::
~*o:::
~*p:::
~*q:::
~*r:::
~*s:::
~*t:::
~*u:::
~*v:::
~*w:::
~*x:::
~*y:::
~*z:::
~*0:::
~*1:::
~*2:::
~*3:::
~*4:::
~*5:::
~*6:::
~*7:::
~*8:::
~*9:::
~*Space:::
~*Enter:::
~*Tab:::
~*Backspace:::
~*Delete:::
~*Insert:::
~*Home:::
~*End:::
~*PgUp:::
~*PgDn:::
~*Left:::
~*Right:::
~*Up:::
~*Down:::
~*Escape:::
~*PrintScreen:::
~*LShift:::
~*RShift:::
~*LControl:::
~*RControl:::
~*LAlt:::
~*RAlt:::
~*Numpad0:::
~*Numpad1:::
~*Numpad2:::
~*Numpad3:::
~*Numpad4:::
~*Numpad5:::
~*Numpad6:::
~*Numpad7:::
~*Numpad8:::
~*Numpad9:::
~*F1:::
~*F2:::
~*F3:::
~*F4:::
~*F5:::
~*F7:::
~*F8:::
~*F9:::
~*F10:::
~*F11:::
~*F12:::
~*SC029:::
~*SC007:::
~*SC008:::
~*SC00C:::
~*SC00B:::
~*SC00D:::
~*SC033:::
~*SC034:::
~*SC035:::
~*SC028:::
{
    if !_recording || !_include_keys
        return
    key := SubStr(A_ThisHotkey, 3)  ; 去掉 ~*
    if key = "F6"
        return
    AddEvent("key", {key: key, pressed: true})
    Emit("EVENT|key|" EscapeArg(key) "|true")
}

StartRecording(name, include_mouse, include_keys) {
    global _recording, _events, _start_tick, _include_mouse, _include_keys, _macro_name
    if _recording
        return
    _events := []
    _start_tick := A_TickCount
    _include_mouse := include_mouse
    _include_keys := include_keys
    _macro_name := name
    _recording := true
    Emit("STATUS|recording|0|0")
    Emit("RESULT|true|RECORDING_START|" name)
}

StopRecording(send_output := true) {
    global _recording, _events, _macro_name, _include_mouse, _include_keys
    if !_recording
        return
    _recording := false
    duration := Round((A_TickCount - _start_tick) / 1000, 1)
    events := _events.Clone()

    if send_output {
        Emit("RESULT|true|" _macro_name "|" events.Length "|" duration "|" events.Length " events")
    }
    _events := []
}

; ── stdin 监听 ──
ListenStdin() {
    stdin := FileOpen("*", "r", "utf-8")
    while !stdin.AtEOF {
        line := Trim(stdin.ReadLine())
        if line = ""
            continue
        parts := StrSplit(line, "|")
        if parts[1] = "start" {
            name := parts.Length >= 2 ? parts[2] : "recording_" A_Now
            mouse := parts.Length >= 3 ? parts[3] = "1" : true
            keys := parts.Length >= 4 ? parts[4] = "1" : true
            StartRecording(name, mouse, keys)
        } else if parts[1] = "stop" {
            StopRecording(true)
        } else if parts[1] = "ping" {
            Emit("PONG|" (_recording ? "1" : "0"))
        }
    }
}

Emit("READY|" DllCall("GetCurrentProcessId"))
SetTimer(ListenStdin, -1)
Persistent true