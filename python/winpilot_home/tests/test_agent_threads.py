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
