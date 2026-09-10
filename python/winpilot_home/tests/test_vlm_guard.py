from winpilot.perception import vlm
from winpilot.model_adapter import model_supports_vision


def test_only_deepseek_vision_exp_is_native_visual():
    assert model_supports_vision(
        "deepseek-v4-flash-vision-exp",
        "deepseek",
    )
    assert not model_supports_vision("deepseek-v4-flash", "deepseek")
    assert not model_supports_vision("deepseek-v4-pro", "deepseek")
    assert not model_supports_vision("deepseek-made-up-vision", "deepseek")


def test_deepseek_v4_is_reported_as_text_only(monkeypatch):
    monkeypatch.setitem(vlm.CONFIG._data, "vlm", {
        "enabled": True,
        "base_url": "https://api.deepseek.com",
        "api_key": "dummy",
        "model": "deepseek-v4-flash",
        "thinking": False,
    })

    assert vlm.configuration_issue() == (
        "Configured DeepSeek model is text-only; use "
        "deepseek-v4-flash-vision-exp or a separate multimodal VLM endpoint "
        "for screenshots/images."
    )
    assert not vlm.is_configured()


def test_deepseek_chat_is_reported_as_text_only(monkeypatch):
    monkeypatch.setitem(vlm.CONFIG._data, "vlm", {
        "enabled": True,
        "base_url": "https://api.deepseek.com",
        "api_key": "dummy",
        "model": "deepseek-chat",
        "thinking": False,
    })

    assert "text-only" in vlm.configuration_issue()
    assert not vlm.is_configured()


def test_deepseek_vision_exp_is_configured(monkeypatch):
    monkeypatch.setitem(vlm.CONFIG._data, "vlm", {
        "enabled": True,
        "base_url": "https://api.deepseek.com",
        "api_key": "dummy",
        "model": "deepseek-v4-flash-vision-exp",
        "thinking": False,
    })

    assert vlm.configuration_issue() == ""
    assert vlm.is_configured()


def test_non_deepseek_multimodal_endpoint_stays_enabled(monkeypatch):
    monkeypatch.setitem(vlm.CONFIG._data, "vlm", {
        "enabled": True,
        "base_url": "http://127.0.0.1:8080/v1",
        "api_key": "dummy",
        "model": "qwen2.5-vl",
        "thinking": False,
    })

    assert vlm.configuration_issue() == ""
    assert vlm.is_configured()


def test_mimo_v25_reuses_main_model_credentials_for_vision(monkeypatch):
    monkeypatch.setitem(vlm.CONFIG._data, "api", {
        "provider": "mimo",
        "base_url": "https://api.xiaomimimo.com/v1",
        "api_key": "main-key",
        "model": "mimo-v2.5",
    })
    monkeypatch.setitem(vlm.CONFIG._data, "vlm", {
        "enabled": True,
        "base_url": "",
        "api_key": "",
        "model": "",
        "thinking": False,
    })

    settings = vlm._settings()

    assert settings["model"] == "mimo-v2.5"
    assert settings["base_url"] == "https://api.xiaomimimo.com/v1"
    assert settings["api_key"] == "main-key"
    assert vlm.is_configured()


def test_semantic_visual_verifier_returns_strict_structured_evidence(monkeypatch):
    from types import SimpleNamespace

    from winpilot.utils.screenshot import Region

    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content='{"passed":false,"confidence":0.94,"subject_present":true,'
                '"colorful":true,"detail_level":"low","observed":["cat"],'
                '"issues":["too crude"]}'
    ))])
    completions = SimpleNamespace(create=lambda **_kwargs: response)
    monkeypatch.setattr(vlm, "is_configured", lambda: True)
    monkeypatch.setattr(vlm, "_settings", lambda: {"model": "mimo-v2.5"})
    monkeypatch.setattr(vlm, "_encode_region", lambda *_args, **_kwargs: ("jpeg", 1.0))
    monkeypatch.setattr(
        vlm,
        "_client",
        lambda: SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )

    result = vlm.verify(Region(0, 0, 100, 100), "a detailed colorful cat")

    assert result["ok"] is True
    assert result["verified"] is False
    assert result["semantic_visual_verified"] is False
    assert result["verify_method"] == "semantic-vlm"
    assert result["verdict"]["issues"] == ["too crude"]
