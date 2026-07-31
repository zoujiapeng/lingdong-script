from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path


def _load_harness():
    path = (
        Path(__file__).resolve().parents[3]
        / "scripts"
        / "client_thread_connector_e2e_run.py"
    )
    spec = importlib.util.spec_from_file_location(
        "client_thread_connector_e2e_run_test",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_fixture_is_one_explicit_stdio_server_and_keeps_token_out_of_goal(tmp_path):
    harness = _load_harness()
    token = "cne2e_private_fixture_value"
    fixture = harness.build_fixture_environment(tmp_path, token=token)
    config = json.loads(fixture.config_path.read_text(encoding="utf-8"))

    assert list(config) == ["mcpServers"]
    assert list(config["mcpServers"]) == [fixture.server_name]
    server = config["mcpServers"][fixture.server_name]
    assert server["command"] == sys.executable
    assert server["args"] == ["-u", str(harness.FIXTURE_SCRIPT.resolve())]
    assert server["env"]["WINPILOT_CONNECTOR_E2E_TOKEN"] == token
    assert server["env"]["WINPILOT_CONNECTOR_E2E_ORACLE"] == str(
        fixture.oracle_path.resolve()
    )
    assert token not in harness.CONNECTOR_READ_TASK
    assert "connector-e2e-" not in harness.CONNECTOR_READ_TASK
    task = harness.connector_read_task(fixture.server_name)
    assert fixture.server_name in task
    assert token not in task
    assert (fixture.probe_module_dir / "sitecustomize.py").read_bytes() == (
        harness.PROBE_SCRIPT.read_bytes()
    )


def test_preopened_notepad_task_keeps_target_and_file_path_out_of_goal(tmp_path):
    harness = _load_harness()
    fixture = harness.build_fixture_environment(
        tmp_path,
        token="cne2e_private_target",
    )

    task = harness.connector_to_current_notepad_task(fixture)

    assert fixture.server_name in task
    assert "当前记事本文档" in task
    assert fixture.token not in task
    assert str(tmp_path.resolve()) not in task
    assert all(term not in task for term in ("Ctrl", "全选", "另存为"))


def test_real_stdio_fixture_advertises_and_executes_one_read_only_tool(
    monkeypatch,
    tmp_path,
):
    harness = _load_harness()
    from winpilot.integrations import mcp_client

    fixture = harness.build_fixture_environment(
        tmp_path,
        token="cne2e_stdio_value",
    )
    monkeypatch.setenv("WINPILOT_MCP_CONFIG", str(fixture.config_path))
    with mcp_client._cache_lock:
        mcp_client._tool_cache.clear()
    try:
        discovered = mcp_client.discover(
            server_name=fixture.server_name,
            timeout_s=20,
        )
        assert discovered["ok"] is True
        assert len(discovered["tools"]) == 1
        descriptor = discovered["tools"][0]
        assert descriptor["name"] == "read_value"
        assert descriptor["annotations"] == {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }

        called = mcp_client.call(
            fixture.server_name,
            "read_value",
            {"key": "verification"},
            require_read_only=True,
            timeout_s=20,
        )
        assert called["ok"] is True
        assert called["structured_content"] == {
            "key": "verification",
            "value": fixture.token,
        }
        invocation = harness.read_invocation_evidence(fixture)
        assert invocation["passed"] is True
        assert invocation["matching_invocation_count"] == 1
    finally:
        with mcp_client._cache_lock:
            mcp_client._tool_cache.clear()


def test_invocation_oracle_filters_calls_before_the_case_boundary(tmp_path):
    harness = _load_harness()
    fixture = harness.build_fixture_environment(tmp_path, token="cne2e_boundary")
    physical_arguments_sha256 = harness._sha256_text(
        json.dumps(
            {"key": harness.FIXTURE_KEY},
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    boundary = time.time_ns()
    events = [
        {
            "format": "connector-e2e-invocation/1",
            "time_ns": boundary - 1,
            "pid": 1,
            "tool": "read_value",
            "key": "verification",
            "physical_arguments_sha256": physical_arguments_sha256,
            "value_sha256": fixture.value_sha256,
        },
        {
            "format": "connector-e2e-invocation/1",
            "time_ns": boundary + 1,
            "pid": 2,
            "tool": "read_value",
            "key": "verification",
            "physical_arguments_sha256": physical_arguments_sha256,
            "value_sha256": fixture.value_sha256,
        },
    ]
    fixture.oracle_path.write_text(
        "\n".join(json.dumps(item) for item in events) + "\n",
        encoding="utf-8",
    )

    evidence = harness.read_invocation_evidence(
        fixture,
        after_time_ns=boundary,
    )

    assert evidence["passed"] is True
    assert evidence["event_count"] == 1
    assert evidence["matching_invocation_count"] == 1


def test_thread_evidence_binds_exact_run_turn_order_and_connector_reply(
    monkeypatch,
):
    harness = _load_harness()
    responses = {
        "/api/v2/arp/runs/run-2": {
            "run": {"thread_id": "thread-1", "turn_id": "turn-2"}
        },
        "/api/v2/threads/thread-1": {
            "thread": {
                "thread_id": "thread-1",
                "turns": [
                    {
                        "run_id": "run-1",
                        "turn_id": "turn-1",
                        "status": "reply",
                        "result": "hello",
                    },
                    {
                        "run_id": "run-2",
                        "turn_id": "turn-2",
                        "status": "reply",
                        "result": "cne2e_exact",
                    },
                ],
            }
        },
    }
    monkeypatch.setattr(
        harness,
        "_runtime_json",
        lambda _driver, path, **_kwargs: responses[path],
    )

    evidence = harness.thread_evidence(
        object(),
        {"result": {"run_id": "run-2"}},
        expected_thread_id="thread-1",
        expected_run_ids=("run-1",),
        expected_result="cne2e_exact",
    )

    assert evidence["passed"] is True
    assert evidence["turn_id"] == "turn-2"
    assert evidence["turn_count"] == 2
    assert evidence["exact_result_match"] is True
    assert "cne2e_exact" not in json.dumps(evidence)


def test_late_context_probe_keeps_marker_close_and_requires_exact_recall():
    harness = _load_harness()
    marker = "walk_private_marker"

    introduction = harness.contextual_chat_prompt(17, 20, marker)
    intervening = harness.contextual_chat_prompt(18, 20, marker)
    recall = harness.contextual_chat_prompt(19, 20, marker)

    assert introduction[2] == "marker_introduction"
    assert marker in introduction[0]
    assert introduction[1] is None
    assert intervening[2] == "ordinary_chat"
    assert marker not in intervening[0]
    assert recall == (
        "我刚才给今晚散步起的代号是什么？只回复代号。",
        marker,
        "marker_recall",
    )
    assert 19 - 17 == 2


def test_thread_case_reuses_base_native_frontend_path_and_same_thread(
    monkeypatch,
    tmp_path,
):
    harness = _load_harness()
    calls = []
    run_counter = 0

    class Driver:
        def window_lifecycle_state(self):
            return {"visible": True, "minimized": False, "foreground": True}

    def fake_thread_evidence(
        _driver,
        result,
        *,
        expected_thread_id,
        expected_run_ids,
        **_kwargs,
    ):
        run_id = result["result"]["run_id"]
        return {
            "source": "server_owned_thread_snapshot",
            "passed": True,
            "thread_id": expected_thread_id or "thread-one",
            "turn_id": f"turn-{run_id}",
            "run_id": run_id,
            "turn_count": len(expected_run_ids) + 1,
            "response_chars": 3,
            "response_sha256": "a" * 64,
        }

    def fake_run_case(name, spec, **kwargs):
        nonlocal run_counter
        run_counter += 1
        run_id = f"run-{run_counter}"
        calls.append({"name": name, "spec": spec, **kwargs})
        kwargs["before_submit"](Driver(), {})
        result = {
            "name": name,
            "result": {"run_id": run_id, "outcome": "terminal", "status": "done"},
        }
        result["task_verdict"] = kwargs["independent_verifier"](result)
        result["passed"] = result["task_verdict"]["passed"] is True
        return result

    monkeypatch.setattr(harness, "thread_evidence", fake_thread_evidence)
    monkeypatch.setattr(harness.client_harness, "run_case", fake_run_case)
    identity = harness.client_harness.ClientIdentity(
        pid=1,
        hwnd=2,
        executable="electron.exe",
        command_line=["electron.exe", "."],
        workspace_verified=True,
    )
    sequence = harness.ThreadSequence()

    first = harness._run_thread_case(
        "chat-01",
        "hello",
        output_root=tmp_path,
        identity=identity,
        sequence=sequence,
        timeout=30,
    )
    second = harness._run_thread_case(
        "chat-02",
        "again",
        output_root=tmp_path,
        identity=identity,
        sequence=sequence,
        timeout=30,
    )

    assert first["passed"] is True and second["passed"] is True
    assert sequence.thread_id == "thread-one"
    assert sequence.run_ids == ["run-1", "run-2"]
    assert calls[0]["reset_session"] is True
    assert calls[0]["required_context_run_id"] == ""
    assert calls[1]["reset_session"] is False
    assert calls[1]["required_context_run_id"] == "run-1"
    assert "after_submit" not in calls[0]
    assert calls[0]["expected_client_identity"] == identity
    assert calls[0]["spec"]["runtime_version"] == "vnext"


def test_file_oracle_requires_fresh_connector_call_and_exact_saved_value(tmp_path):
    harness = _load_harness()
    fixture = harness.build_fixture_environment(tmp_path, token="cne2e_saved")
    physical_arguments_sha256 = harness._sha256_text(
        json.dumps(
            {"key": harness.FIXTURE_KEY},
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    started = time.time_ns()
    fixture.oracle_path.write_text(
        json.dumps(
            {
                "format": "connector-e2e-invocation/1",
                "time_ns": started + 1,
                "pid": 7,
                "tool": "read_value",
                "key": "verification",
                "physical_arguments_sha256": physical_arguments_sha256,
                "value_sha256": fixture.value_sha256,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "value.txt"
    output.write_text(fixture.token, encoding="utf-8")

    passed = harness.file_evidence(
        output,
        fixture,
        after_time_ns=started,
    )
    output.write_text("wrong", encoding="utf-8")
    failed = harness.file_evidence(
        output,
        fixture,
        after_time_ns=started,
    )

    assert passed["passed"] is True
    assert passed["file"]["exact_value"] is True
    assert failed["passed"] is False
    assert failed["file"]["exact_value"] is False
    assert fixture.token not in json.dumps(passed)

    output.write_text(fixture.token + "\n", encoding="utf-8")
    extra_whitespace = harness.file_evidence(
        output,
        fixture,
        after_time_ns=started,
    )
    assert extra_whitespace["passed"] is False
    assert extra_whitespace["file"]["exact_value"] is False


def test_usage_projection_reports_cache_tokens_without_model_content(
    monkeypatch,
    tmp_path,
):
    harness = _load_harness()
    events = [
        {
            "type": "agent_usage",
            "run_id": "run-usage",
            "step_usage": {"prompt_tokens": 10, "private": "not projected"},
            "total": {
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "cache_hit_tokens": 6,
                "cache_miss_tokens": 4,
                "cache_creation_tokens": 0,
                "reasoning_tokens": 1,
            },
        },
        {
            "type": "agent_usage",
            "run_id": "run-usage",
            "total": {
                "prompt_tokens": 22,
                "completion_tokens": 5,
                "cache_hit_tokens": 14,
                "cache_miss_tokens": 8,
                "cache_creation_tokens": 3,
                "reasoning_tokens": 2,
            },
        },
    ]
    (tmp_path / "trace-usage.jsonl").write_text(
        "\n".join(json.dumps(item) for item in events) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(harness.client_harness, "TRACE_ROOT", tmp_path)

    evidence = harness.usage_evidence(
        {
            "result": {"run_id": "run-usage"},
            "bound_trace_ids": ["trace-usage"],
        }
    )

    assert evidence == {
        "observed": True,
        "event_count": 2,
        "total": {
            "prompt_tokens": 22,
            "completion_tokens": 5,
            "cache_hit_tokens": 14,
            "cache_miss_tokens": 8,
            "cache_creation_tokens": 3,
            "reasoning_tokens": 2,
        },
        "parse_errors": [],
    }
    assert "private" not in json.dumps(evidence)


def test_connector_probe_projects_raw_canonical_receipt_chain_without_content(
    tmp_path,
):
    harness = _load_harness()
    fixture = harness.build_fixture_environment(tmp_path, token="cne2e_probe")
    run_id = "arp-probe"
    inner = json.dumps(
        {"key": harness.FIXTURE_KEY},
        sort_keys=True,
        separators=(",", ":"),
    )
    outer = json.dumps(
        {"ref": "connector-tool/1:opaque", "arguments_json": inner},
        sort_keys=True,
        separators=(",", ":"),
    )
    arguments_sha256 = harness._sha256_text(inner)
    events = [
        {
            "format": "connector-e2e-probe/1",
            "phase": "raw_model_connector_call",
            "run_id": run_id,
            "tool": "call_connector_tool",
            "raw_arguments_json": outer,
            "raw_arguments_sha256": harness._sha256_text(outer),
        },
        {
            "format": "connector-e2e-probe/1",
            "phase": "canonical_gateway_call",
            "run_id": run_id,
            "canonical_arguments": {"key": harness.FIXTURE_KEY},
            "canonical_arguments_sha256": arguments_sha256,
            "receipt": {
                "format": "connector-receipt/1",
                "arguments_sha256": arguments_sha256,
                "status": "succeeded",
            },
        },
    ]
    fixture.probe_path.write_text(
        "\n".join(json.dumps(event) for event in events) + "\n",
        encoding="utf-8",
    )

    evidence = harness.read_connector_probe_evidence(fixture, run_id=run_id)

    assert evidence["passed"] is True
    assert evidence["raw_call_count"] == 1
    assert evidence["gateway_call_count"] == 1
    assert evidence["repeated_call_count"] == 0
    assert fixture.token not in json.dumps(evidence)


def test_history_projection_and_text_turn_side_effect_oracle(tmp_path, monkeypatch):
    harness = _load_harness()
    fixture = harness.build_fixture_environment(tmp_path, token="cne2e_history")
    run_id = "arp-history"
    goal_sha256 = harness._sha256_text("target")
    fixture.probe_path.write_text(
        json.dumps(
            {
                "format": "connector-e2e-probe/1",
                "phase": "actor_initial_history_projection",
                "run_id": run_id,
                "goal_sha256": goal_sha256,
                "provider_message_count": 3,
                "history_message_count": 2,
                "history_chars": 9,
                "history_roles": ["user", "assistant"],
                "history_projection_sha256": "a" * 64,
                "provider_projection_sha256": "b" * 64,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "trace-history.jsonl").write_text(
        json.dumps({"type": "agent_usage", "run_id": run_id}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(harness.client_harness, "TRACE_ROOT", tmp_path)
    result = {
        "result": {"run_id": run_id},
        "bound_trace_ids": ["trace-history"],
        "gui_execution_trace": {
            "applied_gui_actions": [],
            "forbidden_events": [],
            "parse_errors": [],
        },
        "execution_evidence": {"tool_attempts": []},
    }

    projection = harness.read_history_projection_evidence(
        fixture,
        run_id=run_id,
        task_sha256=goal_sha256,
    )
    text_only = harness.history_text_only_evidence(result, fixture)

    assert projection["passed"] is True
    assert projection["history_message_count"] == 2
    assert projection["history_chars"] == 9
    assert text_only["passed"] is True
    assert text_only["trace_event_counts"] == {
        "desktop_lease_bound": 0,
        "desktop_lease_requested": 0,
        "vnext_gui_observation": 0,
    }

    with fixture.probe_path.open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                {
                    "format": "connector-e2e-probe/1",
                    "phase": "raw_model_connector_call",
                    "run_id": run_id,
                    "tool": "search_connector_tools",
                }
            )
            + "\n"
        )
    assert harness.history_text_only_evidence(result, fixture)["passed"] is False


def test_history_ab_controls_use_observed_fingerprints_and_actual_history():
    harness = _load_harness()
    expected = {
        "task_sha256": "a" * 64,
        "fixture_config_sha256": "b" * 64,
        "fixture_source_sha256": "c" * 64,
        "output_path_sha256": "d" * 64,
        "initial_document_sha256": "e" * 64,
    }

    def arm(*, thread_id, client_pid, turn_count, history_count, text_only):
        return {
            "thread_id": thread_id,
            "client_pid": client_pid,
            "runtime_identity": {"model": "same"},
            "control_fingerprints": dict(expected),
            "history_text_only_pass": text_only,
            "test_cleanup_pass": True,
            "runtime_idle_after_target": {"passed": True},
            "process_cleanup": {"passed": True},
            "failure": "",
            "target": {
                "task_sha256": expected["task_sha256"],
                "turn_count": turn_count,
                "external": {
                    "document_setup": {
                        "mode": "preopened_existing_file",
                        "initial_sha256": expected["initial_document_sha256"],
                        "initial_differs_from_target": True,
                        "precondition_passed": True,
                        "title_matches_document": True,
                    }
                },
                "history_projection": {
                    "passed": True,
                    "history_message_count": history_count,
                },
            },
        }

    fresh = arm(
        thread_id="thread-a",
        client_pid=1,
        turn_count=1,
        history_count=0,
        text_only=True,
    )
    history = arm(
        thread_id="thread-b",
        client_pid=2,
        turn_count=21,
        history_count=12,
        text_only=True,
    )

    controls = harness._history_ab_controls(
        fresh,
        history,
        expected=expected,
        history_turns=20,
    )

    assert all(controls.values())
    history["control_fingerprints"]["fixture_config_sha256"] = "x" * 64
    changed = harness._history_ab_controls(
        fresh,
        history,
        expected=expected,
        history_turns=20,
    )
    assert changed["same_fixture_config"] is False

    history["control_fingerprints"]["fixture_config_sha256"] = expected[
        "fixture_config_sha256"
    ]
    history["target"]["external"]["document_setup"]["initial_sha256"] = "z" * 64
    changed_initial = harness._history_ab_controls(
        fresh,
        history,
        expected=expected,
        history_turns=20,
    )
    assert changed_initial["same_observed_preopened_document"] is False

    history["process_cleanup"]["passed"] = False
    changed_cleanup = harness._history_ab_controls(
        fresh,
        history,
        expected=expected,
        history_turns=20,
    )
    assert changed_cleanup["both_arm_postconditions_passed"] is False


def test_history_ab_cleanup_interference_has_priority_over_arm_outcomes():
    harness = _load_harness()
    fresh = {
        "target": {"external": {"notepad_cleanup": {"passed": True}}}
    }
    history = {
        "target": {"external": {"notepad_cleanup": {"passed": False}}}
    }

    interference = harness._history_ab_cleanup_interference([fresh, history])
    comparison = harness._history_ab_comparison(
        fresh_pass=True,
        history_pass=False,
        cleanup_interference=interference,
        controls_pass=False,
    )

    assert interference is True
    assert comparison == "pair_invalid_test_interference"

    incomplete = harness._history_ab_comparison(
        fresh_pass=True,
        history_pass=False,
        cleanup_interference=False,
        controls_pass=False,
    )
    assert incomplete == "pair_incomplete_before_paired_target"


def test_target_outcome_requires_external_gui_connector_terminal_and_usage():
    harness = _load_harness()
    complete = {
        "passed": True,
        "outcome": "terminal",
        "status": "done",
        "terminal_evidence": {
            "events": ["vnext_canary_done"],
            "parse_errors": [],
        },
        "usage": {"observed": True, "parse_errors": []},
        "window_lifecycle": {
            "before_submit": {
                "visible": True,
                "minimized": False,
                "foreground": True,
            },
            "before_harness_restore": {
                "visible": True,
                "minimized": False,
            },
        },
        "connector_chain": {"passed": True},
        "history_projection": {"passed": True},
        "external": {
            "passed": True,
            "invocation": {
                "passed": True,
                "physical_arguments_sha256": "a" * 64,
            },
            "file": {"exists": True, "modified_after_start": True},
            "gui_input": {"trace_passed": True},
        },
    }

    passed = harness._target_outcome(complete)
    nonterminal = harness._target_outcome({**complete, "status": "running"})

    assert passed == {
        "external_task_pass": True,
        "self_terminal_pass": True,
        "evidence_complete": True,
        "full_pass": True,
    }
    assert nonterminal["external_task_pass"] is True
    assert nonterminal["self_terminal_pass"] is False
    assert nonterminal["full_pass"] is False

    hidden = {
        **complete,
        "window_lifecycle": {
            **complete["window_lifecycle"],
            "before_harness_restore": {"visible": False, "minimized": True},
        },
    }
    assert harness._target_outcome(hidden)["evidence_complete"] is False


def test_harness_sources_are_utf8():
    harness = _load_harness()
    decoded = {
        path: path.read_bytes().decode("utf-8")
        for path in (Path(harness.__file__), harness.FIXTURE_SCRIPT, harness.PROBE_SCRIPT)
    }
    assert "verification" in decoded[Path(harness.__file__)]
    assert "verification" in decoded[harness.FIXTURE_SCRIPT]
    assert "sitecustomize" in decoded[harness.PROBE_SCRIPT]


def test_managed_launch_refuses_to_replace_preexisting_workspace_client(
    monkeypatch,
    tmp_path,
):
    harness = _load_harness()
    fixture = harness.build_fixture_environment(tmp_path / "fixture")
    monkeypatch.setattr(harness, "_workspace_electron_pids", lambda: [101])

    try:
        harness.launch_managed_client(tmp_path / "managed", fixture)
    except RuntimeError as error:
        assert "pre-existing workspace Electron" in str(error)
    else:  # pragma: no cover - fail closed on accidental process replacement
        raise AssertionError("managed launch unexpectedly replaced a client")
