from winpilot.perception import vlm


def test_deepseek_v4_is_reported_as_text_only(monkeypatch):
    monkeypatch.setitem(vlm.CONFIG._data, "vlm", {
        "enabled": True,
        "base_url": "https://api.deepseek.com",
        "api_key": "dummy",
        "model": "deepseek-v4-flash",
        "thinking": False,
    })

    assert vlm.configuration_issue() == (
        "DeepSeek V4 API is text-only; use a separate multimodal VLM endpoint "
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
