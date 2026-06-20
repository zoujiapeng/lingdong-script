def test_foreground_modal_overlay_detection(monkeypatch):
    from winpilot.perception import fusion
    from winpilot.utils.screenshot import Region

    monkeypatch.setattr(fusion.win32gui, "GetForegroundWindow", lambda: 200)
    monkeypatch.setattr(fusion.win32gui, "GetClassName", lambda hwnd: "XAMLModalWindow")
    monkeypatch.setattr(fusion.win32gui, "GetWindowText", lambda hwnd: "")
    monkeypatch.setattr(fusion.win32gui, "GetWindowRect", lambda hwnd: (120, 120, 520, 520))

    assert fusion._foreground_modal_over_target(100, Region(100, 100, 600, 600))


def test_no_modal_overlay_when_foreground_is_target(monkeypatch):
    from winpilot.perception import fusion
    from winpilot.utils.screenshot import Region

    monkeypatch.setattr(fusion.win32gui, "GetForegroundWindow", lambda: 100)

    assert not fusion._foreground_modal_over_target(100, Region(100, 100, 600, 600))
