from __future__ import annotations


def _profiles() -> dict:
    return {
        "mimo": {
            "label": "MiMo V2.5",
            "api": {
                "provider": "mimo",
                "base_url": "https://api.xiaomimimo.com/v1",
                "api_key": "mimo-secret",
                "model": "mimo-v2.5",
            },
            "vlm": {
                "enabled": True,
                "base_url": "https://api.xiaomimimo.com/v1",
                "api_key": "",
                "model": "mimo-v2.5",
                "provider": "mimo",
            },
        },
        "deepseek": {
            "label": "DeepSeek V4 Vision",
            "api": {
                "provider": "deepseek",
                "base_url": "https://api.deepseek.com",
                "api_key": "deepseek-secret",
                "model": "deepseek-v4-flash-vision-exp",
            },
            "vlm": {
                "enabled": True,
                "base_url": "https://api.deepseek.com",
                "api_key": "",
                "model": "deepseek-v4-flash-vision-exp",
                "provider": "deepseek",
            },
        },
    }


def test_model_profiles_activate_persist_and_reuse_credentials(tmp_path, monkeypatch):
    from winpilot import config as config_mod

    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / "config.json")
    store = config_mod.Config()
    store.update({"model_profiles": _profiles()})

    store.activate_model_profile("mimo")
    assert store.get("active_model_profile") == "mimo"
    assert store.get("api", "model") == "mimo-v2.5"
    assert store.get("api", "api_key") == "mimo-secret"

    store.update({"api": {"temperature": 0.6}})
    store.save_active_model_profile()
    store.activate_model_profile("deepseek")
    assert store.get("api", "model") == "deepseek-v4-flash-vision-exp"
    assert store.get("api", "api_key") == "deepseek-secret"

    reloaded = config_mod.Config()
    reloaded.activate_model_profile("mimo")
    assert reloaded.get("api", "temperature") == 0.6
    assert reloaded.get("api", "api_key") == "mimo-secret"


def test_config_api_switches_profiles_without_round_tripping_secrets(tmp_path, monkeypatch):
    from winpilot import config as config_mod
    from winpilot import server

    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / "config.json")
    store = config_mod.Config()
    store.update({"model_profiles": _profiles()})
    monkeypatch.setattr(server, "CONFIG", store)

    response = server.patch_config({"active_model_profile": "mimo"})
    assert response["active_model_profile"] == "mimo"
    assert response["api"]["api_key"] == "***cret"
    assert response["model_profiles"]["deepseek"]["api"]["api_key"] == "***cret"

    server.patch_config({"api": {"model": "mimo-v2.5-pro", "api_key": "***cret"}})
    assert store.get("api", "api_key") == "mimo-secret"
    assert store.get("model_profiles", "mimo", "api", "model") == "mimo-v2.5-pro"


def test_unknown_model_profile_is_rejected(tmp_path, monkeypatch):
    import pytest
    from winpilot import config as config_mod

    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / "config.json")
    store = config_mod.Config()
    with pytest.raises(ValueError, match="模型档案不存在"):
        store.activate_model_profile("missing")
