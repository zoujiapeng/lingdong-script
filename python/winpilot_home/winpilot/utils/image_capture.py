"""Small image capture helpers shared by agent tools and the loop."""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from typing import Callable

import cv2
import numpy as np
import win32gui

from ..config import CONFIG
from ..perception.surface_binding import InteractionRegion
from . import screenshot


_CROSS_APP_ENTITY = (
    r"(?:记事本|写字板|画图|画板|浏览器|文件资源管理器|资源管理器|微信|企业微信|"
    r"QQ|钉钉|飞书|Word|Excel|PowerPoint|WPS|Chrome|Edge|Firefox|"
    r"窗口|应用|程序)"
)


_DESKTOP_CONTEXT_RE = re.compile(
    r"\b(?:full[- ]?screen|whole desktop|entire desktop|desktop icon|taskbar|"
    r"system tray|notification center|start menu|multiple windows?|both windows?|"
    r"between windows?|across (?:apps?|windows?)|split[- ]?screen|"
    r"second monitor|other monitor|another monitor|left app|right app)\b|"
    r"\b(?:copy|move|drag|transfer|paste)\b.{0,80}\bfrom\b.{1,80}"
    r"\b(?:to|into)\b|"
    r"\b(?:copy|move|drag|transfer|paste)\b.{0,80}\b(?:to|into)\b.{1,80}"
    r"\bfrom\b|"
    r"\bfrom\b.{1,80}\b(?:copy|move|drag|transfer|paste)\b.{0,80}"
    r"\b(?:to|into)\b|"
    r"(?:全屏|整个桌面|桌面图标|任务栏|系统托盘|通知中心|开始菜单|多个窗口|多窗口|"
    r"两个窗口|两窗口|窗口之间|跨应用|跨窗口|分屏|第二显示器|另一显示器|"
    r"其他显示器|左侧应用|右侧应用)|"
    r"(?:复制|移动|拖动|拖拽|传输|粘贴).{0,40}从.{1,40}(?:到|至|进)|"
    r"从.{1,40}(?:复制|移动|拖动|拖拽|传输|粘贴).{0,40}(?:到|至|进)|"
    rf"把.{{0,20}}{_CROSS_APP_ENTITY}.{{0,30}}(?:复制|移动|拖动|拖拽|传输|粘贴)"
    rf".{{0,30}}(?:到|至|进).{{0,20}}{_CROSS_APP_ENTITY}|"
    rf"(?:复制|移动|拖动|拖拽|传输|粘贴).{{0,20}}{_CROSS_APP_ENTITY}"
    rf".{{0,30}}(?:到|至|进).{{0,20}}{_CROSS_APP_ENTITY}",
    re.IGNORECASE,
)

# Multi-window observation is broader than copy/move workflows. Keep this
# separate from the legacy transfer grammar so ordinary wording such as
# "compare both apps" or "look at the left and right windows" cannot silently
# discard half of the evidence when the remembered target happens to be front.
_MULTI_VIEW_CONTEXT_RE = re.compile(
    r"\b(?:compare|comparison|contrast|side[- ]by[- ]side|"
    r"both\s+(?:apps?|applications?|windows?|documents?|pages?)|"
    r"(?:two|multiple)\s+(?:apps?|applications?|windows?|documents?|pages?)|"
    r"left\s+(?:app|application|window|pane|document|page).{0,50}"
    r"right\s+(?:app|application|window|pane|document|page)|"
    r"right\s+(?:app|application|window|pane|document|page).{0,50}"
    r"left\s+(?:app|application|window|pane|document|page))\b|"
    r"(?:同时|一起|一同|并排|分别).{0,20}(?:查看|观察|对照|比较|对比).{0,30}"
    r"(?:两个|多个|应用|程序|窗口|文档|页面)|"
    r"(?:比较|对比|对照).{0,50}(?:应用|程序|窗口|文档|页面|记事本|Word|WPS|浏览器)|"
    r"(?:左边|左侧|左面).{0,50}(?:右边|右侧|右面)|"
    r"(?:右边|右侧|右面).{0,50}(?:左边|左侧|左面)|"
    r"(?:两个|多个).{0,12}(?:应用|程序|窗口|文档|页面)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class VisualCaptureScope:
    """One model-visible frame and its physical screen coordinate system."""

    region: screenshot.Region
    scope: str
    reason: str
    target_hwnd: int | None = None
    target_verified: bool = False
    client_region: screenshot.Region | None = None

    def metadata(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "scope": self.scope,
            "reason": self.reason,
            "target_hwnd": self.target_hwnd,
            "target_verified": self.target_verified,
            "coordinate_space": "physical_screen_pixels",
            "normalized_coordinate_space": "frame_0_1000",
            "screen_region": {
                "left": self.region.left,
                "top": self.region.top,
                "width": self.region.width,
                "height": self.region.height,
            },
        }
        if self.client_region is not None:
            payload["window_client_region"] = {
                "left": self.client_region.left,
                "top": self.client_region.top,
                "width": self.client_region.width,
                "height": self.client_region.height,
            }
            payload["window_client_frame_bounds"] = region_frame_bounds(
                self.client_region, self.region,
            )
        return payload


def window_client_region(hwnd: int) -> screenshot.Region | None:
    """Return the Win32 client rectangle in physical screen pixels.

    The result is an authority envelope for later visual grounding, not an
    automatic action surface: client areas commonly contain toolbars and
    navigation panes.  Failure stays explicit as ``None`` instead of silently
    substituting the whole window.
    """
    try:
        left, top, right, bottom = win32gui.GetClientRect(int(hwnd))
        screen_left, screen_top = win32gui.ClientToScreen(
            int(hwnd), (int(left), int(top)),
        )
        screen_right, screen_bottom = win32gui.ClientToScreen(
            int(hwnd), (int(right), int(bottom)),
        )
        width = int(screen_right) - int(screen_left)
        height = int(screen_bottom) - int(screen_top)
        if width <= 0 or height <= 0:
            return None
        return screenshot.Region(
            int(screen_left), int(screen_top), width, height,
        ).clamp_to_screen()
    except Exception:
        return None


def region_frame_bounds(
    region: screenshot.Region,
    frame_region: screenshot.Region,
) -> list[int] | None:
    """Map a physical region to clipped inclusive 0..1000 frame bounds."""
    left = max(region.left, frame_region.left)
    top = max(region.top, frame_region.top)
    right = min(region.right, frame_region.right)
    bottom = min(region.bottom, frame_region.bottom)
    if right <= left or bottom <= top:
        return None
    x_span = max(1, frame_region.width - 1)
    y_span = max(1, frame_region.height - 1)
    return [
        int(round((left - frame_region.left) * 1000.0 / x_span)),
        int(round((top - frame_region.top) * 1000.0 / y_span)),
        int(round((right - frame_region.left - 1) * 1000.0 / x_span)),
        int(round((bottom - frame_region.top - 1) * 1000.0 / y_span)),
    ]


def select_visual_capture_scope(
    hwnd: int | None,
    *,
    task: str = "",
    enabled: bool | None = None,
    related_window: Callable[[int | None, int | None], bool] | None = None,
) -> VisualCaptureScope:
    """Choose a target-window crop only when it is safe and useful.

    Disabling the optimization always returns the whole virtual desktop.  In
    auto mode a remembered hwnd is not sufficient on its own: the window must
    still exist, be visible, be the foreground operation surface, have sane
    geometry, and the task must not explicitly require desktop/multi-window
    context.  Every failed check falls back to the full desktop rather than
    returning a misleading crop.
    """
    desktop = screenshot.virtual_screen_region()

    def full(reason: str, *, target: int | None = None) -> VisualCaptureScope:
        return VisualCaptureScope(
            region=desktop,
            scope="full_desktop",
            reason=reason,
            target_hwnd=target,
            target_verified=False,
        )

    if enabled is None:
        enabled = bool(CONFIG.get(
            "controller", "target_window_capture_enabled", default=True,
        ))
    if not enabled:
        return full("optimization_disabled")
    task_text = str(task or "")
    if (
        _DESKTOP_CONTEXT_RE.search(task_text)
        or _MULTI_VIEW_CONTEXT_RE.search(task_text)
    ):
        return full("task_requires_desktop_context", target=int(hwnd or 0) or None)
    try:
        target = int(hwnd or 0)
    except (TypeError, ValueError):
        target = 0
    if not target:
        return full("no_target_window")
    try:
        if not win32gui.IsWindow(target):
            return full("target_window_invalid", target=target)
        if not win32gui.IsWindowVisible(target) or win32gui.IsIconic(target):
            return full("target_window_not_visible", target=target)
        foreground = int(win32gui.GetForegroundWindow() or 0)
    except Exception:
        return full("target_window_validation_failed", target=target)
    if foreground != target:
        try:
            related = bool(
                foreground
                and related_window is not None
                and related_window(foreground, target)
            )
        except Exception:
            related = False
        return full(
            "related_foreground_requires_desktop_context"
            if related else "foreground_target_mismatch",
            target=target,
        )
    try:
        raw_region = screenshot.window_region(target)
        region = raw_region.clamp_to_screen()
    except Exception:
        return full("target_window_geometry_unavailable", target=target)
    if region.width < 160 or region.height < 120:
        return full("target_window_too_small", target=target)
    raw_area = max(1, raw_region.width * raw_region.height)
    visible_ratio = (region.width * region.height) / raw_area
    if visible_ratio < 0.90:
        return full("target_window_mostly_offscreen", target=target)
    desktop_area = max(1, desktop.width * desktop.height)
    if (region.width * region.height) / desktop_area >= 0.92:
        return full("target_window_crop_has_no_material_saving", target=target)
    return VisualCaptureScope(
        region=region,
        scope="target_window",
        reason="verified_foreground_target",
        target_hwnd=target,
        target_verified=True,
        client_region=window_client_region(target),
    )


def capture_window_jpeg_b64(hwnd: int, max_side: int = 1120,
                            quality: int = 75,
                            region: screenshot.Region | None = None) -> str:
    """Return an explicit region, or the target window, as resized JPEG data.

    The helper never raises; callers can treat an empty string as "no image".
    """
    try:
        region = (region or screenshot.window_region(hwnd)).clamp_to_screen()
        img = screenshot.capture(region)
        h, w = img.shape[:2]
        if max(h, w) > max_side:
            scale = max_side / max(h, w)
            img = cv2.resize(img, (int(w * scale), int(h * scale)))
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            return ""
        return base64.b64encode(buf.tobytes()).decode()
    except Exception:
        return ""


_CONTINUOUS_INPUT_EDGE_INSET_FRAME_UNITS = 24


def credible_work_surface_frame_bounds(
    image_b64: str,
    *,
    client_frame_bounds: tuple[int, int, int, int] | list[int] | None = None,
) -> dict[str, object] | None:
    """Locate one light work surface in the exact model-visible JPEG.

    Bounds are inclusive 0..1000 coordinates relative to the authoritative
    frame. This constrains ``frame_strokes`` without adding or silently
    reinterpreting another coordinate system.
    """
    if not image_b64:
        return {
            "ok": False,
            "reason": "authoritative frame pixels were unavailable",
            "failure_kind": "unavailable",
        }
    try:
        raw = np.frombuffer(base64.b64decode(image_b64), dtype=np.uint8)
        image = cv2.imdecode(raw, cv2.IMREAD_COLOR)
        if image is None or image.size == 0:
            return {
                "ok": False,
                "reason": "authoritative frame pixels could not be decoded",
                "failure_kind": "unavailable",
            }
        # Local import avoids a package-initialization cycle: perception CV
        # itself depends on low-level screenshot helpers.
        from ..perception import vision_cv

        detection = vision_cv.credible_light_work_surface(image)
        if not isinstance(detection, dict) or detection.get("ok") is not True:
            reason = (
                str(detection.get("reason") or "no credible light work surface was found")
                if isinstance(detection, dict)
                else "work-surface detector returned no decision"
            )
            ambiguous = bool(
                "nested" in reason.casefold()
                or "modal" in reason.casefold()
                or "multiple" in reason.casefold()
                or "ambiguous" in reason.casefold()
            )
            return {
                "ok": False,
                "reason": reason,
                "failure_kind": "ambiguous" if ambiguous else "not_detected",
                "ambiguous": ambiguous,
                "candidate_count": (
                    detection.get("candidate_count")
                    if isinstance(detection, dict)
                    else None
                ),
                "source": "authoritative_frame_pixels",
            }
        bbox = detection.get("bbox")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            return {
                "ok": False,
                "reason": "detector returned invalid work-surface geometry",
                "failure_kind": "invalid",
                "source": "authoritative_frame_pixels",
            }
        x1, y1, x2, y2 = [int(value) for value in bbox]
        height, width = image.shape[:2]
        if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
            return {
                "ok": False,
                "reason": "detector returned out-of-frame work-surface geometry",
                "failure_kind": "invalid",
                "source": "authoritative_frame_pixels",
            }
        x_span = max(1, width - 1)
        y_span = max(1, height - 1)
        detected_bounds = [
            int(round(x1 * 1000.0 / x_span)),
            int(round(y1 * 1000.0 / y_span)),
            int(round((x2 - 1) * 1000.0 / x_span)),
            int(round((y2 - 1) * 1000.0 / y_span)),
        ]
        detected_region = InteractionRegion(
            tuple(detected_bounds),
            coordinate_space="frame_0_1000",
            label="detected_interaction_surface",
            source=str(detection.get("source") or "authoritative_frame_pixels"),
            confidence=max(0.0, min(1.0, float(detection.get("light_ratio") or 0.0))),
        )
        if client_frame_bounds is not None:
            try:
                client_region = InteractionRegion(
                    tuple(int(value) for value in client_frame_bounds),
                    coordinate_space="frame_0_1000",
                    label="window_client_authority",
                    source="win32_client_rect",
                )
                clipped = detected_region.intersection(client_region)
            except (TypeError, ValueError):
                clipped = None
            if clipped is None:
                return {
                    "ok": False,
                    "reason": "detected work surface was outside the window client region",
                    "failure_kind": "invalid",
                    "source": "authoritative_frame_pixels",
                }
            detected_region = clipped
            detected_bounds = list(clipped.bounds)
        left, top, right, bottom = detected_region.bounds
        inset = _CONTINUOUS_INPUT_EDGE_INSET_FRAME_UNITS
        if right - left > inset * 2 + 40 and bottom - top > inset * 2 + 40:
            safe_region = detected_region.inset(inset)
        else:
            safe_region = detected_region
            inset = 0
        safe_bounds = list(safe_region.bounds)
        return {
            "ok": True,
            # Mutating continuous input uses the inset rectangle. Detectors
            # often include the final antialiased row beside scrollbars or
            # resize handles; starting a drag there scrolls/resizes the view
            # and makes an existing result appear to vanish.
            "work_surface_frame_bounds": safe_bounds,
            "detected_work_surface_frame_bounds": detected_bounds,
            "continuous_input_edge_inset": inset,
            "source": "authoritative_frame_pixels",
            "light_ratio": detection.get("light_ratio"),
            "candidate_count": detection.get("candidate_count"),
            "interaction_region": safe_region.to_dict(),
            "detected_interaction_region": detected_region.to_dict(),
        }
    except Exception:
        return {
            "ok": False,
            "reason": "work-surface detection failed on the authoritative frame",
            "failure_kind": "unavailable",
        }


def jpeg_diff_ratio(image_a_b64: str, image_b_b64: str,
                    pixel_thresh: int = 12) -> float | None:
    """Return the changed-pixel fraction for two encoded JPEG frames."""
    try:
        raw_a = np.frombuffer(base64.b64decode(image_a_b64), dtype=np.uint8)
        raw_b = np.frombuffer(base64.b64decode(image_b_b64), dtype=np.uint8)
        image_a = cv2.imdecode(raw_a, cv2.IMREAD_COLOR)
        image_b = cv2.imdecode(raw_b, cv2.IMREAD_COLOR)
        if image_a is None or image_b is None:
            return None
        if image_a.shape != image_b.shape:
            return 1.0
        delta = cv2.absdiff(image_a, image_b)
        changed = int((delta.max(axis=2) > int(pixel_thresh)).sum())
        total = int(image_a.shape[0] * image_a.shape[1])
        return changed / total if total else None
    except Exception:
        return None


def jpeg_diff_ratio_in_normalized_roi(
    image_a_b64: str,
    image_b_b64: str,
    roi: tuple[float, float, float, float],
    pixel_thresh: int = 12,
) -> float | None:
    """Return the changed-pixel fraction inside one 0..1000 frame ROI.

    The two JPEGs are the exact submitted GUI frames, so normalized
    coordinates remain valid even when capture resized a large window.  A
    shape mismatch is treated as a total change, matching ``jpeg_diff_ratio``.
    """
    try:
        raw_a = np.frombuffer(base64.b64decode(image_a_b64), dtype=np.uint8)
        raw_b = np.frombuffer(base64.b64decode(image_b_b64), dtype=np.uint8)
        image_a = cv2.imdecode(raw_a, cv2.IMREAD_COLOR)
        image_b = cv2.imdecode(raw_b, cv2.IMREAD_COLOR)
        if image_a is None or image_b is None:
            return None
        if image_a.shape != image_b.shape:
            return 1.0
        height, width = image_a.shape[:2]
        left, top, right, bottom = (
            max(0.0, min(1000.0, float(value))) for value in roi
        )
        if right <= left or bottom <= top or width <= 0 or height <= 0:
            return None
        x1 = max(0, min(width - 1, int(np.floor(left / 1000.0 * width))))
        y1 = max(0, min(height - 1, int(np.floor(top / 1000.0 * height))))
        x2 = max(x1 + 1, min(width, int(np.ceil(right / 1000.0 * width))))
        y2 = max(y1 + 1, min(height, int(np.ceil(bottom / 1000.0 * height))))
        delta = cv2.absdiff(image_a[y1:y2, x1:x2], image_b[y1:y2, x1:x2])
        changed = int((delta.max(axis=2) > int(pixel_thresh)).sum())
        total = int(delta.shape[0] * delta.shape[1])
        return changed / total if total else None
    except Exception:
        return None


def jpeg_color_activation_in_normalized_roi(
    image_a_b64: str,
    image_b_b64: str,
    roi: tuple[float, float, float, float],
    *,
    saturation_threshold: int = 72,
    value_threshold: int = 45,
) -> dict[str, float | bool] | None:
    """Measure a local achromatic-to-colour state transition.

    Toggle icons commonly encode an active state by filling a previously gray
    glyph with colour.  This helper is deliberately semantic-free: it inspects
    only the central part of the authoritative action ROI and reports the
    saturated-pixel delta.  Callers still decide whether that evidence should
    protect a recently changed control from an immediate inverse click.
    """
    try:
        raw_a = np.frombuffer(base64.b64decode(image_a_b64), dtype=np.uint8)
        raw_b = np.frombuffer(base64.b64decode(image_b_b64), dtype=np.uint8)
        image_a = cv2.imdecode(raw_a, cv2.IMREAD_COLOR)
        image_b = cv2.imdecode(raw_b, cv2.IMREAD_COLOR)
        if image_a is None or image_b is None or image_a.shape != image_b.shape:
            return None
        height, width = image_a.shape[:2]
        left, top, right, bottom = (
            max(0.0, min(1000.0, float(value))) for value in roi
        )
        if right <= left or bottom <= top or width <= 0 or height <= 0:
            return None
        center_x = (left + right) / 2.0
        center_y = (top + bottom) / 2.0
        half_width = min(30.0, max(12.0, (right - left) / 2.0))
        half_height = min(30.0, max(12.0, (bottom - top) / 2.0))
        crop_left = max(0.0, center_x - half_width)
        crop_top = max(0.0, center_y - half_height)
        crop_right = min(1000.0, center_x + half_width)
        crop_bottom = min(1000.0, center_y + half_height)
        x1 = max(0, min(width - 1, int(np.floor(crop_left / 1000.0 * width))))
        y1 = max(0, min(height - 1, int(np.floor(crop_top / 1000.0 * height))))
        x2 = max(x1 + 1, min(width, int(np.ceil(crop_right / 1000.0 * width))))
        y2 = max(y1 + 1, min(height, int(np.ceil(crop_bottom / 1000.0 * height))))

        def colour_ratio(image: np.ndarray) -> float:
            hsv = cv2.cvtColor(image[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
            coloured = (
                (hsv[:, :, 1] >= int(saturation_threshold))
                & (hsv[:, :, 2] >= int(value_threshold))
            )
            return float(coloured.mean()) if coloured.size else 0.0

        before = colour_ratio(image_a)
        after = colour_ratio(image_b)
        delta = after - before
        return {
            "before_ratio": round(before, 6),
            "after_ratio": round(after, 6),
            "delta": round(delta, 6),
            "activated": bool(after >= 0.01 and delta >= 0.008),
            "roi_left": round(crop_left, 2),
            "roi_top": round(crop_top, 2),
            "roi_right": round(crop_right, 2),
            "roi_bottom": round(crop_bottom, 2),
        }
    except Exception:
        return None


def jpeg_diff_ratios_in_normalized_rois(
    image_a_b64: str,
    image_b_b64: str,
    rois: list[tuple[float, float, float, float]],
    pixel_thresh: int = 12,
) -> list[float] | None:
    """Return changed-pixel fractions for several 0..1000 frame ROIs.

    Gesture preflight needs small regions around the start, end, and sampled
    path points.  Decoding the same JPEG once per region adds avoidable
    latency, so this helper decodes the pair once and evaluates every bounded
    ROI against the identical pixels.  Any invalid ROI fails the comparison
    closed instead of silently weakening the authority check.
    """
    try:
        raw_a = np.frombuffer(base64.b64decode(image_a_b64), dtype=np.uint8)
        raw_b = np.frombuffer(base64.b64decode(image_b_b64), dtype=np.uint8)
        image_a = cv2.imdecode(raw_a, cv2.IMREAD_COLOR)
        image_b = cv2.imdecode(raw_b, cv2.IMREAD_COLOR)
        if image_a is None or image_b is None:
            return None
        if image_a.shape != image_b.shape:
            return [1.0 for _ in rois]
        height, width = image_a.shape[:2]
        if width <= 0 or height <= 0:
            return None
        threshold = int(pixel_thresh)
        ratios: list[float] = []
        for roi in rois:
            if len(roi) != 4:
                return None
            left, top, right, bottom = (
                max(0.0, min(1000.0, float(value))) for value in roi
            )
            if right <= left or bottom <= top:
                return None
            x1 = max(0, min(width - 1, int(np.floor(left / 1000.0 * width))))
            y1 = max(0, min(height - 1, int(np.floor(top / 1000.0 * height))))
            x2 = max(x1 + 1, min(width, int(np.ceil(right / 1000.0 * width))))
            y2 = max(y1 + 1, min(height, int(np.ceil(bottom / 1000.0 * height))))
            delta = cv2.absdiff(image_a[y1:y2, x1:x2], image_b[y1:y2, x1:x2])
            total = int(delta.shape[0] * delta.shape[1])
            if total <= 0:
                return None
            changed = int((delta.max(axis=2) > threshold).sum())
            ratios.append(changed / total)
        return ratios
    except Exception:
        return None
