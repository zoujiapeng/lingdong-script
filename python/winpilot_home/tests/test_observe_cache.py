from winpilot.agent.tools import ToolSession
from winpilot.perception import fusion
from winpilot.perception.model import UIElement
from winpilot.utils.screenshot import Region


def _snapshot(title: str = "Demo") -> fusion.Snapshot:
    snap = fusion.Snapshot(hwnd=123, title=title, region=Region(0, 0, 200, 100))
    snap.elements = [
        UIElement(source="uia", role="button", text="Search", cx=10, cy=10, left=1, top=1, width=20, height=20)
    ]
    return snap


def test_observe_reuses_cache_for_same_epoch(monkeypatch):
    session = ToolSession(task="demo")
    monkeypatch.setattr(session, "_resolve_hwnd", lambda args: 123)
    monkeypatch.setattr(session, "_remember_target", lambda hwnd=None, hint="": None)
    monkeypatch.setattr(session, "_visual_state_summary", lambda hwnd: "")
    monkeypatch.setattr(session, "_capture_window_b64", lambda hwnd: "")
    monkeypatch.setattr(session, "_perception_mode", lambda: "normal")
    monkeypatch.setattr("winpilot.agent.perception_tools.CONFIG.get", lambda *a, **k: 10.0)

    calls = {"n": 0}

    def fake_observe(*_args, **_kwargs):
        calls["n"] += 1
        return _snapshot()

    monkeypatch.setattr("winpilot.agent.perception_tools.fusion.observe", fake_observe)

    first = session._t_observe({"query": "Search"})
    second = session._t_observe({"query": "Search"})

    assert calls["n"] == 1
    assert "Search" in first.text
    assert "Search" in second.text
    assert session.snapshot is not None
    assert "cache" in session.snapshot.dim_stats


def test_observe_includes_fast_screen_bulletin_when_available(monkeypatch):
    session = ToolSession(task="demo")
    monkeypatch.setattr(session, "_resolve_hwnd", lambda args: 123)
    monkeypatch.setattr(session, "_remember_target", lambda hwnd=None, hint="": None)
    monkeypatch.setattr(session, "_visual_state_summary", lambda hwnd: "")
    monkeypatch.setattr(session, "_capture_window_b64", lambda hwnd: "")
    monkeypatch.setattr(session, "_perception_mode", lambda: "normal")
    monkeypatch.setattr("winpilot.agent.perception_tools.CONFIG.get", lambda *a, **k: 10.0)
    monkeypatch.setattr("winpilot.agent.perception_tools.fusion.observe", lambda *_args, **_kwargs: _snapshot())

    session._fast_gui_controller = type("Controller", (), {
        "watcher": type("Watcher", (), {
            "status": lambda self: {
                "sequence": 2,
                "running": False,
                "last_changed": True,
                "last_ratio": 0.0123,
                "changed_regions": [{"left": 5, "top": 6, "width": 7, "height": 8}],
                "samples": 4,
                "capture_latency_ms": 3,
                "diff_latency_ms": 1,
            },
        })(),
    })()

    result = session._t_observe({"query": "Search"})

    assert "Screen watch bulletin" in result.text
    assert "changed=True" in result.text
    assert "roi=5,6,7x8" in result.text


def test_full_observe_query_runs_targeted_ocr_when_uia_misses_content(monkeypatch):
    from winpilot.agent import perception_tools, tools

    session = ToolSession(task="click Press pulse")
    session.run_id = "query-ocr"
    monkeypatch.setattr(session, "_resolve_hwnd", lambda args: 123)
    monkeypatch.setattr(session, "_remember_target", lambda hwnd=None, hint="": None)
    monkeypatch.setattr(session, "_visual_state_summary", lambda hwnd: "")
    monkeypatch.setattr(session, "_capture_window_b64", lambda hwnd: "")
    monkeypatch.setattr(session, "_perception_mode", lambda: "normal")
    monkeypatch.setattr(perception_tools.CONFIG, "get", lambda section, key=None, default=None: 0.0 if key == "observe_cache_ttl_s" else default)
    monkeypatch.setattr(tools, "window_region", lambda hwnd: Region(0, 0, 500, 400))
    monkeypatch.setattr(perception_tools.fusion, "observe", lambda *args, **kwargs: _snapshot("Browser"))
    monkeypatch.setattr(perception_tools.ocr, "collect", lambda region: [
        UIElement(
            source="ocr", role="text", text="Press pulse",
            cx=200, cy=160, left=160, top=145, width=80, height=30,
        )
    ])

    result = session._t_observe({"query": "Press pulse", "include_icons": False})

    assert "Press pulse" in result.text
    assert "query_directed_ocr" in result.text
    assert session.snapshot.dim_stats["ocr_query"]["count"] == 1


def test_observe_changed_only_uses_recent_screen_delta_roi(monkeypatch):
    from types import SimpleNamespace

    from winpilot.agent import perception_tools, tools

    session = ToolSession(task="demo")
    monkeypatch.setattr(session, "_resolve_hwnd", lambda args: 123)
    monkeypatch.setattr(session, "_remember_target", lambda hwnd=None, hint="": None)
    monkeypatch.setattr(session, "_visual_state_summary", lambda hwnd: "")
    monkeypatch.setattr(session, "_capture_window_b64", lambda hwnd: "")
    monkeypatch.setattr(session, "_perception_mode", lambda: "normal")
    monkeypatch.setattr(tools, "window_region", lambda hwnd: Region(0, 0, 500, 400))
    monkeypatch.setattr(perception_tools.win32, "_window_text", lambda hwnd: "Changed App")
    monkeypatch.setattr(perception_tools.win32, "collect", lambda hwnd: [
        UIElement(source="win32", role="button", text="Inside", cx=115, cy=115, left=100, top=100, width=30, height=20),
        UIElement(source="win32", role="button", text="Outside", cx=420, cy=320, left=400, top=300, width=40, height=30),
    ])

    captured_regions = []

    def fake_ocr_collect(region, min_conf=None):
        captured_regions.append(region)
        return [
            UIElement(source="ocr", role="text", text="Changed Text", cx=125, cy=120, left=100, top=110, width=80, height=20)
        ]

    monkeypatch.setattr(perception_tools.ocr, "collect", fake_ocr_collect)
    monkeypatch.setattr(perception_tools.vision_cv, "detect_icon_candidates", lambda region: [])

    def fail_full_observe(*_args, **_kwargs):
        raise AssertionError("changed_only observe should not call full fusion.observe")

    monkeypatch.setattr(perception_tools.fusion, "observe", fail_full_observe)

    def fake_config_get(section, key=None, default=None):
        if section == "fusion" and key == "observe_cache_ttl_s":
            return 0.0
        if section == "fusion" and key == "changed_region_pad_px":
            return 10
        if section == "fusion" and key == "changed_region_max_regions":
            return 3
        return default

    monkeypatch.setattr(perception_tools.CONFIG, "get", fake_config_get)

    session._fast_gui_controller = SimpleNamespace(
        watcher=SimpleNamespace(
            status=lambda: {
                "sequence": 2,
                "running": False,
                "last_changed": True,
                "last_ratio": 0.01,
                "changed_regions": [{"left": 95, "top": 95, "width": 40, "height": 30}],
                "samples": 2,
                "capture_latency_ms": 4,
                "diff_latency_ms": 1,
            }
        )
    )

    result = session._t_observe({"changed_only": True, "include_icons": False})

    assert "Changed-only observe" in result.text
    assert "Changed Text" in result.text
    assert "Inside" in result.text
    assert "overlaps_changed_roi" in result.text
    assert "Outside" not in result.text
    assert captured_regions == [Region(85, 85, 60, 50)]
    assert session.snapshot is not None
    assert session.snapshot.region == Region(85, 85, 60, 50)


def test_changed_only_ocr_reads_cached_transient_press_frame(monkeypatch):
    from types import SimpleNamespace

    import numpy as np

    from winpilot.agent import perception_tools, tools

    session = ToolSession(task="read PRESS_PULSE_ACTIVE")
    session.run_id = "transient-observe"
    monkeypatch.setattr(session, "_resolve_hwnd", lambda args: 123)
    monkeypatch.setattr(session, "_remember_target", lambda hwnd=None, hint="": None)
    monkeypatch.setattr(session, "_visual_state_summary", lambda hwnd: "")
    monkeypatch.setattr(session, "_capture_window_b64", lambda hwnd: "")
    monkeypatch.setattr(session, "_perception_mode", lambda: "normal")
    monkeypatch.setattr(tools, "window_region", lambda hwnd: Region(0, 0, 500, 400))
    monkeypatch.setattr(perception_tools.win32, "_window_text", lambda hwnd: "Transient Target")
    monkeypatch.setattr(perception_tools.win32, "collect", lambda hwnd: [])
    monkeypatch.setattr(perception_tools.ocr, "collect", lambda region: [
        UIElement(source="ocr", role="text", text="IDLE", cx=region.left + 20, cy=region.top + 20, left=region.left, top=region.top + 10, width=40, height=20),
    ])
    monkeypatch.setattr(perception_tools.ocr, "recognize", lambda image, origin=(0, 0), min_conf=None: [
        UIElement(source="ocr", role="text", text=text, cx=origin[0] + left + 25, cy=origin[1] + 20, left=origin[0] + left, top=origin[1] + 10, width=50, height=20)
        for text, left in (("PRESS", 0), ("PULSE", 52), ("ACTIVE 1", 104))
    ])
    monkeypatch.setattr(perception_tools.vision_cv, "detect_icon_candidates", lambda region: [])
    monkeypatch.setattr(perception_tools.CONFIG, "get", lambda section, key=None, default=None: 0.0 if key == "observe_cache_ttl_s" else default)

    session._fast_gui_controller = SimpleNamespace(watcher=SimpleNamespace(
        status=lambda: {
            "action_changed_regions": [{"left": 100, "top": 120, "width": 120, "height": 40}],
            "last_action_change_age_ms": 20,
        },
        action_evidence=lambda max_age_ms=5000: ([(np.zeros((50, 140, 3), dtype=np.uint8), Region(90, 110, 140, 50))], True),
    ))

    result = session._t_observe({"changed_only": True, "query": "PRESS_PULSE_ACTIVE", "include_icons": False})

    assert "PRESS PULSE ACTIVE 1" in result.text
    assert "transient_action_evidence" in result.text
    assert session.snapshot.dim_stats["ocr"]["transient_evidence_count"] == 3


def test_changed_only_observe_uses_predictive_ocr_without_second_inference(monkeypatch):
    import time
    from types import SimpleNamespace

    from winpilot.agent import perception_tools, tools

    session = ToolSession(task="read PRESS_PULSE_ACTIVE")
    session.run_id = "predictive-hit"
    monkeypatch.setattr(session, "_resolve_hwnd", lambda args: 123)
    monkeypatch.setattr(session, "_remember_target", lambda hwnd=None, hint="": None)
    monkeypatch.setattr(session, "_visual_state_summary", lambda hwnd: "")
    monkeypatch.setattr(session, "_capture_window_b64", lambda hwnd: "")
    monkeypatch.setattr(session, "_perception_mode", lambda: "normal")
    monkeypatch.setattr(tools, "window_region", lambda hwnd: Region(0, 0, 500, 400))
    monkeypatch.setattr(perception_tools.win32, "_window_text", lambda hwnd: "Transient Target")
    monkeypatch.setattr(perception_tools.win32, "collect", lambda hwnd: [])
    monkeypatch.setattr(perception_tools.ocr, "collect", lambda region: (_ for _ in ()).throw(AssertionError("predictive hit should skip current OCR")))
    monkeypatch.setattr(perception_tools.ocr, "recognize", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("predictive hit should skip evidence OCR")))
    monkeypatch.setattr(perception_tools.CONFIG, "get", lambda section, key=None, default=None: 0.0 if key == "observe_cache_ttl_s" else default)
    element = UIElement(
        source="ocr", role="text", text="PRESS PULSE ACTIVE 9",
        cx=170, cy=140, left=100, top=130, width=140, height=20,
        states=["predictive_ocr", "transient_action_evidence", "ocr_line_merge"],
    )
    session._predictive_ocr_cache = {
        "created_ms": int(time.monotonic() * 1000),
        "elements": [element],
        "transient": True,
        "elapsed_ms": 120,
    }
    session._fast_gui_controller = SimpleNamespace(watcher=SimpleNamespace(status=lambda: {
        "action_changed_regions": [{"left": 90, "top": 120, "width": 170, "height": 50}],
        "last_action_change_age_ms": 10,
    }))

    result = session._t_observe({"changed_only": True, "query": "PRESS_PULSE_ACTIVE", "include_icons": False})

    assert "PRESS PULSE ACTIVE 9" in result.text
    assert session.snapshot.dim_stats["ocr"]["predictive_cache_hit"] is True
    assert session.snapshot.dim_stats["ocr"]["ms"] < 50


def test_changed_only_observe_reuses_shared_daemon_incremental_ocr(monkeypatch):
    from types import SimpleNamespace

    from winpilot.agent import perception_tools, tools
    from winpilot.fast.perception_daemon import PERCEPTION_DAEMON

    session = ToolSession(task="read DAEMON_STATUS_READY")
    session.run_id = "daemon-hit"
    session._perception_lease = SimpleNamespace(hwnd=123)
    monkeypatch.setattr(session, "_resolve_hwnd", lambda args: 123)
    monkeypatch.setattr(session, "_remember_target", lambda hwnd=None, hint="": None)
    monkeypatch.setattr(session, "_visual_state_summary", lambda hwnd: "")
    monkeypatch.setattr(session, "_capture_window_b64", lambda hwnd: "")
    monkeypatch.setattr(session, "_perception_mode", lambda: "normal")
    monkeypatch.setattr(tools, "window_region", lambda hwnd: Region(0, 0, 500, 400))
    monkeypatch.setattr(perception_tools.win32, "_window_text", lambda hwnd: "Daemon Target")
    monkeypatch.setattr(perception_tools.win32, "collect", lambda hwnd: [])
    monkeypatch.setattr(
        perception_tools.ocr,
        "collect",
        lambda region: (_ for _ in ()).throw(
            AssertionError("daemon cache hit should skip current OCR")
        ),
    )
    monkeypatch.setattr(
        perception_tools.ocr,
        "recognize",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("daemon cache hit should skip evidence OCR")
        ),
    )
    monkeypatch.setattr(
        perception_tools.CONFIG,
        "get",
        lambda section, key=None, default=None: (
            0.0 if key == "observe_cache_ttl_s" else default
        ),
    )
    element = UIElement(
        source="ocr", role="text", text="DAEMON_STATUS_READY",
        cx=170, cy=140, left=100, top=130, width=180, height=20,
        states=["predictive_ocr", "incremental_perception"],
    )
    monkeypatch.setattr(
        PERCEPTION_DAEMON,
        "incremental_elements",
        lambda hwnd, **kwargs: ([element], {
            "hit": True,
            "source": "perception_daemon",
            "source_elapsed_ms": 90,
            "record_count": 1,
            "inflight": False,
        }),
    )
    session._fast_gui_controller = SimpleNamespace(watcher=SimpleNamespace(status=lambda: {
        "action_changed_regions": [
            {"left": 90, "top": 120, "width": 220, "height": 50}
        ],
        "last_action_change_age_ms": 10,
    }))

    result = session._t_observe({
        "changed_only": True,
        "query": "DAEMON_STATUS_READY",
        "include_icons": False,
    })

    assert "DAEMON_STATUS_READY" in result.text
    assert session.snapshot.dim_stats["ocr"]["predictive_cache_hit"] is True
    assert session.snapshot.dim_stats["ocr"]["predictive_source"] == "perception_daemon"


def test_observe_changed_only_query_miss_falls_back_to_full_observe(monkeypatch):
    from types import SimpleNamespace

    from winpilot.agent import perception_tools, tools
    from winpilot.perception.fusion import Snapshot

    session = ToolSession(task="demo")
    monkeypatch.setattr(session, "_resolve_hwnd", lambda args: 123)
    monkeypatch.setattr(session, "_remember_target", lambda hwnd=None, hint="": None)
    monkeypatch.setattr(session, "_visual_state_summary", lambda hwnd: "")
    monkeypatch.setattr(session, "_capture_window_b64", lambda hwnd: "")
    monkeypatch.setattr(session, "_perception_mode", lambda: "normal")
    monkeypatch.setattr(tools, "window_region", lambda hwnd: Region(0, 0, 500, 400))
    monkeypatch.setattr(perception_tools.win32, "_window_text", lambda hwnd: "Changed App")
    monkeypatch.setattr(perception_tools.win32, "collect", lambda hwnd: [])
    monkeypatch.setattr(perception_tools.ocr, "collect", lambda region, min_conf=None: [
        UIElement(source="ocr", role="text", text="Unrelated ROI", cx=120, cy=120, left=100, top=110, width=90, height=20)
    ])
    monkeypatch.setattr(perception_tools.vision_cv, "detect_icon_candidates", lambda region: [])

    full_observe_calls = []

    def fake_full_observe(hwnd, include_icons=True, perception_mode="normal"):
        full_observe_calls.append((hwnd, include_icons, perception_mode))
        return Snapshot(
            hwnd=hwnd,
            title="Changed App",
            region=Region(0, 0, 500, 400),
            elements=[
                UIElement(source="ocr", role="text", text="Target Text", cx=300, cy=200, left=260, top=190, width=100, height=20),
            ],
        )

    monkeypatch.setattr(perception_tools.fusion, "observe", fake_full_observe)

    def fake_config_get(section, key=None, default=None):
        if section == "fusion" and key == "observe_cache_ttl_s":
            return 0.0
        if section == "fusion" and key == "changed_region_pad_px":
            return 10
        return default

    monkeypatch.setattr(perception_tools.CONFIG, "get", fake_config_get)

    session._fast_gui_controller = SimpleNamespace(
        watcher=SimpleNamespace(
            status=lambda: {
                "sequence": 2,
                "running": False,
                "last_changed": True,
                "last_ratio": 0.01,
                "changed_regions": [{"left": 95, "top": 95, "width": 40, "height": 30}],
                "samples": 2,
                "capture_latency_ms": 4,
                "diff_latency_ms": 1,
            }
        )
    )

    result = session._t_observe({"changed_only": True, "query": "Target", "include_icons": False})

    assert "Changed-only observe query fallback" in result.text
    assert "Filtered elements matching query \"target\"" in result.text
    assert "Target Text" in result.text
    assert "Unrelated ROI" not in result.text
    assert full_observe_calls == [(123, False, "normal")]
    assert session.snapshot is not None
    assert session.snapshot.region == Region(0, 0, 500, 400)


def test_observe_changed_only_uses_recent_roi_after_screen_stabilizes(monkeypatch):
    from types import SimpleNamespace

    from winpilot.agent import perception_tools, tools

    session = ToolSession(task="demo")
    monkeypatch.setattr(session, "_resolve_hwnd", lambda args: 123)
    monkeypatch.setattr(session, "_remember_target", lambda hwnd=None, hint="": None)
    monkeypatch.setattr(session, "_visual_state_summary", lambda hwnd: "")
    monkeypatch.setattr(session, "_capture_window_b64", lambda hwnd: "")
    monkeypatch.setattr(session, "_perception_mode", lambda: "normal")
    monkeypatch.setattr(tools, "window_region", lambda hwnd: Region(0, 0, 500, 400))
    monkeypatch.setattr(perception_tools.win32, "_window_text", lambda hwnd: "Changed App")
    monkeypatch.setattr(perception_tools.win32, "collect", lambda hwnd: [])
    monkeypatch.setattr(perception_tools.ocr, "collect", lambda region, min_conf=None: [
        UIElement(source="ocr", role="text", text="Recent ROI Text", cx=120, cy=120, left=100, top=110, width=90, height=20)
    ])
    monkeypatch.setattr(perception_tools.vision_cv, "detect_icon_candidates", lambda region: [])
    monkeypatch.setattr(
        perception_tools.fusion,
        "observe",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("recent changed ROI should avoid full observe")
        ),
    )

    def fake_config_get(section, key=None, default=None):
        if section == "fusion" and key == "observe_cache_ttl_s":
            return 0.0
        if section == "fusion" and key == "changed_region_pad_px":
            return 10
        if section == "fusion" and key == "changed_region_recent_ms":
            return 30000
        return default

    monkeypatch.setattr(perception_tools.CONFIG, "get", fake_config_get)

    session._fast_gui_controller = SimpleNamespace(
        watcher=SimpleNamespace(
            status=lambda: {
                "sequence": 9,
                "running": True,
                "last_changed": False,
                "last_ratio": 0.0,
                "changed_regions": [],
                "recent_changed_regions": [{"left": 95, "top": 95, "width": 40, "height": 30}],
                "last_change_age_ms": 12000,
                "samples": 20,
                "capture_latency_ms": 4,
                "diff_latency_ms": 1,
            }
        )
    )

    result = session._t_observe({"changed_only": True, "include_icons": False})

    assert "Changed-only observe" in result.text
    assert "Recent ROI Text" in result.text
    assert "returned the normal observe" not in result.text


def test_mutating_dispatch_invalidates_observe_cache(monkeypatch):
    session = ToolSession(task="demo")
    key = (123, True, "normal", session._screen_epoch)
    session.store_observe_cache(key, _snapshot())
    assert session.cached_observe(key, 10.0) is not None

    monkeypatch.setattr(session, "_t_click", lambda args: type("R", (), {
        "text": "{}",
        "is_observe": False,
        "terminal": None,
        "summary": "",
    })())

    session.dispatch("click", {"x": 1, "y": 1, "skip_screen_delta": True})

    assert session._screen_epoch == 1
    assert session.cached_observe(key, 10.0) is None


def test_canvas_fast_observe_skips_full_fusion_when_uia_disabled(monkeypatch):
    import numpy as np

    from winpilot.agent import perception_tools, tools

    session = ToolSession(task="draw a triangle on the canvas")
    monkeypatch.setattr(session, "_resolve_hwnd", lambda args: 123)
    monkeypatch.setattr(session, "_remember_target", lambda hwnd=None, hint="": None)
    monkeypatch.setattr(session, "_capture_window_b64", lambda hwnd: "")
    monkeypatch.setattr(session, "_perception_mode", lambda: "visual")
    monkeypatch.setattr(
        perception_tools.CONFIG,
        "enabled_dimensions",
        lambda: ["win32", "ocr", "cv", "vlm"],
    )

    def fake_config_get(section, key=None, default=None):
        if section == "fusion" and key == "observe_cache_ttl_s":
            return 0.0
        if section == "fusion" and key == "canvas_fast_observe":
            return True
        return default

    monkeypatch.setattr(perception_tools.CONFIG, "get", fake_config_get)
    monkeypatch.setattr(tools, "window_region", lambda hwnd: Region(10, 20, 200, 120))
    monkeypatch.setattr(tools, "capture", lambda region: np.full((120, 200, 3), 255, dtype=np.uint8))
    monkeypatch.setattr(perception_tools.win32, "_window_text", lambda hwnd: "Paint")
    monkeypatch.setattr(perception_tools.win32, "collect", lambda hwnd: [])
    monkeypatch.setattr(perception_tools.win32, "explorer_items", lambda hwnd: [])
    monkeypatch.setattr(perception_tools.vision_cv, "detect_icon_candidates", lambda region: [
        UIElement(source="cv", role="icon", text="", cx=30, cy=40, left=20, top=30, width=10, height=10)
    ])

    def fail_full_observe(*_args, **_kwargs):
        raise AssertionError("full fusion.observe should not run for fast canvas observe")

    monkeypatch.setattr(perception_tools.fusion, "observe", fail_full_observe)

    result = session._t_observe({})

    assert "Window: \"Paint\"" in result.text
    assert "ocr:0 in 0ms" in result.text
    assert session.snapshot.dim_stats["ocr"]["skipped"] == "canvas_fast_observe"
    assert "Visual state summary (likely_canvas@" in result.text


def test_canvas_fast_observe_query_forces_full_fusion(monkeypatch):
    from winpilot.agent import perception_tools

    session = ToolSession(task="draw a triangle on the canvas")
    monkeypatch.setattr(session, "_resolve_hwnd", lambda args: 123)
    monkeypatch.setattr(session, "_remember_target", lambda hwnd=None, hint="": None)
    monkeypatch.setattr(session, "_visual_state_summary", lambda hwnd: "")
    monkeypatch.setattr(session, "_capture_window_b64", lambda hwnd: "")
    monkeypatch.setattr(session, "_perception_mode", lambda: "visual")
    monkeypatch.setattr(
        perception_tools.CONFIG,
        "enabled_dimensions",
        lambda: ["win32", "ocr", "cv"],
    )

    def fake_config_get(section, key=None, default=None):
        if section == "fusion" and key == "observe_cache_ttl_s":
            return 0.0
        if section == "fusion" and key == "canvas_fast_observe":
            return True
        return default

    monkeypatch.setattr(perception_tools.CONFIG, "get", fake_config_get)
    calls = {"full": 0}

    def fake_full_observe(*_args, **_kwargs):
        calls["full"] += 1
        return _snapshot("Paint")

    monkeypatch.setattr(perception_tools.fusion, "observe", fake_full_observe)

    result = session._t_observe({"query": "Search"})

    assert calls["full"] == 1
    assert "Search" in result.text
def test_actionable_query_rejects_long_uia_log_but_accepts_ocr_label():
    from winpilot.agent.tools import ToolSession
    from winpilot.perception.model import UIElement

    long_log = UIElement(
        source="uia",
        role="text",
        text="Press pulse failed in old run " + "x" * 500,
        cx=100,
        cy=100,
    )
    ocr_label = UIElement(
        source="ocr",
        role="text",
        text="Press pulse",
        cx=100,
        cy=100,
    )

    assert ToolSession._query_element_is_grounded("Press pulse", long_log, actionable=True) is False
    assert ToolSession._query_element_is_grounded("Press pulse", ocr_label, actionable=True) is True


def test_ocr_identifier_match_tolerates_one_character_error():
    from winpilot.perception.ocr import _match_text

    assert _match_text("PRESS_PULSE_ACTIVE", "PRESSPULSEACTLVE1") is True
    assert _match_text("PRESS_PULSE_ACTIVE", "unrelated historical message") is False


def test_ocr_identifier_match_corrects_digit_letter_confusion_inside_ui_line():
    from winpilot.perception.ocr import _match_text

    assert _match_text("LOCAL_COUNTER_0", "Status: IDLE LOCAL COUNTER O") is True
    assert _match_text("LOCAL_COUNTER_0", "LOCAL_COUNTER_20") is False
