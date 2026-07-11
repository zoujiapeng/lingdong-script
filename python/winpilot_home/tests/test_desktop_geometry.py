from __future__ import annotations


def test_virtual_desktop_clamp_preserves_negative_monitor_coordinates(monkeypatch):
    from winpilot.utils import screenshot

    monkeypatch.setattr(screenshot, "_virtual_metrics", lambda: (-1920, 0, 3840, 1080))

    assert screenshot.virtual_screen_region() == screenshot.Region(-1920, 0, 3840, 1080)
    assert screenshot.Region(-1800, 100, 200, 80).clamp_to_screen() == screenshot.Region(-1800, 100, 200, 80)
    assert screenshot.Region(-2100, 100, 300, 80).clamp_to_screen() == screenshot.Region(-1920, 100, 120, 80)


def test_desktop_geometry_maps_capture_pixels_to_physical_screen():
    from winpilot.utils.screenshot import DesktopGeometry, Region

    geometry = DesktopGeometry(
        virtual=Region(-1920, -200, 4480, 1640),
        primary=Region(0, 0, 2560, 1440),
    )

    assert geometry.screen_from_capture(120, 80) == (-1800, -120)
    assert geometry.capture_from_screen(-1800, -120) == (120, 80)
    roi = Region(300, 400, 800, 600)
    assert geometry.screen_from_capture(25, 30, roi) == (325, 430)
