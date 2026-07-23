from __future__ import annotations

from collections.abc import Callable

import pytest

from winpilot.process_identity import process_identity_for
from winpilot.vnext.actor import ActorDecisionEnvelope, parse_actor_proposal
from winpilot.vnext.capabilities import PointerDisposition, PointerLeaseState
from winpilot.vnext.runtime.mutation_attempt import call_once_with_permit
from winpilot.vnext.runtime.mutation_permits import (
    MutationEnvelope,
    MutationPermitAuthority,
    MutationPermitError,
)
from winpilot.vnext.runtime.target_activation import TargetActivationEnvelope
from winpilot.vnext.targeting import (
    TargetCandidate,
    candidate_fingerprint_for,
    candidate_ref_for,
)
from winpilot.visual_runtime.commit_admission import (
    FrameCommitIdentity,
    TargetCommitIdentity,
)


def _actor_envelope(decision_id: str = "decision-actor") -> ActorDecisionEnvelope:
    return ActorDecisionEnvelope(
        decision_id=decision_id,
        mission_id="mission-1",
        run_id="run-1",
        mission_revision=1,
        obligation_id="obligation-1",
        frame=FrameCommitIdentity(
            2, 1, 101, (0, 0, 800, 600), "target_window",
            (800, 600), "a" * 64,
        ),
        target=TargetCommitIdentity(
            "target-1", 101, 101, 7, "process-" + "1" * 32, 1,
            (0, 0, 800, 600),
        ),
        pointer=PointerLeaseState(PointerDisposition.RELEASED),
        broker_projection_id="projection-1",
        capability_grant_ids=(),
        allowed_evidence_scope=("frame-proof-2",),
        proposal=parse_actor_proposal({
            "format": "actor-proposal/1",
            "kind": "desktop_action",
            "action": "click",
            "args": {"image_x": 10, "image_y": 20},
            "local_intent": "select the visible control",
        }),
    )


def _activation_envelope(
    decision_id: str = "decision-activation",
) -> TargetActivationEnvelope:
    process_identity = process_identity_for(
        process_id=41,
        executable_path=r"C:\Apps\host.exe",
        creation_time_ns=100,
    )
    candidate = TargetCandidate(
        candidate_ref=candidate_ref_for(
            activation="switch_window",
            identity=f"901|41|{process_identity}",
        ),
        activation="switch_window",
        state="visible",
        label="Visible workspace",
        provenance=("visible_windows",),
        window_title="Workspace",
        hwnd=901,
        process_id=41,
        process_identity=process_identity,
    )
    return TargetActivationEnvelope(
        decision_id=decision_id,
        mission_id="mission-1",
        run_id="run-1",
        mission_revision=1,
        obligation_id="obligation-1",
        frame=FrameCommitIdentity(
            3, 0, 1, (0, 0, 1920, 1080), "full_desktop",
            (1280, 720), "b" * 64,
        ),
        pointer=PointerLeaseState(PointerDisposition.RELEASED),
        broker_projection_id="projection-2",
        capability_grant_ids=(),
        allowed_evidence_scope=("frame-proof-3",),
        candidate_fingerprint=candidate_fingerprint_for((candidate,)),
        proposal=parse_actor_proposal({
            "format": "actor-proposal/1",
            "kind": "desktop_action",
            "action": "activate_target",
            "args": {"candidate_ordinal": 1},
            "local_intent": "activate the visible workspace",
        }),
        candidate=candidate,
    )


@pytest.mark.parametrize("factory", (_actor_envelope, _activation_envelope))
def test_attempt_supports_current_envelopes_and_closes_active_admission(
    factory: Callable[[], MutationEnvelope],
) -> None:
    authority = MutationPermitAuthority("mission-1", "run-1")
    envelope = factory()
    calls = 0

    def execute(submitted: MutationEnvelope) -> object:
        nonlocal calls
        calls += 1
        assert authority.begin_admission(submitted).allowed is True
        return {"mechanical": True}

    result = call_once_with_permit(
        authority=authority,
        envelope=envelope,
        turn_index=0,
        still_current=lambda: True,
        execute=execute,
    )

    assert calls == 1
    assert result.execute_returned is True
    assert result.transition == {"mechanical": True}
    assert result.controller_error is None
    assert result.pending_permit_revoked is False
    assert result.active_admission_closed is True
    assert authority.pending_count == authority.active_count == 0


@pytest.mark.parametrize("consume_mode", ("none", "consume", "begin", "finish"))
def test_attempt_cleanup_facts_cover_every_permit_disposition(
    consume_mode: str,
) -> None:
    authority = MutationPermitAuthority("mission-1", "run-1")
    envelope = _actor_envelope(f"decision-{consume_mode}")

    def execute(submitted: MutationEnvelope) -> str:
        if consume_mode == "consume":
            assert authority.consume(submitted).allowed
        elif consume_mode in {"begin", "finish"}:
            assert authority.begin_admission(submitted).allowed
            if consume_mode == "finish":
                assert authority.finish_admission(submitted.decision_id)
        return consume_mode

    result = call_once_with_permit(
        authority=authority,
        envelope=envelope,
        turn_index=0,
        still_current=lambda: True,
        execute=execute,
    )

    assert result.transition == consume_mode
    assert result.pending_permit_revoked is (consume_mode == "none")
    assert result.active_admission_closed is (consume_mode == "begin")
    assert authority.pending_count == authority.active_count == 0


def test_controller_exception_is_returned_after_active_cleanup() -> None:
    authority = MutationPermitAuthority("mission-1", "run-1")
    envelope = _activation_envelope()
    failure = RuntimeError("controller failed")

    def execute(submitted: MutationEnvelope) -> object:
        assert authority.begin_admission(submitted).allowed
        raise failure

    result = call_once_with_permit(
        authority=authority,
        envelope=envelope,
        turn_index=0,
        still_current=lambda: True,
        execute=execute,
    )

    assert result.execute_returned is False
    assert result.transition is None
    assert result.controller_error is failure
    assert result.pending_permit_revoked is False
    assert result.active_admission_closed is True
    assert authority.pending_count == authority.active_count == 0


def test_controller_exception_before_consumption_revokes_pending_permit() -> None:
    authority = MutationPermitAuthority("mission-1", "run-1")
    failure = RuntimeError("controller failed before admission")

    def execute(_submitted: MutationEnvelope) -> object:
        raise failure

    result = call_once_with_permit(
        authority=authority,
        envelope=_actor_envelope(),
        turn_index=0,
        still_current=lambda: True,
        execute=execute,
    )

    assert result.controller_error is failure
    assert result.pending_permit_revoked is True
    assert result.active_admission_closed is False
    assert authority.pending_count == authority.active_count == 0


def test_base_exception_propagates_only_after_cleanup() -> None:
    class Abort(BaseException):
        pass

    authority = MutationPermitAuthority("mission-1", "run-1")
    envelope = _actor_envelope()

    def execute(submitted: MutationEnvelope) -> object:
        assert authority.begin_admission(submitted).allowed
        raise Abort()

    with pytest.raises(Abort):
        call_once_with_permit(
            authority=authority,
            envelope=envelope,
            turn_index=0,
            still_current=lambda: True,
            execute=execute,
        )
    assert authority.pending_count == authority.active_count == 0


def test_attempt_calls_issue_execute_revoke_finish_exactly_once_in_order() -> None:
    events: list[str] = []

    class SpyAuthority(MutationPermitAuthority):
        def issue(self, *args, **kwargs):
            events.append("issue")
            return super().issue(*args, **kwargs)

        def revoke(self, *args, **kwargs):
            events.append("revoke")
            return super().revoke(*args, **kwargs)

        def finish_admission(self, *args, **kwargs):
            events.append("finish")
            return super().finish_admission(*args, **kwargs)

    authority = SpyAuthority("mission-1", "run-1")

    def execute(_submitted: MutationEnvelope) -> str:
        events.append("execute")
        return "returned"

    result = call_once_with_permit(
        authority=authority,
        envelope=_actor_envelope(),
        turn_index=0,
        still_current=lambda: True,
        execute=execute,
    )

    assert result.transition == "returned"
    assert events == ["issue", "execute", "revoke", "finish"]


def test_issue_failure_never_calls_controller_or_changes_existing_permit() -> None:
    authority = MutationPermitAuthority("mission-1", "run-1")
    envelope = _actor_envelope()
    authority.issue(envelope, turn_index=0, still_current=lambda: True)
    calls = 0

    def execute(_submitted: MutationEnvelope) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(MutationPermitError):
        call_once_with_permit(
            authority=authority,
            envelope=envelope,
            turn_index=1,
            still_current=lambda: True,
            execute=execute,
        )
    assert calls == 0
    assert authority.pending_count == 1


@pytest.mark.parametrize("index", range(24))
def test_attempt_never_retries_for_varied_controller_results(index: int) -> None:
    authority = MutationPermitAuthority("mission-1", "run-1")
    envelope = _actor_envelope(f"decision-property-{index}")
    calls = 0

    def execute(submitted: MutationEnvelope) -> int:
        nonlocal calls
        calls += 1
        if index % 3 == 0:
            assert authority.consume(submitted).allowed
        return index

    result = call_once_with_permit(
        authority=authority,
        envelope=envelope,
        turn_index=index,
        still_current=lambda: True,
        execute=execute,
    )

    assert calls == 1
    assert result.transition == index
    assert authority.pending_count == authority.active_count == 0
