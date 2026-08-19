from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


def _load_harness():
    path = Path(__file__).resolve().parents[3] / "scripts" / "client_e2e_run.py"
    spec = importlib.util.spec_from_file_location("client_e2e_run", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves the module by name while decorating ClientIdentity.
    import sys

    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_harness_keeps_the_exact_eleven_and_one_combined_long_goal():
    harness = _load_harness()

    assert list(harness.ORIGINAL_CASES) == [str(index) for index in range(1, 12)]
    assert harness.ORIGINAL_CASES["11"]["task"] == (
        "去qq找我的手机持续聊天,他如果发@电脑就说明在命令你,你要照做"
    )
    combined = harness.MIXED_CASES["mix-all-11"]["task"]
    positions = [
        combined.index(harness.ORIGINAL_CASES[str(index)]["task"])
        for index in range(1, 12)
    ]
    assert positions == sorted(positions)
    assert harness.MIXED_CASES["mix-all-11"]["max_steps"] == 400


def test_harness_has_no_execution_policy_selector_argument():
    source = (
        Path(__file__).resolve().parents[3]
        / "scripts"
        / "client_e2e_run.py"
    ).read_text(encoding="utf-8")

    assert "--execution-constraint" not in source
    assert "EXECUTION_CONSTRAINT_SELECTOR" not in source


def test_control_value_uses_complete_text_when_value_pattern_is_truncated():
    harness = _load_harness()
    full = "one long goal that continues beyond the value pattern"
    prefix = full[:13]

    class Range:
        def GetText(self, length):
            assert length == -1
            return full + "\r\n"

    class Control:
        ControlTypeName = "EditControl"

        def GetPattern(self, pattern_id):
            if pattern_id == harness.auto.PatternId.ValuePattern:
                return SimpleNamespace(Value=prefix)
            if pattern_id == harness.auto.PatternId.TextPattern:
                return SimpleNamespace(DocumentRange=Range())
            return None

    assert harness._control_value(Control()) == full


def test_control_value_keeps_empty_editor_empty_when_text_is_accessible_name():
    harness = _load_harness()

    class Range:
        def GetText(self, length):
            assert length == -1
            return f"{harness.GOAL_INPUT}\r\n"

    class Control:
        Name = harness.GOAL_INPUT
        ControlTypeName = "EditControl"

        def GetPattern(self, pattern_id):
            if pattern_id == harness.auto.PatternId.ValuePattern:
                return SimpleNamespace(Value="")
            if pattern_id == harness.auto.PatternId.TextPattern:
                return SimpleNamespace(DocumentRange=Range())
            return None

    assert harness._control_value(Control()) == ""


def test_document_text_can_verify_a_long_goal_when_edit_value_is_truncated(
    monkeypatch,
):
    harness = _load_harness()
    goal = "one complete long goal"

    class Range:
        def GetText(self, length):
            assert length == -1
            return f"before {goal} after"

    document = SimpleNamespace(
        GetPattern=lambda pattern_id: SimpleNamespace(DocumentRange=Range())
    )
    monkeypatch.setattr(
        harness,
        "_visible_descendants",
        lambda window, control_type: [document],
    )

    assert harness._document_contains_text(object(), goal) is True


def test_named_button_lookup_short_circuits_a_large_accessibility_tree(
    monkeypatch,
):
    harness = _load_harness()
    visited = []

    class Control:
        def __init__(self, name, control_type="ButtonControl"):
            self.Name = name
            self.ControlTypeName = control_type
            self.IsOffscreen = False

    def walk(_window, *, includeTop):
        assert includeTop is False
        controls = [
            Control("document", "DocumentControl"),
            Control(harness.NAV_AGENT),
        ]
        controls.extend(Control(f"trace-{index}") for index in range(10_000))
        for control in controls:
            visited.append(control.Name)
            yield control, 1

    monkeypatch.setattr(harness.auto, "WalkControl", walk)

    match = harness._first_visible_descendant(
        object(),
        "Button",
        name=harness.NAV_AGENT,
    )

    assert match.Name == harness.NAV_AGENT
    assert visited == ["document", harness.NAV_AGENT]


def test_partial_name_lookup_short_circuits_the_goal_editor(
    monkeypatch,
):
    harness = _load_harness()
    visited = []

    class Control:
        ControlTypeName = "EditControl"
        IsOffscreen = False

        def __init__(self, name):
            self.Name = name

    def walk(_window, *, includeTop):
        assert includeTop is False
        for control in (
            Control("filter"),
            Control(harness.GOAL_INPUT),
            Control("late"),
        ):
            visited.append(control.Name)
            yield control, 1

    monkeypatch.setattr(harness.auto, "WalkControl", walk)

    match = harness._first_visible_descendant(
        object(),
        "Edit",
        name=harness.GOAL_INPUT,
    )

    assert match.Name == harness.GOAL_INPUT
    assert visited == [
        "filter",
        harness.GOAL_INPUT,
    ]


def test_goal_entry_uses_native_keyboard_input_without_value_pattern(monkeypatch):
    harness = _load_harness()
    calls = []

    class Control:
        def Click(self):
            calls.append(("click",))

    monkeypatch.setattr(
        harness.auto,
        "SendKeys",
        lambda text, **kwargs: calls.append(("keys", text, kwargs)),
    )
    monkeypatch.setattr(
        harness.auto,
        "SendUnicodeChar",
        lambda char, *, charMode: calls.append(("unicode", ord(char), charMode)) or 1,
    )
    monkeypatch.setattr(harness.time, "sleep", lambda _seconds: None)

    evidence = harness._type_control_text(Control(), "A🙂")

    assert calls[:2] == [
        ("click",),
        ("keys", "{Ctrl}a{Delete}", {"interval": 0.01, "waitTime": 0.1}),
    ]
    assert [item[1] for item in calls[2:]] == [ord("A"), 0xD83D, 0xDE42]
    assert evidence == {
        "control_discovery": "windows_uia",
        "goal_entry": "windows_sendinput_unicode",
        "requested_codepoints": 2,
        "submitted_utf16_units": 3,
        "successful_unicode_calls": 3,
    }


def test_cold_start_clicks_new_conversation_and_proves_blank_composer(
    monkeypatch,
    tmp_path,
):
    harness = _load_harness()
    driver = object.__new__(harness.ElectronClientDriver)
    driver.output_dir = tmp_path
    navigated = []
    captures = []
    invoked = []
    refreshes = []
    state = {
        "workbench_open": True,
        "drawer_open": False,
        "new_conversation": False,
    }
    drawer = object()
    stale_root = object()
    fresh_root = object()

    class NewConversationButton:
        def __init__(self, owner):
            self.owner = owner

        def GetInvokePattern(self):
            return SimpleNamespace(Invoke=self._invoke)

        def _invoke(self):
            invoked.append(self.owner)
            if self.owner == "drawer":
                state["drawer_open"] = False
                state["new_conversation"] = True

        def Click(self):
            self._invoke()

    drawer_button = NewConversationButton("drawer")
    background_button = NewConversationButton("background")

    class GoalInput:
        def GetPattern(self, pattern_id):
            assert pattern_id == harness.auto.PatternId.ValuePattern
            return SimpleNamespace(Value="")

    driver.restore_and_focus = lambda: None
    driver.window = stale_root
    driver.identity = SimpleNamespace(hwnd=4242)

    def navigate(title):
        navigated.append(title)
        if title == harness.CLOSE_WORKBENCH:
            state["workbench_open"] = False
        elif title == harness.OPEN_CONVERSATIONS and state["workbench_open"]:
            raise AssertionError("conversation drawer is hidden by the workbench")
        elif title == harness.OPEN_CONVERSATIONS:
            state["drawer_open"] = True
        elif title == harness.NEW_CONVERSATION:
            raise AssertionError("new conversation lookup must be drawer-scoped")

    def capture(name):
        captures.append(name)
        return str(tmp_path / name)

    def find(_window, control_type, *, name="", **_kwargs):
        if (
            _window in {stale_root, fresh_root}
            and control_type == "Group"
            and name == harness.CONVERSATION_DRAWER
        ):
            return drawer if state["drawer_open"] else None
        if (
            _window is drawer
            and control_type == "Button"
            and name == harness.NEW_CONVERSATION
        ):
            return drawer_button
        if (
            _window in {stale_root, fresh_root}
            and control_type == "Button"
            and name == harness.NEW_CONVERSATION
        ):
            return background_button
        if (
            state["new_conversation"]
            and _window is fresh_root
            and control_type == "Group"
            and name == harness.NEW_CONVERSATION
        ):
            return object()
        if (
            state["new_conversation"]
            and _window is fresh_root
            and control_type == "Edit"
            and name == harness.GOAL_INPUT
        ):
            return GoalInput()
        return None

    driver.navigate = navigate
    driver.capture_window = capture

    def refresh(hwnd):
        assert hwnd == 4242
        refreshes.append(hwnd)
        return fresh_root

    monkeypatch.setattr(harness.auto, "ControlFromHandle", refresh)
    monkeypatch.setattr(harness, "_first_visible_descendant", find)
    monkeypatch.setattr(harness.time, "sleep", lambda _seconds: None)

    result = driver.start_new_conversation_through_client()

    assert state["workbench_open"] is False
    assert navigated == [
        harness.NAV_AGENT,
        harness.CLOSE_WORKBENCH,
        harness.OPEN_CONVERSATIONS,
    ]
    assert invoked == ["drawer"]
    assert refreshes == [4242]
    assert driver.window is fresh_root
    assert captures == ["before-new-conversation.png", "new-conversation.png"]
    assert result == {
        "action": "new_conversation",
        "aria_label": harness.NEW_CONVERSATION,
        "postcondition": {
            "conversation_drawer_present": False,
            "new_conversation_region_present": True,
            "goal_input": "",
        },
        "proof": {
            "before": str(tmp_path / "before-new-conversation.png"),
            "after": str(tmp_path / "new-conversation.png"),
        },
    }

    source = (
        Path(__file__).resolve().parents[3]
        / "scripts"
        / "client_e2e_run.py"
    ).read_text(encoding="utf-8")
    assert "清空会话记忆" not in source
    assert r"^会话 \d+$" not in source


def test_navigation_reasserts_the_client_foreground_before_click(monkeypatch):
    harness = _load_harness()
    driver = object.__new__(harness.ElectronClientDriver)
    events = []
    driver.restore_and_focus = lambda: events.append("focus")
    driver.button = lambda title, timeout=0.0: SimpleNamespace(
        Click=lambda: events.append(("click", title, timeout)),
    )
    monkeypatch.setattr(harness.time, "sleep", lambda _seconds: None)

    driver.navigate("destination")

    assert events == ["focus", ("click", "destination", 5.0)]


def test_trace_snapshot_tracks_outer_arp_jsonl_files_not_artifact_directories(
    monkeypatch,
    tmp_path,
):
    harness = _load_harness()
    (tmp_path / "arp-one.jsonl").write_text("{}\n", encoding="utf-8")
    (tmp_path / "arp-directory").mkdir()
    (tmp_path / "20260718-artifacts").mkdir()
    (tmp_path / "other.jsonl").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(harness, "TRACE_ROOT", tmp_path)

    assert harness._trace_names() == {"arp-one"}


def test_runtime_status_retries_a_transient_managed_endpoint_timeout(
    monkeypatch,
):
    harness = _load_harness()
    driver = object.__new__(harness.ElectronClientDriver)
    driver._runtime_url = "http://127.0.0.1:1"
    driver._runtime_token = "test-token"
    calls = {"count": 0}

    class Response:
        def __enter__(self):
            return SimpleNamespace(
                read=lambda: b"",
            )

        def __exit__(self, *_args):
            return False

    def urlopen(_request, *, timeout):
        assert timeout == 3
        calls["count"] += 1
        if calls["count"] == 1:
            raise TimeoutError("transient status timeout")
        return Response()

    monkeypatch.setattr(harness.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(
        harness.json,
        "load",
        lambda _response: {
            "running": True,
            "arp_running": True,
            "runtime_jobs": [
                {
                    "job_id": "arp-job-test",
                    "kind": "arp",
                    "status": "running",
                    "created_at": 10.0,
                    "started_at": 10.1,
                    "metadata": {
                        "run_id": "arp-test",
                        "runtime_version": "vnext",
                        "graph_id": "vnext-live-agent-test",
                        "private": "must-not-project",
                    },
                    "error": "",
                    "result": {"canonical_arguments": {"x": 17}},
                }
            ],
            "desktop_action_queue": {
                "submitted": 1,
                "completed": 0,
                "last_action": "click",
                "async_latest": [
                    {"physical_arguments": {"x": 17, "y": 19}}
                ],
            },
            "current_run": {
                "run_id": "arp-test",
                "kind": "arp",
                "runtime_version": "vnext",
                "runtime_budget": {
                    "max_turns": 30,
                    "max_runtime_s": 300,
                    "max_model_calls": 60,
                },
                "no_legacy_fallback": True,
                "status": "running",
                "phase": "executing",
                "goal": "test",
                "current_node_id": "full-goal",
                "current_tool": None,
                "error": None,
            },
        },
    )
    monkeypatch.setattr(harness.time, "sleep", lambda _seconds: None)

    status = driver.runtime_status()
    assert status["current_run"]["run_id"] == "arp-test"
    assert status["current_run"]["runtime_version"] == "vnext"
    assert status["current_run"]["runtime_budget"] == {
        "max_turns": 30,
        "max_runtime_s": 300,
        "max_model_calls": 60,
    }
    assert status["current_run"]["no_legacy_fallback"] is True
    assert status["runtime_jobs"] == [
        {
            "job_id": "arp-job-test",
            "kind": "arp",
            "status": "running",
            "created_at": 10.0,
            "started_at": 10.1,
            "metadata": {
                "run_id": "arp-test",
                "runtime_version": "vnext",
                "graph_id": "vnext-live-agent-test",
            },
            "error": "",
        }
    ]
    assert status["desktop_action_queue"] == {
        "submitted": 1,
        "completed": 0,
        "last_action": "click",
    }
    serialized = json.dumps(status, sort_keys=True)
    assert "must-not-project" not in serialized
    assert "canonical_arguments" not in serialized
    assert "physical_arguments" not in serialized
    assert calls["count"] == 2


def test_runtime_status_rejects_a_wrong_control_token_without_retrying(
    monkeypatch,
):
    harness = _load_harness()
    driver = object.__new__(harness.ElectronClientDriver)
    driver._runtime_url = "http://127.0.0.1:1"
    driver._runtime_token = "wrong-token"
    calls = {"count": 0}

    def urlopen(request, *, timeout):
        calls["count"] += 1
        raise harness.urllib.error.HTTPError(
            request.full_url,
            401,
            "Authentication required",
            {},
            None,
        )

    monkeypatch.setattr(harness.urllib.request, "urlopen", urlopen)

    try:
        driver.runtime_status()
    except RuntimeError as exc:
        assert str(exc) == "managed runtime rejected the Electron control token"
    else:
        raise AssertionError("wrong managed runtime token was accepted")
    assert calls["count"] == 1


def test_cold_start_runtime_probe_uses_one_long_request_budget():
    harness = _load_harness()
    driver = object.__new__(harness.ElectronClientDriver)
    calls = []
    driver.runtime_status = lambda **kwargs: calls.append(kwargs) or {"running": False}

    assert driver.wait_for_runtime_ready() == {"running": False}
    assert calls == [
        {
            "probe_timeout_s": 45.0,
            "request_timeout_s": 30.0,
        }
    ]


def test_wait_for_result_accepts_vnext_job_before_current_run_projection():
    harness = _load_harness()
    driver = object.__new__(harness.ElectronClientDriver)
    statuses = [
        {
            "running": True,
            "arp_running": True,
            "current_run": {
                "run_id": "arp-previous",
                "goal": "previous",
                "status": "failed",
            },
            "runtime_jobs": [
                {
                    "job_id": "arp-job-new",
                    "kind": "arp",
                    "status": "running",
                    "metadata": {
                        "run_id": "arp-new",
                        "runtime_version": "vnext",
                        "graph_id": "graph-new",
                    },
                }
            ],
        },
        {
            "running": False,
            "arp_running": False,
            "current_run": {
                "run_id": "arp-new",
                "goal": "goal",
                "status": "failed",
            },
            "runtime_jobs": [
                {
                    "job_id": "arp-job-new",
                    "kind": "arp",
                    "status": "failed",
                    "metadata": {
                        "run_id": "arp-new",
                        "runtime_version": "vnext",
                        "graph_id": "graph-new",
                    },
                }
            ],
        },
    ]
    driver.runtime_status = (
        lambda: statuses.pop(0) if len(statuses) > 1 else statuses[0]
    )
    driver.buttons = lambda: []
    driver.texts = lambda: []
    driver._topbar_status = lambda _task: ""

    result = driver.wait_for_result(
        "goal",
        5,
        previous_run_id="arp-previous",
        accepted_run_id="arp-new",
    )

    assert result["outcome"] == "terminal"
    assert result["run_id"] == "arp-new"
    assert result["status"] == "failed"


def test_wait_for_result_does_not_treat_followup_send_as_running_task_terminal(
    monkeypatch,
):
    harness = _load_harness()
    driver = object.__new__(harness.ElectronClientDriver)
    statuses = [
        {
            "running": False,
            "current_run": {
                "run_id": "arp-new",
                "goal": "goal",
                "status": "running",
            },
            "runtime_jobs": [
                {
                    "status": "running",
                    "metadata": {"run_id": "arp-new"},
                }
            ],
        },
        {
            "running": False,
            "current_run": {
                "run_id": "arp-new",
                "goal": "goal",
                "status": "done",
            },
        },
    ]
    calls = []

    def runtime_status():
        calls.append(True)
        return statuses.pop(0) if len(statuses) > 1 else statuses[0]

    driver.runtime_status = runtime_status
    driver.buttons = lambda: [SimpleNamespace(Name=harness.RUN_TASK)]
    driver.texts = lambda: []
    driver._topbar_status = lambda _task: ""
    clock = [0.0]

    def now():
        clock[0] += 0.1
        return clock[0]

    monkeypatch.setattr(harness.time, "time", now)
    monkeypatch.setattr(harness.time, "sleep", lambda _seconds: None)

    result = driver.wait_for_result(
        "goal",
        5,
        previous_run_id="arp-old",
        accepted_run_id="arp-new",
    )

    assert len(calls) == 2
    assert result["outcome"] == "terminal"
    assert result["status"] == "done"


def test_active_run_probe_does_not_borrow_another_runs_running_flag():
    harness = _load_harness()
    status = {
        "running": True,
        "current_run": {"run_id": "arp-other", "status": "running"},
        "runtime_jobs": [],
    }

    assert harness._runtime_has_active_run(status) is True
    assert harness._runtime_has_active_run(status, "arp-target") is False


def test_active_run_probe_ignores_a_stale_running_projection():
    harness = _load_harness()
    stale = {
        "running": False,
        "arp_running": False,
        "current_run": {"run_id": "arp-target", "status": "running"},
        "runtime_jobs": [],
    }
    owned_by_job = {
        **stale,
        "runtime_jobs": [
            {
                "status": "running",
                "metadata": {"run_id": "arp-target"},
            }
        ],
    }

    assert harness._runtime_has_active_run(stale, "arp-target") is False
    assert harness._runtime_has_active_run(owned_by_job, "arp-target") is True


def test_stop_active_run_cancels_exact_run_before_restoring_client(
    monkeypatch,
):
    harness = _load_harness()
    driver = object.__new__(harness.ElectronClientDriver)
    driver._runtime_url = "http://127.0.0.1:8765"
    driver._runtime_token = "test-token"
    driver.identity = SimpleNamespace(hwnd=42)
    driver.window = object()
    statuses = [
        {
            "running": True,
            "current_run": {"run_id": "arp/target", "status": "running"},
            "runtime_jobs": [],
        },
        {
            "running": False,
            "current_run": {"run_id": "arp/target", "status": "canceled"},
            "runtime_jobs": [],
        },
    ]
    driver.runtime_status = lambda: statuses.pop(0)
    events = []
    driver.restore_and_focus = lambda: events.append("restore")

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def urlopen(request, *, timeout):
        events.append((request.get_method(), request.full_url, timeout))
        return Response()

    monkeypatch.setattr(harness.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(harness.json, "load", lambda _response: {"ok": True})
    monkeypatch.setattr(harness.auto, "ControlFromHandle", lambda _hwnd: None)
    monkeypatch.setattr(
        harness,
        "_first_visible_descendant",
        lambda *_args, **_kwargs: object(),
    )

    result = driver.stop_active_run(run_id="arp/target", timeout=1)

    assert result["requested_stop"] is True
    assert result["runtime_stopped"] is True
    assert result["cancel_error"] == ""
    assert events == [
        (
            "POST",
            "http://127.0.0.1:8765/api/v2/runs/arp%2Ftarget/cancel",
            5,
        ),
        "restore",
    ]


def test_stop_active_run_records_404_when_the_run_finishes_concurrently(
    monkeypatch,
):
    harness = _load_harness()
    driver = object.__new__(harness.ElectronClientDriver)
    driver._runtime_url = "http://127.0.0.1:8765"
    driver._runtime_token = "test-token"
    driver.identity = SimpleNamespace(hwnd=42)
    driver.window = object()
    statuses = [
        {
            "running": True,
            "current_run": {"run_id": "arp-target", "status": "running"},
            "runtime_jobs": [],
        },
        {
            "running": False,
            "current_run": {"run_id": "arp-target", "status": "done"},
            "runtime_jobs": [],
        },
    ]
    driver.runtime_status = lambda: statuses.pop(0)
    restored = []
    driver.restore_and_focus = lambda: restored.append(True)

    def urlopen(request, *, timeout):
        raise harness.urllib.error.HTTPError(
            request.full_url,
            404,
            "not found",
            None,
            None,
        )

    monkeypatch.setattr(harness.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(harness.auto, "ControlFromHandle", lambda _hwnd: None)
    monkeypatch.setattr(
        harness,
        "_first_visible_descendant",
        lambda *_args, **_kwargs: object(),
    )

    result = driver.stop_active_run(run_id="arp-target", timeout=1)

    assert result["requested_stop"] is False
    assert result["runtime_stopped"] is True
    assert result["cancel_error"] == "HTTPError: 404"
    assert restored == [True]


def test_wait_for_result_accepts_direct_reply_as_success(monkeypatch):
    harness = _load_harness()
    driver = object.__new__(harness.ElectronClientDriver)
    driver.runtime_status = lambda: {
        "running": False,
        "current_run": {
            "run_id": "arp-reply",
            "goal": "say hello",
            "status": "reply",
        },
    }
    driver.buttons = lambda: []
    driver.texts = lambda: []
    driver._topbar_status = lambda _task: ""
    monkeypatch.setattr(harness.time, "sleep", lambda _seconds: None)

    result = driver.wait_for_result(
        "say hello",
        5,
        accepted_run_id="arp-reply",
    )

    assert result["outcome"] == "terminal"
    assert result["status"] == "done"
    assert result["status_label"] == "reply"


def test_window_lifecycle_state_is_captured_without_restoring(monkeypatch):
    harness = _load_harness()
    driver = object.__new__(harness.ElectronClientDriver)
    driver.identity = SimpleNamespace(hwnd=711)
    monkeypatch.setattr(harness.win32gui, "IsWindowVisible", lambda hwnd: hwnd == 711)
    monkeypatch.setattr(harness.win32gui, "IsIconic", lambda hwnd: hwnd == 711)
    monkeypatch.setattr(harness.win32gui, "GetForegroundWindow", lambda: 912)
    monkeypatch.setattr(
        harness.win32gui,
        "GetWindowRect",
        lambda hwnd: (10, 20, 810, 620) if hwnd == 711 else (),
    )

    assert driver.window_lifecycle_state() == {
        "visible": True,
        "minimized": True,
        "foreground": False,
        "rect": [10, 20, 810, 620],
    }


def test_window_evidence_capture_falls_back_to_desktop_for_minimized_rect(
    monkeypatch,
    tmp_path,
):
    harness = _load_harness()
    driver = object.__new__(harness.ElectronClientDriver)
    driver.output_dir = tmp_path
    driver.window = SimpleNamespace(
        BoundingRectangle=SimpleNamespace(
            left=-32000,
            top=-32000,
            right=-32000,
            bottom=-32000,
        )
    )
    calls = []

    class Image:
        def save(self, path):
            calls.append(("save", path))

    def grab(**kwargs):
        calls.append(("grab", kwargs))
        return Image()

    monkeypatch.setattr(harness.ImageGrab, "grab", grab)

    captured = driver.capture_window("handoff.png")

    assert captured == str(tmp_path / "handoff.png")
    assert calls == [
        ("grab", {"all_screens": True}),
        ("save", tmp_path / "handoff.png"),
    ]


def test_optional_handoff_without_admitted_run_id_does_not_bind_literal_none():
    harness = _load_harness()

    assert harness._admitted_run_id(
        {"target_hwnd": 991, "focused": True},
    ) == ""
    assert harness._admitted_run_id(
        {"admitted_run_id": None},
    ) == ""
    assert harness._admitted_run_id(
        {"admitted_run_id": "arp-new"},
    ) == "arp-new"


def test_cleanup_scope_matches_only_the_active_submitted_run():
    harness = _load_harness()

    assert harness._active_run_matches_submission(
        {
            "running": True,
            "current_run": {
                "run_id": "arp-new",
                "goal": "  target   task ",
            },
        },
        "target task",
        "arp-old",
    ) is True
    assert harness._active_run_matches_submission(
        {
            "running": True,
            "current_run": {
                "run_id": "arp-new",
                "goal": "another task",
            },
        },
        "target task",
        "arp-old",
    ) is False
    assert harness._active_run_matches_submission(
        {
            "running": False,
            "current_run": {
                "run_id": "arp-new",
                "goal": "target task",
                "status": "running",
            },
            "runtime_jobs": [
                {
                    "status": "running",
                    "metadata": {"run_id": "arp-new"},
                }
            ],
        },
        "target task",
        "arp-old",
    ) is True
    assert harness._active_run_matches_submission(
        {
            "running": True,
            "current_run": {
                "run_id": "arp-old",
                "goal": "target task",
            },
        },
        "target task",
        "arp-old",
    ) is False


def test_legacy_graph_name_projection_matches_without_accepting_arbitrary_prefixes():
    harness = _load_harness()
    task = (
        "Calculate 37 times 19 in the current Calculator and leave the final "
        "result displayed."
    )
    assert len(task) > 80

    assert harness._runtime_goal_matches_submission(
        task[:80],
        task,
        "legacy",
    ) is True
    assert harness._runtime_goal_matches_submission(
        task[:79],
        task,
        "legacy",
    ) is False
    assert harness._runtime_goal_matches_submission(
        task[:80],
        task,
        "vnext",
    ) is False


def test_cleanup_scope_accepts_exact_legacy_graph_name_projection():
    harness = _load_harness()
    task = (
        "Calculate 37 times 19 in the current Calculator and leave the final "
        "result displayed."
    )

    assert harness._active_run_matches_submission(
        {
            "running": True,
            "current_run": {
                "run_id": "arp-new",
                "runtime_version": "legacy",
                "goal": task[:80],
            },
        },
        task,
        "arp-old",
    ) is True


def test_memory_isolation_requires_client_toggle_contract_and_clean_runner(
    monkeypatch,
    tmp_path,
):
    harness = _load_harness()
    trace = tmp_path / "arp-cold.jsonl"
    trace.write_text(
        "\n".join(
            json.dumps(item)
            for item in (
                {
                    "type": "arp_run_start",
                    "task_contract": {"experience_policy": "disabled"},
                },
                {
                    "type": "agent_start",
                    "run_id": "child-run",
                    "has_experience": False,
                    "runtime": {"experience_enabled": False},
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(harness, "TRACE_ROOT", tmp_path)

    passed = harness._memory_isolation_evidence(
        ["arp-cold"], {"cold_start_toggle": 1},
    )
    assert passed["passed"] is True
    assert passed["contract_experience_policies"] == ["disabled"]
    assert passed["agent_starts"][0]["has_experience"] is False

    toggle_off = harness._memory_isolation_evidence(
        ["arp-cold"], {"cold_start_toggle": 0},
    )
    assert toggle_off["passed"] is False

    dirty = trace.read_text(encoding="utf-8").replace(
        '"has_experience": false', '"has_experience": true',
    )
    trace.write_text(dirty, encoding="utf-8")
    replayed = harness._memory_isolation_evidence(
        ["arp-cold"], {"cold_start_toggle": 1},
    )
    assert replayed["passed"] is False


def test_independent_session_reset_requires_empty_contract_context(
    monkeypatch,
    tmp_path,
):
    harness = _load_harness()
    trace = tmp_path / "arp-reset.jsonl"
    base = {
        "type": "arp_run_start",
        "run_id": "arp-reset",
        "task_contract": {
            "task": "generic task",
            "experience_policy": "disabled",
            "session_context": [],
        },
    }
    trace.write_text(json.dumps(base) + "\n", encoding="utf-8")
    monkeypatch.setattr(harness, "TRACE_ROOT", tmp_path)

    clean = harness._memory_isolation_evidence(
        ["arp-reset"],
        {"cold_start_toggle": 1},
        require_empty_session=True,
    )
    assert clean["passed"] is True
    assert clean["session_empty"] is True

    base["task_contract"]["session_context"] = [{"run_id": "old-run"}]
    trace.write_text(json.dumps(base) + "\n", encoding="utf-8")
    contaminated = harness._memory_isolation_evidence(
        ["arp-reset"],
        {"cold_start_toggle": 1},
        require_empty_session=True,
    )
    assert contaminated["passed"] is False


def test_vnext_memory_report_marks_an_absent_session_context_as_empty(
    monkeypatch,
    tmp_path,
):
    harness = _load_harness()
    trace = tmp_path / "arp-vnext.jsonl"
    trace.write_text(
        json.dumps({
            "type": "arp_run_start",
            "runtime_version": "vnext",
            "experience_policy": "disabled",
        }) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(harness, "TRACE_ROOT", tmp_path)

    evidence = harness._memory_isolation_evidence(
        ["arp-vnext"],
        {},
        require_empty_session=True,
        runtime_version="vnext",
    )

    assert evidence["passed"] is True
    assert evidence["cold_start_toggle"] == 0
    assert evidence["session_contexts"] == []
    assert evidence["session_empty"] is True


def test_trace_binding_uses_only_the_exact_client_run(monkeypatch, tmp_path):
    harness = _load_harness()
    client_run_id = "20260718-010203-client"
    # The real ARP file may be named a second before the client-visible run id.
    bound_trace_id = "arp-20260718-010202-bound"
    (tmp_path / f"{bound_trace_id}.jsonl").write_text("\n".join([
        json.dumps({
            "type": "arp_run_start",
            "run_id": bound_trace_id,
            "task_contract": {"task": "target task"},
        }),
        json.dumps({
            "type": "agent_start",
            "run_id": client_run_id,
            "task": "target task",
        }),
    ]) + "\n", encoding="utf-8")
    (tmp_path / "arp-20260718-010203-other.jsonl").write_text("\n".join([
        json.dumps({
            "type": "arp_run_start",
            "run_id": "arp-20260718-010203-other",
            "task_contract": {"task": "target task"},
        }),
        json.dumps({
            "type": "agent_start",
            "run_id": "20260718-010203-other",
            "task": "target task",
        }),
    ]) + "\n", encoding="utf-8")
    monkeypatch.setattr(harness, "TRACE_ROOT", tmp_path)

    evidence = harness._bound_trace_evidence(client_run_id, "target task")

    assert evidence["passed"] is True
    assert evidence["trace_ids"] == [bound_trace_id]
    assert evidence["matching_trace_count"] == 1
    assert harness._bound_trace_evidence(client_run_id, "other task")["passed"] is False


def test_wait_for_result_binds_one_matching_run_and_rejects_replacement(monkeypatch):
    harness = _load_harness()

    def driver_for(statuses):
        driver = object.__new__(harness.ElectronClientDriver)
        queue = list(statuses)
        driver.runtime_status = lambda: queue.pop(0) if len(queue) > 1 else queue[0]
        driver.buttons = lambda: []
        driver.texts = lambda: []
        driver._topbar_status = lambda _task: ""
        return driver

    clock = [0.0]

    def now():
        clock[0] += 0.1
        return clock[0]

    monkeypatch.setattr(harness.time, "time", now)
    monkeypatch.setattr(harness.time, "sleep", lambda _seconds: None)

    completed = driver_for([
        {
            "running": True,
            "current_run": {
                "run_id": "arp-target", "goal": "target task", "status": "running",
            },
        },
        {
            "running": False,
            "current_run": {
                "run_id": "arp-target", "goal": "target task", "status": "done",
            },
        },
    ]).wait_for_result("target task", 5, previous_run_id="arp-old")
    assert completed["outcome"] == "terminal"
    assert completed["run_id"] == "arp-target"

    clock[0] = 0.0
    replaced = driver_for([
        {
            "running": True,
            "current_run": {
                "run_id": "arp-target", "goal": "target task", "status": "running",
            },
        },
        {
            "running": True,
            "current_run": {
                "run_id": "arp-other", "goal": "other task", "status": "running",
            },
        },
    ]).wait_for_result("target task", 5, previous_run_id="arp-old")
    assert replaced["outcome"] == "run_replaced"
    assert replaced["run_id"] == "arp-target"
    assert replaced["replacement_run_id"] == "arp-other"


def test_wait_for_result_binds_legacy_run_with_truncated_graph_name(monkeypatch):
    harness = _load_harness()
    task = (
        "Calculate 37 times 19 in the current Calculator and leave the final "
        "result displayed."
    )
    statuses = [
        {
            "running": True,
            "current_run": {
                "run_id": "arp-target",
                "runtime_version": "legacy",
                "goal": task[:80],
                "status": "running",
            },
        },
        {
            "running": False,
            "current_run": {
                "run_id": "arp-target",
                "runtime_version": "legacy",
                "goal": task[:80],
                "status": "done",
            },
        },
    ]
    driver = object.__new__(harness.ElectronClientDriver)
    driver.runtime_status = (
        lambda: statuses.pop(0) if len(statuses) > 1 else statuses[0]
    )
    def fail_if_uia_is_walked():
        raise AssertionError("terminal runtime state must short-circuit UIA")

    driver.buttons = fail_if_uia_is_walked
    driver.texts = fail_if_uia_is_walked
    driver._topbar_status = lambda _task: ""
    clock = [0.0]

    def now():
        clock[0] += 0.1
        return clock[0]

    monkeypatch.setattr(harness.time, "time", now)
    monkeypatch.setattr(harness.time, "sleep", lambda _seconds: None)

    result = driver.wait_for_result(task, 5, previous_run_id="arp-old")

    assert result["outcome"] == "terminal"
    assert result["run_id"] == "arp-target"
    assert result["status"] == "done"


def test_wait_for_result_returns_runtime_fail_without_waiting_for_timeout(monkeypatch):
    harness = _load_harness()
    driver = object.__new__(harness.ElectronClientDriver)
    statuses = [
        {
            "running": False,
            "current_run": {
                "run_id": "arp-target", "goal": "target task", "status": "fail",
            },
        },
    ]
    driver.runtime_status = lambda: statuses.pop(0) if len(statuses) > 1 else statuses[0]
    driver.buttons = lambda: []
    driver.texts = lambda: []
    driver._topbar_status = lambda _task: ""
    clock = [0.0]

    def now():
        clock[0] += 0.1
        return clock[0]

    monkeypatch.setattr(harness.time, "time", now)
    monkeypatch.setattr(harness.time, "sleep", lambda _seconds: None)

    result = driver.wait_for_result("target task", 300, previous_run_id="arp-old")

    assert result["outcome"] == "terminal"
    assert result["status"] == "failed"
    assert result["status_label"] == "fail"
    assert result["run_id"] == "arp-target"
    assert result["active_seen"] is False
    assert clock[0] < 5


def test_wait_for_result_does_not_walk_uia_again_after_timeout(monkeypatch):
    harness = _load_harness()
    driver = object.__new__(harness.ElectronClientDriver)
    driver.runtime_status = lambda: {
        "running": True,
        "current_run": {
            "run_id": "arp-target",
            "goal": "target task",
            "status": "running",
        },
        "runtime_jobs": [],
    }
    driver.buttons = lambda: []
    text_reads = []

    def texts():
        text_reads.append(True)
        return ["last visible projection"]

    driver.texts = texts
    driver._topbar_status = lambda _task: ""
    clock = [0.0]
    monkeypatch.setattr(harness.time, "time", lambda: clock[0])
    monkeypatch.setattr(
        harness.time,
        "sleep",
        lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )

    result = driver.wait_for_result(
        "target task",
        1,
        accepted_run_id="arp-target",
    )

    assert result["outcome"] == "timeout"
    assert result["visible_text"] == ["last visible projection"]
    assert text_reads == [True]


def test_persistent_result_uses_the_client_active_probe_not_trace_diagnostics(
    monkeypatch,
    tmp_path,
):
    harness = _load_harness()
    trace = tmp_path / "arp-persistent.jsonl"
    trace.write_text(
        "\n".join(
            json.dumps(item)
            for item in (
                {
                    "type": "agent_tool",
                    "tool": "open_conversation",
                    "result_meta": {"ok": True, "verified": True},
                },
                {
                    "type": "agent_tool",
                    "tool": "read_text",
                    "result_meta": {"ok": True},
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(harness, "TRACE_ROOT", tmp_path)

    evidence = harness._execution_evidence(["arp-persistent"])
    assert evidence["conversation_bound"] is True
    assert evidence["monitoring_started"] is True
    assert evidence["persistent_ready"] is True

    result = {
        "expected": "persistent",
        "result": {
            "outcome": "persistent_active",
            "status": "running",
            "active_seen": True,
        },
        "memory_isolation": {"passed": True},
        "trace_binding": {"passed": True},
        "execution_evidence": evidence,
        "task_verdict": {"source": "external_probe", "passed": True},
    }
    assert harness._case_execution_valid(result) is True
    assert harness._case_passed(result) is True
    result["execution_evidence"] = {"persistent_ready": False}
    assert harness._case_execution_valid(result) is True
    assert harness._case_passed(result) is True
    result["result"]["active_seen"] = False
    assert harness._case_execution_valid(result) is False
    assert harness._case_passed(result) is False


def test_vnext_execution_evidence_projects_raw_receipts_and_terminal_event(
    monkeypatch,
    tmp_path,
):
    harness = _load_harness()
    trace = tmp_path / "arp-vnext.jsonl"
    trace.write_text(
        "\n".join(
            json.dumps(item)
            for item in (
                {
                    "type": "vnext_raw_receipt",
                    "controller_action": "hotkey",
                    "outcome": "applied",
                    "input_completed": True,
                },
                {
                    "type": "vnext_raw_receipt",
                    "controller_action": "click",
                    "outcome": "rejected",
                    "input_completed": False,
                },
                {
                    "type": "live_agent_turn",
                    "proposal_kind": "claim_obligation_done",
                    "status": "mission_verified",
                },
                {
                    "type": "live_agent_done",
                    "status": "mission_verified",
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(harness, "TRACE_ROOT", tmp_path)

    evidence = harness._execution_evidence(["arp-vnext"])

    assert evidence["tool_attempts"] == [
        "hotkey",
        "click",
        "claim_obligation_done",
    ]
    assert evidence["successful_tools"] == [
        "hotkey",
        "claim_obligation_done",
    ]
    assert evidence["terminal_events"] == ["live_agent_done"]
    assert evidence["parse_errors"] == []


def test_gui_execution_trace_accepts_only_vnext_os_input_receipts(
    monkeypatch,
    tmp_path,
):
    harness = _load_harness()
    trace = tmp_path / "arp-gui.jsonl"
    trace.write_text(
        "\n".join(
            json.dumps(item)
            for item in (
                {
                    "type": "arp_run_start",
                    "run_id": "arp-gui",
                    "graph_id": "graph-gui",
                    "runtime_version": "vnext",
                    "no_legacy_fallback": True,
                },
                {
                    "type": "live_agent_start",
                    "run_id": "arp-gui",
                    "mission_id": "mission-gui",
                    "runtime_version": "vnext",
                    "no_legacy_fallback": True,
                },
                {
                    "type": "vnext_raw_receipt",
                    "run_id": "arp-gui",
                    "mission_id": "mission-gui",
                    "task_id": "graph-gui",
                    "runtime_version": "vnext",
                    "no_legacy_fallback": True,
                    "controller_action": "click",
                    "outcome": "applied",
                    "input_completed": True,
                },
                {
                    "type": "vnext_raw_receipt",
                    "run_id": "arp-gui",
                    "mission_id": "mission-gui",
                    "task_id": "graph-gui",
                    "runtime_version": "vnext",
                    "no_legacy_fallback": True,
                    "controller_action": "input_sequence",
                    "outcome": "applied",
                    "input_completed": True,
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(harness, "TRACE_ROOT", tmp_path)

    evidence = harness._gui_execution_trace_evidence(
        ["arp-gui"],
        "arp-gui",
    )

    assert evidence["passed"] is True
    assert evidence["applied_gui_actions"] == ["click", "input_sequence"]
    assert evidence["bound_mission_ids"] == ["mission-gui"]
    assert evidence["bound_task_ids"] == ["graph-gui"]
    trace.write_text(
        trace.read_text(encoding="utf-8")
        + json.dumps({
            "type": "agent_tool",
            "tool": "run_shell",
            "runtime_version": "legacy",
        })
        + "\n",
        encoding="utf-8",
    )
    rejected = harness._gui_execution_trace_evidence(
        ["arp-gui"],
        "arp-gui",
    )
    assert rejected["passed"] is False
    assert any("agent_tool" in item for item in rejected["forbidden_events"])
    assert any(
        "runtime_version=legacy" in item
        for item in rejected["forbidden_events"]
    )


def test_gui_execution_trace_rejects_receipt_from_different_outer_run(
    monkeypatch,
    tmp_path,
):
    harness = _load_harness()
    trace = tmp_path / "arp-gui.jsonl"
    trace.write_text(
        "\n".join(
            json.dumps(item)
            for item in (
                {
                    "type": "arp_run_start",
                    "run_id": "arp-gui",
                    "graph_id": "graph-gui",
                    "runtime_version": "vnext",
                    "no_legacy_fallback": True,
                },
                {
                    "type": "live_agent_start",
                    "run_id": "arp-gui",
                    "mission_id": "mission-gui",
                    "runtime_version": "vnext",
                    "no_legacy_fallback": True,
                },
                {
                    "type": "vnext_raw_receipt",
                    "run_id": "arp-other",
                    "mission_id": "mission-gui",
                    "task_id": "graph-gui",
                    "runtime_version": "vnext",
                    "no_legacy_fallback": True,
                    "controller_action": "click",
                    "outcome": "applied",
                    "input_completed": True,
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(harness, "TRACE_ROOT", tmp_path)

    evidence = harness._gui_execution_trace_evidence(
        ["arp-gui"],
        "arp-gui",
    )

    assert evidence["passed"] is False
    assert evidence["applied_gui_actions"] == []
    assert any(
        "receipt_run_id=arp-other;expected=arp-gui" in item
        for item in evidence["forbidden_events"]
    )


def test_gui_execution_trace_rejects_mismatched_mission_or_task_binding(
    monkeypatch,
    tmp_path,
):
    harness = _load_harness()
    trace = tmp_path / "arp-gui.jsonl"
    trace.write_text(
        "\n".join(
            json.dumps(item)
            for item in (
                {
                    "type": "arp_run_start",
                    "run_id": "arp-gui",
                    "graph_id": "graph-gui",
                    "runtime_version": "vnext",
                    "no_legacy_fallback": True,
                },
                {
                    "type": "live_agent_start",
                    "run_id": "arp-gui",
                    "mission_id": "mission-gui",
                    "runtime_version": "vnext",
                    "no_legacy_fallback": True,
                },
                {
                    "type": "vnext_raw_receipt",
                    "run_id": "arp-gui",
                    "mission_id": "mission-other",
                    "task_id": "graph-gui",
                    "runtime_version": "vnext",
                    "no_legacy_fallback": True,
                    "controller_action": "click",
                    "outcome": "applied",
                    "input_completed": True,
                },
                {
                    "type": "vnext_raw_receipt",
                    "run_id": "arp-gui",
                    "mission_id": "mission-gui",
                    "task_id": "graph-other",
                    "runtime_version": "vnext",
                    "no_legacy_fallback": True,
                    "controller_action": "click",
                    "outcome": "applied",
                    "input_completed": True,
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(harness, "TRACE_ROOT", tmp_path)

    evidence = harness._gui_execution_trace_evidence(
        ["arp-gui"],
        "arp-gui",
    )

    assert evidence["passed"] is False
    assert evidence["applied_gui_actions"] == []
    assert any(
        "receipt_mission_id=mission-other" in item
        for item in evidence["forbidden_events"]
    )
    assert any(
        "receipt_task_binding=graph-other" in item
        for item in evidence["forbidden_events"]
    )


def test_execution_validity_does_not_depend_on_frontend_policy_state():
    harness = _load_harness()
    result = {
        "expected": "terminal",
        "result": {"outcome": "terminal", "status": "done"},
        "memory_isolation": {"passed": True},
        "trace_binding": {
            "passed": True,
            "no_legacy_fallback": True,
        },
        "gui_execution_trace": {"passed": False},
    }

    assert harness._case_execution_valid(result) is True


def test_visual_actor_completion_is_used_when_no_external_oracle_exists():
    harness = _load_harness()
    result = {
        "expected": "terminal",
        "result": {"outcome": "terminal", "status": "done"},
        "memory_isolation": {"passed": True},
        "trace_binding": {"passed": True},
        "execution_evidence": {},
        "task_verdict": {"source": "visual_actor", "status": "verified", "passed": True},
    }

    assert harness._case_execution_valid(result) is True
    assert harness._case_passed(result) is True

    result["task_verdict"] = {"source": "external_probe", "passed": True}
    assert harness._case_passed(result) is True

    result["task_verdict"] = {"source": "runtime_status", "passed": True}
    assert harness._case_passed(result) is False

    result["result"]["status"] = "failed"
    result["task_verdict"] = {
        "source": "visual_actor",
        "status": "not_verified",
        "passed": False,
    }
    assert harness._case_execution_valid(result) is True
    assert harness._case_passed(result) is False


def test_terminal_blocked_run_is_valid_plumbing_when_external_oracle_passes():
    harness = _load_harness()
    result = {
        "expected": "terminal",
        "result": {"outcome": "terminal", "status": "failed"},
        "memory_isolation": {"passed": True},
        "trace_binding": {"passed": True},
        "task_verdict": {"source": "external_probe", "passed": True},
    }

    assert harness._case_execution_valid(result) is True
    assert harness._case_passed(result) is True


def test_suite_preserves_only_case_two_to_case_three_short_term_context(
    monkeypatch,
    tmp_path,
):
    harness = _load_harness()
    calls: list[tuple[str, bool, str]] = []

    def fake_run_case(
        name,
        spec,
        *,
        output_root,
        reset_session,
        required_context_run_id="",
        **_kwargs,
    ):
        calls.append((name, reset_session, required_context_run_id))
        persistent = spec.get("expected") == "persistent"
        return {
            "name": name,
            "task": spec["task"],
            "expected": spec.get("expected", "terminal"),
            "result": {
                "outcome": "persistent_active" if persistent else "terminal",
                "status": "running" if persistent else "done",
                "run_id": f"run-{name}",
                "active_seen": persistent,
            },
            "memory_isolation": {"passed": True},
            "trace_binding": {"passed": True},
            "execution_evidence": {"persistent_ready": persistent},
            "task_verdict": {"source": "external_probe", "passed": True},
            "passed": True,
        }

    monkeypatch.setattr(harness, "run_case", fake_run_case)
    report = harness.run_suite("original", output_root=tmp_path)

    assert report["passed"] is True
    assert len(calls) == 11
    assert calls[0] == ("original-1", True, "")
    assert calls[1] == ("original-2", True, "")
    assert calls[2] == ("original-3", False, "run-original-2")
    assert all(reset is True for _name, reset, _context in calls[3:])


def test_suite_stops_after_cleanup_really_failed(monkeypatch, tmp_path):
    harness = _load_harness()
    calls = []

    def fake_run_case(name, spec, **_kwargs):
        calls.append(name)
        return {
            "name": name,
            "task": spec["task"],
            "expected": spec.get("expected", "terminal"),
            "result": {"outcome": "timeout", "status": "timeout"},
            "memory_isolation": {"passed": True},
            "trace_binding": {"passed": True},
            "task_verdict": {"source": "visual_actor", "passed": False},
            "timeout_cleanup": {"runtime_stopped": False},
        }

    monkeypatch.setattr(harness, "run_case", fake_run_case)

    report = harness.run_suite("original", output_root=tmp_path)

    assert calls == ["original-1"]
    assert report["case_count"] == 1
    assert report["passed"] is False
