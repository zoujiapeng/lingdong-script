import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest


@pytest.fixture
def runtime_info_module():
    from winpilot import runtime_info

    runtime_info._reset_runtime_caches_for_tests()
    yield runtime_info
    runtime_info._reset_runtime_caches_for_tests()


def test_runtime_fingerprint_exposes_non_secret_service_identity(runtime_info_module):
    runtime_fingerprint = runtime_info_module.runtime_fingerprint

    info = runtime_fingerprint()

    assert info["contract_version"] == 3
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


def test_runtime_checks_reuse_independent_short_ttl_caches(monkeypatch, runtime_info_module):
    clock = [100.0]
    calls = {"source": 0, "git": 0}

    def source_stamp():
        calls["source"] += 1
        return runtime_info_module._LOADED_SOURCE_STAMP

    def git_state():
        calls["git"] += 1
        return "current-commit", "master", False

    monkeypatch.setattr(runtime_info_module, "_monotonic", lambda: clock[0])
    monkeypatch.setattr(runtime_info_module, "_source_stamp", source_stamp)
    monkeypatch.setattr(runtime_info_module, "_read_git_state", git_state)
    runtime_info_module._reset_runtime_caches_for_tests()

    first = runtime_info_module.runtime_fingerprint()
    second = runtime_info_module.runtime_fingerprint()
    assert runtime_info_module.runtime_source_drifted() is False

    assert first["git"]["current_commit"] == "current-commit"
    assert second["git"]["current_commit"] == "current-commit"
    assert calls == {"source": 1, "git": 1}

    # The safety-critical source check refreshes sooner without invoking git.
    clock[0] += runtime_info_module._SOURCE_STAMP_TTL_S + 0.001
    assert runtime_info_module.runtime_source_drifted() is False
    assert calls == {"source": 2, "git": 1}

    # Git diagnostics refresh only after their own, longer TTL.
    clock[0] = 100.0 + runtime_info_module._GIT_STATE_TTL_S + 0.001
    runtime_info_module.runtime_fingerprint()
    assert calls == {"source": 3, "git": 2}


def test_source_change_is_reported_within_short_ttl_and_stays_fail_closed(
    monkeypatch, runtime_info_module
):
    clock = [200.0]
    stamp = [runtime_info_module._LOADED_SOURCE_STAMP]
    calls = [0]

    def source_stamp():
        calls[0] += 1
        return stamp[0]

    monkeypatch.setattr(runtime_info_module, "_monotonic", lambda: clock[0])
    monkeypatch.setattr(runtime_info_module, "_source_stamp", source_stamp)
    runtime_info_module._reset_runtime_caches_for_tests()

    assert runtime_info_module.runtime_source_drifted() is False
    stamp[0] = "changed-source"

    clock[0] += runtime_info_module._SOURCE_STAMP_TTL_S - 0.001
    assert runtime_info_module.runtime_source_drifted() is False

    clock[0] += 0.002
    assert runtime_info_module.runtime_source_drifted() is True
    assert calls[0] == 2

    # Reverting files must not make an already-stale interpreter compatible.
    stamp[0] = runtime_info_module._LOADED_SOURCE_STAMP
    clock[0] += 60.0
    assert runtime_info_module.runtime_source_drifted() is True
    assert runtime_info_module.runtime_fingerprint()["source"]["drifted"] is True
    assert calls[0] == 2


def test_runtime_cache_refresh_is_single_flight_across_threads(
    monkeypatch, runtime_info_module
):
    calls = {"source": 0, "git": 0}
    calls_lock = threading.Lock()

    def source_stamp():
        with calls_lock:
            calls["source"] += 1
        time.sleep(0.02)
        return runtime_info_module._LOADED_SOURCE_STAMP

    def git_state():
        with calls_lock:
            calls["git"] += 1
        time.sleep(0.02)
        return "current-commit", "master", False

    monkeypatch.setattr(runtime_info_module, "_source_stamp", source_stamp)
    monkeypatch.setattr(runtime_info_module, "_read_git_state", git_state)
    runtime_info_module._reset_runtime_caches_for_tests()

    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(
            executor.map(lambda _: runtime_info_module.runtime_fingerprint(), range(24))
        )

    assert all(item["source"]["drifted"] is False for item in results)
    assert calls == {"source": 1, "git": 1}
