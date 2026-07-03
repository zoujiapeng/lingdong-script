from winpilot.perception import reducer
from winpilot.perception.fusion import Snapshot
from winpilot.perception.model import UIElement
from winpilot.utils.screenshot import Region


def test_perception_reducer_prioritizes_relevant_input_and_button():
    snapshot = Snapshot(
        hwnd=7,
        title="Target Form",
        region=Region(10, 20, 800, 500),
        elements=[
            UIElement(elem_id=1, source="ocr", role="text", text="Footer", cx=40, cy=480, left=20, top=470, width=80, height=20),
            UIElement(elem_id=2, source="uia", role="edit", text="Target input", cx=260, cy=140, left=120, top=120, width=280, height=36),
            UIElement(elem_id=3, source="uia", role="button", text="Apply Input", cx=450, cy=140, left=410, top=120, width=90, height=36),
        ],
    )

    reduced = reducer.reduce_snapshot(
        snapshot,
        task="Type FAST_WRAP_OK_777 into the target input and click Apply Input",
        query="target input apply",
        recent_changes={"changed_regions": [{"left": 100, "top": 100, "width": 430, "height": 90}]},
        max_candidates=4,
    )

    assert reduced["screen_stage"] == "form_or_search_surface"
    assert reduced["top_candidates"]
    labels = [item["label"] for item in reduced["top_candidates"]]
    assert "Target input" in labels
    assert "Apply Input" in labels
    assert any("execute_subgoal" in item for item in reduced["recommended_next_controller_actions"])


def test_render_reduced_snapshot_keeps_compact_candidate_text():
    snapshot = Snapshot(
        hwnd=8,
        title="Search App",
        region=Region(0, 0, 640, 400),
        elements=[
            UIElement(elem_id=1, source="ocr", role="text", text="Search", cx=40, cy=40, left=10, top=30, width=60, height=20),
            UIElement(elem_id=2, source="uia", role="button", text="Go", cx=340, cy=40, left=310, top=25, width=60, height=30),
        ],
    )

    text = reducer.render_reduced_snapshot(snapshot, task="search for cats", query="Search")

    assert "Perception reducer:" in text
    assert "top_candidates:" in text
    assert "Search" in text
