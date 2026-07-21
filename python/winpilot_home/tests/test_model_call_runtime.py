"""Unified model-request retry and mutation replay contracts."""
from __future__ import annotations

import threading

import pytest

from winpilot.action.replay_policy import (
    MutationOutcome,
    ReplayDisposition,
    decide_action_replay,
)
from winpilot.modeling import (
    FollowupKind,
    InvalidModelRequest,
    ModelCallError,
    ModelCallRuntime,
    ModelRequestEnvelope,
    ModelRetryPolicy,
    classify_followup,
    create_openai_client,
)


def _request(**overrides):
    value = {
        "model": "vision-model",
        "messages": [{"role": "user", "content": "data:image/jpeg;base64,AAAA"}],
        "tools": [],
        "temperature": 0.0,
    }
    value.update(overrides)
    return value


def _envelope(decision_id: str = "decision-1", **overrides):
    return ModelRequestEnvelope.bind(
        decision_id=decision_id,
        model_role="visual_actor",
        purpose="next_semantic_action",
        provider_request=_request(**overrides),
    )


def test_envelope_freezes_request_without_aliasing_mutable_containers() -> None:
    request = _request()
    envelope = ModelRequestEnvelope.bind(
        decision_id="decision-1",
        model_role="visual_actor",
        purpose="next_semantic_action",
        provider_request=request,
    )
    request["messages"][0]["content"] = "changed"

    first = envelope.provider_request()
    first["messages"][0]["content"] = "transport mutation"
    second = envelope.provider_request()

    assert second["messages"][0]["content"].endswith("AAAA")
    assert envelope.request_fingerprint == _envelope().request_fingerprint


def test_envelope_fingerprint_is_key_order_stable_and_value_sensitive() -> None:
    left = ModelRequestEnvelope.bind(
        decision_id="d",
        model_role="task_profiler",
        purpose="profile",
        provider_request={"z": 1, "a": [True, None, 1.5]},
    )
    right = ModelRequestEnvelope.bind(
        decision_id="d",
        model_role="task_profiler",
        purpose="profile",
        provider_request={"a": [True, None, 1.5], "z": 1},
    )
    changed = ModelRequestEnvelope.bind(
        decision_id="d",
        model_role="task_profiler",
        purpose="profile",
        provider_request={"a": [True, None, 1.6], "z": 1},
    )
    assert left.request_fingerprint == right.request_fingerprint
    assert left.request_fingerprint != changed.request_fingerprint


@pytest.mark.parametrize("bad", (float("nan"), float("inf"), object(), {1: "value"}))
def test_envelope_rejects_non_json_values(bad) -> None:
    with pytest.raises(InvalidModelRequest):
        _envelope(extra=bad)


def test_runtime_retries_only_the_exact_frozen_request() -> None:
    received = []
    received_content = []

    def send_once(request):
        received.append(request)
        received_content.append(request["messages"][0]["content"])
        request["messages"][0]["content"] = "transport mutated its copy"
        if len(received) == 1:
            raise TimeoutError("temporary timeout")
        return "ok"

    outcome = ModelCallRuntime(sleeper=lambda _delay: None).execute(
        _envelope(),
        send_once,
        policy=ModelRetryPolicy(max_attempts=2, initial_backoff_s=0),
    )

    assert outcome.response == "ok"
    assert outcome.request_retry_count == 1
    assert received_content == [
        "data:image/jpeg;base64,AAAA",
        "data:image/jpeg;base64,AAAA",
    ]
    assert {item.request_fingerprint for item in outcome.attempts} == {
        _envelope().request_fingerprint
    }


@pytest.mark.parametrize("status", (429, 500, 503))
def test_runtime_retries_transient_http_statuses(status) -> None:
    class ProviderError(Exception):
        status_code = status

    calls = 0

    def send_once(_request):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ProviderError("transient")
        return "ok"

    outcome = ModelCallRuntime(sleeper=lambda _delay: None).execute(
        _envelope(), send_once,
        policy=ModelRetryPolicy(max_attempts=2, initial_backoff_s=0),
    )
    assert outcome.response == "ok"
    assert calls == 2


@pytest.mark.parametrize("status", (400, 401, 403, 404, 422))
def test_runtime_does_not_retry_non_transient_http_statuses(status) -> None:
    class ProviderError(Exception):
        status_code = status

    calls = 0

    def send_once(_request):
        nonlocal calls
        calls += 1
        raise ProviderError("invalid request")

    with pytest.raises(ModelCallError) as caught:
        ModelCallRuntime(sleeper=lambda _delay: None).execute(
            _envelope(), send_once,
            policy=ModelRetryPolicy(max_attempts=3, initial_backoff_s=0),
        )
    assert calls == 1
    assert caught.value.request_retry_count == 0


def test_runtime_honors_cancellation_before_first_send() -> None:
    stopped = threading.Event()
    stopped.set()
    with pytest.raises(ModelCallError, match="canceled"):
        ModelCallRuntime().execute(
            _envelope(), lambda _request: pytest.fail("must not send"),
            stop_event=stopped,
        )


def test_observer_failure_never_resends_successful_request() -> None:
    calls = 0

    def send_once(_request):
        nonlocal calls
        calls += 1
        return "ok"

    outcome = ModelCallRuntime().execute(
        _envelope(),
        send_once,
        on_attempt=lambda _attempt: (_ for _ in ()).throw(RuntimeError("observer broke")),
    )
    assert outcome.response == "ok"
    assert calls == 1


def test_error_message_words_do_not_make_program_errors_retryable() -> None:
    calls = 0

    def send_once(_request):
        nonlocal calls
        calls += 1
        raise ValueError("connection schema timeout is invalid")

    with pytest.raises(ModelCallError) as caught:
        ModelCallRuntime().execute(
            _envelope(), send_once,
            policy=ModelRetryPolicy(max_attempts=3, initial_backoff_s=0),
        )
    assert calls == 1
    assert caught.value.attempts[0].retryable is False


def test_followup_contract_separates_retry_poll_and_redecision() -> None:
    original = _envelope()
    same = _envelope()
    changed = _envelope(temperature=0.5)
    new_decision = _envelope("decision-2", temperature=0.5)

    assert classify_followup(original, same, FollowupKind.REQUEST_RETRY) is FollowupKind.REQUEST_RETRY
    assert classify_followup(original, None, FollowupKind.POLL) is FollowupKind.POLL
    assert classify_followup(original, new_decision, FollowupKind.REDECISION) is FollowupKind.REDECISION
    with pytest.raises(InvalidModelRequest):
        classify_followup(original, changed, FollowupKind.REQUEST_RETRY)
    with pytest.raises(InvalidModelRequest):
        classify_followup(original, same, FollowupKind.REDECISION)
    with pytest.raises(InvalidModelRequest):
        classify_followup(original, same, FollowupKind.POLL)
    with pytest.raises(InvalidModelRequest):
        classify_followup(original, same, FollowupKind.ACTION_REPLAY)


def test_openai_client_factory_disables_sdk_retries() -> None:
    captured = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return object()

    create_openai_client(
        base_url="https://provider.invalid/v1",
        api_key="secret",
        timeout=17,
        client_factory=factory,
    )
    assert captured["max_retries"] == 0
    assert captured["timeout"] == 17


@pytest.mark.parametrize(
    ("outcome", "allowed", "disposition"),
    (
        (MutationOutcome.NOT_STARTED, False, ReplayDisposition.NEW_DECISION),
        (MutationOutcome.NOT_APPLIED, False, ReplayDisposition.REOBSERVE),
        (MutationOutcome.APPLIED, False, ReplayDisposition.REOBSERVE),
        (MutationOutcome.OUTCOME_UNKNOWN, False, ReplayDisposition.RECONCILE_OR_BLOCK),
    ),
)
def test_mutations_are_not_replayed_without_explicit_safe_evidence(
    outcome, allowed, disposition
) -> None:
    decision = decide_action_replay(
        outcome, explicitly_idempotent=True, replay_authorized=False
    )
    assert decision.allowed is allowed
    assert decision.disposition is disposition


def test_only_explicit_idempotent_deterministically_not_applied_action_can_replay() -> None:
    decision = decide_action_replay(
        MutationOutcome.NOT_APPLIED,
        explicitly_idempotent=True,
        replay_authorized=True,
    )
    assert decision.allowed is True
    assert decision.disposition is ReplayDisposition.REPLAY_EXPLICIT_IDEMPOTENT
    for outcome in (MutationOutcome.APPLIED, MutationOutcome.OUTCOME_UNKNOWN):
        assert decide_action_replay(
            outcome, explicitly_idempotent=True, replay_authorized=True
        ).allowed is False


@pytest.mark.parametrize("truthy", (1, "true", [True]))
def test_replay_authority_requires_literal_booleans(truthy) -> None:
    assert decide_action_replay(
        MutationOutcome.NOT_APPLIED,
        explicitly_idempotent=truthy,
        replay_authorized=True,
    ).allowed is False
    assert decide_action_replay(
        MutationOutcome.NOT_APPLIED,
        explicitly_idempotent=True,
        replay_authorized=truthy,
    ).allowed is False


def test_malformed_mutation_outcome_fails_closed() -> None:
    decision = decide_action_replay(
        "provider_says_maybe",
        explicitly_idempotent=True,
        replay_authorized=True,
    )
    assert decision.allowed is False
    assert decision.disposition is ReplayDisposition.RECONCILE_OR_BLOCK
