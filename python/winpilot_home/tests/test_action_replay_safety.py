"""Mutation verification may poll, but must not blindly replay input."""
from __future__ import annotations

from winpilot.action import verify


def _no_change(*_args, **_kwargs):
    return verify.VerifyOutcome(
        verified=False,
        detail="no authoritative postcondition",
        method="test",
    )


def test_action_exception_after_possible_input_is_never_replayed(monkeypatch) -> None:
    calls = 0

    def action():
        nonlocal calls
        calls += 1
        raise RuntimeError("SendInput may already have run")

    monkeypatch.setattr(verify, "post_state_summary", lambda: {})
    result = verify.run_with_verify(
        "click",
        action,
        expect={"screen_stable": True},
        retryable=True,
    )
    assert calls == 1
    assert result["attempts"] == 1
    assert result["mutation_outcome"] == "outcome_unknown"
    assert result["action_replay_suppressed"] is True


def test_failed_postcondition_does_not_repeat_successful_mutation(monkeypatch) -> None:
    calls = 0

    def action():
        nonlocal calls
        calls += 1
        return {"ok": True}

    monkeypatch.setattr(verify, "check", _no_change)
    monkeypatch.setattr(verify, "post_state_summary", lambda: {})
    result = verify.run_with_verify(
        "draw_strokes",
        action,
        expect={"screen_stable": True},
        retryable=True,
    )
    assert calls == 1
    assert result["verified"] is False
    assert result["mutation_outcome"] == "applied"
    assert result["action_replay_suppressed"] is True


def test_untrusted_not_applied_payload_does_not_grant_legacy_replay(monkeypatch) -> None:
    calls = 0

    def action():
        nonlocal calls
        calls += 1
        return {
            "ok": False,
            "mutation_outcome": "not_applied",
            "action_idempotent": True,
            "action_replay_authorized": True,
        }

    monkeypatch.setattr(verify, "check", _no_change)
    monkeypatch.setattr(verify, "post_state_summary", lambda: {})
    result = verify.run_with_verify(
        "click",
        action,
        expect={"screen_stable": True},
        retryable=True,
    )
    assert calls == 1
    assert result["attempts"] == 1
    assert result["action_replay_suppressed"] is True
