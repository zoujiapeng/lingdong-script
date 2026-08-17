from dataclasses import FrozenInstanceError, replace
import threading
from types import SimpleNamespace

import pytest

from winpilot.visual_runtime.frame_authority import FrameAuthority


def _commit(authority: FrameAuthority, *, epoch: int = 3, payload: str = "jpeg"):
    return authority.commit(
        epoch=epoch,
        hwnd=41,
        region=(10, 20, 800, 600),
        scope="target_window",
        reason="verified_target",
        submitted_size=(800, 600),
        image_b64=payload,
        captured_at=100.0,
        expected_previous_frame_id=(
            authority.current.frame_id if authority.current is not None else 0
        ),
    )


def test_frame_authority_commits_one_immutable_current_state():
    authority = FrameAuthority()

    first = _commit(authority)
    second = _commit(authority, epoch=4, payload="new-jpeg")

    assert authority.current is second
    assert first.frame_id == 1
    assert second.frame_id == 2
    assert first.image_sha256 != second.image_sha256
    assert authority.matches(2, 4) is True
    assert authority.matches(1, 3) is False
    assert second.is_fresh_for(epoch=4, max_age_s=1.0, now=100.5) is True
    assert second.is_fresh_for(epoch=3) is False
    with pytest.raises(FrozenInstanceError):
        second.epoch = 9


def test_frame_authority_rejects_lost_update():
    authority = FrameAuthority()
    _commit(authority)

    with pytest.raises(RuntimeError, match="changed before commit"):
        authority.commit(
            epoch=3,
            hwnd=41,
            region=(10, 20, 800, 600),
            scope="target_window",
            reason="verified_target",
            submitted_size=(800, 600),
            image_b64="stale",
            captured_at=101.0,
            expected_previous_frame_id=0,
        )


def test_run_if_current_pins_the_complete_identity_and_calls_once():
    authority = FrameAuthority()
    current = _commit(authority)
    calls = []

    def commit_input():
        calls.append(authority.current)
        return {"input_completed": True}

    admitted, result = authority.run_if_current(current, commit_input)

    assert admitted is True
    assert result == {"input_completed": True}
    assert calls == [current]


@pytest.mark.parametrize(
    ("field", "stale_value"),
    [
        ("frame_id", 2),
        ("epoch", 4),
        ("hwnd", 42),
        ("region", (11, 20, 800, 600)),
        ("scope", "virtual_screen"),
        ("submitted_size", (799, 600)),
        ("image_sha256", "0" * 64),
    ],
)
def test_run_if_current_rejects_each_stale_identity_field_without_calling(
    field,
    stale_value,
):
    authority = FrameAuthority()
    current = _commit(authority)
    stale = replace(current, **{field: stale_value})
    calls = []

    assert authority.run_if_current(stale, lambda: calls.append("called")) == (
        False,
        None,
    )
    assert calls == []


def test_run_if_current_rejects_when_no_frame_is_current():
    authority = FrameAuthority()
    expected = _commit(FrameAuthority())
    calls = []

    assert authority.run_if_current(expected, lambda: calls.append("called")) == (
        False,
        None,
    )
    assert calls == []


def test_run_if_current_propagates_callback_exception_and_releases_pin():
    authority = FrameAuthority()
    current = _commit(authority)
    failure = LookupError("raw input outcome is unknown")

    def fail_after_commit_started():
        raise failure

    with pytest.raises(LookupError, match="outcome is unknown") as caught:
        authority.run_if_current(current, fail_after_commit_started)

    assert caught.value is failure
    assert authority.run_if_current(current, lambda: "recovered") == (
        True,
        "recovered",
    )


def test_run_if_current_blocks_other_thread_frame_commit_until_callback_returns():
    authority = FrameAuthority()
    current = _commit(authority)
    callback_entered = threading.Event()
    release_callback = threading.Event()
    pin_finished = threading.Event()
    commit_started = threading.Event()
    commit_finished = threading.Event()
    results = []

    def pinned_callback():
        callback_entered.set()
        assert release_callback.wait(timeout=2.0)
        return "input committed"

    def pin_worker():
        try:
            results.append(authority.run_if_current(current, pinned_callback))
        finally:
            pin_finished.set()

    def commit_worker():
        commit_started.set()
        try:
            authority.commit(
                epoch=4,
                hwnd=41,
                region=(10, 20, 800, 600),
                scope="target_window",
                reason="post_input",
                submitted_size=(800, 600),
                image_b64="new-jpeg",
                captured_at=101.0,
                expected_previous_frame_id=current.frame_id,
            )
        finally:
            commit_finished.set()

    pin_thread = threading.Thread(target=pin_worker)
    commit_thread = threading.Thread(target=commit_worker)
    pin_thread.start()
    assert callback_entered.wait(timeout=2.0)
    commit_thread.start()
    assert commit_started.wait(timeout=2.0)

    assert commit_finished.wait(timeout=0.05) is False
    release_callback.set()
    assert pin_finished.wait(timeout=2.0)
    assert commit_finished.wait(timeout=2.0)
    pin_thread.join(timeout=2.0)
    commit_thread.join(timeout=2.0)

    assert not pin_thread.is_alive()
    assert not commit_thread.is_alive()
    assert results == [(True, "input committed")]
    assert authority.current is not current


def test_run_if_current_fails_fast_on_same_thread_reentrant_frame_commit():
    authority = FrameAuthority()
    current = _commit(authority)

    with pytest.raises(RuntimeError, match="from its pinned callback"):
        authority.run_if_current(
            current,
            lambda: _commit(authority, epoch=4, payload="reentrant"),
        )

    assert authority.current is current
    assert authority.run_if_current(current, lambda: "still-current") == (
        True,
        "still-current",
    )


def test_run_if_current_fails_fast_on_same_thread_nested_pin():
    authority = FrameAuthority()
    current = _commit(authority)
    nested_calls = []

    def nested_pin():
        return authority.run_if_current(
            current,
            lambda: nested_calls.append("nested callback"),
        )

    with pytest.raises(RuntimeError, match="recursively enter"):
        authority.run_if_current(current, nested_pin)

    assert nested_calls == []
    assert authority.run_if_current(current, lambda: "pin released") == (
        True,
        "pin released",
    )
