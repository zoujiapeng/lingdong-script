from types import SimpleNamespace


def test_text_appears_scoped_to_target_window(monkeypatch):
    from winpilot.action import verify

    monkeypatch.setattr(verify.win32gui, "GetForegroundWindow", lambda: 123)
    monkeypatch.setattr(verify.win32gui, "IsWindowVisible", lambda hwnd: True)
    monkeypatch.setattr(
        verify.fusion,
        "observe",
        lambda *a, **k: SimpleNamespace(elements=[]),
    )
    monkeypatch.setattr(verify.ocr, "find_text", lambda *a, **k: True)

    out = verify._eval_once({"text_appears": "健康科技"}, hwnd=123, level=2)
    assert not out.verified
    assert "未找到文字" in out.detail


def test_text_appears_can_use_fullscreen_without_target(monkeypatch):
    from winpilot.action import verify

    monkeypatch.setattr(verify.win32gui, "GetForegroundWindow", lambda: 0)
    monkeypatch.setattr(verify.win32gui, "IsWindowVisible", lambda hwnd: False)
    monkeypatch.setattr(verify.ocr, "find_text", lambda *a, **k: True)

    out = verify._eval_once({"text_appears": "健康科技"}, hwnd=None, level=2)
    assert out.verified


def test_communication_done_requires_final_visible_payload(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner('用qq给我的手机发信息"健康科技"')
    runner.session.hwnd = 123
    runner.result_text = '任务完成: 已发送"健康科技"'
    runner._recent_results = [
        ("type_text", '{"ok": true, "verified": true}'),
        ("hotkey", '{"ok": true, "keys": ["enter"]}'),
    ]

    monkeypatch.setattr(
        "winpilot.agent.loop.fusion.observe",
        lambda *a, **k: SimpleNamespace(to_text=lambda n=50: 'Window: "QQ"\n会话列表\n我的手机'),
    )

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_a, **_k):
                    raise AssertionError("LLM judge should not run after fast rejection")

    assert runner._verify_done(_Client()) is False
    assert runner._done_rejects == 1


def test_input_done_requires_required_visible_text(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner(
        "打开 Sticky Notes (new)，点击新建笔记，输入两行：第一行=锚定修复测试，第二行=状态=完成。"
    )
    runner.session.hwnd = 123
    runner.result_text = "已完成"
    runner._recent_results = [
        ("read_text", "便笺\n新建笔记\n最近的笔记"),
    ]

    monkeypatch.setattr(
        "winpilot.agent.loop.fusion.observe",
        lambda *a, **k: SimpleNamespace(to_text=lambda n=50: 'Window: "Sticky Notes (new)"\n便笺\n最近的笔记'),
    )

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_a, **_k):
                    raise AssertionError("LLM judge should not run after fast rejection")

    assert runner._verify_done(_Client()) is False
    assert runner._done_rejects == 1


def test_gui_only_shell_policy():
    from winpilot.agent.loop import _gui_only_shell_allowed, _is_gui_only_task

    assert _is_gui_only_task("GUI-only test. Use Notepad.")
    assert _is_gui_only_task("\u5168\u7a0b\u4e3b\u8981\u64cd\u4f5c\u5fc5\u987b\u8d70GUI")
    assert _is_gui_only_task("\u5fc5\u987b\u901a\u8fc7GUI\u64cd\u4f5c\uff0c\u4e0d\u8981\u7528\u547d\u4ee4\u884c")
    assert _is_gui_only_task("GUI方式测试：打开画图，用鼠标拖动画布。")
    assert _is_gui_only_task("必须通过可见GUI操作，不要用脚本生成图片。")
    assert _gui_only_shell_allowed(
        'Test-Path "C:\\Users\\zou\\Desktop\\x.txt"; Get-Content "C:\\Users\\zou\\Desktop\\x.txt"'
    )
    assert not _gui_only_shell_allowed("Start-Process calc.exe")
    assert not _gui_only_shell_allowed("Set-Content C:\\Users\\zou\\Desktop\\x.txt hello")
    assert not _gui_only_shell_allowed("powershell -ExecutionPolicy Bypass -File C:\\Users\\zou\\gui_final.ps1")


def test_window_title_expect_condition(monkeypatch):
    from winpilot.action import verify

    monkeypatch.setattr(verify.win32gui, "GetForegroundWindow", lambda: 456)
    monkeypatch.setattr(verify.win32gui, "IsWindowVisible", lambda hwnd: True)
    monkeypatch.setattr(verify.win32, "_window_text", lambda hwnd: "WinPilotDeep_append.txt - Notepad")

    out = verify._eval_once({"window_title": "WinPilotDeep_append.txt"}, hwnd=123, level=0)

    assert out.verified


def test_save_task_refuses_cancel_button_click():
    from winpilot.agent.tools import ToolSession
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    session = ToolSession(task="Save this file as C:\\Users\\zou\\Desktop\\x.txt")
    session.snapshot = Snapshot(
        hwnd=1,
        title="Save As",
        region=Region(0, 0, 800, 600),
        elements=[UIElement(elem_id=7, role="button", text="Cancel", cx=10, cy=10, source="uia")],
    )

    result = session._t_click({"element_id": 7})

    assert "Refused to click" in result.text


def test_assistant_history_truncates_verbose_content():
    from winpilot.agent.loop import _sanitize_assistant

    msg = SimpleNamespace(content="x" * 2000, tool_calls=[])

    out = _sanitize_assistant(msg)

    assert len(out["content"]) < 1100
    assert "full response is kept in the LLM trace" in out["content"]


def test_drawing_done_requires_drag(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw a house on a canvas and save as C:\\Users\\zou\\Desktop\\x.png")
    runner.session.hwnd = 123
    runner.result_text = "done"
    runner._recent_results = [("click", '{"ok": true}')]

    monkeypatch.setattr(
        "winpilot.agent.loop.fusion.observe",
        lambda *a, **k: SimpleNamespace(to_text=lambda n=50: 'Window: "Paint"'),
    )

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_a, **_k):
                    raise AssertionError("LLM judge should not run after fast rejection")

    assert runner._verify_done(_Client()) is False
    assert runner._done_rejects == 1


def test_drawing_done_requires_requested_image_file(monkeypatch, tmp_path):
    from winpilot.agent.loop import AgentRunner

    missing = str(tmp_path / "missing.png")
    runner = AgentRunner(f"draw a house on a canvas and save as {missing}")
    runner.session.hwnd = 123
    runner.result_text = "done"
    runner._steps_log = ['drag({"x1": 1, "y1": 1, "x2": 20, "y2": 20})']
    runner._recent_results = [("drag", '{"ok": true, "visual_change_detected": true}')]

    monkeypatch.setattr(
        "winpilot.agent.loop.fusion.observe",
        lambda *a, **k: SimpleNamespace(to_text=lambda n=50: 'Window: "Paint"'),
    )

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_a, **_k):
                    raise AssertionError("LLM judge should not run after fast rejection")

    assert runner._verify_done(_Client()) is False
    assert runner._done_rejects == 1


def test_drawing_done_requires_requested_colors(monkeypatch, tmp_path):
    import cv2
    import numpy as np

    from winpilot.agent.loop import AgentRunner

    out = tmp_path / "black_only.png"
    image = np.full((120, 160, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (20, 20), (100, 80), (0, 0, 0), 3)
    cv2.imwrite(str(out), image)

    runner = AgentRunner(f"draw a red sun and blue house on a canvas and save as {out}")
    runner.session.hwnd = 123
    runner.result_text = "done"
    runner._steps_log = ['drag({"x1": 1, "y1": 1, "x2": 20, "y2": 20})']
    runner._recent_results = [("drag", '{"ok": true, "visual_change_detected": true}')]

    monkeypatch.setattr(
        "winpilot.agent.loop.fusion.observe",
        lambda *a, **k: SimpleNamespace(to_text=lambda n=50: 'Window: "Paint"'),
    )

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_a, **_k):
                    raise AssertionError("LLM judge should not run after fast rejection")

    assert runner._verify_done(_Client()) is False
    assert runner._done_rejects == 1


def test_simple_drawing_done_uses_deterministic_visual_evidence(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw a house outline on the canvas")
    runner.session.hwnd = 123
    runner.result_text = "done"
    runner._steps_log = [
        'draw_strokes({"strokes": [[[10, 10], [50, 50]]], "duration": 0.3})'
    ]
    runner._recent_results = [
        ("draw_strokes", '{"ok": true, "visual_change_detected": true}'),
    ]

    monkeypatch.setattr(
        "winpilot.agent.loop.fusion.observe",
        lambda *a, **k: SimpleNamespace(to_text=lambda n=50: 'Window: "Paint"'),
    )

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_a, **_k):
                    raise AssertionError("LLM judge should not run with deterministic drawing evidence")

    assert runner._verify_done(_Client()) is True
    assert runner._done_verified is True


def test_desktop_icon_drag_done_uses_final_uia_position(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner(
        "把桌面上的 WinPilotDragTest_123 文件夹图标通过鼠标拖动到桌面右上角附近。"
    )
    runner.session.hwnd = 123
    runner.result_text = "done"
    runner._steps_log = ['drag({"x1": 100, "y1": 500, "x2": 1850, "y2": 80})']
    runner._recent_results = [
        ("drag", '{"ok": true, "visual_change_detected": true}'),
    ]

    screen = (
        'Window: "Program Manager" hwnd=1 rect=(0,0,1920x1080)\n'
        '[160] listitem "WinPilotDragTest_123" center=(1852,58) '
        'bbox=(1805,5,94,107) checked,selected,focused src=uia'
    )
    monkeypatch.setattr(
        "winpilot.agent.loop.fusion.observe",
        lambda *a, **k: SimpleNamespace(to_text=lambda n=50: screen),
    )

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_a, **_k):
                    raise AssertionError("LLM judge should not run with deterministic drag evidence")

    assert runner._verify_done(_Client()) is True
    assert runner._done_verified is True


def test_desktop_icon_drag_done_rejects_wrong_area(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner(
        "把桌面上的 WinPilotDragTest_123 文件夹图标通过鼠标拖动到桌面右上角附近。"
    )
    runner.session.hwnd = 123
    runner.result_text = "done"
    runner._steps_log = ['drag({"x1": 100, "y1": 500, "x2": 960, "y2": 540})']
    runner._recent_results = [
        ("drag", '{"ok": true, "visual_change_detected": true}'),
    ]

    screen = (
        'Window: "Program Manager" hwnd=1 rect=(0,0,1920x1080)\n'
        '[160] listitem "WinPilotDragTest_123" center=(960,540) '
        'bbox=(913,507,94,67) checked src=uia'
    )
    monkeypatch.setattr(
        "winpilot.agent.loop.fusion.observe",
        lambda *a, **k: SimpleNamespace(to_text=lambda n=50: screen),
    )

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_a, **_k):
                    return SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(content='{"done": false, "reason": "wrong area"}'))]
                    )

    assert runner._verify_done(_Client()) is False


def test_snapshot_to_text_query_surfaces_omitted_target():
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    elements = [
        UIElement(elem_id=i, role="listitem", text=f"item_{i}", cx=i, cy=i)
        for i in range(200)
    ]
    elements.append(
        UIElement(
            elem_id=201,
            role="listitem",
            text="WinPilotDragTest_999",
            cx=1852,
            cy=58,
            left=1805,
            top=5,
            width=94,
            height=67,
        )
    )
    snap = Snapshot(hwnd=1, title="Program Manager", region=Region(0, 0, 1920, 1080), elements=elements)

    plain = snap.to_text(max_elements=160)
    filtered = snap.to_text(max_elements=160, query="WinPilotDragTest_999")

    assert "WinPilotDragTest_999" not in plain
    assert 'Filtered elements matching query "winpilotdragtest_999"' in filtered
    assert "WinPilotDragTest_999" in filtered
    assert "center=(1852,58)" in filtered


def test_drawing_stops_mutation_after_plan_done_and_file_exists(tmp_path):
    from winpilot.agent.loop import AgentRunner

    out = tmp_path / "done.png"
    out.write_bytes(b"not-really-a-png-but-existing-is-enough-for-the-guard")
    runner = AgentRunner(f"draw a house on a canvas and save as {out}")
    runner._steps_log = ['drag({"x1": 1, "y1": 1, "x2": 20, "y2": 20})']
    runner.session.plan = SimpleNamespace(all_done=lambda: True)

    assert runner._drawing_should_stop_mutating()


def test_gui_search_done_requires_on_screen_query(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner(
        "Open Steam client and search Stardew Valley in the store page."
    )
    runner.session.hwnd = 123
    runner.result_text = "done"
    runner._recent_results = [
        ("run_shell", "Steam library manifests show Stardew Valley is not installed"),
    ]

    monkeypatch.setattr(
        "winpilot.agent.loop.fusion.observe",
        lambda *a, **k: SimpleNamespace(to_text=lambda n=50: 'Window: "Desktop"\nNo Steam window'),
    )

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_a, **_k):
                    raise AssertionError("LLM judge should not run after fast rejection")

    assert runner._verify_done(_Client()) is False
    assert runner._done_rejects == 1


def test_store_search_allows_store_deeplink_but_blocks_run_protocol():
    from winpilot.agent.loop import _blocks_store_search_action

    task = "Open Steam client and search Stardew Valley in the store page."

    assert _blocks_store_search_action(
        task, "launch", {"command": "steam://rungameid/413150"}
    )
    assert _blocks_store_search_action(
        task, "run_shell", {"command": "Start-Process steam://install/413150"}
    )
    assert not _blocks_store_search_action(
        task, "launch", {"command": "steam://store/413150"}
    )
    assert not _blocks_store_search_action(
        task, "run_shell", {"command": "Start-Process https://store.steampowered.com/app/413150/"}
    )


def test_upload_publish_blocks_media_playback_or_analysis_shell():
    from winpilot.agent.loop import _blocks_upload_media_analysis, _is_upload_publish_task

    task = "打开抖音，上传 C:\\Users\\zou\\Desktop\\demo.mp4，填写标题，但不要最终发布"

    assert _is_upload_publish_task(task)
    assert _is_upload_publish_task(
        "Use creator page with C:\\Users\\zou\\Desktop\\winpilot_douyin_test.mp4"
    )

    assert _blocks_upload_media_analysis(
        task, "run_shell", {"command": "ffprobe C:\\Users\\zou\\Desktop\\demo.mp4"}
    )
    assert _blocks_upload_media_analysis(
        task, "run_shell", {"command": "Start-Process 'C:\\Users\\zou\\Desktop\\demo.mp4'"}
    )
    assert _blocks_upload_media_analysis(
        task,
        "run_detached",
        {"script": "Start-Process wmplayer.exe -ArgumentList 'C:\\Users\\zou\\Desktop\\demo.mp4'"},
    )
    assert _blocks_upload_media_analysis(
        task,
        "launch",
        {"command": '"C:\\Program Files\\Windows Media Player\\wmplayer.exe" "C:\\Users\\zou\\Desktop\\demo.mp4"'},
    )
    assert _blocks_upload_media_analysis(
        task,
        "launch",
        {"command": '"C:\\Program Files\\VideoLAN\\VLC\\vlc.exe" "C:\\Users\\zou\\Desktop\\demo.mp4"'},
    )
    assert _blocks_upload_media_analysis(
        task,
        "launch",
        {"command": "C:\\Users\\zou\\Desktop\\demo.mp4"},
    )
    assert _blocks_upload_media_analysis(
        task,
        "find_program",
        {"name": "Windows Media Player"},
    )
    assert _blocks_upload_media_analysis(
        task,
        "find_program",
        {"name": "VLC media player"},
    )
    assert _blocks_upload_media_analysis(
        task,
        "run_shell",
        {"command": "Get-Command wmplayer.exe"},
    )
    assert not _blocks_upload_media_analysis(
        task, "run_shell", {"command": "Test-Path 'C:\\Users\\zou\\Desktop\\demo.mp4'"}
    )
    assert not _blocks_upload_media_analysis(
        task, "launch", {"command": "C:\\Program Files (x86)\\ByteDance\\douyin\\douyin.exe"}
    )


def test_upload_publish_task_does_not_inject_media_playbook(monkeypatch):
    from winpilot.agent import playbooks

    books = [
        playbooks.Playbook(
            name="media-playback",
            description="",
            content="media",
            keywords=("mp4",),
        ),
        playbooks.Playbook(
            name="file-dialog",
            description="",
            content="file dialog",
            keywords=("upload",),
        ),
    ]
    monkeypatch.setattr(playbooks, "load_all", lambda: books)
    monkeypatch.setattr(playbooks, "enabled", lambda: True)

    matched = playbooks.for_task("upload C:\\Users\\zou\\Desktop\\demo.mp4 to Douyin")

    assert [book.name for book in matched] == ["file-dialog"]
