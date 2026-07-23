"""Pure, fail-closed contracts for one existing-window activation attempt."""
from __future__ import annotations

import hashlib
import json
import math
import random

import pytest

from winpilot.process_identity import process_identity_for
from winpilot.vnext.actor import ACTOR_PROPOSAL_FORMAT, DesktopActionProposal
from winpilot.vnext.capabilities import PointerLeaseState
from winpilot.vnext.runtime.target_activation import (
    ACTIVATED_TARGET_IDENTITY_FORMAT,
    MAX_ACTIVATED_TARGET_TITLE_CHARS,
    TARGET_ACTIVATION_ENVELOPE_FORMAT,
    TARGET_ACTIVATION_TRANSITION_FORMAT,
    ActivatedTargetIdentity,
    InvalidTargetActivationEnvelope,
    InvalidTargetActivationTransition,
    TargetActivationEnvelope,
    TargetActivationOutcome,
    TargetActivationTransition,
)
from winpilot.vnext.targeting import (
    TargetCandidate,
    candidate_fingerprint_for,
    candidate_ref_for,
    launch_identity_for,
)
from winpilot.visual_runtime.commit_admission import FrameCommitIdentity


def _process_identity(pid: int = 41, *, created: int = 100) -> str:
    return process_identity_for(
        process_id=pid,
        executable_path=fr"C:\Apps\host-{pid}.exe",
        creation_time_ns=created,
    )


def _candidate(
    *,
    hwnd: int = 901,
    process_id: int = 41,
    created: int = 100,
    state: str = "visible",
) -> TargetCandidate:
    identity = _process_identity(process_id, created=created)
    return TargetCandidate(
        candidate_ref=candidate_ref_for(
            activation="switch_window",
            identity=f"{hwnd}|{process_id}|{identity}",
        ),
        activation="switch_window",
        state=state,
        label="Visible workspace",
        provenance=("visible_windows",),
        window_title="Workspace",
        hwnd=hwnd,
        process_id=process_id,
        process_identity=identity,
    )


def _proposal(**overrides: object) -> DesktopActionProposal:
    values: dict[str, object] = {
        "format": ACTOR_PROPOSAL_FORMAT,
        "kind": "desktop_action",
        "action": "activate_target",
        "args": {"candidate_ordinal": 1},
        "local_intent": "bring the selected visible target to the foreground",
    }
    values.update(overrides)
    return DesktopActionProposal.model_validate(values)


def _frame(**overrides: object) -> FrameCommitIdentity:
    values: dict[str, object] = {
        "frame_id": 82,
        "epoch": 0,
        "hwnd": 1,
        "region": (0, 0, 1920, 1080),
        "scope": "full_desktop",
        "submitted_size": (1280, 720),
        "image_sha256": "a" * 64,
    }
    values.update(overrides)
    return FrameCommitIdentity(**values)


def _envelope(
    *,
    candidate: TargetCandidate | None = None,
    **overrides: object,
) -> TargetActivationEnvelope:
    selected = candidate or _candidate()
    values: dict[str, object] = {
        "format": TARGET_ACTIVATION_ENVELOPE_FORMAT,
        "decision_id": "decision-activation-1",
        "mission_id": "mission-1",
        "run_id": "run-1",
        "mission_revision": 4,
        "obligation_id": "obligation-1",
        "frame": _frame(),
        "pointer": PointerLeaseState(),
        "broker_projection_id": "projection-1",
        "capability_grant_ids": ("grant-2", "grant-1"),
        "allowed_evidence_scope": ("evidence-frame-1", "evidence-target-1"),
        "candidate_fingerprint": candidate_fingerprint_for((selected,)),
        "proposal": _proposal(),
        "candidate": selected,
    }
    values.update(overrides)
    return TargetActivationEnvelope(**values)


def _activated(**overrides: object) -> ActivatedTargetIdentity:
    values: dict[str, object] = {
        "format": ACTIVATED_TARGET_IDENTITY_FORMAT,
        "hwnd": 901,
        "root_hwnd": 901,
        "process_id": 41,
        "process_identity": _process_identity(),
        "window_region": (10, 20, 800, 600),
        "title_snapshot": "Workspace",
    }
    values.update(overrides)
    return ActivatedTargetIdentity(**values)


def _transition(**overrides: object) -> TargetActivationTransition:
    values: dict[str, object] = {
        "format": TARGET_ACTIVATION_TRANSITION_FORMAT,
        "outcome": "completed",
        "action_id": "decision-activation-1",
        "action": "activate_target",
        "candidate_ref": _candidate().candidate_ref,
        "mutation_completed": True,
        "activated_target": _activated(),
        "post_frame_id": 83,
        "evidence_refs": ("evidence-foreground-1", "evidence-frame-2"),
        "failure_reason": "",
    }
    values.update(overrides)
    return TargetActivationTransition(**values)


def test_activation_envelope_is_versioned_strict_and_deterministic() -> None:
    envelope = _envelope()
    raw = envelope.to_dict()

    assert raw["format"] == "target-activation-envelope/0"
    assert raw["proposal"]["action"] == "activate_target"
    assert raw["proposal"]["args"] == {"candidate_ordinal": 1}
    assert raw["candidate"]["hwnd"] == 901
    assert raw["capability_grant_ids"] == ["grant-1", "grant-2"]
    assert TargetActivationEnvelope.from_dict(raw) == envelope
    assert json.loads(envelope.to_json()) == raw
    assert envelope.to_json() == TargetActivationEnvelope.from_dict(
        dict(reversed(tuple(raw.items())))
    ).to_json()


@pytest.mark.parametrize(
    "rewrite",
    (
        lambda raw: {key: value for key, value in raw.items() if key != "run_id"},
        lambda raw: {**raw, "extra": True},
        lambda raw: {**raw, "format": "target-activation-envelope/1"},
        lambda raw: {**raw, "mission_revision": True},
        lambda raw: {**raw, "mission_revision": -1},
        lambda raw: {**raw, "candidate_fingerprint": "target-candidates-bad"},
    ),
)
def test_activation_envelope_rejects_malformed_or_old_wire_data(rewrite) -> None:
    with pytest.raises(InvalidTargetActivationEnvelope):
        TargetActivationEnvelope.from_dict(rewrite(_envelope().to_dict()))


@pytest.mark.parametrize(
    "field,value",
    (
        ("frame", _frame()),
        ("pointer", PointerLeaseState()),
        ("proposal", _proposal()),
        ("candidate", _candidate()),
        ("capability_grant_ids", ("grant-1",)),
        ("allowed_evidence_scope", ("evidence-1",)),
    ),
)
def test_activation_envelope_wire_parser_rejects_python_only_values(
    field: str,
    value: object,
) -> None:
    raw = _envelope().to_dict()
    raw[field] = value
    with pytest.raises(InvalidTargetActivationEnvelope, match="non-JSON"):
        TargetActivationEnvelope.from_dict(raw)


def test_activation_envelope_wire_parser_rejects_nonfinite_and_cyclic_json() -> None:
    nonfinite = _envelope().to_dict()
    nonfinite["proposal"]["args"]["candidate_ordinal"] = math.nan
    with pytest.raises(InvalidTargetActivationEnvelope, match="non-JSON number"):
        TargetActivationEnvelope.from_dict(nonfinite)

    cyclic = _envelope().to_dict()
    cyclic["proposal"]["args"]["cycle"] = cyclic
    with pytest.raises(InvalidTargetActivationEnvelope, match="cyclic"):
        TargetActivationEnvelope.from_dict(cyclic)


@pytest.mark.parametrize(
    "frame",
    (
        _frame(scope="target_window"),
        _frame(epoch=1),
    ),
)
def test_activation_envelope_requires_epoch_zero_full_desktop_authority(
    frame: FrameCommitIdentity,
) -> None:
    with pytest.raises(ValueError):
        _envelope(frame=frame)


def test_activation_envelope_requires_released_pointer_authority() -> None:
    with pytest.raises(ValueError):
        _envelope(
            pointer=PointerLeaseState(
                disposition="held",
                lease_id="pointer-lease-1",
                physically_owned=True,
            ),
        )


@pytest.mark.parametrize(
    "proposal",
    (
        _proposal(action="click", args={"candidate_ordinal": 1}),
        _proposal(args={"candidate_ordinal": 1, "unexpected": "invented"}),
        _proposal(args={"candidate_ordinal": True}),
        _proposal(args={"candidate_ordinal": 0}),
        _proposal(args={"candidate_ordinal": 17}),
    ),
)
def test_activation_envelope_accepts_only_one_ordinal_activation_proposal(
    proposal: DesktopActionProposal,
) -> None:
    with pytest.raises(ValueError):
        _envelope(proposal=proposal)


def test_activation_envelope_fails_closed_for_open_app_candidates() -> None:
    launch_identity = launch_identity_for(
        kind="exe", target=r"C:\Apps\Workspace.exe",
    )
    candidate = TargetCandidate(
        candidate_ref=candidate_ref_for(
            activation="open_app", identity=launch_identity,
        ),
        activation="open_app",
        state="installed",
        label="Workspace",
        provenance=("installed_catalog",),
        launch_kind="exe",
        launch_target=r"C:\Apps\Workspace.exe",
    )
    with pytest.raises(ValueError, match="existing-window"):
        _envelope(candidate=candidate)


def test_activation_envelope_rejects_weak_private_candidate_wire_identity() -> None:
    raw = _envelope().to_dict()
    raw["candidate"]["process_identity"] = "process-invented"
    with pytest.raises(InvalidTargetActivationEnvelope):
        TargetActivationEnvelope.from_dict(raw)


def test_private_candidate_identity_changes_the_transaction_fingerprint() -> None:
    first = _envelope(candidate=_candidate(hwnd=901, process_id=41, created=100))
    second_candidate = _candidate(hwnd=901, process_id=41, created=101)
    second = _envelope(
        candidate=second_candidate,
        candidate_fingerprint=candidate_fingerprint_for((second_candidate,)),
    )

    assert first.candidate.candidate_ref != second.candidate.candidate_ref
    assert first.candidate_fingerprint != second.candidate_fingerprint
    assert hashlib.sha256(first.to_json().encode()).digest() != hashlib.sha256(
        second.to_json().encode()
    ).digest()


def test_random_wire_key_order_replays_to_one_canonical_envelope() -> None:
    raw = _envelope().to_dict()
    expected = _envelope().to_json()
    rng = random.Random(8128)
    for _ in range(50):
        shuffled = list(raw.items())
        rng.shuffle(shuffled)
        assert TargetActivationEnvelope.from_dict(dict(shuffled)).to_json() == expected


def test_activated_target_identity_is_strict_versioned_and_round_trips() -> None:
    identity = _activated()
    raw = identity.to_dict()
    assert raw["format"] == "activated-target-identity/0"
    assert raw["window_region"] == [10, 20, 800, 600]
    assert ActivatedTargetIdentity.from_dict(raw) == identity

    for rewrite in (
        lambda value: {**value, "format": "activated-target-identity/1"},
        lambda value: {**value, "hwnd": True},
        lambda value: {**value, "root_hwnd": 0},
        lambda value: {**value, "process_id": math.nan},
        lambda value: {**value, "process_identity": "process-invented"},
        lambda value: {**value, "window_region": [0, 0, 0, 1]},
        lambda value: {
            **value,
            "title_snapshot": "x" * (MAX_ACTIVATED_TARGET_TITLE_CHARS + 1),
        },
        lambda value: {**value, "extra": "forbidden"},
    ):
        with pytest.raises(ValueError):
            ActivatedTargetIdentity.from_dict(rewrite(raw))


@pytest.mark.parametrize("mutation_completed", (False, True))
def test_completed_transition_supports_zero_or_applied_mutation_with_post_proof(
    mutation_completed: bool,
) -> None:
    transition = _transition(mutation_completed=mutation_completed)
    assert transition.outcome is TargetActivationOutcome.COMPLETED
    assert transition.mutation_completed is mutation_completed
    assert TargetActivationTransition.from_dict(
        transition.to_dict()
    ) == transition


def test_rejected_and_unknown_transitions_encode_distinct_replay_semantics() -> None:
    rejected = _transition(
        outcome="rejected",
        mutation_completed=False,
        activated_target=None,
        post_frame_id=None,
        evidence_refs=(),
        failure_reason="candidate identity changed before the primitive",
    )
    unknown = _transition(
        outcome="unknown",
        mutation_completed=None,
        activated_target=None,
        post_frame_id=83,
        evidence_refs=("evidence-frame-2",),
        failure_reason="the primitive may have executed but foreground proof failed",
    )

    assert rejected.outcome is TargetActivationOutcome.REJECTED
    assert unknown.outcome is TargetActivationOutcome.UNKNOWN
    assert json.loads(rejected.to_json()) == rejected.to_dict()
    assert TargetActivationTransition.from_dict(unknown.to_dict()) == unknown


@pytest.mark.parametrize(
    "overrides",
    (
        {"outcome": "completed", "mutation_completed": None},
        {"outcome": "completed", "activated_target": None},
        {"outcome": "completed", "post_frame_id": None},
        {"outcome": "completed", "evidence_refs": ()},
        {"outcome": "completed", "failure_reason": "not actually complete"},
        {"outcome": "rejected", "mutation_completed": True, "activated_target": None,
         "post_frame_id": None, "evidence_refs": (), "failure_reason": "failed"},
        {"outcome": "rejected", "activated_target": _activated(),
         "post_frame_id": None, "evidence_refs": (), "failure_reason": "failed"},
        {"outcome": "rejected", "failure_reason": ""},
        {"outcome": "unknown", "mutation_completed": False, "activated_target": None,
         "post_frame_id": None, "evidence_refs": (), "failure_reason": "uncertain"},
        {"outcome": "unknown", "activated_target": _activated(),
         "mutation_completed": None, "failure_reason": "uncertain"},
        {"outcome": "unknown", "mutation_completed": None, "failure_reason": ""},
        {"outcome": "unknown", "mutation_completed": None,
         "post_frame_id": None, "evidence_refs": ("evidence-without-frame",),
         "failure_reason": "uncertain"},
        {"action": "click"},
        {"post_frame_id": True},
    ),
)
def test_activation_transition_rejects_invalid_three_state_combinations(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        _transition(**overrides)


@pytest.mark.parametrize(
    "rewrite",
    (
        lambda raw: {key: value for key, value in raw.items() if key != "action_id"},
        lambda raw: {**raw, "extra": True},
        lambda raw: {**raw, "format": "target-activation-transition/1"},
        lambda raw: {**raw, "outcome": "maybe"},
        lambda raw: {**raw, "post_frame_id": math.nan},
    ),
)
def test_activation_transition_rejects_malformed_or_old_wire_data(rewrite) -> None:
    with pytest.raises(InvalidTargetActivationTransition):
        TargetActivationTransition.from_dict(rewrite(_transition().to_dict()))


@pytest.mark.parametrize("outcome", (b"completed", 1, True))
def test_activation_transition_rejects_coerced_non_string_outcome(
    outcome: object,
) -> None:
    with pytest.raises(ValueError, match="outcome must be a string"):
        _transition(outcome=outcome)
