from types import SimpleNamespace


def _status(sequence: int = 3) -> dict:
    return {
        "sequence": sequence,
        "running": True,
        "last_changed": True,
        "last_ratio": 0.02,
        "changed_regions": [
            {"left": 10, "top": 20, "width": 30, "height": 40},
        ],
        "samples": 5,
        "capture_latency_ms": 4,
        "diff_latency_ms": 1,
    }


def test_world_model_screen_bulletin_advisory_is_edge_triggered():
    from winpilot.perception.world import WorldModel

    world = WorldModel()
    world.record_screen_bulletin(_status())

    state = world.state()
    assert state["screen_watch"]["sequence"] == 3
    assert state["screen_watch"]["last_changed"] is True

    first = world.screen_advisory()
    second = world.screen_advisory()

    assert "ratio=0.0200" in first
    assert "roi=(10,20,30x40)" in first
    assert second == ""


def test_world_model_retains_source_bound_facts_per_run():
    from winpilot.perception.world import WorldModel

    world = WorldModel()
    world.record_facts(
        subject="Product A",
        facts={"weight": "141 g", "battery": "70 days"},
        source_url="https://example.test/a",
        evidence=["Weight: 141 g", "Battery life: 70 days"],
    )
    world.record_facts(
        subject="Product B",
        facts={"weight": "99 g"},
        source_url="https://example.test/b",
    )

    records = world.working_set()
    first = world.working_set_advisory()
    second = world.working_set_advisory()

    assert [record["subject"] for record in records] == ["Product A", "Product B"]
    assert records[0]["facts"]["weight"] == "141 g"
    assert records[1]["source_url"] == "https://example.test/b"
    assert "do not mix subjects" in first
    assert "Product A" in first and "Product B" in first
    assert second == ""
    assert world.state()["working_set_count"] == 2


def test_record_facts_tool_updates_run_working_set():
    import json

    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="compare two visible product pages")
    result = session.dispatch("record_facts", {
        "subject": "Product A",
        "facts": {"connection": "Bluetooth"},
        "source_url": "https://example.test/a",
        "evidence": ["Bluetooth visible in specifications"],
    })
    payload = json.loads(result.text)

    assert payload["ok"] is True
    assert payload["verified"] is True
    assert payload["record"]["facts"] == {"connection": "Bluetooth"}
    assert session.world.working_set()[0]["subject"] == "Product A"


def test_record_facts_duplicate_subject_is_update_not_new_source():
    import json

    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="compare two separate product pages")
    first = json.loads(session.dispatch("record_facts", {
        "subject": "Product A",
        "facts": {"weight": "100 g"},
        "source_url": "https://example.test/a",
    }).text)
    update = json.loads(session.dispatch("record_facts", {
        "subject": "Product A",
        "facts": {"battery": "70 days"},
        "source_url": "https://example.test/a",
    }).text)

    assert first["created_new_source"] is True
    assert update["created_new_source"] is False
    assert update["updated_existing_source"] is True
    assert update["verified"] is False
    assert len(update["working_set"]) == 1

    session._research_exploration_actions = 10
    session.dispatch("record_facts", {
        "subject": "Product A",
        "facts": {"color": "black"},
        "source_url": "https://example.test/a",
    })
    assert session._research_exploration_actions == 10


def test_record_facts_rejects_new_subject_bound_to_existing_source_url():
    import json

    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="compare two separate product pages")
    session.dispatch("record_facts", {
        "subject": "Product A",
        "facts": {"weight": "100 g"},
        "source_url": "https://example.test/a",
    })
    duplicate = json.loads(session.dispatch("record_facts", {
        "subject": "Product B",
        "facts": {"weight": "90 g"},
        "source_url": "https://example.test/a",
    }).text)

    assert duplicate["blocked"] is True
    assert duplicate["duplicate_source_url"] is True
    assert len(session.world.working_set()) == 1


def test_record_facts_uses_visible_address_bar_url_over_model_claim():
    import json

    from winpilot.agent.tools import ToolSession
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    session = ToolSession(task="compare two separate product pages")
    session.snapshot = Snapshot(
        hwnd=42,
        title="Product - Google Chrome",
        region=Region(0, 0, 1920, 1020),
        elements=[UIElement(
            elem_id=1, source="uia", role="edit",
            text="地址和搜索栏=logitech.com/en-us/shop/p/mx-master-3s",
            cx=910, cy=79, left=198, top=64, width=1427, height=31,
        )],
    )

    payload = json.loads(session.dispatch("record_facts", {
        "subject": "MX Master 3S",
        "facts": {"weight": "not visible"},
        "source_url": "https://www.logitech.com/products/mice/mx-master-3s.html",
    }).text)

    assert payload["source_url_corrected"] is True
    assert payload["record"]["source_url"] == "https://logitech.com/en-us/shop/p/mx-master-3s"


def test_record_facts_normalizes_source_fact_to_visible_address_bar():
    import json

    from winpilot.agent.tools import ToolSession
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    session = ToolSession(task="collect a product source")
    session.snapshot = Snapshot(
        hwnd=42,
        title="Product - Google Chrome",
        region=Region(0, 0, 1920, 1020),
        elements=[UIElement(
            elem_id=1, source="uia", role="edit",
            text="地址和搜索栏=example.test/products/real",
            cx=910, cy=79, left=198, top=64, width=1427, height=31,
        )],
    )

    payload = json.loads(session.dispatch("record_facts", {
        "subject": "Product",
        "facts": {"来源": "https://invented.test/wrong", "价格": "$10"},
        "source_url": "https://invented.test/wrong",
    }).text)

    assert payload["record"]["facts"]["来源"] == "https://example.test/products/real"


def test_verified_subgoal_is_blocked_when_repeated_on_same_screen_epoch(monkeypatch):
    import json

    from winpilot.agent import scope_guard
    from winpilot.agent.tool_result import ToolResult
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="navigate and collect visible facts")
    calls = []
    session._t_execute_subgoal = lambda args: calls.append(args) or ToolResult(
        '{"ok": true, "verified": true}'
    )
    session._t_open_app = lambda _args: ToolResult('{"ok": true, "verified": true}')
    monkeypatch.setattr(scope_guard, "ensure_scope", lambda *_args, **_kwargs: None)
    args = {
        "subgoal": "Navigate to the source page",
        "target_window": {"hwnd": 100},
        "input": {"label": "address", "text": "https://example.test/a"},
    }

    first = json.loads(session.dispatch("execute_subgoal", args).text)
    duplicate = json.loads(session.dispatch("execute_subgoal", args).text)

    assert first["verified"] is True
    assert duplicate["blocked"] is True
    assert duplicate["duplicate_verified_subgoal"] is True
    assert len(calls) == 1

    session.dispatch("open_app", {"name": "Browser"})
    after_screen_mutation = json.loads(session.dispatch("execute_subgoal", args).text)

    assert after_screen_mutation["verified"] is True
    assert len(calls) == 2


def test_identical_no_progress_subgoal_is_blocked_after_two_attempts(monkeypatch):
    import json

    from winpilot.agent import scope_guard
    from winpilot.agent.tool_result import ToolResult
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="inspect a visible page")
    calls = []
    session._t_execute_subgoal = lambda args: calls.append(args) or ToolResult(
        '{"ok": true, "verified": false, "results": '
        '[{"action": "wait_stable", "data": {"delta": {"changed": false}}}]}'
    )
    monkeypatch.setattr(scope_guard, "ensure_scope", lambda *_args, **_kwargs: None)
    args = {"subgoal": "Expand details", "click": {"label": "More", "role": "button"}}

    assert json.loads(session.dispatch("execute_subgoal", args).text)["ok"] is True
    assert json.loads(session.dispatch("execute_subgoal", args).text)["ok"] is True
    blocked = json.loads(session.dispatch("execute_subgoal", args).text)

    assert blocked["duplicate_no_progress_subgoal"] is True
    assert len(calls) == 2


def test_multi_source_research_budget_forces_record_before_more_local_actions():
    import json

    from winpilot.agent.tool_result import ToolResult
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="Inspect two separate product pages and compare each source URL")
    session._t_read_text = lambda _args: ToolResult("visible source text")
    for _ in range(10):
        session.dispatch("read_text", {})

    blocked = json.loads(session.dispatch("scroll", {"x": 10, "y": 10, "amount": -3}).text)

    assert blocked["research_exploration_budget_exhausted"] is True
    session.dispatch("record_facts", {
        "subject": "Product A",
        "facts": {"weight": "not visible"},
        "source_url": "https://example.test/a",
    })
    assert session._research_budget_blocks("scroll", {"x": 10, "y": 10, "amount": -3}) is False


def test_multi_source_research_budget_covers_fast_wrapped_scroll_batch():
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="Inspect two separate product pages and compare each source URL")
    session._research_exploration_actions = 10

    assert session._research_budget_blocks("run_action_batch", {
        "actions": [{"action": "scroll", "x": 10, "y": 10, "amount": -5}],
    }) is True
    assert session._research_budget_blocks("execute_subgoal", {
        "input": {"text": "https://example.test/next"},
    }) is True
    assert session._research_budget_blocks("open_app", {"name": "Browser"}) is True
    assert session._research_budget_blocks("read_text", {}) is False


def test_tool_session_records_fast_screen_bulletin_after_dispatch():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.agent.tools import ToolSession

    class Watcher:
        def status(self):
            return _status(sequence=7)

    session = ToolSession(task="demo")
    session._fast_gui_controller = SimpleNamespace(watcher=Watcher())
    session._t_ping = lambda args: ToolResult("ok")

    result = session.dispatch("ping", {})

    assert result.text == "ok"
    assert session.world.state()["screen_watch"]["sequence"] == 7


def test_tool_session_attaches_screen_delta_to_visible_action():
    import json

    from winpilot.agent.tool_result import ToolResult
    from winpilot.agent.tools import ToolSession
    from winpilot.fast.types import ScreenDelta
    from winpilot.utils.screenshot import Region

    class Watcher:
        def __init__(self):
            self.samples = 0

        def sample_once(self):
            self.samples += 1
            if self.samples == 1:
                return ScreenDelta(False, 0.0, [], 1)
            return ScreenDelta(True, 0.0123, [Region(10, 20, 30, 40)], 2)

        def status(self):
            return _status(sequence=self.samples)

    watcher = Watcher()
    session = ToolSession(task="demo")
    session._fast_gui_controller = SimpleNamespace(watcher=watcher)
    session._t_click = lambda args: ToolResult('{"ok": true, "verified": false}')

    result = session.dispatch("click", {"x": 1, "y": 2})
    data = json.loads(result.text)

    assert watcher.samples == 2
    assert data["screen_delta"]["changed"] is True
    assert data["screen_delta"]["ratio"] == 0.0123
    assert data["screen_delta"]["regions"][0]["left"] == 10
    assert "action_target" in data
    assert session.world.state()["screen_watch"]["sequence"] == 2


def test_tool_session_can_skip_screen_delta_for_visible_action():
    import json

    from winpilot.agent.tool_result import ToolResult
    from winpilot.agent.tools import ToolSession

    class Watcher:
        def sample_once(self):
            raise AssertionError("screen delta should be skipped")

        def status(self):
            return {}

    session = ToolSession(task="demo")
    session._fast_gui_controller = SimpleNamespace(watcher=Watcher())
    session._t_click = lambda args: ToolResult('{"ok": true}')

    result = session.dispatch("click", {"x": 1, "y": 2, "skip_screen_delta": True})

    assert "screen_delta" not in json.loads(result.text)


def test_tool_session_action_delta_uses_independent_before_after_frames():
    import json

    import numpy as np

    from winpilot.agent.tool_result import ToolResult
    from winpilot.agent.tools import ToolSession
    from winpilot.utils.screenshot import Region

    frames = [
        np.zeros((40, 50, 3), dtype=np.uint8),
        np.zeros((40, 50, 3), dtype=np.uint8),
    ]
    frames[1][10:22, 20:36] = 255
    captures = []

    def fake_capture(region):
        captures.append(region)
        return frames[min(len(captures) - 1, 1)]

    class Watcher:
        pixel_thresh = 12

        def __init__(self):
            self.action_regions = []

        def status(self):
            return {
                "sequence": len(self.action_regions),
                "running": True,
                "action_changed_regions": [
                    {"left": r.left, "top": r.top, "width": r.width, "height": r.height}
                    for r in self.action_regions
                ],
            }

        def record_action_delta(self, delta, region, capture_ms=0, diff_ms=None):
            self.action_regions = list(delta.regions)

    class Controller:
        def __init__(self):
            self.watcher = Watcher()
            self.capture_fn = fake_capture

        def _target_region(self):
            return Region(100, 200, 50, 40)

    session = ToolSession(task="demo")
    session._fast_gui_controller = Controller()
    session._t_click = lambda args: ToolResult('{"ok": true, "verified": false}')

    result = session.dispatch("click", {"x": 1, "y": 2})
    data = json.loads(result.text)

    assert len(captures) == 2
    assert data["screen_delta"]["changed"] is True
    region = data["screen_delta"]["regions"][0]
    assert region["left"] <= 120
    assert region["left"] + region["width"] >= 136
    action_region = data["screen_watch"]["action_changed_regions"][0]
    assert action_region["left"] <= 120
    assert action_region["left"] + action_region["width"] >= 136


def test_scroll_retries_with_physical_input_when_post_message_has_no_effect(monkeypatch):
    import json

    import numpy as np

    from winpilot.action import executor
    from winpilot.agent.tool_result import ToolResult
    from winpilot.agent.tools import ToolSession
    from winpilot.utils.screenshot import Region

    before = np.zeros((40, 60, 3), dtype=np.uint8)
    unchanged = before.copy()
    retried = before.copy()
    retried[8:34, 4:56] = 255
    frames = iter([before, unchanged, retried])
    retry_calls = []

    class Watcher:
        pixel_thresh = 12

        def status(self):
            return {"sequence": 1, "running": True}

        def record_action_delta(self, delta, region, **_kwargs):
            self.delta = delta

    class Controller:
        def __init__(self):
            self.watcher = Watcher()
            self.capture_fn = lambda _region: next(frames)

        def _target_region(self):
            return Region(100, 200, 60, 40)

    def retry(x, y, amount):
        retry_calls.append((x, y, amount))
        return {"ok": True, "via": "sendinput_restored"}

    monkeypatch.setattr(executor, "scroll_sendinput_restored", retry)
    session = ToolSession(task="scroll a visible page")
    session._fast_gui_controller = Controller()
    session._t_scroll = lambda _args: ToolResult('{"ok": true, "via": "post_message"}')

    data = json.loads(session.dispatch("scroll", {"x": 130, "y": 220, "amount": -5}).text)

    assert retry_calls == [(130, 220, -5)]
    assert data["ok"] is True
    assert data["via"] == "sendinput_restored"
    assert data["scroll_retry"]["changed"] is True
    assert data["screen_delta"]["changed"] is True
    assert data["screen_delta"]["delta_source"] == "scroll_retry"


def test_scroll_reports_no_visible_effect_after_both_paths_fail(monkeypatch):
    import json

    import numpy as np

    from winpilot.action import executor
    from winpilot.agent.tool_result import ToolResult
    from winpilot.agent.tools import ToolSession
    from winpilot.utils.screenshot import Region

    frame = np.zeros((40, 60, 3), dtype=np.uint8)

    class Watcher:
        pixel_thresh = 12

        def status(self):
            return {"sequence": 1, "running": True}

        def record_action_delta(self, delta, region, **_kwargs):
            self.delta = delta

    class Controller:
        def __init__(self):
            self.watcher = Watcher()
            self.capture_fn = lambda _region: frame.copy()

        def _target_region(self):
            return Region(0, 0, 60, 40)

    monkeypatch.setattr(
        executor,
        "scroll_sendinput_restored",
        lambda _x, _y, _amount: {"ok": True, "via": "sendinput_restored"},
    )
    session = ToolSession(task="scroll a visible page")
    session._fast_gui_controller = Controller()
    session._t_scroll = lambda _args: ToolResult('{"ok": true, "via": "post_message"}')

    data = json.loads(session.dispatch("scroll", {"x": 20, "y": 20, "amount": -3}).text)

    assert data["ok"] is False
    assert data["verified"] is False
    assert data["no_visible_effect"] is True
    assert data["needs_observe"] is True
    assert data["screen_delta"]["changed"] is False


def test_tool_session_preserves_pressed_state_when_after_frame_reverts():
    import json

    import numpy as np

    from winpilot.action import executor
    from winpilot.agent.tool_result import ToolResult
    from winpilot.agent.tools import ToolSession
    from winpilot.utils.screenshot import Region

    before = np.zeros((60, 80, 3), dtype=np.uint8)
    pressed = before.copy()
    pressed[20:38, 28:58] = 255
    after = before.copy()
    frames = [before, pressed, after]
    captures = 0

    def fake_capture(_region):
        nonlocal captures
        frame = frames[min(captures, len(frames) - 1)]
        captures += 1
        return frame

    class Watcher:
        pixel_thresh = 12

        def __init__(self):
            self.delta = None

        def status(self):
            return {"sequence": 1, "running": True}

        def record_action_delta(self, delta, region, capture_ms=0, diff_ms=None):
            self.delta = delta

    class Controller:
        def __init__(self):
            self.watcher = Watcher()
            self.capture_fn = fake_capture

        def _target_region(self):
            return Region(100, 200, 80, 60)

    session = ToolSession(task="observe the pressed state")
    session.run_id = "transient-run"
    session._fast_gui_controller = Controller()

    def click_with_transient_probe(_args):
        assert executor._sample_pressed_state() is True
        assert session._fast_gui_controller.watcher.delta.changed is True
        return ToolResult('{"ok": true, "verified": false}')

    session._t_click = click_with_transient_probe
    result = session.dispatch("click", {"x": 1, "y": 2})
    data = json.loads(result.text)

    assert captures == 3
    assert data["screen_delta"]["changed"] is True
    assert data["screen_delta"]["delta_source"] == "press"
    assert data["screen_delta"]["transient_observed"] is True
    assert data["screen_delta"]["press_sample_count"] == 1
    assert session._fast_gui_controller.watcher.delta.changed is True


def test_tool_session_does_not_claim_transient_when_press_frame_is_unchanged():
    import json

    import numpy as np

    from winpilot.action import executor
    from winpilot.agent.tool_result import ToolResult
    from winpilot.agent.tools import ToolSession
    from winpilot.utils.screenshot import Region

    frame = np.zeros((40, 60, 3), dtype=np.uint8)

    class Watcher:
        pixel_thresh = 12

        def status(self):
            return {"sequence": 1, "running": True}

        def record_action_delta(self, delta, region, capture_ms=0, diff_ms=None):
            self.delta = delta

    class Controller:
        def __init__(self):
            self.watcher = Watcher()
            self.capture_fn = lambda _region: frame.copy()

        def _target_region(self):
            return Region(0, 0, 60, 40)

    session = ToolSession(task="no visual press change")
    session._fast_gui_controller = Controller()

    def click_with_probe(_args):
        assert executor._sample_pressed_state() is True
        return ToolResult('{"ok": true}')

    session._t_click = click_with_probe
    data = json.loads(session.dispatch("click", {"x": 1, "y": 2}).text)

    assert data["screen_delta"]["changed"] is False
    assert data["screen_delta"]["delta_source"] == "press"
    assert data["screen_delta"]["transient_observed"] is False


def test_mouse_down_records_pressed_frame_as_transient_while_still_held(monkeypatch):
    import json

    import numpy as np

    from winpilot.action import executor
    from winpilot.agent.tool_result import ToolResult
    from winpilot.agent.tools import ToolSession
    from winpilot.utils.screenshot import Region

    before = np.zeros((60, 80, 3), dtype=np.uint8)
    pressed = before.copy()
    pressed[18:42, 24:64] = 255
    frames = iter([before, pressed, pressed.copy()])

    class Watcher:
        pixel_thresh = 12

        def status(self):
            return {"sequence": 1, "running": True}

        def record_action_delta(self, delta, region, **_kwargs):
            self.delta = delta

    class Controller:
        def __init__(self):
            self.watcher = Watcher()
            self.capture_fn = lambda _region: next(frames)

        def _target_region(self):
            return Region(100, 200, 80, 60)

    session = ToolSession(task="capture a held mouse state")
    session._fast_gui_controller = Controller()

    def mouse_down_with_probe(_args):
        assert executor._sample_pressed_state() is True
        return ToolResult('{"ok": true, "press_sampled": true}')

    session._t_mouse_down = mouse_down_with_probe
    data = json.loads(session.dispatch("mouse_down", {"button": "left"}).text)

    assert data["screen_delta"]["delta_source"] == "press"
    assert data["screen_delta"]["press_sample_count"] == 1
    assert data["screen_delta"]["transient_observed"] is True


def test_executor_mouse_down_invokes_installed_press_probe(monkeypatch):
    from winpilot.action import executor

    calls = []
    monkeypatch.setattr(executor, "_send_inputs", lambda inputs: calls.append(inputs))
    previous = executor.install_press_probe(lambda: calls.append("probe"))
    try:
        result = executor.mouse_down.__wrapped__("left")
    finally:
        executor.restore_press_probe(previous)
    released = executor.release_held_mouse_buttons()

    assert result["ok"] is True
    assert result["press_sampled"] is True
    assert calls[1] == "probe"
    assert released == ["left"]


def test_tool_session_close_releases_held_mouse_button(monkeypatch):
    from winpilot.action import executor
    from winpilot.agent.tools import ToolSession

    calls = []
    monkeypatch.setattr(executor, "_send_inputs", lambda inputs: calls.append(inputs))
    executor.mouse_down.__wrapped__("left")

    ToolSession(task="release held input").close()

    assert executor.release_held_mouse_buttons() == []
    assert len(calls) == 2


def test_screen_watcher_emits_change_event_with_run_context():
    import queue

    from winpilot.fast.types import ScreenDelta
    from winpilot.fast.watcher import ScreenWatcher
    from winpilot.utils.log import BUS
    from winpilot.utils.screenshot import Region

    events = BUS.subscribe()
    try:
        watcher = ScreenWatcher(event_context_provider=lambda: {"run_id": "watch-run"})
        watcher._update_bulletin(
            ScreenDelta(True, 0.04, [Region(10, 20, 30, 40)], 2),
            Region(0, 0, 100, 100),
            capture_ms=5,
            diff_ms=2,
        )
        event = events.get(timeout=1)
    finally:
        BUS.unsubscribe(events)

    assert event["type"] == "screen_watch"
    assert event["run_id"] == "watch-run"
    assert event["regions"][0]["left"] == 10


def test_predictive_ocr_prefetches_action_evidence(monkeypatch):
    import time

    import numpy as np

    from winpilot.agent import tools
    from winpilot.agent.tools import ToolSession
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    session = ToolSession(task="read transient result")
    session.run_id = "prefetch-run"
    crop_region = Region(100, 120, 180, 60)
    watcher = type("Watcher", (), {
        "action_evidence": lambda self, max_age_ms=5000: ([(np.zeros((60, 180, 3), dtype=np.uint8), crop_region)], True),
        "status": lambda self: {"last_action_change_ms": 123},
    })()
    monkeypatch.setattr(tools.ocr, "recognize", lambda image, origin=(0, 0): [
        UIElement(source="ocr", role="text", text="PRESS PULSE ACTIVE 1", cx=180, cy=140, left=120, top=130, width=120, height=20)
    ])

    session._schedule_predictive_ocr(watcher)
    deadline = time.time() + 2
    while session._predictive_ocr_cache is None and time.time() < deadline:
        time.sleep(0.01)

    assert session._predictive_ocr_cache is not None
    element = session._predictive_ocr_cache["elements"][0]
    assert "predictive_ocr" in element.states
    assert "transient_action_evidence" in element.states


def test_tool_session_auto_starts_and_releases_shared_screen_watch_for_target(monkeypatch):
    from winpilot.agent.tools import ToolSession
    from winpilot.fast.perception_daemon import PERCEPTION_DAEMON

    class Watcher:
        def __init__(self):
            self.running = False
            self.start_calls = 0
            self.stop_calls = 0

        def start(self):
            self.running = True
            self.start_calls += 1

        def stop(self):
            self.running = False
            self.stop_calls += 1

        def status(self):
            return {
                "sequence": self.start_calls + self.stop_calls,
                "running": self.running,
                "last_changed": False,
                "last_ratio": 0.0,
                "changed_regions": [],
                "samples": 0,
                "capture_latency_ms": 0,
                "diff_latency_ms": 0,
            }

    watcher = Watcher()
    released = []
    lease = SimpleNamespace(hwnd=123, watcher=watcher, cache=SimpleNamespace())
    monkeypatch.setattr(PERCEPTION_DAEMON, "acquire", lambda hwnd, run_id="": lease)
    monkeypatch.setattr(PERCEPTION_DAEMON, "release", lambda item: released.append(item))
    session = ToolSession(task="demo")
    session.run_id = "run-1"
    session._window_alive = lambda hwnd: bool(hwnd)
    session._fast_gui_controller = SimpleNamespace(watcher=watcher)

    session._remember_target(123, "Target App")

    assert watcher.start_calls == 1
    assert session.world.state()["screen_watch"]["running"] is True

    session.close()

    assert released == [lease]
    assert watcher.stop_calls == 0
    assert watcher.running is True


def test_tool_session_auto_watch_requires_real_run_id():
    from winpilot.agent.tools import ToolSession

    class Watcher:
        def start(self):
            raise AssertionError("ad-hoc ToolSession tests should not autostart watcher")

        def status(self):
            return {"running": False}

    session = ToolSession(task="demo")
    session._window_alive = lambda hwnd: bool(hwnd)
    session._fast_gui_controller = SimpleNamespace(watcher=Watcher())

    session._remember_target(123, "Target App")


def test_tool_session_respects_screen_watch_autostart_toggle(monkeypatch):
    from winpilot.agent import tools
    from winpilot.agent.tools import ToolSession

    class Watcher:
        def start(self):
            raise AssertionError("autostart should be disabled")

        def status(self):
            return {"running": False}

    def fake_get(section, key=None, default=None):
        if section == "fusion" and key == "screen_watch_autostart":
            return False
        return default

    monkeypatch.setattr(tools.CONFIG, "get", fake_get)

    session = ToolSession(task="demo")
    session._window_alive = lambda hwnd: bool(hwnd)
    session._fast_gui_controller = SimpleNamespace(watcher=Watcher())

    session._remember_target(123, "Target App")


def test_changed_only_observe_prefers_fresh_action_roi(monkeypatch):
    from winpilot.agent import tools
    from winpilot.agent.tools import ToolSession
    from winpilot.utils.screenshot import Region

    status = {
        "action_changed_regions": [
            {"left": 100, "top": 120, "width": 30, "height": 40},
        ],
        "last_action_change_age_ms": 100,
        "recent_changed_regions": [
            {"left": 800, "top": 820, "width": 30, "height": 40},
        ],
        "last_change_age_ms": 100,
    }

    session = ToolSession(task="demo")
    session._fast_screen_status = lambda: status
    monkeypatch.setattr(tools, "window_region", lambda hwnd: Region(0, 0, 1000, 1000))

    regions = session._recent_changed_regions(1)

    assert regions
    assert regions[0].left <= 100
    assert regions[0].right >= 130
    assert regions[0].left < 800


def test_duplicate_action_guard_blocks_fourth_identical_side_effect(monkeypatch):
    import json

    from winpilot.agent import tools
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="open an application")
    session.world_enabled = True
    args = {"command": "calculator"}
    for _ in range(3):
        session.world.record_action("launch", args)
    monkeypatch.setattr(tools.scope_guard, "ensure_scope", lambda *_args, **_kwargs: None)
    session._t_launch = lambda _args: (_ for _ in ()).throw(
        AssertionError("guard must run before the side effect")
    )

    result = json.loads(session.dispatch("launch", args).text)

    assert result["blocked"] is True
    assert result["repeat_count"] == 4
    assert result["needs_replan"] is True
