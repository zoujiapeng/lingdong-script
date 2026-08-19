import json
from types import SimpleNamespace

import pytest


def _mock_vlm_response(monkeypatch, content: str) -> None:
    from winpilot.perception import vlm

    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
    )
    completions = SimpleNamespace(create=lambda **_kwargs: response)
    monkeypatch.setattr(vlm, "is_configured", lambda: True)
    monkeypatch.setattr(vlm, "_settings", lambda: {"model": "test-vlm"})
    monkeypatch.setattr(vlm, "_encode_region", lambda *_args, **_kwargs: ("jpeg", 1.0))
    monkeypatch.setattr(
        vlm,
        "_client",
        lambda: SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )


def test_vlm_calls_use_unified_role_scoped_model_boundary(monkeypatch):
    from winpilot.perception import vlm
    from winpilot.utils.screenshot import Region

    captured = []
    encoded_regions = []
    contents = {
        "describe_screen_region": "visible description",
        "inspect_authoritative_visual_artifact": '{"value":true}',
        "verify_visual_completion": json.dumps({
            "passed": True,
            "confidence": 0.99,
            "subject_present": True,
            "colorful": None,
            "detail_level": "unknown",
            "observed": ["target"],
            "issues": [],
        }),
        "ground_interactive_elements": "[]",
    }

    def invoke(_client, **kwargs):
        captured.append(kwargs)
        response = SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=contents[kwargs["purpose"]]),
        )])
        return SimpleNamespace(response=response)

    def encode_region(*_args, **_kwargs):
        encoded_regions.append(True)
        return "jpeg-once", 1.0

    encoded_array = SimpleNamespace(tobytes=lambda: b"array-jpeg-once")
    monkeypatch.setattr(vlm, "is_configured", lambda: True)
    monkeypatch.setattr(vlm, "_settings", lambda: {"model": "test-vlm"})
    monkeypatch.setattr(vlm, "_client", lambda: object())
    monkeypatch.setattr(vlm, "_encode_region", encode_region)
    monkeypatch.setattr(vlm.cv2, "imencode", lambda *_args, **_kwargs: (
        True, encoded_array,
    ))
    monkeypatch.setattr(vlm, "invoke_chat_completion", invoke)

    region = Region(0, 0, 100, 100)
    image = SimpleNamespace(size=3, shape=(1, 1, 3))
    assert vlm.describe(region, "what is visible?") == "visible description"
    assert vlm.structured_json_from_array(image, "return JSON") == {"value": True}
    assert vlm.verify(region, "target is visible")["verified"] is True
    assert vlm.collect(region) == []

    assert encoded_regions == [True, True, True]
    assert [
        (item["model_role"], item["purpose"])
        for item in captured
    ] == [
        ("legacy_visual_helper", "describe_screen_region"),
        ("legacy_visual_helper", "inspect_authoritative_visual_artifact"),
        ("legacy_visual_verifier", "verify_visual_completion"),
        ("legacy_visual_helper", "ground_interactive_elements"),
    ]
    expected_prefixes = (
        "perception-vlm-describe-",
        "perception-vlm-structured-json-",
        "perception-vlm-verify-",
        "perception-vlm-collect-",
    )
    for call, prefix in zip(captured, expected_prefixes, strict=True):
        assert call["decision_id"].startswith(prefix)
        assert "policy" not in call  # Unified boundary defaults to one attempt.
        content = call["provider_request"]["messages"][0]["content"]
        assert content[0]["image_url"]["url"].startswith(
            "data:image/jpeg;base64,"
        )


def test_vlm_json_parse_failure_is_not_retried(monkeypatch):
    from winpilot.perception import vlm

    sends = []
    encodes = []
    response = SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content="not JSON"),
    )])

    class Completions:
        def create(self, **request):
            sends.append(request)
            return response

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=Completions()),
    )
    encoded = SimpleNamespace(tobytes=lambda: b"same-jpeg")
    monkeypatch.setattr(vlm, "is_configured", lambda: True)
    monkeypatch.setattr(vlm, "_settings", lambda: {"model": "test-vlm"})
    monkeypatch.setattr(vlm, "_client", lambda: client)
    monkeypatch.setattr(vlm.cv2, "imencode", lambda *_args, **_kwargs: (
        encodes.append(True) or True,
        encoded,
    ))

    image = SimpleNamespace(size=3, shape=(1, 1, 3))
    with pytest.raises(ValueError, match="no JSON object"):
        vlm.structured_json_from_array(image, "return JSON")

    assert len(encodes) == 1
    assert len(sends) == 1


def test_vlm_collect_keeps_only_valid_explicitly_confident_groundings(monkeypatch):
    from winpilot.perception import vlm
    from winpilot.utils.screenshot import Region

    items = [
        {
            "role": "button",
            "text": "Apply",
            "box": [100, 200, 300, 600],
            "confidence": 0.73,
        },
        {"role": "button", "text": "missing confidence", "box": [1, 1, 2, 2]},
        {
            "role": "made-up-role",
            "text": "bad role",
            "box": [1, 1, 2, 2],
            "confidence": 0.9,
        },
        {
            "role": "button",
            "text": "inverted",
            "box": [400, 100, 200, 300],
            "confidence": 0.9,
        },
        {
            "role": "button",
            "text": "out of bounds",
            "box": [-1, 0, 100, 100],
            "confidence": 0.9,
        },
        {
            "role": "button",
            "text": "legacy point",
            "x": 50,
            "y": 50,
            "confidence": 0.9,
        },
    ]
    _mock_vlm_response(monkeypatch, json.dumps(items))

    elements = vlm.collect(Region(100, 200, 400, 200))

    assert len(elements) == 1
    element = elements[0]
    assert element.text == "Apply"
    assert element.role == "button"
    assert element.confidence == 0.73
    assert element.bbox == (140, 240, 220, 320)


def test_vlm_verify_rejects_low_confidence_pass(monkeypatch):
    from winpilot.perception import vlm
    from winpilot.utils.screenshot import Region

    _mock_vlm_response(monkeypatch, json.dumps({
        "passed": True,
        "confidence": 0.42,
        "subject_present": True,
        "colorful": None,
        "detail_level": "high",
        "observed": ["target"],
        "issues": [],
    }))

    result = vlm.verify(Region(0, 0, 100, 100), "the target is visible")

    assert result["ok"] is True
    assert result["verified"] is False
    assert result["semantic_visual_verified"] is False
    assert "confidence_below_threshold" in result["rejection_reasons"]


def test_vlm_verify_rejects_contradictory_pass(monkeypatch):
    from winpilot.perception import vlm
    from winpilot.utils.screenshot import Region

    _mock_vlm_response(monkeypatch, json.dumps({
        "passed": True,
        "confidence": 0.96,
        "subject_present": False,
        "colorful": True,
        "detail_level": "low",
        "observed": ["rough shape"],
        "issues": ["requested subject is absent"],
    }))

    result = vlm.verify(Region(0, 0, 100, 100), "a detailed colorful cat")

    assert result["verified"] is False
    assert set(result["rejection_reasons"]) == {
        "reported_issues",
        "subject_not_present",
        "detail_level_inconsistent",
    }


def test_vlm_verify_accepts_consistent_high_confidence_pass(monkeypatch):
    from winpilot.perception import vlm
    from winpilot.utils.screenshot import Region

    _mock_vlm_response(monkeypatch, json.dumps({
        "passed": True,
        "confidence": 0.93,
        "subject_present": True,
        "colorful": True,
        "detail_level": "high",
        "observed": ["detailed cat"],
        "issues": [],
    }))

    result = vlm.verify(Region(0, 0, 100, 100), "a detailed colorful cat")

    assert result["verified"] is True
    assert result["semantic_visual_verified"] is True
    assert result["rejection_reasons"] == []


def test_vlm_verify_ignores_aesthetics_not_requested_by_user(monkeypatch):
    from winpilot.perception import vlm
    from winpilot.utils.screenshot import Region

    _mock_vlm_response(monkeypatch, json.dumps({
        "passed": False,
        "confidence": 0.94,
        "subject_present": True,
        "colorful": False,
        "detail_level": "low",
        "observed": ["recognizable flower outline"],
        "issues": [
            "drawing is not colorful",
            "drawing is very basic and lacks detail",
        ],
    }))

    result = vlm.verify(
        Region(0, 0, 100, 100),
        "the canvas visibly contains a clearly recognizable flower",
    )

    assert result["verified"] is True
    assert result["semantic_visual_verified"] is True
    assert result["aesthetic_only_override"] is True
    assert result["effective_issues"] == []
    assert len(result["ignored_unrequested_issues"]) == 2


def test_vlm_verify_keeps_explicit_color_and_detail_requirements(monkeypatch):
    from winpilot.perception import vlm
    from winpilot.utils.screenshot import Region

    _mock_vlm_response(monkeypatch, json.dumps({
        "passed": False,
        "confidence": 0.94,
        "subject_present": True,
        "colorful": False,
        "detail_level": "low",
        "observed": ["plain flower outline"],
        "issues": ["not colorful", "lacks requested detail"],
    }))

    result = vlm.verify(
        Region(0, 0, 100, 100),
        "a detailed colorful flower",
    )

    assert result["verified"] is False
    assert result["aesthetic_only_override"] is False
    assert result["effective_issues"] == ["not colorful", "lacks requested detail"]


def test_element_locator_uses_perception_confidence():
    from winpilot.fast.element_cache import ElementCache
    from winpilot.fast.types import VisualAnchor
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    snapshot = Snapshot(
        hwnd=1,
        title="Visual app",
        region=Region(0, 0, 1000, 600),
        elements=[
            UIElement(
                elem_id=1, source="vlm", role="button", text="Apply",
                cx=180, cy=120, left=140, top=100, width=80, height=40,
                confidence=0.25,
            ),
            UIElement(
                elem_id=2, source="vlm", role="button", text="Apply",
                cx=820, cy=120, left=780, top=100, width=80, height=40,
                confidence=0.95,
            ),
        ],
    )

    result = ElementCache().locate_with_trace(
        snapshot,
        VisualAnchor(label="Apply", role="button"),
    )

    assert result.element is not None
    assert result.element.elem_id == 2


def test_element_locator_rejects_near_tied_distinct_candidates():
    from winpilot.fast.element_cache import ElementCache
    from winpilot.fast.types import VisualAnchor
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    snapshot = Snapshot(
        hwnd=1,
        title="Visual app",
        region=Region(0, 0, 1000, 600),
        elements=[
            UIElement(
                elem_id=1, source="vlm", role="button", text="Delete",
                cx=180, cy=120, left=140, top=100, width=80, height=40,
                confidence=0.9,
            ),
            UIElement(
                elem_id=2, source="vlm", role="button", text="Delete",
                cx=820, cy=120, left=780, top=100, width=80, height=40,
                confidence=0.9,
            ),
        ],
    )

    result = ElementCache().locate_with_trace(
        snapshot,
        VisualAnchor(label="Delete", role="button"),
    )

    assert result.element is None
    assert result.reason == "ambiguous_top_candidates"
    assert len(result.candidates) == 2
