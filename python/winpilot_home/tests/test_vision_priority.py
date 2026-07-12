"""Tests for perception_mode / vision_priority_task (backward-compat)."""
from winpilot.perception import intent


# ---- perception_mode() ------------------------------------------------

def test_mode_drawing():
    assert intent.perception_mode("画一只精致的猫") == "visual"


def test_mode_chat_qq():
    assert intent.perception_mode("用QQ给我的设备发消息") == "visual"


def test_mode_mirror():
    assert intent.perception_mode("和我的手机持续聊天") == "visual"


def test_mode_title_carries_signal():
    # Generic follow-up text, but window title has the signal.
    assert intent.perception_mode("继续", "QQ") == "visual"


def test_mode_office_pixel_text_surface():
    assert intent.perception_mode("Create a workbook in WPS Spreadsheets") == "pixel_text"
    assert intent.perception_mode("继续", "工作簿31 - WPS Office") == "pixel_text"


def test_mode_plain_task():
    assert intent.perception_mode("打开计算器") == "normal"
    assert intent.perception_mode("列出所有窗口") == "normal"


# ---- vision_priority_task() backward-compat ---------------------------

def test_intent_flags_drawing_chat_mirror():
    assert intent.vision_priority_task("画一只精致的猫")
    assert intent.vision_priority_task("用QQ给我的设备发消息")
    assert intent.vision_priority_task("和我的手机持续聊天")
    assert intent.vision_priority_task("继续", "QQ")


def test_intent_ignores_plain_tasks():
    assert not intent.vision_priority_task("打开计算器")
    assert not intent.vision_priority_task("列出所有窗口")


# ---- fusion.observe integration (perception_mode propagation) ---------

def _fake_observe_env(monkeypatch):
    """Stub all perception dims; track whether each ran."""
    from winpilot.perception import fusion, uia, ocr, win32, vision_cv, vlm
    from winpilot.utils.screenshot import Region
    from winpilot.perception.model import UIElement

    region = Region(0, 0, 800, 600)
    monkeypatch.setattr(fusion, "window_region", lambda hwnd: region)
    monkeypatch.setattr(fusion.win32, "_window_text", lambda hwnd: "QQ")
    monkeypatch.setattr(fusion, "_foreground_modal_over_target", lambda hwnd, reg: False)
    config_get = fusion.CONFIG.get

    def fake_config_get(section, key=None, default=None):
        if section == "fusion" and key == "ocr_skip_when_uia_rich":
            return True
        if section == "fusion" and key == "vlm_skip_when_uia_rich":
            return True
        if section == "fusion" and key == "vlm_assist_threshold":
            return 60
        if section == "fusion" and key == "vlm_ocr_rich_threshold":
            return 12
        if section == "fusion" and key == "uia_rich_threshold":
            return 20
        return config_get(section, key, default=default)

    monkeypatch.setattr(fusion.CONFIG, "get", fake_config_get)
    monkeypatch.setattr(
        fusion.CONFIG,
        "enabled_dimensions",
        lambda: ["uia", "win32", "ocr", "cv", "vlm"],
    )

    # UIA returns a RICH set (>= normal threshold=20, < vlm_assist_threshold=60).
    rich = [
        UIElement(source="uia", role="button", text=f"btn{i}",
                  cx=80 + (i % 6) * 90, cy=120 + (i // 6) * 70,
                  left=78 + (i % 6) * 90, top=118 + (i // 6) * 70,
                  width=48, height=24)
        for i in range(30)
    ]
    monkeypatch.setattr(uia, "collect", lambda hwnd: list(rich))
    monkeypatch.setattr(win32, "collect", lambda hwnd: [])
    monkeypatch.setattr(win32, "explorer_items", lambda hwnd: [])
    monkeypatch.setattr(vision_cv, "detect_icon_candidates", lambda reg: [])

    ran = {"ocr": False, "vlm": False}

    def fake_ocr(reg, min_conf=0.55):
        ran["ocr"] = True
        return []

    def fake_vlm_collect(reg):
        ran["vlm"] = True
        return []

    monkeypatch.setattr(ocr, "collect", fake_ocr)
    monkeypatch.setattr(vlm, "collect", fake_vlm_collect)
    monkeypatch.setattr(vlm, "is_configured", lambda: True)
    return ran


def test_ocr_skipped_when_uia_rich_and_normal_mode(monkeypatch):
    from winpilot.perception import fusion
    ran = _fake_observe_env(monkeypatch)
    fusion.observe(123, include_icons=False, perception_mode="normal")
    assert ran["ocr"] is False


def test_toolbar_only_uia_count_does_not_suppress_ocr(monkeypatch):
    from winpilot.perception import fusion, ocr, uia
    from winpilot.perception.model import UIElement

    ran = _fake_observe_env(monkeypatch)
    toolbar = [
        UIElement(
            source="uia", role="button", text=f"toolbar{i}",
            cx=20 + i * 20, cy=40, left=10 + i * 20, top=28,
            width=18, height=24,
        )
        for i in range(30)
    ]
    monkeypatch.setattr(uia, "collect", lambda hwnd: toolbar)
    monkeypatch.setattr(ocr, "collect", lambda region, min_conf=0.55: ran.__setitem__("ocr", True) or [])

    snapshot = fusion.observe(123, include_icons=False, perception_mode="normal")

    assert ran["ocr"] is True
    assert snapshot.dim_stats["uia"]["rich"] is False
    assert snapshot.dim_stats["uia"]["content_count"] == 0


def test_ocr_forced_when_visual_mode(monkeypatch):
    from winpilot.perception import fusion
    ran = _fake_observe_env(monkeypatch)
    fusion.observe(123, include_icons=False, perception_mode="visual")
    assert ran["ocr"] is True


def test_vlm_assist_runs_in_visual_mode_below_threshold(monkeypatch):
    """In visual mode, VLM should run when uia_count < vlm_assist_threshold (60)."""
    from winpilot.perception import fusion
    ran = _fake_observe_env(monkeypatch)
    # UIA returns 30 text elements (< 60 vlm_assist_threshold)
    fusion.observe(123, include_icons=False, perception_mode="visual")
    assert ran["vlm"] is True


def test_vlm_still_skipped_in_normal_mode_when_uia_rich(monkeypatch):
    """In normal mode, VLM should still be skipped when UIA is rich."""
    from winpilot.perception import fusion
    ran = _fake_observe_env(monkeypatch)
    fusion.observe(123, include_icons=False, perception_mode="normal")
    assert ran["vlm"] is False


def test_backward_compat_vision_priority_true(monkeypatch):
    """vision_priority=True must behave identically to perception_mode='visual'."""
    from winpilot.perception import fusion
    ran = _fake_observe_env(monkeypatch)
    fusion.observe(123, include_icons=False, vision_priority=True)
    assert ran["ocr"] is True
    assert ran["vlm"] is True


def test_vlm_assist_skips_when_visual_ocr_is_already_rich(monkeypatch):
    from winpilot.perception import fusion, ocr
    from winpilot.perception.model import UIElement

    ran = _fake_observe_env(monkeypatch)

    def rich_ocr(_region, min_conf=0.55):
        ran["ocr"] = True
        return [
            UIElement(source="ocr", role="text", text=f"row {index}", cx=80, cy=80 + index * 20)
            for index in range(16)
        ]

    monkeypatch.setattr(ocr, "collect", rich_ocr)
    snapshot = fusion.observe(123, include_icons=False, perception_mode="visual")

    assert ran["ocr"] is True
    assert ran["vlm"] is False
    assert snapshot.dim_stats["vlm"]["skipped"] == "ocr_rich"


def test_ocr_forced_when_pixel_text_mode(monkeypatch):
    from winpilot.perception import fusion
    ran = _fake_observe_env(monkeypatch)
    fusion.observe(123, include_icons=False, perception_mode="pixel_text")
    assert ran["ocr"] is True


def test_pixel_text_mode_skips_vlm_when_ocr_is_readable(monkeypatch):
    from winpilot.perception import fusion, ocr
    from winpilot.perception.model import UIElement

    ran = _fake_observe_env(monkeypatch)

    def readable_ocr(region, min_conf=0.55):
        ran["ocr"] = True
        return [UIElement(source="ocr", role="text", text="A1", cx=80, cy=180)]

    monkeypatch.setattr(ocr, "collect", readable_ocr)
    fusion.observe(123, include_icons=False, perception_mode="pixel_text")

    assert ran["ocr"] is True
    assert ran["vlm"] is False


def test_visual_observe_does_not_block_on_hung_uia_provider(monkeypatch):
    import time

    from winpilot.perception import fusion, uia

    ran = _fake_observe_env(monkeypatch)
    previous_get = fusion.CONFIG.get

    def bounded_config_get(section, key=None, default=None):
        if section == "fusion" and key == "visual_dimension_timeout_s":
            return 0.05
        if section == "fusion" and key == "uia_timeout_cooldown_s":
            return 1.0
        return previous_get(section, key, default=default)

    monkeypatch.setattr(fusion.CONFIG, "get", bounded_config_get)
    monkeypatch.setattr(uia, "collect", lambda _hwnd: (time.sleep(0.4) or []))
    fusion._UIA_SUPPRESSED_UNTIL.clear()

    started = time.monotonic()
    snapshot = fusion.observe(9876, include_icons=False, perception_mode="visual")
    elapsed = time.monotonic() - started

    assert elapsed < 0.25
    assert ran["ocr"] is True
    assert snapshot.dim_stats["uia"]["timed_out"] is True
    assert fusion._uia_suppressed(9876) is True
