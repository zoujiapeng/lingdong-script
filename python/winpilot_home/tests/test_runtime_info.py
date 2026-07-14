def test_runtime_fingerprint_exposes_non_secret_service_identity():
    from winpilot.runtime_info import runtime_fingerprint

    info = runtime_fingerprint()

    assert info["pid"]
    assert info["repo_root"]
    assert info["winpilot_home"]
    assert "git" in info
    assert "commit" in info["git"]
    assert info["git"]["commit"] == info["git"]["loaded_commit"]
    assert "current_commit" in info["git"]
    assert info["source"]["loaded_stamp"]
    assert info["source"]["current_stamp"]
    assert isinstance(info["source"]["drifted"], bool)
    assert "capabilities" in info
    assert info["capabilities"]["runtime_profile"] is True
    assert info["capabilities"]["automation_ir"] is True
    assert info["capabilities"]["replay_jobs"] is True
    assert info["capabilities"]["scheduler"] is True
    assert info["capabilities"]["desktop_action_queue"] is True
    flat = repr(info).casefold()
    assert "api_key" not in flat
    assert "secret" not in flat
