from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from winpilot.agent.frame_authority import FrameAuthority
from winpilot.agent.loop import AgentRunner


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


def test_runner_and_session_share_the_same_frame_authority():
    runner = AgentRunner("perform the visible task")

    assert runner.session._frame_authority is runner._frame_authority
    assert runner._structured_grounding_is_current() is False


def test_spatial_grounding_rejects_a_superseded_frame_in_the_same_epoch(
    monkeypatch,
):
    runner = AgentRunner("perform the visible task")
    runner.session.hwnd = 41
    runner.session._screen_epoch = 3
    region = SimpleNamespace(left=10, top=20, width=800, height=600)
    monkeypatch.setattr(
        "winpilot.agent.loop.select_visual_capture_scope",
        lambda *_args, **_kwargs: SimpleNamespace(
            region=region,
            scope="target_window",
        ),
    )

    submitted = _commit(runner._frame_authority)
    runner._mirror_authoritative_frame(submitted)
    assert runner._has_fresh_visual_grounding() is True

    # A newer capture can be committed within the same screen epoch.  Until
    # its pixels are mirrored/submitted to the model, the older frame must no
    # longer authorize a spatial action.
    _commit(runner._frame_authority, payload="newer-jpeg")

    assert runner._has_fresh_visual_grounding() is False
