def test_launch_url_uses_windows_protocol_handler(monkeypatch):
    from winpilot.action import executor

    opened = []
    popen_calls = []

    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor.os, "startfile", lambda target: opened.append(target), raising=False)
    monkeypatch.setattr(executor.subprocess, "Popen", lambda *a, **k: popen_calls.append((a, k)))

    result = executor.launch("http://127.0.0.1:8877/gui_agent_target.html")

    assert result["ok"] is True
    assert result["via"] == "url"
    assert opened == ["http://127.0.0.1:8877/gui_agent_target.html"]
    assert popen_calls == []


def test_launch_browser_wrapper_extracts_url(monkeypatch):
    from winpilot.action import executor

    opened = []
    popen_calls = []

    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor.os, "startfile", lambda target: opened.append(target), raising=False)
    monkeypatch.setattr(executor.subprocess, "Popen", lambda *a, **k: popen_calls.append((a, k)))

    result = executor.launch('start msedge http://127.0.0.1:8877/gui_agent_target.html')

    assert result["ok"] is True
    assert result["target"] == "http://127.0.0.1:8877/gui_agent_target.html"
    assert opened == ["http://127.0.0.1:8877/gui_agent_target.html"]
    assert popen_calls == []


def test_launch_non_url_keeps_shell_path(monkeypatch):
    from winpilot.action import executor

    opened = []
    popen_calls = []

    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor.os, "startfile", lambda target: opened.append(target), raising=False)
    monkeypatch.setattr(executor.subprocess, "Popen", lambda *a, **k: popen_calls.append((a, k)))

    result = executor.launch("notepad")

    assert result["ok"] is True
    assert opened == []
    assert popen_calls
