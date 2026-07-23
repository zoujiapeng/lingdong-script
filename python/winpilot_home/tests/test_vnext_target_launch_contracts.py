"""Pure, fail-closed contracts for one installed-target launch attempt."""
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
    ActivatedTargetIdentity,
)
from winpilot.vnext.runtime.target_launch import (
    TARGET_LAUNCH_ENVELOPE_FORMAT,
    TARGET_LAUNCH_TRANSITION_FORMAT,
    InvalidTargetLaunchEnvelope,
    InvalidTargetLaunchTransition,
    TargetLaunchEnvelope,
    TargetLaunchOutcome,
    TargetLaunchTransition,
)
from winpilot.vnext.targeting import (
    TargetCandidate,
    candidate_fingerprint_for,
    candidate_ref_for,
    launch_identity_for,
)
from winpilot.visual_runtime.commit_admission import FrameCommitIdentity


def _process_identity(pid: int = 51, *, created: int = 200) -> str:
    return process_identity_for(
        process_id=pid,
        executable_path=fr"C:\Apps\launched-{pid}.exe",
        creation_time_ns=created,
    )


def _candidate(
    *,
    kind: str = "exe",
    target: str = r"C:\Apps\Workspace.exe",
    label: str = "Installed workspace",
) -> TargetCandidate:
    launch_identity = launch_identity_for(kind=kind, target=target)
    return TargetCandidate(
        candidate_ref=candidate_ref_for(
            activation="open_app",
            identity=launch_identity,
        ),
        activation="open_app",
        state="installed",
        label=label,
        provenance=("installed_catalog",),
        launch_kind=kind,
        launch_target=target,
    )


def _proposal(**overrides: object) -> DesktopActionProposal:
    values: dict[str, object] = {
        "format": ACTOR_PROPOSAL_FORMAT,
        "kind": "desktop_action",
        "action": "activate_target",
        "args": {"candidate_ordinal": 1},
        "local_intent": "activate the selected installed target",
    }
    values.update(overrides)
    return DesktopActionProposal.model_validate(values)


def _frame(**overrides: object) -> FrameCommitIdentity:
    values: dict[str, object] = {
        "frame_id": 92,
        "epoch": 0,
        "hwnd": 1,
        "region": (0, 0, 1920, 1080),
        "scope": "full_desktop",
        "submitted_size": (1280, 720),
        "image_sha256": "c" * 64,
    }
    values.update(overrides)
    return FrameCommitIdentity(**values)


def _envelope(
    *,
    candidate: TargetCandidate | None = None,
    **overrides: object,
) -> TargetLaunchEnvelope:
    selected = candidate or _candidate()
    values: dict[str, object] = {
        "format": TARGET_LAUNCH_ENVELOPE_FORMAT,
        "decision_id": "decision-launch-1",
        "mission_id": "mission-1",
        "run_id": "run-1",
        "mission_revision": 5,
        "obligation_id": "obligation-1",
        "frame": _frame(),
        "pointer": PointerLeaseState(),
        "broker_projection_id": "projection-launch-1",
        "capability_grant_ids": ("grant-2", "grant-1"),
        "allowed_evidence_scope": (
            "evidence-frame-1",
            "evidence-target-1",
        ),
        "candidate_fingerprint": candidate_fingerprint_for((selected,)),
        "proposal": _proposal(),
        "candidate": selected,
    }
    values.update(overrides)
    return TargetLaunchEnvelope(**values)


def _activated(**overrides: object) -> ActivatedTargetIdentity:
    values: dict[str, object] = {
        "format": ACTIVATED_TARGET_IDENTITY_FORMAT,
        "hwnd": 951,
        "root_hwnd": 951,
        "process_id": 51,
        "process_identity": _process_identity(),
        "window_region": (20, 30, 900, 650),
        "title_snapshot": "Workspace",
    }
    values.update(overrides)
    return ActivatedTargetIdentity(**values)


def _transition(**overrides: object) -> TargetLaunchTransition:
    values: dict[str, object] = {
        "format": TARGET_LAUNCH_TRANSITION_FORMAT,
        "outcome": "completed",
        "action_id": "decision-launch-1",
        "action": "activate_target",
        "candidate_ref": _candidate().candidate_ref,
        "launch_request_count": 1,
        "launch_process_id": 51,
        "launch_process_identity": _process_identity(),
        "activated_target": _activated(),
        "post_frame_id": 93,
        "evidence_refs": (
            "evidence-launch-causality-1",
            "evidence-post-frame-1",
        ),
        "failure_reason": "",
    }
    values.update(overrides)
    return TargetLaunchTransition(**values)


def test_launch_envelope_is_versioned_strict_and_deterministic() -> None:
    envelope = _envelope()
    raw = envelope.to_dict()

    assert raw["format"] == "target-launch-envelope/0"
    assert raw["proposal"]["action"] == "activate_target"
    assert raw["proposal"]["args"] == {"candidate_ordinal": 1}
    assert raw["candidate"]["activation"] == "open_app"
    assert raw["candidate"]["launch_target"] == r"C:\Apps\Workspace.exe"
    assert raw["capability_grant_ids"] == ["grant-1", "grant-2"]
    assert TargetLaunchEnvelope.from_dict(raw) == envelope
    assert json.loads(envelope.to_json()) == raw
    assert envelope.to_json() == TargetLaunchEnvelope.from_dict(
        dict(reversed(tuple(raw.items())))
    ).to_json()


@pytest.mark.parametrize(
    "rewrite",
    (
        lambda raw: {
            key: value for key, value in raw.items() if key != "run_id"
        },
        lambda raw: {**raw, "extra": True},
        lambda raw: {**raw, "format": "target-launch-envelope/1"},
        lambda raw: {**raw, "mission_revision": True},
        lambda raw: {**raw, "mission_revision": -1},
        lambda raw: {
            **raw,
            "candidate_fingerprint": "target-candidates-bad",
        },
    ),
)
def test_launch_envelope_rejects_malformed_or_old_wire_data(rewrite) -> None:
    with pytest.raises(InvalidTargetLaunchEnvelope):
        TargetLaunchEnvelope.from_dict(rewrite(_envelope().to_dict()))


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
def test_launch_envelope_wire_parser_rejects_python_only_values(
    field: str,
    value: object,
) -> None:
    raw = _envelope().to_dict()
    raw[field] = value
    with pytest.raises(InvalidTargetLaunchEnvelope, match="non-JSON"):
        TargetLaunchEnvelope.from_dict(raw)


def test_launch_envelope_wire_parser_rejects_nonfinite_and_cyclic_json() -> None:
    nonfinite = _envelope().to_dict()
    nonfinite["proposal"]["args"]["candidate_ordinal"] = math.inf
    with pytest.raises(InvalidTargetLaunchEnvelope, match="non-JSON number"):
        TargetLaunchEnvelope.from_dict(nonfinite)

    cyclic = _envelope().to_dict()
    cyclic["proposal"]["args"]["cycle"] = cyclic
    with pytest.raises(InvalidTargetLaunchEnvelope, match="cyclic"):
        TargetLaunchEnvelope.from_dict(cyclic)


@pytest.mark.parametrize(
    "frame",
    (
        _frame(scope="target_window"),
        _frame(epoch=1),
    ),
)
def test_launch_envelope_requires_epoch_zero_full_desktop_authority(
    frame: FrameCommitIdentity,
) -> None:
    with pytest.raises(ValueError):
        _envelope(frame=frame)


def test_launch_envelope_requires_released_pointer_authority() -> None:
    with pytest.raises(ValueError):
        _envelope(pointer=PointerLeaseState(
            disposition="held",
            lease_id="pointer-lease-1",
            physically_owned=True,
        ))


@pytest.mark.parametrize(
    "proposal",
    (
        _proposal(action="click", args={"candidate_ordinal": 1}),
        _proposal(args={"candidate_ordinal": 1, "extra": "invented"}),
        _proposal(args={"candidate_ordinal": True}),
        _proposal(args={"candidate_ordinal": 0}),
        _proposal(args={"candidate_ordinal": 17}),
    ),
)
def test_launch_envelope_accepts_only_one_ordinal_activation_proposal(
    proposal: DesktopActionProposal,
) -> None:
    with pytest.raises(ValueError):
        _envelope(proposal=proposal)


def test_launch_envelope_accepts_only_installed_open_app_candidate() -> None:
    identity = _process_identity()
    visible = TargetCandidate(
        candidate_ref=candidate_ref_for(
            activation="switch_window",
            identity=f"951|51|{identity}",
        ),
        activation="switch_window",
        state="visible",
        label="Visible workspace",
        provenance=("visible_windows",),
        window_title="Workspace",
        hwnd=951,
        process_id=51,
        process_identity=identity,
    )
    with pytest.raises(ValueError, match="installed open-app"):
        _envelope(candidate=visible)

    raw = _envelope().to_dict()
    raw["candidate"]["state"] = "visible"
    with pytest.raises(InvalidTargetLaunchEnvelope):
        TargetLaunchEnvelope.from_dict(raw)


def test_launch_envelope_rejects_weak_private_candidate_wire_identity() -> None:
    raw = _envelope().to_dict()
    raw["candidate"]["candidate_ref"] = "target-invented"
    with pytest.raises(InvalidTargetLaunchEnvelope):
        TargetLaunchEnvelope.from_dict(raw)


def test_private_launch_identity_changes_transaction_fingerprint() -> None:
    first_candidate = _candidate(target=r"C:\Apps\Workspace.exe")
    second_candidate = _candidate(target=r"C:\Apps\Workspace-2.exe")
    first = _envelope(
        candidate=first_candidate,
        candidate_fingerprint=candidate_fingerprint_for((first_candidate,)),
    )
    second = _envelope(
        candidate=second_candidate,
        candidate_fingerprint=candidate_fingerprint_for((second_candidate,)),
    )

    assert first.candidate.candidate_ref != second.candidate.candidate_ref
    assert first.candidate_fingerprint != second.candidate_fingerprint
    assert hashlib.sha256(first.to_json().encode()).digest() != hashlib.sha256(
        second.to_json().encode()
    ).digest()


def test_random_launch_candidates_replay_to_canonical_envelopes() -> None:
    rng = random.Random(9173)
    canonical: set[str] = set()
    for index in range(50):
        candidate = _candidate(
            target=fr"C:\Apps\Random-{index:02d}.exe",
            label=f"Random {rng.randrange(10_000)}",
        )
        envelope = _envelope(
            candidate=candidate,
            candidate_fingerprint=candidate_fingerprint_for((candidate,)),
        )
        raw = envelope.to_dict()
        shuffled = list(raw.items())
        rng.shuffle(shuffled)
        replayed = TargetLaunchEnvelope.from_dict(dict(shuffled))
        assert replayed == envelope
        assert replayed.to_json() == envelope.to_json()
        canonical.add(envelope.to_json())
    assert len(canonical) == 50


def test_completed_transition_has_one_request_and_exact_process_causality() -> None:
    transition = _transition()

    assert transition.outcome is TargetLaunchOutcome.COMPLETED
    assert transition.launch_request_count == 1
    assert transition.launch_process_id == transition.activated_target.process_id
    assert (
        transition.launch_process_identity
        == transition.activated_target.process_identity
    )
    assert TargetLaunchTransition.from_dict(
        transition.to_dict()
    ) == transition
    assert json.loads(transition.to_json()) == transition.to_dict()


def test_rejected_and_unknown_transitions_have_distinct_replay_semantics() -> None:
    rejected = _transition(
        outcome="rejected",
        launch_request_count=0,
        launch_process_id=None,
        launch_process_identity=None,
        activated_target=None,
        post_frame_id=None,
        evidence_refs=(),
        failure_reason="authority changed before the launch primitive",
    )
    unknown = _transition(
        outcome="unknown",
        launch_request_count=1,
        launch_process_id=None,
        launch_process_identity=None,
        activated_target=None,
        post_frame_id=93,
        evidence_refs=("evidence-post-frame-1",),
        failure_reason="launch returned without reliable process identity",
    )

    assert rejected.outcome is TargetLaunchOutcome.REJECTED
    assert unknown.outcome is TargetLaunchOutcome.UNKNOWN
    assert rejected.launch_request_count == 0
    assert unknown.launch_request_count == 1
    assert TargetLaunchTransition.from_dict(rejected.to_dict()) == rejected
    assert TargetLaunchTransition.from_dict(unknown.to_dict()) == unknown


@pytest.mark.parametrize(
    "overrides",
    (
        {"outcome": "completed", "launch_request_count": 0},
        {"outcome": "completed", "launch_request_count": None},
        {"outcome": "completed", "launch_process_id": None},
        {"outcome": "completed", "launch_process_identity": None},
        {"outcome": "completed", "activated_target": None},
        {"outcome": "completed", "post_frame_id": None},
        {"outcome": "completed", "evidence_refs": ()},
        {"outcome": "completed", "failure_reason": "not complete"},
        {
            "outcome": "completed",
            "launch_process_id": 52,
        },
        {
            "outcome": "completed",
            "launch_process_identity": _process_identity(created=201),
        },
        {
            "outcome": "rejected",
            "launch_request_count": 1,
            "launch_process_id": None,
            "launch_process_identity": None,
            "activated_target": None,
            "post_frame_id": None,
            "evidence_refs": (),
            "failure_reason": "failed",
        },
        {
            "outcome": "rejected",
            "launch_request_count": 0,
            "launch_process_id": 51,
            "launch_process_identity": None,
            "activated_target": None,
            "post_frame_id": None,
            "evidence_refs": (),
            "failure_reason": "failed",
        },
        {
            "outcome": "rejected",
            "launch_request_count": 0,
            "launch_process_id": None,
            "launch_process_identity": None,
            "activated_target": _activated(),
            "post_frame_id": None,
            "evidence_refs": (),
            "failure_reason": "failed",
        },
        {
            "outcome": "rejected",
            "launch_request_count": 0,
            "launch_process_id": None,
            "launch_process_identity": None,
            "activated_target": None,
            "post_frame_id": None,
            "evidence_refs": (),
            "failure_reason": "",
        },
        {
            "outcome": "unknown",
            "launch_request_count": 0,
            "launch_process_id": None,
            "launch_process_identity": None,
            "activated_target": None,
            "post_frame_id": None,
            "evidence_refs": (),
            "failure_reason": "uncertain",
        },
        {
            "outcome": "unknown",
            "launch_request_count": 1,
            "launch_process_id": 51,
            "launch_process_identity": None,
            "activated_target": None,
            "failure_reason": "uncertain",
        },
        {
            "outcome": "unknown",
            "launch_request_count": None,
            "launch_process_id": None,
            "launch_process_identity": None,
            "activated_target": _activated(),
            "failure_reason": "uncertain",
        },
        {
            "outcome": "unknown",
            "launch_request_count": None,
            "launch_process_id": None,
            "launch_process_identity": None,
            "activated_target": None,
            "failure_reason": "",
        },
        {
            "outcome": "unknown",
            "launch_request_count": 1,
            "launch_process_id": None,
            "launch_process_identity": None,
            "activated_target": None,
            "post_frame_id": None,
            "evidence_refs": ("evidence-without-frame",),
            "failure_reason": "uncertain",
        },
        {"action": "click"},
        {"launch_request_count": True},
        {"post_frame_id": True},
    ),
)
def test_launch_transition_rejects_invalid_three_state_combinations(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        _transition(**overrides)


@pytest.mark.parametrize(
    "rewrite",
    (
        lambda raw: {
            key: value for key, value in raw.items() if key != "action_id"
        },
        lambda raw: {
            key: value for key, value in raw.items() if key != "action"
        },
        lambda raw: {
            key: value for key, value in raw.items() if key != "failure_reason"
        },
        lambda raw: {**raw, "extra": True},
        lambda raw: {**raw, "format": "target-launch-transition/1"},
        lambda raw: {**raw, "outcome": "maybe"},
        lambda raw: {**raw, "post_frame_id": math.nan},
        lambda raw: {**raw, "launch_request_count": 2},
    ),
)
def test_launch_transition_rejects_malformed_or_old_wire_data(rewrite) -> None:
    with pytest.raises(InvalidTargetLaunchTransition):
        TargetLaunchTransition.from_dict(rewrite(_transition().to_dict()))


@pytest.mark.parametrize("outcome", (b"completed", 1, True))
def test_launch_transition_rejects_coerced_non_string_outcome(
    outcome: object,
) -> None:
    with pytest.raises(ValueError, match="outcome must be a string"):
        _transition(outcome=outcome)


def test_launch_transition_wire_parser_rejects_python_and_nonfinite_values() -> None:
    python_value = _transition().to_dict()
    python_value["activated_target"] = _activated()
    with pytest.raises(InvalidTargetLaunchTransition, match="non-JSON"):
        TargetLaunchTransition.from_dict(python_value)

    nonfinite = _transition().to_dict()
    nonfinite["post_frame_id"] = math.nan
    with pytest.raises(InvalidTargetLaunchTransition, match="non-JSON number"):
        TargetLaunchTransition.from_dict(nonfinite)
