from winpilot.perception import reducer
from winpilot.perception.fusion import Snapshot
from winpilot.perception.model import UIElement
from winpilot.utils.screenshot import Region
import json
from types import SimpleNamespace


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


def test_perception_reducer_marks_open_chat_and_stops_contact_reclick_loop():
    target = "\u6211\u7684\u624b\u673a"
    snapshot = Snapshot(
        hwnd=9,
        title="Chat",
        region=Region(0, 0, 1920, 1020),
        elements=[
            UIElement(elem_id=1, source="uia", role="edit", text="Search", cx=175, cy=82, left=117, top=70, width=116, height=25),
            UIElement(elem_id=2, source="uia", role="text", text=target, cx=180, cy=542, left=150, top=532, width=60, height=20),
            UIElement(elem_id=3, source="ocr", role="text", text=target, cx=353, cy=86, left=315, top=74, width=76, height=24),
        ],
    )

    reduced = reducer.reduce_snapshot(
        snapshot,
        task=f"use chat to talk with {target}",
        communication_context={"active": True, "recipient": target},
    )
    untyped = reducer.reduce_snapshot(
        snapshot,
        task=f"use chat to talk with {target}",
    )

    assert reduced["screen_stage"] == "chat_conversation"
    assert reduced["top_candidates"][0]["role"] == "synthetic_chat_composer"
    assert reduced["stats"]["input_like"] == 2
    assert "do not click the contact row again" in reduced["recommended_next_controller_actions"][0]
    assert untyped["chat_context"] == {}


def test_grounded_explicit_send_observe_returns_proposal_without_mutation():
    from winpilot.agent.perception_tools import PerceptionToolMixin
    from winpilot.agent.tool_result import ToolResult

    calls = []
    task = 'send "AUTO_SEND_0712" and confirm it in chat history'
    session = SimpleNamespace(
        task=task,
        task_intent={"original_text": task, "operation": ["send"], "send_mode": "send"},
        hwnd=9,
        run_id="test-auto-send",
        _auto_communication_attempted=False,
        _pending_controller_events=[],
    )

    def dispatch(name, args):
        calls.append((name, args))
        return ToolResult(json.dumps({
            "ok": True,
            "verified": True,
            "evidence_packet": {
                "matched_texts": ["AUTO_SEND_0712"],
                "visible_text_preview": "AUTO_SEND_0712",
            },
        }))

    session.dispatch = dispatch
    reduction = {"chat_context": {"center": [700, 700]}}

    result = PerceptionToolMixin._maybe_auto_execute_grounded_communication(session, reduction)
    repeated = PerceptionToolMixin._maybe_auto_execute_grounded_communication(session, reduction)

    assert result is not None
    assert repeated is not None
    assert calls == []
    proposal = json.loads(result.text)
    assert proposal["executed"] is False
    assert proposal["proposal_only"] is True
    assert proposal["recommended_tool"] == "execute_subgoal"
    assert proposal["arguments"]["input"]["text"] == "AUTO_SEND_0712"
    assert proposal["arguments"]["click"]["expected_region"] == "bottom-right"
    assert session._pending_controller_events == []


def test_grounded_chat_does_not_autogenerate_content_for_open_ended_chat():
    from winpilot.agent.perception_tools import PerceptionToolMixin
    task = "use chat to talk with My Phone"
    session = SimpleNamespace(
        task=task,
        task_intent={"original_text": task, "operation": ["send"], "send_mode": "send"},
        hwnd=9,
        run_id="test-open-chat",
        _auto_communication_attempted=False,
        _pending_controller_events=[],
        dispatch=lambda *_args: (_ for _ in ()).throw(AssertionError("must not dispatch")),
    )

    result = PerceptionToolMixin._maybe_auto_execute_grounded_communication(
        session,
        {"chat_context": {"center": [700, 700]}},
    )

    assert result is None


def test_file_dialog_reducer_prefers_filename_field_over_grid_item_names():
    snapshot = Snapshot(
        hwnd=10,
        title="Save As",
        region=Region(0, 0, 1920, 1020),
        elements=[
            UIElement(elem_id=1, source="uia", role="edit", text="Name=project_20260712", cx=300, cy=280, left=240, top=270, width=120, height=40),
            UIElement(elem_id=2, source="uia", role="edit", text="Name=backup_20260712", cx=450, cy=280, left=390, top=270, width=120, height=40),
            UIElement(elem_id=3, source="win32", role="combobox", text="*.txt", cx=1035, cy=836, left=166, top=822, width=1739, height=28),
            UIElement(elem_id=4, source="uia", role="button", text="Save", cx=1708, cy=973, left=1653, top=957, width=110, height=33),
        ],
    )

    reduced = reducer.reduce_snapshot(
        snapshot,
        task="save project_20260712.txt",
        query="file name",
        max_candidates=4,
    )

    assert reduced["top_candidates"][0]["elem_id"] == 3
    assert "wide file-dialog input" in reduced["top_candidates"][0]["why_relevant"]


def test_perception_reducer_and_raw_sample_exclude_far_off_window_uia_children():
    snapshot = Snapshot(
        hwnd=11,
        title="Workbook1",
        region=Region(900, 250, 1100, 880),
        elements=[
            UIElement(elem_id=1, source="uia", role="button", text="stale", cx=-1600, cy=280, left=-1615, top=265, width=30, height=30),
            UIElement(elem_id=2, source="uia", role="edit", text="A1", cx=1040, cy=465, left=960, top=452, width=160, height=26),
            UIElement(elem_id=3, source="uia", role="button", text="Save", cx=1900, cy=300, left=1860, top=282, width=80, height=36),
        ],
    )

    reduced = reducer.reduce_snapshot(snapshot, task="enter data in A1", query="A1")
    raw = snapshot.to_text(max_elements=20)

    assert reduced["stats"]["total_elements"] == 2
    assert reduced["stats"]["raw_total_elements"] == 3
    assert reduced["stats"]["off_window_filtered"] == 1
    assert all(candidate["label"] != "stale" for candidate in reduced["top_candidates"])
    assert 'button "stale"' not in raw
    assert "filtered 1 off-window/stale" in raw


def test_perception_reducer_marks_recovery_banner_non_blocking_when_grid_is_editable():
    snapshot = Snapshot(
        hwnd=12,
        title="工作簿14 - WPS Office",
        region=Region(0, 0, 1920, 1020),
        elements=[
            UIElement(elem_id=1, source="uia", role="edit", text="A1", cx=105, cy=209, left=28, top=197, width=154, height=25),
            UIElement(elem_id=2, source="uia", role="text", text="上一次未正常关闭，已恢复到最新版本。", cx=683, cy=166, left=548, top=149, width=270, height=35),
            UIElement(elem_id=3, source="uia", role="button", text="发送报告", cx=1853, cy=166, left=1823, top=149, width=60, height=35),
        ],
    )

    reduced = reducer.reduce_snapshot(snapshot, task="enter table data into cells A1:D7")

    assert reduced["screen_stage"] == "spreadsheet_grid_with_nonblocking_notice"
    assert reduced["non_blocking_notice"]["blocking"] is False
    assert "do not click" in reduced["recommended_next_controller_actions"][0].casefold()


def test_snapshot_multi_field_query_matches_any_term_and_ranks_multi_hits_first():
    snapshot = Snapshot(
        hwnd=15,
        title="Product page",
        region=Region(0, 0, 1200, 800),
        elements=[
            UIElement(elem_id=1, source="uia", role="text", text="Navigation", cx=50, cy=50),
            UIElement(elem_id=2, source="uia", role="text", text="Battery life: 70 days", cx=500, cy=400),
            UIElement(elem_id=3, source="uia", role="text", text="Bluetooth connection and weight 141 g", cx=500, cy=450),
        ],
    )

    rendered = snapshot.to_text(query="battery, bluetooth, weight")

    assert "matching any query term" in rendered
    assert "Bluetooth connection and weight 141 g" in rendered
    assert "Battery life: 70 days" in rendered
    assert "Navigation" not in rendered
    assert rendered.index("Bluetooth connection") < rendered.index("Battery life")


def test_editable_spreadsheet_suppresses_stale_creating_document_from_model_view():
    from winpilot.agent.perception_tools import _suppress_nonblocking_notice_lines

    snapshot = Snapshot(
        hwnd=13,
        title="Workbook17 - WPS Office",
        region=Region(0, 0, 1920, 1020),
        elements=[
            UIElement(elem_id=1, source="ocr", role="text", text="A1", cx=105, cy=209),
            UIElement(
                elem_id=2,
                source="uia",
                role="text",
                text="\u6b63\u5728\u65b0\u5efa\u6587\u6863\u00b7\u00b7\u00b7",
                cx=960,
                cy=599,
            ),
        ],
    )

    reduced = reducer.reduce_snapshot(snapshot, task="enter table data into cells A1:D7")
    rendered = _suppress_nonblocking_notice_lines(snapshot.to_text(), reduced["non_blocking_notice"])

    assert reduced["screen_stage"] == "spreadsheet_grid_with_nonblocking_notice"
    assert "\u6b63\u5728\u65b0\u5efa\u6587\u6863" not in rendered
    assert "complete raw elements remain" in rendered


def test_writer_treats_stale_creating_document_notice_as_non_blocking():
    from winpilot.agent.perception_tools import _suppress_nonblocking_notice_lines

    snapshot = Snapshot(
        hwnd=14,
        title="文字文稿26 - WPS Office",
        region=Region(0, 0, 1920, 1020),
        elements=[
            UIElement(
                elem_id=1,
                source="uia",
                role="window",
                text="文字文稿26 - WPS 文字",
                cx=960,
                cy=535,
                left=0,
                top=50,
                width=1920,
                height=970,
            ),
            UIElement(
                elem_id=2,
                source="uia",
                role="text",
                text="正在新建文档···",
                cx=960,
                cy=599,
            ),
        ],
    )

    reduced = reducer.reduce_snapshot(
        snapshot,
        task="create a brand-new blank document and type a report",
    )
    rendered = _suppress_nonblocking_notice_lines(
        snapshot.to_text(),
        reduced["non_blocking_notice"],
    )

    assert reduced["screen_stage"] == "document_editor_with_nonblocking_notice"
    assert reduced["non_blocking_notice"]["surface"] == "document_editor"
    assert "ctrl+n now" in reduced["recommended_next_controller_actions"][0].casefold()
    assert "正在新建文档" not in rendered


def test_writer_ocr_chrome_is_enough_to_mark_recovery_notice_non_blocking():
    snapshot = Snapshot(
        hwnd=15,
        title="文字文稿2 - WPS Office",
        region=Region(0, 0, 1920, 1020),
        elements=[
            UIElement(elem_id=1, source="ocr", role="text", text="开始", cx=150, cy=100),
            UIElement(elem_id=2, source="ocr", role="text", text="插入", cx=220, cy=100),
            UIElement(elem_id=3, source="ocr", role="text", text="页面：1/1", cx=90, cy=990),
            UIElement(elem_id=4, source="ocr", role="text", text="字数：0", cx=200, cy=990),
            UIElement(
                elem_id=5,
                source="ocr",
                role="text",
                text="上一次未正常关闭，已恢复到最新版本。",
                cx=960,
                cy=150,
            ),
            UIElement(elem_id=6, source="ocr", role="text", text="发送报告", cx=1800, cy=150),
        ],
    )

    reduced = reducer.reduce_snapshot(snapshot, task="type the requested report")

    assert reduced["screen_stage"] == "document_editor_with_nonblocking_notice"
    assert reduced["non_blocking_notice"]["blocking"] is False
    assert "do not wait" in reduced["recommended_next_controller_actions"][0].casefold()
