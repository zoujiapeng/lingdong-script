def test_native_desktop_profile_does_not_start_background_semantic_perception(
    monkeypatch,
):
    from winpilot.agent import fast_tools
    from winpilot.agent.runtime_profile import RuntimeProfile
    from winpilot.agent.tools import ToolSession

    profile = RuntimeProfile(stage_type="ExecuteDesktopTask")
    session = ToolSession(
        task="perform the visible task",
        runtime_profile=profile,
        task_intent={"original_text": "perform the visible task"},
    )
    session.hwnd = 41
    monkeypatch.setattr(
        fast_tools.PERCEPTION_DAEMON,
        "acquire",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("native runtime must not acquire the semantic daemon")
        ),
    )

    controller = session._fast_controller()

    assert profile.perception_daemon is False
    assert session._perception_lease is None
    assert controller is session._fast_gui_controller


def test_legacy_runtime_can_explicitly_enable_background_perception():
    from winpilot.agent.runtime_profile import RuntimeProfile

    profile = RuntimeProfile(perception_daemon=True)

    assert profile.perception_daemon is True
    assert "perception_daemon" in profile.capabilities


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
    assert _DEFAULTS["fusion"]["predictive_ocr_enabled"] is False
    assert _DEFAULTS["ocr"]["warm_on_startup"] is False
