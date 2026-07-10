from pathlib import Path
from types import SimpleNamespace

import numpy as np

from winpilot.memory import replay_anchors
from winpilot.memory.store import MemoryStore
from winpilot.recorder import trace as trace_mod


def _anchor(template: str = "target.png") -> dict:
    return {
        "key": "1",
        "text": "Export",
        "role": "button",
        "template": template,
        "relative_bbox": [0.25, 0.4, 0.2, 0.1],
        "click_offset": [0.5, 0.5],
        "confidence": 0.9,
        "hits": 0,
        "misses": 0,
        "learned_at": 1,
        "updated_at": 1,
        "last_used_at": 0,
    }


def test_replay_anchor_profile_round_trips_through_memory_meta(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")

    assert replay_anchors.save_profile("skill:export", {"1": _anchor()}, store=store) == 1
    loaded = replay_anchors.load_profile("skill:export", store=store)

    assert loaded["1"]["text"] == "Export"
    assert loaded["1"]["template"] == "target.png"
    assert loaded["1"]["relative_bbox"] == [0.25, 0.4, 0.2, 0.1]
    store.close()


def test_cached_anchor_uses_local_template_and_skips_uia(monkeypatch):
    monkeypatch.setattr(replay_anchors, "load_profile", lambda _profile: {"1": _anchor()})
    monkeypatch.setattr(
        trace_mod.uia,
        "find_by_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("UIA fallback should not run")),
    )
    monkeypatch.setattr(trace_mod, "window_region", lambda _hwnd: trace_mod.Region(100, 50, 800, 600))
    monkeypatch.setattr(trace_mod.Replayer, "_focus_for_anchor_lookup", lambda _self: True)
    monkeypatch.setattr(
        trace_mod.vision_cv,
        "match_template",
        lambda *_args, **_kwargs: [
            SimpleNamespace(
                left=300, top=290, width=160, height=60,
                cx=380, cy=320, confidence=0.97,
            ),
        ],
    )

    replayer = trace_mod.Replayer(anchor_profile="skill:export")
    replayer.hwnd = 123
    point = replayer._relocate({"tool": "click", "text": "Export"}, "1")

    assert point == (380, 320)
    assert replayer._last_relocation["source"] == "anchor_cache"
    assert replayer._last_relocation["anchor_cache"]["hit"] is True
    assert replayer._anchor_summary()["hit_rate"] == 1.0


def test_stale_anchor_falls_back_to_semantic_selector(monkeypatch):
    monkeypatch.setattr(replay_anchors, "load_profile", lambda _profile: {"1": _anchor()})
    monkeypatch.setattr(trace_mod, "window_region", lambda _hwnd: trace_mod.Region(0, 0, 800, 600))
    monkeypatch.setattr(trace_mod.Replayer, "_focus_for_anchor_lookup", lambda _self: True)
    monkeypatch.setattr(trace_mod.vision_cv, "match_template", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        trace_mod.uia,
        "find_by_text",
        lambda _hwnd, _text: [
            SimpleNamespace(
                text="Export", source="uia", left=640, top=90,
                width=120, height=40, cx=700, cy=110,
            ),
        ],
    )

    replayer = trace_mod.Replayer(anchor_profile="skill:export")
    replayer.hwnd = 123
    point = replayer._relocate({"tool": "click", "text": "Export"}, "1")

    assert point == (700, 110)
    assert replayer._last_relocation["source"] == "uia"
    assert replayer._last_relocation["anchor_cache"]["hit"] is False
    metrics = replayer._anchor_summary()
    assert metrics["miss_count"] == 1
    assert metrics["fallback_count"] == 1


def test_verified_fallback_can_learn_normalized_template(monkeypatch, tmp_path):
    saved: list[tuple[tuple[int, ...], str]] = []
    persisted: list[dict] = []
    monkeypatch.setattr(replay_anchors, "load_profile", lambda _profile: {})
    monkeypatch.setattr(
        replay_anchors,
        "save_profile",
        lambda profile, anchors: persisted.append({"profile": profile, "anchors": anchors}) or len(anchors),
    )
    monkeypatch.setattr(
        trace_mod.vision_cv,
        "save_template_image",
        lambda image, name: saved.append((image.shape, name)) or Path(tmp_path / f"{name}.png"),
    )

    replayer = trace_mod.Replayer(anchor_profile="skill:export")
    replayer.hwnd = 123
    replayer._last_relocation = {
        "source": "uia",
        "text": "Export",
        "point": [300, 220],
        "bbox": [240, 195, 120, 50],
    }
    frame = np.full((300, 500, 3), 255, dtype=np.uint8)

    learned = replayer._learn_anchor(
        "1",
        {"tool": "click", "text": "Export", "role": "button"},
        frame,
        trace_mod.Region(100, 100, 500, 300),
    )
    replayer._flush_anchor_cache()

    assert learned is True
    assert saved[0][0] == (56, 126, 3)
    record = persisted[0]["anchors"]["1"]
    assert record["relative_bbox"] == [0.274, 0.30666666666666664, 0.252, 0.18666666666666668]
    assert record["click_offset"] == [0.5, 0.5]
    assert replayer._anchor_summary()["learned_count"] == 1
