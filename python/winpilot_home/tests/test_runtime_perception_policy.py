def test_server_ocr_warmup_requires_explicit_startup_opt_in(monkeypatch):
    from winpilot import server

    calls = []
    monkeypatch.setattr(server.CONFIG, "enabled_dimensions", lambda: ["ocr"])
    monkeypatch.setattr(
        server.CONFIG,
        "get",
        lambda *path, default=None: False
        if path == ("ocr", "warm_on_startup")
        else default,
    )
    monkeypatch.setattr(
        server.ocr,
        "warm_async",
        lambda: calls.append("ocr") or True,
    )
    monkeypatch.setattr(server.search, "warm_async", lambda: False)

    server._warm_perception_dimensions()

    assert calls == []


def test_default_config_keeps_uia_and_ocr_opt_in():
    from winpilot.config import _DEFAULTS

    assert _DEFAULTS["perception"]["uia"]["enabled"] is False
    assert _DEFAULTS["perception"]["ocr"]["enabled"] is False
    assert _DEFAULTS["ocr"]["warm_on_startup"] is False
    assert _DEFAULTS["vnext"]["save_click_reference_frames"] is False
