def test_title_contains_normalizes_underscores_spaces_and_case():
    from winpilot.perception import win32

    assert win32.title_contains("WinPilot GUI Agent Target - Google Chrome", "gui_agent_target")
    assert win32.title_contains("WinPilot-GUI Agent Target", "GUI_Agent")
    assert not win32.title_contains("Untitled - Notepad", "gui_agent_target")
