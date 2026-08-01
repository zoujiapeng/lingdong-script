import json

import pytest


def test_thread_history_contains_only_successful_user_facing_turns():
    from winpilot.orchestrator.agent_threads import AgentThreadStore
    from winpilot.utils.log import BUS

    store = AgentThreadStore()
    try:
        thread_id, first_turn, history = store.begin_turn("Remember ALPHA.")
        assert history == []
        store.bind_run(
            thread_id=thread_id,
            turn_id=first_turn,
            run_id="run-first",
        )
        store.on_event({
            "type": "arp_run_done",
            "run_id": "run-first",
            "status": "reply",
            "user_result": "ALPHA",
        })

        same_thread, second_turn, history = store.begin_turn(
            "What did I ask you to remember?",
            thread_id=thread_id,
        )
        assert same_thread == thread_id
        assert history == [
            {"role": "user", "content": "Remember ALPHA."},
            {"role": "assistant", "content": "ALPHA"},
        ]
        store.bind_run(
            thread_id=thread_id,
            turn_id=second_turn,
            run_id="run-second",
        )
        store.on_event({
            "type": "arp_run_done",
            "run_id": "run-second",
            "status": "failed",
            "result": "internal failure detail",
        })

        _, _, history = store.begin_turn(
            "Continue.",
            thread_id=thread_id,
        )
        assert history == [
            {"role": "user", "content": "Remember ALPHA."},
            {"role": "assistant", "content": "ALPHA"},
        ]
    finally:
        BUS.remove_sink(store.on_event)


def test_thread_capacity_never_evicts_an_active_turn(monkeypatch):
    from winpilot.orchestrator import agent_threads
    from winpilot.utils.log import BUS

    monkeypatch.setattr(agent_threads, "_MAX_THREADS", 2)
    store = agent_threads.AgentThreadStore()
    try:
        first_thread, _, _ = store.begin_turn("first active turn")
        second_thread, _, _ = store.begin_turn("second active turn")

        with pytest.raises(
            agent_threads.AgentThreadError,
            match="too many active threads",
        ):
            store.begin_turn("third active turn")

        assert store.snapshot(first_thread)["thread_id"] == first_thread
        assert store.snapshot(second_thread)["thread_id"] == second_thread
    finally:
        BUS.remove_sink(store.on_event)


def test_thread_continuation_checkpoint_round_trips_exact_history(tmp_path):
    from winpilot.orchestrator.agent_threads import AgentThreadStore
    from winpilot.utils.log import BUS

    checkpoint = tmp_path / "thread-continuation.json"
    original = AgentThreadStore(checkpoint_path=checkpoint)
    restored = AgentThreadStore(checkpoint_path=checkpoint)
    try:
        thread_id, turn_id, _ = original.begin_turn("Remember BRAVO.")
        original.bind_run(
            thread_id=thread_id,
            turn_id=turn_id,
            run_id="run-bravo",
        )
        original.on_event({
            "type": "arp_run_done",
            "run_id": "run-bravo",
            "status": "reply",
            "user_result": "BRAVO",
        })

        assert restored.load_checkpoint() == 1
        assert restored.completed_run("run-bravo") == {
            "thread_id": thread_id,
            "turn_id": turn_id,
            "run_id": "run-bravo",
            "goal": "Remember BRAVO.",
            "status": "reply",
            "result": "BRAVO",
            "created_at": pytest.approx(
                original.snapshot(thread_id)["turns"][0]["created_at"]
            ),
            "updated_at": pytest.approx(
                original.snapshot(thread_id)["turns"][0]["updated_at"]
            ),
        }
        same_thread, _, history = restored.begin_turn(
            "What was the marker?",
            thread_id=thread_id,
        )
        assert same_thread == thread_id
        assert history == [
            {"role": "user", "content": "Remember BRAVO."},
            {"role": "assistant", "content": "BRAVO"},
        ]
    finally:
        BUS.remove_sink(original.on_event)
        BUS.remove_sink(restored.on_event)


def test_thread_checkpoint_keeps_only_the_bounded_model_history(tmp_path):
    from winpilot.orchestrator.agent_threads import AgentThreadStore
    from winpilot.utils.log import BUS

    checkpoint = tmp_path / "thread-continuation.json"
    original = AgentThreadStore(checkpoint_path=checkpoint)
    restored = AgentThreadStore(checkpoint_path=checkpoint)
    try:
        thread_id = ""
        for index in range(8):
            thread_id, turn_id, _ = original.begin_turn(
                f"question-{index}",
                thread_id=thread_id,
            )
            original.bind_run(
                thread_id=thread_id,
                turn_id=turn_id,
                run_id=f"run-{index}",
            )
            original.on_event({
                "type": "arp_run_done",
                "run_id": f"run-{index}",
                "status": "done",
                "user_result": f"answer-{index}",
            })

        assert restored.load_checkpoint() == 1
        snapshot = restored.snapshot(thread_id)
        assert [turn["run_id"] for turn in snapshot["turns"]] == [
            f"run-{index}" for index in range(2, 8)
        ]
        _, _, history = restored.begin_turn("continue", thread_id=thread_id)
        assert len(history) == 12
        assert history[0]["content"] == "question-2"
        assert history[-1]["content"] == "answer-7"
    finally:
        BUS.remove_sink(original.on_event)
        BUS.remove_sink(restored.on_event)


def test_invalid_thread_checkpoint_fails_closed_without_partial_load(tmp_path):
    from winpilot.orchestrator.agent_threads import (
        AgentThreadError,
        AgentThreadStore,
    )
    from winpilot.utils.log import BUS

    checkpoint = tmp_path / "thread-continuation.json"
    checkpoint.write_text(
        '{"format":"agent-thread-continuation/1","threads":['
        '{"thread_id":"duplicate","created_at":1,"updated_at":1,"turns":[]},'
        '{"thread_id":"duplicate","created_at":1,"updated_at":1,"turns":[]}'
        ']}',
        encoding="utf-8",
    )
    store = AgentThreadStore(checkpoint_path=checkpoint)
    try:
        with pytest.raises(AgentThreadError, match="duplicate checkpoint thread_id"):
            store.load_checkpoint()
        thread_id, _, history = store.begin_turn("fresh after rejection")
        assert thread_id.startswith("thread-")
        assert history == []
    finally:
        BUS.remove_sink(store.on_event)


def test_checkpoint_write_failure_never_blocks_conversation(monkeypatch, tmp_path):
    from winpilot.orchestrator.agent_threads import AgentThreadStore
    from winpilot.utils.log import BUS

    store = AgentThreadStore(checkpoint_path=tmp_path / "continuation.json")

    def fail_checkpoint_write():
        raise PermissionError("checkpoint unavailable")

    monkeypatch.setattr(store, "_save_checkpoint_locked", fail_checkpoint_write)
    try:
        thread_id, turn_id, history = store.begin_turn("Remember CHARLIE.")
        assert history == []
        store.bind_run(
            thread_id=thread_id,
            turn_id=turn_id,
            run_id="run-charlie",
        )
        store.on_event({
            "type": "arp_run_done",
            "run_id": "run-charlie",
            "status": "reply",
            "user_result": "CHARLIE",
        })

        _, _, history = store.begin_turn("Continue.", thread_id=thread_id)
        assert history == [
            {"role": "user", "content": "Remember CHARLIE."},
            {"role": "assistant", "content": "CHARLIE"},
        ]
    finally:
        BUS.remove_sink(store.on_event)


def test_checkpoint_omits_threads_without_successful_history(tmp_path):
    from winpilot.orchestrator.agent_threads import AgentThreadStore
    from winpilot.utils.log import BUS

    checkpoint = tmp_path / "continuation.json"
    store = AgentThreadStore(checkpoint_path=checkpoint)
    try:
        failed_thread, failed_turn, _ = store.begin_turn("failed")
        store.bind_run(
            thread_id=failed_thread,
            turn_id=failed_turn,
            run_id="run-failed",
        )
        store.on_event({
            "type": "arp_run_done",
            "run_id": "run-failed",
            "status": "failed",
        })

        kept_thread, kept_turn, _ = store.begin_turn("kept")
        store.bind_run(
            thread_id=kept_thread,
            turn_id=kept_turn,
            run_id="run-kept",
        )
        store.on_event({
            "type": "arp_run_done",
            "run_id": "run-kept",
            "status": "done",
            "user_result": "visible result",
        })

        payload = json.loads(checkpoint.read_text(encoding="utf-8"))
        assert [item["thread_id"] for item in payload["threads"]] == [kept_thread]
    finally:
        BUS.remove_sink(store.on_event)


def test_checkpoint_with_non_string_status_fails_closed(tmp_path):
    from winpilot.orchestrator.agent_threads import (
        AgentThreadError,
        AgentThreadStore,
    )
    from winpilot.utils.log import BUS

    checkpoint = tmp_path / "continuation.json"
    checkpoint.write_text(
        json.dumps({
            "format": "agent-thread-continuation/1",
            "threads": [{
                "thread_id": "thread-invalid",
                "created_at": 1,
                "updated_at": 1,
                "turns": [{
                    "turn_id": "turn-invalid",
                    "run_id": "run-invalid",
                    "goal": "goal",
                    "status": [],
                    "result": "result",
                    "created_at": 1,
                    "updated_at": 1,
                }],
            }],
        }),
        encoding="utf-8",
    )
    store = AgentThreadStore(checkpoint_path=checkpoint)
    try:
        with pytest.raises(AgentThreadError, match="invalid or duplicate"):
            store.load_checkpoint()
    finally:
        BUS.remove_sink(store.on_event)


def test_restored_result_projection_is_a_consistent_terminal(monkeypatch):
    from winpilot import server

    monkeypatch.setattr(
        server.AGENT_THREADS,
        "completed_run",
        lambda run_id: {
            "goal": "remembered goal",
            "status": "reply",
            "result": "remembered result",
            "updated_at": 42.0,
        } if run_id == "run-restored" else None,
    )
    original = {
        "run_id": "run-restored",
        "status": "interrupted",
        "phase": "executing",
        "manual_paused": True,
        "current_node_id": "full-goal",
        "current_tool": "desktop.click",
        "error": "service stopped",
    }

    restored = server._thread_result_projection(original)

    assert restored == {
        "run_id": "run-restored",
        "goal": "remembered goal",
        "status": "reply",
        "result": "remembered result",
        "phase": "terminal",
        "manual_paused": False,
        "current_node_id": None,
        "current_tool": None,
        "error": "",
        "updated_at": 42.0,
        "finished_at": 42.0,
    }
    assert original["status"] == "interrupted"
