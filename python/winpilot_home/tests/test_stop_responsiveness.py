"""Stop responsiveness: verify polling and the LLM call both honor stop_event."""
import threading
import time

from winpilot.action import verify


def test_check_aborts_on_stop_event(monkeypatch):
    # Avoid real perception: make each eval cheap + non-verified.
    monkeypatch.setattr(verify, "_eval_once",
                        lambda *a, **k: verify.VerifyOutcome(False, "no", ""))
    ev = threading.Event()
    ev.set()
    t0 = time.monotonic()
    out = verify.check({"text_appears": "x"}, timeout_s=5.0, stop_event=ev)
    assert out.verified is False
    assert time.monotonic() - t0 < 1.0, "should return immediately when stopped, not wait the 5s timeout"


def test_check_without_stop_runs_to_timeout(monkeypatch):
    monkeypatch.setattr(verify, "_eval_once",
                        lambda *a, **k: verify.VerifyOutcome(False, "no", ""))
    t0 = time.monotonic()
    verify.check({"text_appears": "x"}, timeout_s=1.0)  # no stop_event
    assert time.monotonic() - t0 >= 0.9, "without stop it should poll until timeout"


def test_chat_abandons_inflight_call_on_stop():
    from winpilot.agent.loop import AgentRunner
    ev = threading.Event()
    runner = AgentRunner("打开记事本输入你好", stop_event=ev)  # non-coding task
    runner.messages = [{"role": "user", "content": "hi"}]

    class SlowClient:
        class chat:
            class completions:
                @staticmethod
                def create(**_kw):
                    time.sleep(5)
                    return "LATE"  # should be abandoned, never used

    threading.Timer(0.2, ev.set).start()
    t0 = time.monotonic()
    out = runner._chat(SlowClient(), [])
    assert out is None, "stop during the LLM call must abandon it (return None)"
    assert time.monotonic() - t0 < 2.0, "should not wait for the slow call to finish"
