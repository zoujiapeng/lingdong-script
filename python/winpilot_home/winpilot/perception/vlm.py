"""Optional VLM dimension: when the user configures a multimodal model
(MiMo / Qwen2.5-VL / GPT-4o-class via any OpenAI-compatible endpoint),
WinPilot gains true vision:

- ``describe(region, question)`` — ad-hoc visual Q&A about a screen region.
- ``collect(region)`` — VLM-grounded element extraction (slowest dimension,
  lowest default priority; used when structured dimensions fail).

The vision invocation may reuse the main model when that endpoint accepts
``image_url`` content (currently verified for ``mimo-v2.5``), or use a
separate endpoint for a text-only main model.
"""
from __future__ import annotations

import base64
import json
import math
import re
import uuid
from pathlib import Path
from typing import Any

import cv2

from ..config import CONFIG
from ..model_adapter import model_profile_from_config
from ..modeling import create_openai_client, invoke_chat_completion
from ..utils.log import logger
from ..utils.screenshot import Region, capture
from .model import UIElement

_DEEPSEEK_TEXT_ONLY_RE = re.compile(
    r"^(deepseek-chat|deepseek-reasoner|deepseek-v4(?:-flash|-pro)?)$",
    re.I,
)

_MODEL_CALL_CONTRACTS = {
    "describe": ("legacy_visual_helper", "describe_screen_region"),
    "structured-json": (
        "legacy_visual_helper",
        "inspect_authoritative_visual_artifact",
    ),
    "verify": ("legacy_visual_verifier", "verify_visual_completion"),
    "collect": ("legacy_visual_helper", "ground_interactive_elements"),
}


def _settings() -> dict:
    """Resolve VLM settings, reusing the main model when it has vision."""
    vlm = dict(CONFIG.get("vlm", default={}) or {})
    api = dict(CONFIG.get("api", default={}) or {})
    if vlm.get("enabled") and model_profile_from_config(api).supports_vision:
        vlm["base_url"] = vlm.get("base_url") or api.get("base_url") or ""
        vlm["api_key"] = vlm.get("api_key") or api.get("api_key") or ""
        vlm["model"] = vlm.get("model") or api.get("model") or ""
    return vlm


def configuration_issue() -> str:
    """Return a human-readable reason the VLM path is unavailable.

    DeepSeek's current public chat API rejects image_url inputs for the text
    models we tested (deepseek-chat, deepseek-reasoner, deepseek-v4-flash,
    deepseek-v4-pro), so it cannot back the visual/perception pipeline
    directly. Use a separate multimodal endpoint as the vision proxy.
    """
    vlm = _settings()
    base_url = str(vlm.get("base_url") or "").lower()
    model = str(vlm.get("model") or "")
    if vlm.get("enabled") and base_url and model:
        if "deepseek" in base_url and _DEEPSEEK_TEXT_ONLY_RE.fullmatch(model):
            return (
                "DeepSeek V4 API is text-only; use a separate multimodal VLM "
                "endpoint for screenshots/images."
            )
    return ""


def is_configured() -> bool:
    vlm = _settings()
    return bool(vlm.get("enabled") and vlm.get("base_url") and vlm.get("model")
                and not configuration_issue())


def _client():
    vlm = _settings()
    return create_openai_client(
        base_url=vlm["base_url"],
        api_key=vlm.get("api_key") or "none",
        timeout=120,
    )


def _encode_region(region: Region, max_side: int = 1280) -> tuple[str, float]:
    """JPEG-base64 of a region; returns (b64, scale_applied)."""
    image = capture(region.clamp_to_screen())
    h, w = image.shape[:2]
    scale = 1.0
    if max(h, w) > max_side:
        scale = max_side / max(h, w)
        image = cv2.resize(image, (int(w * scale), int(h * scale)))
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise RuntimeError("JPEG 编码失败")
    return base64.b64encode(buf.tobytes()).decode(), scale


def _extra_body() -> dict:
    """MiMo-style reasoning models burn the whole token budget on thinking
    before emitting JSON — disable it unless explicitly turned on
    (benchmarked: grounding precision is equal-or-better without thinking).
    """
    if CONFIG.get("vlm", "thinking", default=False):
        return {}
    return {"thinking": {"type": "disabled"}}


def _invoke_model(operation: str, provider_request: dict[str, Any]) -> Any:
    """Bind one read-only visual call before the transport sees the payload."""
    model_role, purpose = _MODEL_CALL_CONTRACTS[operation]
    return invoke_chat_completion(
        _client(),
        decision_id=f"perception-vlm-{operation}-{uuid.uuid4().hex}",
        model_role=model_role,
        purpose=purpose,
        provider_request=provider_request,
    ).response


def describe(region: Region, question: str) -> str:
    """Ask the configured VLM a question about a screen region."""
    if not is_configured():
        issue = configuration_issue()
        return f"VLM 未配置{f'：{issue}' if issue else ''}"
    region = region.clamp_to_screen()
    b64, _scale = _encode_region(region)
    vlm = _settings()
    provider_request = {
        "model": vlm["model"],
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    },
                    {"type": "text", "text": question},
                ],
            }
        ],
        "max_tokens": 800,
        "temperature": 0.1,
        "extra_body": _extra_body(),
    }
    try:
        resp = _invoke_model("describe", provider_request)
        return resp.choices[0].message.content or ""
    except Exception as exc:
        logger.warning("VLM describe 失败: %s", exc)
        return f"VLM 调用失败: {exc}"


def _structured_json_from_array(
    image: Any,
    prompt: str,
    *,
    max_tokens: int,
) -> dict:
    if not is_configured():
        raise RuntimeError(configuration_issue() or "VLM is not configured")
    if image is None or image.size == 0:
        raise ValueError("authoritative visual artifact could not be decoded")
    height, width = image.shape[:2]
    scale = min(1.0, 1280.0 / max(1, height, width))
    if scale < 1.0:
        image = cv2.resize(
            image,
            (max(1, int(width * scale)), max(1, int(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    ok, encoded = cv2.imencode(
        ".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 88],
    )
    if not ok:
        raise RuntimeError("authoritative visual artifact could not be encoded")
    b64 = base64.b64encode(encoded.tobytes()).decode()
    settings = _settings()
    provider_request = {
        "model": settings["model"],
        "messages": [{
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                },
                {"type": "text", "text": str(prompt or "").strip()},
            ],
        }],
        "max_tokens": max(300, min(6000, int(max_tokens))),
        "temperature": 0.0,
        "extra_body": _extra_body(),
    }
    response = _invoke_model("structured-json", provider_request)
    raw = str(response.choices[0].message.content or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("visual model returned no JSON object")
    value = json.loads(raw[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("visual model returned a non-object JSON value")
    return value


def structured_json_from_array(
    image: Any,
    prompt: str,
    *,
    max_tokens: int = 900,
) -> dict:
    """Return strict JSON from the exact caller-owned in-memory frame.

    Local controller resolvers use this for small semantic preconditions where
    writing a perception frame as a user-sendable artifact would conflate two
    lifecycles.  The caller still owns frame lineage and postcondition checks.
    """
    return _structured_json_from_array(
        image,
        prompt,
        max_tokens=max_tokens,
    )


def structured_json_from_image(
    image_path: str | Path,
    prompt: str,
    *,
    max_tokens: int = 1800,
) -> dict:
    """Return one strict JSON object grounded in an existing image artifact.

    The task runtime persists the authoritative observation before asking for a
    semantic decision.  Reading that exact file here avoids a second capture
    silently changing the coordinate frame between perception and planning.
    This helper is intentionally domain-neutral; callers own their typed
    schema and must validate the returned object before any mutation.
    """
    path = Path(image_path)
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError("authoritative visual artifact is missing or empty")
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    return _structured_json_from_array(
        image,
        prompt,
        max_tokens=max_tokens,
    )


def verify(region: Region, criteria: str) -> dict:
    """Strictly judge visible pixels against user-facing completion criteria."""
    if not is_configured():
        return {
            "ok": False,
            "verified": False,
            "semantic_visual_verified": False,
            "verify_method": "semantic-vlm",
            "error": configuration_issue() or "VLM is not configured",
        }
    region = region.clamp_to_screen()
    b64, _scale = _encode_region(region, max_side=1120)
    settings = _settings()
    criteria_text = str(criteria or "").strip()
    colorfulness_required = bool(re.search(
        r"\b(?:colorful|colourful|multicolou?r(?:ed)?)\b|(?:彩色|多彩|五颜六色)",
        criteria_text,
        re.IGNORECASE,
    ))
    detail_required = bool(re.search(
        r"\b(?:detail(?:ed)?|intricate|high[- ]quality)\b|(?:精细|精致|细节|高质量)",
        criteria_text,
        re.IGNORECASE,
    ))
    aesthetic_quality_required = bool(re.search(
        r"\b(?:beautiful|pretty|polished|professional|aesthetic|attractive)\b|"
        r"(?:好看|漂亮|美观|精美|专业|有质感)",
        criteria_text,
        re.IGNORECASE,
    ))
    prompt = (
        "You are a strict visual completion verifier for a GUI agent. Judge only "
        "what is visibly present in the screenshot; do not infer success from the "
        "request. Criteria: " + criteria_text + "\n"
        "Project the criteria literally. Never invent an aesthetic requirement: a "
        "monochrome, simple, or low-detail result can pass when the requested subject "
        "and every explicitly requested property are clearly recognizable. Color, "
        "beauty, polish, realism, and decorative detail are mandatory only when the "
        "criteria explicitly request them. Do not list an unrequested aesthetic as an "
        "issue. "
        f"Explicit colorful requirement={colorfulness_required}; explicit detail "
        f"requirement={detail_required}; explicit aesthetic-quality "
        f"requirement={aesthetic_quality_required}.\n"
        "Return ONLY one JSON object with keys: passed (boolean), confidence "
        "(0..1), subject_present (boolean or null), colorful (boolean or null), "
        "detail_level (low|medium|high|unknown), observed (array of short strings), "
        "issues (array of short strings). passed must be false when any explicit "
        "criterion is missing, the subject is only vaguely recognizable, or "
        "requested detail/quality is visibly inadequate. When passed is true, "
        "subject_present must be true and issues must be empty."
    )
    provider_request = {
        "model": settings["model"],
        "messages": [{
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                },
                {"type": "text", "text": prompt},
            ],
        }],
        "max_tokens": 600,
        "temperature": 0.0,
        "extra_body": _extra_body(),
    }
    try:
        resp = _invoke_model("verify", provider_request)
        raw = str(resp.choices[0].message.content or "").strip()
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("visual verifier returned no JSON object")
        verdict = json.loads(raw[start : end + 1])
        if not isinstance(verdict, dict):
            raise ValueError("visual verifier returned a non-object payload")
        raw_passed = verdict.get("passed") is True
        confidence = verdict.get("confidence")
        confidence_valid = (
            isinstance(confidence, (int, float))
            and not isinstance(confidence, bool)
            and math.isfinite(float(confidence))
            and 0.0 <= float(confidence) <= 1.0
        )
        try:
            min_confidence = float(CONFIG.get(
                "vlm", "verify_min_confidence", default=0.8,
            ) or 0.8)
        except (TypeError, ValueError):
            min_confidence = 0.8
        min_confidence = max(0.0, min(1.0, min_confidence))
        issues = verdict.get("issues")
        issues_valid = isinstance(issues, list) and all(
            isinstance(issue, str) for issue in issues
        )
        relevant_issues: list[str] = []
        ignored_unrequested_issues: list[str] = []
        semantic_failure = re.compile(
            r"recogniz|unrecogniz|subject|object|absent|not present|not visible|"
            r"unrelated|unfinished|incomplete|abstract|silhouette|structure|"
            r"disconnect|overlap|无法识别|不可识别|主体|缺失|不存在|未完成|抽象|轮廓|结构|断开",
            re.IGNORECASE,
        )
        color_aesthetic = re.compile(
            r"colou?r|colorful|monochrome|black.?and.?white|彩色|颜色|色彩|黑白|单色",
            re.IGNORECASE,
        )
        detail_aesthetic = re.compile(
            r"detail|basic|crude|simple|rough|polish|aesthetic|pretty|beautiful|"
            r"quality|decorat|精细|细节|简单|简陋|粗糙|好看|漂亮|美观|精美|装饰|质量",
            re.IGNORECASE,
        )
        for issue in issues if issues_valid else []:
            fragments = [
                part.strip()
                for part in re.split(r"[;；。]+", issue)
                if part.strip()
            ] or [issue.strip()]
            kept: list[str] = []
            ignored: list[str] = []
            for fragment in fragments:
                semantic = bool(semantic_failure.search(fragment))
                unrequested_color = bool(
                    color_aesthetic.search(fragment)
                    and not colorfulness_required
                )
                unrequested_quality = bool(
                    detail_aesthetic.search(fragment)
                    and not detail_required
                    and not aesthetic_quality_required
                )
                if not semantic and (unrequested_color or unrequested_quality):
                    ignored.append(fragment)
                else:
                    kept.append(fragment)
            if kept:
                relevant_issues.append("; ".join(kept))
            if ignored:
                ignored_unrequested_issues.append("; ".join(ignored))
        no_issues = issues_valid and not relevant_issues
        subject_present = verdict.get("subject_present")
        subject_consistent = subject_present is True
        detail_level = str(verdict.get("detail_level") or "").strip().casefold()
        detail_valid = detail_level in {"low", "medium", "high", "unknown"}
        detail_consistent = detail_valid and (
            not detail_required or detail_level == "high"
        )
        aesthetic_only_override = bool(
            not raw_passed
            and issues_valid
            and ignored_unrequested_issues
            and not relevant_issues
            and subject_consistent
        )
        effective_passed = bool(raw_passed or aesthetic_only_override)
        passed = bool(
            effective_passed
            and confidence_valid
            and float(confidence) >= min_confidence
            and no_issues
            and subject_consistent
            and detail_consistent
        )
        rejection_reasons: list[str] = []
        if raw_passed and not confidence_valid:
            rejection_reasons.append("invalid_confidence")
        elif raw_passed and float(confidence) < min_confidence:
            rejection_reasons.append("confidence_below_threshold")
        if raw_passed and not no_issues:
            rejection_reasons.append("reported_issues")
        if raw_passed and not subject_consistent:
            rejection_reasons.append("subject_not_present")
        if raw_passed and not detail_consistent:
            rejection_reasons.append("detail_level_inconsistent")
        return {
            "ok": True,
            "verified": passed,
            "semantic_visual_verified": passed,
            "verify_method": "semantic-vlm",
            "criteria": criteria_text,
            "verdict": verdict,
            "effective_issues": relevant_issues,
            "ignored_unrequested_issues": ignored_unrequested_issues,
            "aesthetic_only_override": aesthetic_only_override,
            "explicit_requirements": {
                "colorful": colorfulness_required,
                "detail": detail_required,
                "aesthetic_quality": aesthetic_quality_required,
            },
            "verify_confidence": float(confidence) if confidence_valid else None,
            "verify_min_confidence": min_confidence,
            "rejection_reasons": rejection_reasons,
        }
    except Exception as exc:
        logger.warning("VLM verify failed: %s", exc)
        return {
            "ok": False,
            "verified": False,
            "semantic_visual_verified": False,
            "verify_method": "semantic-vlm",
            "error": str(exc),
        }


_GROUND_PROMPT = (
    "Locate every interactive UI element and text label in this screenshot. "
    "Reply ONLY with a JSON array; each item: "
    '{"role": "button|edit|text|icon|link|checkbox|image", "text": "<text>", '
    '"box": [x1, y1, x2, y2], "confidence": 0.0} '
    "where coordinates are NORMALIZED to 0-1000 (top-left origin, "
    "1000 = right/bottom edge of the image). confidence must be a JSON number "
    "from 0 to 1 representing grounding certainty. Omit uncertain guesses."
)

_GROUND_ROLES = {"button", "edit", "text", "icon", "link", "checkbox", "image"}


def _validated_grounding_item(item: object) -> tuple[str, str, list[float], float] | None:
    """Validate one model-produced grounding item without repairing guesses."""
    if not isinstance(item, dict):
        return None
    role = str(item.get("role") or "").strip().casefold()
    if role not in _GROUND_ROLES:
        return None
    text = item.get("text", "")
    if not isinstance(text, str):
        return None
    confidence = item.get("confidence")
    if (
        not isinstance(confidence, (int, float))
        or isinstance(confidence, bool)
        or not math.isfinite(float(confidence))
        or not 0.0 < float(confidence) <= 1.0
    ):
        return None
    box = item.get("box")
    if not isinstance(box, list) or len(box) != 4:
        return None
    if any(
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        for value in box
    ):
        return None
    coords = [float(value) for value in box]
    x1, y1, x2, y2 = coords
    if not (0.0 <= x1 < x2 <= 1000.0 and 0.0 <= y1 < y2 <= 1000.0):
        return None
    return role, text.strip(), coords, float(confidence)


def _decode_box(box: list, region: Region, scale: float) -> tuple[int, int, int, int]:
    """Box -> absolute screen rect (left, top, width, height).

    The grounding contract is a single 0-1000 normalized grid. ``scale`` is
    retained for call-site compatibility but deliberately ignored; accepting a
    second implicit pixel protocol made small-image coordinates ambiguous.
    """
    x1, y1, x2, y2 = (float(v) for v in box)
    left = region.left + x1 / 1000 * region.width
    top = region.top + y1 / 1000 * region.height
    width = (x2 - x1) / 1000 * region.width
    height = (y2 - y1) / 1000 * region.height
    return int(left), int(top), max(1, int(width)), max(1, int(height))


def collect(region: Region) -> list[UIElement]:
    """VLM-grounded element list for a region. Never raises."""
    if not is_configured():
        return []
    try:
        region = region.clamp_to_screen()
        b64, scale = _encode_region(region)
        vlm = _settings()
        provider_request = {
            "model": vlm["model"],
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                        },
                        {"type": "text", "text": _GROUND_PROMPT},
                    ],
                }
            ],
            "max_tokens": 3000,
            "temperature": 0.0,
            "extra_body": _extra_body(),
        }
        resp = _invoke_model("collect", provider_request)
        raw = resp.choices[0].message.content or "[]"
        start, end = raw.find("["), raw.rfind("]")
        if start < 0 or end <= start:
            return []
        items = json.loads(raw[start : end + 1])
        if not isinstance(items, list):
            return []
        elements = []
        for item in items[:80]:
            try:
                validated = _validated_grounding_item(item)
                if validated is None:
                    continue
                role, text, box, confidence = validated
                left, top, width, height = _decode_box(box, region, scale)
                elements.append(
                    UIElement(
                        source="vlm",
                        role=role,
                        text=text,
                        cx=left + width // 2,
                        cy=top + height // 2,
                        left=left, top=top, width=width, height=height,
                        confidence=confidence,
                    )
                )
            except (KeyError, ValueError, TypeError):
                continue
        return elements
    except Exception as exc:
        logger.warning("VLM collect 失败: %s", exc)
        return []
