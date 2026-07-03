import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from engine.executor import DSLExecutor


def run_with_spy(source: str):
    calls = []

    def handler(action, **kwargs):
        calls.append((action, kwargs))
        return {"success": True, "action": action, **kwargs}

    executor = DSLExecutor(action_handler=handler)
    executor.set_dialog_auto(False)
    result = executor.run_dsl_sync(source)
    return result, calls


def test_input_text_clicks_coordinates_then_types():
    result, calls = run_with_spy('INPUT_TEXT 10 20 "hello world"')

    assert result["success"] is True
    assert calls == [
        ("click", {"target": "10,20"}),
        ("type", {"text": "hello world"}),
    ]


def test_click_text_dispatches_to_click_action():
    result, calls = run_with_spy('CLICK_TEXT "Confirm"')

    assert result["success"] is True
    assert calls == [("click", {"target": "Confirm"})]


def test_click_image_dispatches_to_image_click_action():
    result, calls = run_with_spy('CLICK_IMAGE "button.png" sim=0.8 timeout=2')

    assert result["success"] is True
    assert calls == [
        (
            "image_click",
            {
                "targets": "button.png",
                "similarity": 0.8,
                "method": "single",
                "timeout": 2.0,
                "offset_x": 0,
                "offset_y": 0,
                "match_all": False,
            },
        )
    ]
