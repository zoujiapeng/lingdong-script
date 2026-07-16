from __future__ import annotations

import pytest


def region(bounds, *, label="surface"):
    from winpilot.perception.surface_binding import InteractionRegion

    return InteractionRegion(
        bounds,
        coordinate_space="frame_0_1000",
        label=label,
        source="test_pixels",
        confidence=0.9,
    )


def update(binding, candidate, *, frame_id, hwnd=91, initial=False, client=None):
    return binding.update(
        candidate,
        hwnd=hwnd,
        frame_region=(480, 154, 1008, 720),
        capture_scope="target_window",
        client_region=client,
        frame_id=frame_id,
        initial=initial,
    )


def test_surface_binding_retains_outer_geometry_when_content_splits_detector():
    from winpilot.perception.surface_binding import SurfaceBinding

    binding = SurfaceBinding()
    outer = region((120, 300, 960, 930))
    first = update(binding, outer, frame_id=1, initial=True)
    # A detector that mistakes the largest blank side for the whole surface
    # reports a three-edge-connected subset. The coordinate contract remains
    # the original outer boundary.
    blank_side = region((500, 300, 960, 930))
    second = update(binding, blank_side, frame_id=2)

    assert first.ok is True
    assert second.ok is True
    assert second.region == outer
    assert second.retained_anchor is True
    assert second.policy == "stable_binding_retained_after_component_shrink"


def test_surface_binding_uses_client_region_as_authority_envelope():
    from winpilot.perception.surface_binding import SurfaceBinding

    binding = SurfaceBinding()
    client = region((40, 120, 980, 980), label="window_client")
    spilling_candidate = region((20, 100, 990, 990))

    result = update(
        binding,
        spilling_candidate,
        frame_id=1,
        initial=True,
        client=client,
    )

    assert result.ok is True
    assert result.region is not None
    assert result.region.bounds == client.bounds
    assert binding.anchor == result.region


def test_surface_binding_rejects_stale_frame_and_ambiguous_current_pixels():
    from winpilot.perception.surface_binding import SurfaceBinding

    binding = SurfaceBinding()
    update(binding, region((100, 200, 900, 900)), frame_id=8, initial=True)

    stale = update(binding, region((100, 200, 900, 900)), frame_id=8)
    ambiguous = binding.update(
        region((100, 200, 900, 900)),
        hwnd=91,
        frame_region=(480, 154, 1008, 720),
        capture_scope="target_window",
        frame_id=9,
        ambiguous=True,
    )

    assert stale.ok is False
    assert stale.policy == "stale_authoritative_frame"
    assert ambiguous.ok is False
    assert ambiguous.policy == "ambiguous_current_surface_fail_closed"
    # Fail-closed observations do not destroy the last trusted binding.
    assert binding.anchor is not None
    assert binding.anchor.bounds == (100, 200, 900, 900)


def test_surface_binding_requires_repeated_layout_displacement():
    from winpilot.perception.surface_binding import SurfaceBinding

    binding = SurfaceBinding(displacement_confirmations=2)
    update(binding, region((120, 300, 960, 930)), frame_id=1, initial=True)
    replacement = region((60, 220, 900, 880))

    pending = update(binding, replacement, frame_id=2)
    rebound = update(binding, replacement, frame_id=3)

    assert pending.ok is False
    assert pending.policy == "layout_displacement_pending_confirmation"
    assert rebound.ok is True
    assert rebound.region == replacement
    assert rebound.policy == "rebound_after_confirmed_layout_displacement"
    assert rebound.generation == 2


def test_explicit_action_roi_is_scoped_without_rebinding_surface():
    from winpilot.perception.surface_binding import SurfaceBinding

    binding = SurfaceBinding()
    outer = region((100, 200, 900, 900))
    update(binding, outer, frame_id=1, initial=True)
    drag_lane = region((240, 360, 720, 520), label="action_roi")

    declared = binding.declare_action_roi("current_action", drag_lane)

    assert declared == drag_lane
    assert binding.action_region("current_action") == drag_lane
    assert binding.action_region() == outer
    assert binding.metadata()["action_rois"]["current_action"]["bounds"] == [
        240, 360, 720, 520,
    ]
    with pytest.raises(ValueError, match="contained"):
        binding.declare_action_roi(
            "outside", region((20, 20, 200, 250), label="action_roi"),
        )


def test_new_window_identity_creates_fresh_binding_and_clears_action_rois():
    from winpilot.perception.surface_binding import SurfaceBinding

    binding = SurfaceBinding()
    update(binding, region((100, 200, 900, 900)), frame_id=1, initial=True)
    binding.declare_action_roi(
        "target", region((200, 300, 400, 500), label="action_roi"),
    )

    replacement = update(
        binding,
        region((50, 100, 950, 950)),
        frame_id=1,
        hwnd=92,
    )

    assert replacement.ok is True
    assert replacement.policy == "fresh_target_binding"
    assert replacement.generation == 2
    assert binding.action_region("target") is None


def test_interaction_region_rejects_coordinate_space_mixups():
    from winpilot.perception.surface_binding import InteractionRegion

    normalized = region((100, 100, 900, 900))
    physical = InteractionRegion(
        (100, 100, 900, 900), coordinate_space="physical_screen_pixels",
    )

    with pytest.raises(ValueError, match="different coordinate spaces"):
        normalized.contains(physical)
