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
import re

import cv2

from ..config import CONFIG
from ..model_adapter import model_profile_from_config
from ..utils.log import logger
from ..utils.screenshot import Region, capture
from .model import UIElement

_DEEPSEEK_TEXT_ONLY_RE = re.compile(
    r"^(deepseek-chat|deepseek-reasoner|deepseek-v4(?:-flash|-pro)?)$",
    re.I,
)


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
    from openai import OpenAI

    vlm = _settings()
    return OpenAI(
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


def describe(region: Region, question: str) -> str:
    """Ask the configured VLM a question about a screen region."""
    if not is_configured():
        issue = configuration_issue()
        return f"VLM 未配置{f'：{issue}' if issue else ''}"
    region = region.clamp_to_screen()
    b64, _scale = _encode_region(region)
    vlm = _settings()
    try:
        resp = _client().chat.completions.create(
            model=vlm["model"],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                        {"type": "text", "text": question},
                    ],
                }
            ],
            max_tokens=800,
            temperature=0.1,
            extra_body=_extra_body(),
        )
        return resp.choices[0].message.content or ""
    except Exception as exc:
        logger.warning("VLM describe 失败: %s", exc)
        return f"VLM 调用失败: {exc}"


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
    prompt = (
        "You are a strict visual completion verifier for a GUI agent. Judge only "
        "what is visibly present in the screenshot; do not infer success from the "
        "request. Criteria: " + str(criteria or "").strip() + "\n"
        "Return ONLY one JSON object with keys: passed (boolean), confidence "
        "(0..1), subject_present (boolean or null), colorful (boolean or null), "
        "detail_level (low|medium|high|unknown), observed (array of short strings), "
        "issues (array of short strings). passed must be false when any explicit "
        "criterion is missing, the subject is only vaguely recognizable, or "
        "requested detail/quality is visibly inadequate."
    )
    try:
        resp = _client().chat.completions.create(
            model=settings["model"],
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": prompt},
                ],
            }],
            max_tokens=600,
            temperature=0.0,
            extra_body=_extra_body(),
        )
        raw = str(resp.choices[0].message.content or "").strip()
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("visual verifier returned no JSON object")
        verdict = json.loads(raw[start : end + 1])
        if not isinstance(verdict, dict):
            raise ValueError("visual verifier returned a non-object payload")
        passed = verdict.get("passed") is True
        return {
            "ok": True,
            "verified": passed,
            "semantic_visual_verified": passed,
            "verify_method": "semantic-vlm",
            "criteria": str(criteria or "").strip(),
            "verdict": verdict,
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
    '"box": [x1, y1, x2, y2]} '
    "where coordinates are NORMALIZED to 0-1000 (top-left origin, "
    "1000 = right/bottom edge of the image)."
)


def _decode_box(box: list, region: Region, scale: float) -> tuple[int, int, int, int]:
    """Box -> absolute screen rect (left, top, width, height).

    MiMo / Qwen2.5-VL lineage natively grounds on a 0-1000 normalized grid
    (verified by benchmark: mean error ~6px once decoded). Normalized coords
    map directly onto the region — independent of the JPEG downscale. Values
    > 1000 are treated as pixels in the submitted (possibly scaled) image.
    """
    x1, y1, x2, y2 = (float(v) for v in box)
    if max(x1, y1, x2, y2) <= 1000.0:
        left = region.left + x1 / 1000 * region.width
        top = region.top + y1 / 1000 * region.height
        width = (x2 - x1) / 1000 * region.width
        height = (y2 - y1) / 1000 * region.height
    else:
        left = region.left + x1 / scale
        top = region.top + y1 / scale
        width = (x2 - x1) / scale
        height = (y2 - y1) / scale
    return int(left), int(top), max(1, int(width)), max(1, int(height))


def collect(region: Region) -> list[UIElement]:
    """VLM-grounded element list for a region. Never raises."""
    if not is_configured():
        return []
    try:
        region = region.clamp_to_screen()
        b64, scale = _encode_region(region)
        vlm = _settings()
        resp = _client().chat.completions.create(
            model=vlm["model"],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                        {"type": "text", "text": _GROUND_PROMPT},
                    ],
                }
            ],
            max_tokens=3000,
            temperature=0.0,
            extra_body=_extra_body(),
        )
        raw = resp.choices[0].message.content or "[]"
        start, end = raw.find("["), raw.rfind("]")
        if start < 0 or end <= start:
            return []
        items = json.loads(raw[start : end + 1])
        elements = []
        for item in items[:80]:
            try:
                box = item.get("box") or []
                if len(box) != 4:
                    # Legacy single-point answers: keep a small clickable box.
                    x = int(int(item["x"]) / scale) + region.left
                    y = int(int(item["y"]) / scale) + region.top
                    left, top, width, height = x - 8, y - 8, 16, 16
                else:
                    left, top, width, height = _decode_box(box, region, scale)
                elements.append(
                    UIElement(
                        source="vlm",
                        role=str(item.get("role", "elem")),
                        text=str(item.get("text", "")),
                        cx=left + width // 2,
                        cy=top + height // 2,
                        left=left, top=top, width=width, height=height,
                        confidence=0.8,
                    )
                )
            except (KeyError, ValueError, TypeError):
                continue
        return elements
    except Exception as exc:
        logger.warning("VLM collect 失败: %s", exc)
        return []
