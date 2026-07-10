"""Low-latency visual servo tracking for moving GUI targets."""
from __future__ import annotations

import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Callable

import cv2
import numpy as np

from ..perception.model import UIElement
from ..utils.screenshot import Region


CaptureFn = Callable[[Region], np.ndarray]
MoveFn = Callable[[int, int], Any]
OcrFn = Callable[..., list[UIElement]]


@dataclass(frozen=True)
class ServoConfig:
    iterations: int = 4
    tolerance_px: int = 5
    settle_ms: int = 24
    search_radius_px: int = 180
    min_template_confidence: float = 0.74
    strict: bool = True

    @classmethod
    def from_action(cls, action: dict[str, Any]) -> "ServoConfig":
        def bounded_int(key: str, default: int, low: int, high: int) -> int:
            try:
                return max(low, min(high, int(action.get(key) or default)))
            except (TypeError, ValueError):
                return default

        try:
            confidence = float(action.get("servo_template_threshold") or 0.74)
        except (TypeError, ValueError):
            confidence = 0.74
        raw_strict = action.get("servo_strict", True)
        if isinstance(raw_strict, str):
            strict = raw_strict.strip().casefold() in {"1", "true", "yes", "on"}
        else:
            strict = bool(raw_strict)
        return cls(
            iterations=bounded_int("servo_iterations", 4, 1, 6),
            tolerance_px=bounded_int("servo_tolerance_px", 5, 1, 30),
            settle_ms=bounded_int("servo_settle_ms", 24, 5, 120),
            search_radius_px=bounded_int("servo_search_radius_px", 180, 60, 420),
            min_template_confidence=max(0.55, min(0.96, confidence)),
            strict=strict,
        )


def _now_ms() -> int:
    return int(time.monotonic() * 1000)


def _norm_text(value: str) -> str:
    return "".join(
        ch for ch in str(value or "").casefold()
        if ch.isalnum() or "\u4e00" <= ch <= "\u9fff"
    )


def _text_matches(labels: list[str], text: str) -> bool:
    candidate = _norm_text(text)
    if not candidate:
        return False
    for label in labels:
        expected = _norm_text(label)
        if not expected:
            continue
        if expected in candidate or candidate in expected:
            return True
        if len(expected) >= 5 and SequenceMatcher(None, expected, candidate).ratio() >= 0.82:
            return True
    return False


def _element_region(element: UIElement, pad: int = 0) -> Region:
    width = max(16, int(element.width or 0))
    height = max(12, int(element.height or 0))
    left = int(element.left or (element.cx - width // 2))
    top = int(element.top or (element.cy - height // 2))
    return Region(
        left - pad,
        top - pad,
        width + pad * 2,
        height + pad * 2,
    ).clamp_to_screen()


def _search_region(element: UIElement, config: ServoConfig, expansion: float = 1.0) -> Region:
    width = max(16, int(element.width or 0))
    height = max(12, int(element.height or 0))
    radius_x = max(config.search_radius_px, int(width * 1.75))
    radius_y = max(int(config.search_radius_px * 0.65), height * 3)
    radius_x = int(radius_x * expansion)
    radius_y = int(radius_y * expansion)
    return Region(
        int(element.cx) - radius_x,
        int(element.cy) - radius_y,
        radius_x * 2,
        radius_y * 2,
    ).clamp_to_screen()


def _template_estimate(
    template: np.ndarray | None,
    frame: np.ndarray,
    region: Region,
    initial: UIElement,
    threshold: float,
) -> tuple[UIElement | None, dict[str, Any]]:
    if (
        template is None or frame is None
        or getattr(template, "size", 0) == 0 or getattr(frame, "size", 0) == 0
        or template.ndim != 3 or frame.ndim != 3
    ):
        return None, {"available": False}
    template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    frame_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    if float(template_gray.std()) < 3.0:
        return None, {"available": False, "reason": "low_contrast_template"}

    best: tuple[float, int, int, int, int] | None = None
    scales_tested: list[float] = []

    def test_scale(scale: float) -> None:
        nonlocal best
        width = max(8, int(round(template_gray.shape[1] * scale)))
        height = max(8, int(round(template_gray.shape[0] * scale)))
        if width >= frame_gray.shape[1] or height >= frame_gray.shape[0]:
            return
        scales_tested.append(scale)
        candidate = cv2.resize(template_gray, (width, height), interpolation=cv2.INTER_AREA)
        response = cv2.matchTemplate(frame_gray, candidate, cv2.TM_CCOEFF_NORMED)
        _minimum, confidence, _minimum_at, location = cv2.minMaxLoc(response)
        item = (float(confidence), int(location[0]), int(location[1]), width, height)
        if best is None or item[0] > best[0]:
            best = item

    # Desktop controls normally translate without resizing. Keep the hot path
    # to one exact-size correlation and pay for scale recovery only when it
    # cannot clear the configured confidence threshold.
    test_scale(1.0)
    if best is None or best[0] < threshold:
        test_scale(0.9)
        test_scale(1.1)
    if best is None:
        return None, {"available": False, "reason": "template_larger_than_search"}
    confidence, x, y, width, height = best
    metadata = {
        "available": True,
        "confidence": round(confidence, 4),
        "threshold": round(threshold, 4),
        "scales_tested": scales_tested,
    }
    if confidence < threshold:
        metadata["reason"] = "below_threshold"
        return None, metadata
    left = region.left + x
    top = region.top + y
    return UIElement(
        elem_id=initial.elem_id,
        source="servo-template",
        role=initial.role,
        text=initial.text,
        cx=left + width // 2,
        cy=top + height // 2,
        left=left,
        top=top,
        width=width,
        height=height,
        confidence=confidence,
    ), metadata


def _ocr_estimate(
    frame: np.ndarray,
    region: Region,
    labels: list[str],
    current: UIElement,
    ocr_fn: OcrFn | None,
) -> tuple[UIElement | None, dict[str, Any]]:
    if not labels or not callable(ocr_fn):
        return None, {"used": False}
    try:
        candidates = ocr_fn(frame, origin=(region.left, region.top), min_conf=0.3)
    except TypeError:
        candidates = ocr_fn(frame, origin=(region.left, region.top))
    except Exception as exc:
        return None, {"used": True, "error": str(exc)}
    matches = [item for item in candidates if _text_matches(labels, item.text)]
    if not matches:
        return None, {"used": True, "count": 0}
    match = min(
        matches,
        key=lambda item: (int(item.cx) - int(current.cx)) ** 2
        + (int(item.cy) - int(current.cy)) ** 2,
    )
    return match, {
        "used": True,
        "count": len(matches),
        "confidence": round(float(match.confidence or 0.0), 4),
        "text": match.text,
    }


def _estimate(
    template: np.ndarray | None,
    initial: UIElement,
    current: UIElement,
    labels: list[str],
    region: Region,
    frame: np.ndarray,
    config: ServoConfig,
    ocr_fn: OcrFn | None,
) -> tuple[UIElement | None, dict[str, Any]]:
    template_hit, template_meta = _template_estimate(
        template,
        frame,
        region,
        initial,
        config.min_template_confidence,
    )
    # A strong local template is the cheap inner-loop path. OCR only runs when
    # the template is missing, changed by hover styling, or marginal.
    template_confidence = float(template_meta.get("confidence") or 0.0)
    ocr_threshold = min(0.94, max(0.78, config.min_template_confidence + 0.03))
    needs_ocr = template_hit is None or template_confidence < ocr_threshold
    ocr_hit, ocr_meta = _ocr_estimate(
        frame,
        region,
        labels,
        current,
        ocr_fn if needs_ocr else None,
    )
    if template_hit is not None and ocr_hit is not None:
        distance = (
            (int(template_hit.cx) - int(ocr_hit.cx)) ** 2
            + (int(template_hit.cy) - int(ocr_hit.cy)) ** 2
        ) ** 0.5
        # OCR centers the label while the template centers the whole control.
        # Agreement makes the template geometry semantically grounded.
        if distance <= max(36, int(template_hit.width * 0.6)):
            template_hit.source = "servo-template+ocr"
            template_hit.confidence = max(template_hit.confidence, ocr_hit.confidence)
            return template_hit, {
                "source": template_hit.source,
                "template": template_meta,
                "ocr": ocr_meta,
                "agreement_px": round(distance, 1),
            }
        return ocr_hit, {
            "source": "servo-ocr",
            "template": template_meta,
            "ocr": ocr_meta,
            "agreement_px": round(distance, 1),
            "reason": "semantic_ocr_overrode_disagreeing_template",
        }
    if template_hit is not None:
        return template_hit, {
            "source": "servo-template",
            "template": template_meta,
            "ocr": ocr_meta,
        }
    if ocr_hit is not None:
        return ocr_hit, {
            "source": "servo-ocr",
            "template": template_meta,
            "ocr": ocr_meta,
        }
    return None, {
        "source": "none",
        "template": template_meta,
        "ocr": ocr_meta,
    }


def track_visual_target(
    initial: UIElement,
    labels: list[str],
    *,
    capture_fn: CaptureFn,
    move_fn: MoveFn,
    ocr_fn: OcrFn | None = None,
    config: ServoConfig | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> tuple[UIElement | None, dict[str, Any]]:
    """Track, approach, and confirm a target immediately before mouse-down."""
    config = config or ServoConfig()
    started = _now_ms()
    template_region = _element_region(initial)
    try:
        template = capture_fn(template_region)
    except Exception:
        template = None
    current = initial
    corrections = 0
    iterations: list[dict[str, Any]] = []
    source_counts: dict[str, int] = {}
    lost_reason = ""
    stable_streak = 0

    for index in range(config.iterations):
        move_fn(int(current.cx), int(current.cy))
        sleep_fn(config.settle_ms / 1000.0)
        region = _search_region(current, config, expansion=1.0 if index else 1.2)
        capture_started = _now_ms()
        try:
            frame = capture_fn(region)
        except Exception as exc:
            lost_reason = f"capture_failed: {exc}"
            iterations.append({"index": index + 1, "found": False, "error": str(exc)})
            break
        capture_ms = max(0, _now_ms() - capture_started)
        estimate_started = _now_ms()
        estimated, evidence = _estimate(
            template, initial, current, labels, region, frame, config, ocr_fn,
        )
        estimate_ms = max(0, _now_ms() - estimate_started)
        if estimated is None:
            lost_reason = "target_not_visible_in_local_roi"
            iterations.append({
                "index": index + 1,
                "found": False,
                "region": [region.left, region.top, region.width, region.height],
                "capture_ms": capture_ms,
                "estimate_ms": estimate_ms,
                "evidence": evidence,
            })
            break
        source = str(evidence.get("source") or estimated.source or "unknown")
        source_counts[source] = source_counts.get(source, 0) + 1
        dx = int(estimated.cx) - int(current.cx)
        dy = int(estimated.cy) - int(current.cy)
        corrected = abs(dx) > config.tolerance_px or abs(dy) > config.tolerance_px
        if corrected:
            corrections += 1
            stable_streak = 0
        else:
            stable_streak += 1
        iterations.append({
            "index": index + 1,
            "found": True,
            "from": {"x": int(current.cx), "y": int(current.cy)},
            "to": {"x": int(estimated.cx), "y": int(estimated.cy)},
            "correction": {"dx": dx, "dy": dy},
            "corrected": corrected,
            "stable_streak": stable_streak,
            "region": [region.left, region.top, region.width, region.height],
            "capture_ms": capture_ms,
            "estimate_ms": estimate_ms,
            "evidence": evidence,
        })
        current = estimated
        # Hover-triggered movement can begin one compositor frame after the
        # cursor arrives. Requiring consecutive stable estimates prevents the
        # first stale frame from ending the control loop prematurely.
        if stable_streak >= 2:
            break

    # Atomic pre-click phase: place the cursor on the latest estimate, then
    # confirm once more. The caller can now press without another 50ms move.
    confirmed = False
    final_confirmation: dict[str, Any] = {}
    if not lost_reason:
        move_fn(int(current.cx), int(current.cy))
        sleep_fn(max(0.005, config.settle_ms / 2000.0))
        region = _search_region(current, config, expansion=0.75)
        capture_started = _now_ms()
        try:
            frame = capture_fn(region)
            capture_ms = max(0, _now_ms() - capture_started)
            estimated, evidence = _estimate(
                template, initial, current, labels, region, frame, config, ocr_fn,
            )
            if estimated is None:
                lost_reason = "final_confirmation_lost_target"
                final_confirmation = {
                    "found": False,
                    "capture_ms": capture_ms,
                    "evidence": evidence,
                }
            else:
                dx = int(estimated.cx) - int(current.cx)
                dy = int(estimated.cy) - int(current.cy)
                stable = abs(dx) <= config.tolerance_px and abs(dy) <= config.tolerance_px
                final_confirmation = {
                    "found": True,
                    "stable": stable,
                    "capture_ms": capture_ms,
                    "correction": {"dx": dx, "dy": dy},
                    "evidence": evidence,
                }
                current = estimated
                if stable:
                    confirmed = True
                else:
                    corrections += 1
                    move_fn(int(current.cx), int(current.cy))
                    sleep_fn(max(0.005, config.settle_ms / 2000.0))
                    # The final correction is accepted only after a second
                    # local estimate proves that the target stopped moving.
                    region2 = _search_region(current, config, expansion=0.6)
                    frame2 = capture_fn(region2)
                    checked, evidence2 = _estimate(
                        template, initial, current, labels, region2, frame2, config, ocr_fn,
                    )
                    if checked is not None:
                        dx2 = int(checked.cx) - int(current.cx)
                        dy2 = int(checked.cy) - int(current.cy)
                        confirmed = abs(dx2) <= config.tolerance_px and abs(dy2) <= config.tolerance_px
                        current = checked
                        final_confirmation["second_check"] = {
                            "found": True,
                            "stable": confirmed,
                            "correction": {"dx": dx2, "dy": dy2},
                            "evidence": evidence2,
                        }
                    else:
                        final_confirmation["second_check"] = {
                            "found": False,
                            "evidence": evidence2,
                        }
                    if not confirmed:
                        lost_reason = "target_still_moving_at_click_deadline"
        except Exception as exc:
            lost_reason = f"final_confirmation_failed: {exc}"
            final_confirmation = {"found": False, "error": str(exc)}

    trace = {
        "enabled": True,
        "confirmed": confirmed,
        "strict": config.strict,
        "initial": {"x": int(initial.cx), "y": int(initial.cy)},
        "final": {"x": int(current.cx), "y": int(current.cy)},
        "corrected": corrections > 0,
        "correction_count": corrections,
        "iterations": iterations,
        "final_confirmation": final_confirmation,
        "source_counts": source_counts,
        "lost_reason": lost_reason,
        "elapsed_ms": max(0, _now_ms() - started),
    }
    if confirmed or not config.strict:
        return current, trace
    return None, trace
