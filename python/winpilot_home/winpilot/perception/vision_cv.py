"""OpenCV dimension: what OCR cannot see.

- Icon-candidate detection: contour analysis finds small square-ish UI glyphs
  (toolbar icons, title-bar buttons) and reports their coordinates.
- Template matching: locate a known image (icon screenshot in templates_img/)
  on screen with multi-scale matching.
- Color probe: dominant/average color of a point or region (button states,
  progress bars, theme changes).
- Stability / loading detection: frame differencing tells whether a region is
  still animating (spinner, video, progress) or has settled.
"""
from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np

from ..config import TEMPLATES_IMG_DIR
from ..utils.log import logger
from ..utils.screenshot import Region, capture, virtual_screen_region
from .model import UIElement

_ICON_MIN, _ICON_MAX = 12, 64          # icon side length range (px)
_ICON_AR_TOL = 0.45                    # |1 - w/h| tolerance for square-ish shapes


def _detect_control_candidates_from_image(
    image: np.ndarray,
    region: Region,
    max_items: int = 120,
) -> list[UIElement]:
    """Find rectangular controls without assigning semantic labels.

    This is intentionally separate from the default icon stream. It is used
    on demand when a short OCR label (for example a keypad digit or operator)
    was not detected, so a dense control grid does not pollute normal prompts.
    """
    if image is None or image.size == 0:
        return []
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 20, 80)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    image_area = max(1, int(image.shape[0] * image.shape[1]))
    candidates: list[UIElement] = []
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        if not (24 <= width <= min(640, max(24, region.width - 4))):
            continue
        if not (18 <= height <= min(180, max(18, region.height - 4))):
            continue
        aspect = width / max(1, height)
        if not 0.35 <= aspect <= 12.0:
            continue
        rect_area = width * height
        if rect_area > image_area * 0.22:
            continue
        fill = float(cv2.contourArea(contour)) / max(1, rect_area)
        if fill < 0.68:
            continue
        candidates.append(UIElement(
            source="cv-control",
            role="button",
            text="",
            cx=region.left + x + width // 2,
            cy=region.top + y + height // 2,
            left=region.left + x,
            top=region.top + y,
            width=width,
            height=height,
            confidence=min(0.9, 0.45 + fill * 0.35),
        ))

    candidates.sort(key=lambda item: (item.top, item.left, -(item.width * item.height)))
    kept: list[UIElement] = []
    for candidate in candidates:
        duplicate = any(
            candidate.iou(existing) >= 0.82
            or (
                abs(candidate.cx - existing.cx) <= 3
                and abs(candidate.cy - existing.cy) <= 3
                and abs(candidate.width - existing.width) <= 5
                and abs(candidate.height - existing.height) <= 5
            )
            for existing in kept
        )
        if duplicate:
            continue
        kept.append(candidate)
        if len(kept) >= max_items:
            break
    return kept


def detect_control_candidates(region: Region, max_items: int = 120) -> list[UIElement]:
    """Capture ``region`` and return unlabeled rectangular control candidates."""
    try:
        region = region.clamp_to_screen()
        return _detect_control_candidates_from_image(capture(region), region, max_items=max_items)
    except Exception as exc:
        logger.warning("CV control detection failed: %s", exc)
        return []


def detect_icon_candidates(region: Region, max_items: int = 40) -> list[UIElement]:
    """Find small square-ish glyph regions that OCR would miss. Never raises."""
    try:
        region = region.clamp_to_screen()
        image = capture(region)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 60, 160)
        edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        candidates: list[UIElement] = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if not (_ICON_MIN <= w <= _ICON_MAX and _ICON_MIN <= h <= _ICON_MAX):
                continue
            if abs(1 - w / h) > _ICON_AR_TOL:
                continue
            # require some ink density so we skip hollow noise boxes
            roi = edges[y : y + h, x : x + w]
            if cv2.countNonZero(roi) < w * h * 0.08:
                continue
            candidates.append(
                UIElement(
                    source="cv",
                    role="icon",
                    text="",
                    cx=region.left + x + w // 2,
                    cy=region.top + y + h // 2,
                    left=region.left + x,
                    top=region.top + y,
                    width=w,
                    height=h,
                    confidence=0.5,
                )
            )
        # de-overlap: keep larger ones first
        candidates.sort(key=lambda e: e.width * e.height, reverse=True)
        kept: list[UIElement] = []
        for cand in candidates:
            if all(cand.iou(k) < 0.3 for k in kept):
                kept.append(cand)
            if len(kept) >= max_items:
                break
        return kept
    except Exception as exc:
        logger.warning("CV 图标检测失败: %s", exc)
        return []


def list_templates() -> list[str]:
    """Names of available image templates (templates_img/*.png)."""
    try:
        return sorted(p.stem for p in TEMPLATES_IMG_DIR.glob("*.png"))
    except OSError:
        return []


def save_template(region: Region, name: str) -> Path:
    """Capture a screen region into templates_img/<name>.png for find_image.

    This is how the icon library gets built up over time — the agent (or the
    user) snapshots an icon once, then template matching can find it later.
    """
    safe = "".join(c for c in name if c.isalnum() or c in "-_")[:80]
    if not safe:
        raise ValueError("模板名无效（去除特殊字符后为空），请用中英文/数字命名")
    TEMPLATES_IMG_DIR.mkdir(exist_ok=True)
    path = TEMPLATES_IMG_DIR / f"{safe}.png"
    image = capture(region.clamp_to_screen())
    ok, buf = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError("PNG 编码失败")
    buf.tofile(str(path))  # tofile handles non-ASCII paths on Windows
    logger.info("已保存模板: %s (%dx%d)", path.name, region.width, region.height)
    return path


def save_template_image(image: np.ndarray, name: str) -> Path:
    """Persist an already captured BGR crop as a reusable template."""
    safe = "".join(c for c in name if c.isalnum() or c in "-_")[:80]
    if not safe:
        raise ValueError("template name is empty after sanitization")
    if image is None or image.ndim != 3 or image.shape[0] < 8 or image.shape[1] < 8:
        raise ValueError("template image must be a BGR crop of at least 8x8 pixels")
    TEMPLATES_IMG_DIR.mkdir(exist_ok=True)
    path = TEMPLATES_IMG_DIR / f"{safe}.png"
    ok, buf = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError("PNG encoding failed")
    buf.tofile(str(path))
    return path


def match_template(
    template_name: str,
    region: Region | None = None,
    threshold: float = 0.80,
    max_matches: int = 5,
) -> list[UIElement]:
    """Multi-scale template matching against templates_img/<name>.png."""
    path = Path(template_name)
    if not path.is_absolute():
        path = TEMPLATES_IMG_DIR / template_name
    if path.suffix == "":
        path = path.with_suffix(".png")
    if not path.exists():
        logger.warning("模板不存在: %s", path)
        return []
    template = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if template is None:
        return []

    if region is None:
        region = virtual_screen_region()
    region = region.clamp_to_screen()
    haystack = capture(region)

    results: list[UIElement] = []
    for scale in (1.0, 0.85, 1.15, 0.7, 1.3):
        th, tw = int(template.shape[0] * scale), int(template.shape[1] * scale)
        if th < 8 or tw < 8 or th >= haystack.shape[0] or tw >= haystack.shape[1]:
            continue
        scaled = cv2.resize(template, (tw, th), interpolation=cv2.INTER_AREA)
        res = cv2.matchTemplate(haystack, scaled, cv2.TM_CCOEFF_NORMED)
        loc = np.where(res >= threshold)
        for y, x in zip(*loc):
            results.append(
                UIElement(
                    source="cv",
                    role="image",
                    text=path.stem,
                    cx=region.left + x + tw // 2,
                    cy=region.top + y + th // 2,
                    left=region.left + int(x),
                    top=region.top + int(y),
                    width=tw,
                    height=th,
                    confidence=float(res[y, x]),
                )
            )
        if results:
            break  # best scale found; don't mix scales
    results.sort(key=lambda e: e.confidence, reverse=True)
    deduped: list[UIElement] = []
    for item in results:
        if all(item.iou(k) < 0.3 for k in deduped):
            deduped.append(item)
        if len(deduped) >= max_matches:
            break
    return deduped


def color_probe(x: int, y: int, radius: int = 4) -> dict:
    """Average + dominant color around a point. Returns hex strings."""
    region = Region(x - radius, y - radius, radius * 2 + 1, radius * 2 + 1).clamp_to_screen()
    image = capture(region)
    bgr_mean = image.reshape(-1, 3).mean(axis=0)
    avg = tuple(int(c) for c in bgr_mean[::-1])  # RGB
    pixels = image.reshape(-1, 3)
    # dominant: most frequent quantized color
    quantized = (pixels // 32 * 32)
    colors, counts = np.unique(quantized, axis=0, return_counts=True)
    dom_bgr = colors[counts.argmax()]
    dom = tuple(int(c) for c in dom_bgr[::-1])
    return {
        "average_rgb": avg,
        "average_hex": "#%02x%02x%02x" % avg,
        "dominant_rgb": dom,
        "dominant_hex": "#%02x%02x%02x" % dom,
    }


def region_mean_color(region: Region) -> tuple[int, int, int]:
    image = capture(region.clamp_to_screen())
    bgr = image.reshape(-1, 3).mean(axis=0)
    return tuple(int(c) for c in bgr[::-1])


def diff_ratio(img_a: np.ndarray, img_b: np.ndarray, pixel_thresh: int = 12) -> float:
    """Fraction of pixels that changed between two same-size BGR frames."""
    if img_a.shape != img_b.shape:
        return 1.0
    delta = cv2.absdiff(img_a, img_b)
    changed = (delta.max(axis=2) > pixel_thresh).sum()
    total = img_a.shape[0] * img_a.shape[1]
    return changed / total if total else 0.0


def wait_screen_stable(
    region: Region,
    stable_ms: int = 600,
    timeout_s: float = 15.0,
    max_change: float = 0.002,
) -> dict:
    """Block until the region stops changing (loading finished) or timeout.

    Returns {"stable": bool, "waited_s": float}.
    """
    region = region.clamp_to_screen()
    deadline = time.monotonic() + timeout_s
    started = time.monotonic()
    prev = capture(region)
    stable_since: float | None = None
    while time.monotonic() < deadline:
        time.sleep(0.12)
        current = capture(region)
        if diff_ratio(prev, current) <= max_change:
            if stable_since is None:
                stable_since = time.monotonic()
            elif (time.monotonic() - stable_since) * 1000 >= stable_ms:
                return {"stable": True, "waited_s": round(time.monotonic() - started, 2)}
        else:
            stable_since = None
        prev = current
    return {"stable": False, "waited_s": round(time.monotonic() - started, 2)}


def is_animating(region: Region, sample_s: float = 0.8) -> bool:
    """Quick check: is anything moving in the region (spinner/progress)?"""
    region = region.clamp_to_screen()
    first = capture(region)
    time.sleep(sample_s / 2)
    second = capture(region)
    time.sleep(sample_s / 2)
    third = capture(region)
    return diff_ratio(first, second) > 0.001 or diff_ratio(second, third) > 0.001


def image_ink_stats_from_array(image: np.ndarray, path: str = "") -> dict:
    """Lightweight non-visual summary for an image array.

    This deliberately does not claim semantic understanding. It only tells a
    text-only agent/judge whether an image is blank-ish and where visible ink is.
    """
    if image is None:
        return {"exists": bool(path), "path": path, "readable": False}
    h, w = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    # Treat near-white as background; this works for common canvas/image tasks.
    mask = gray < 245
    ink_pixels = int(mask.sum())
    total = int(mask.size)
    stats: dict = {
        "exists": True,
        "readable": True,
        "width": int(w),
        "height": int(h),
        "ink_pixels": ink_pixels,
        "ink_ratio": round(ink_pixels / total, 6) if total else 0.0,
    }
    if path:
        stats["path"] = path
    if ink_pixels == 0:
        stats.update({"blankish": True, "component_count": 0})
        return stats
    ys, xs = np.where(mask)
    bbox = [int(xs.min()), int(ys.min()), int(xs.max() + 1), int(ys.max() + 1)]
    stats["ink_bbox"] = bbox
    num_labels, labels, comp_stats, _centroids = cv2.connectedComponentsWithStats(
        mask.astype("uint8"), connectivity=8
    )
    comps: list[dict] = []
    min_area = max(12, int(total * 0.00001))
    for label in range(1, num_labels):
        x, y, cw, ch, area = comp_stats[label].tolist()
        if area < min_area:
            continue
        comps.append({
            "bbox": [int(x), int(y), int(x + cw), int(y + ch)],
            "area": int(area),
        })
    comps.sort(key=lambda item: item["area"], reverse=True)
    stats["blankish"] = ink_pixels < max(50, total * 0.0001)
    stats["component_count"] = len(comps)
    stats["largest_components"] = comps[:8]
    return stats


def image_ink_stats(path: str) -> dict:
    """Lightweight non-visual summary for saved drawings/screenshots."""
    p = Path(path)
    if not p.exists():
        return {"exists": False, "path": str(p)}
    image = cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_COLOR)
    return image_ink_stats_from_array(image, str(p))


def coarse_color_masks(image: np.ndarray) -> dict[str, np.ndarray]:
    """Return the shared coarse UI/drawing color vocabulary.

    Palette location and post-action evidence must use identical thresholds.
    Pale pink is deliberately separated from red, while neutral colors are
    separated by value instead of treating every dark UI pixel as black.
    """
    if image is None or getattr(image, "size", 0) == 0:
        return {}
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    chroma = (sat > 70) & (val > 50)
    return {
        "red": (sat >= 145) & (val > 70) & ((hue <= 9) | (hue >= 172)),
        "orange": chroma & (val >= 210) & (hue > 9) & (hue <= 24),
        "brown": chroma & (val >= 70) & (val < 210) & (hue > 9) & (hue <= 24),
        "yellow": chroma & (hue > 24) & (hue <= 38),
        "lime": chroma & (val >= 150) & (hue > 38) & (hue <= 58),
        "green": chroma & (hue > 38) & (hue <= 88),
        "cyan": chroma & (hue > 82) & (hue < 105),
        "blue": chroma & (hue >= 95) & (hue <= 135),
        "purple": chroma & (hue > 135) & (hue < 165),
        "magenta": (sat >= 90) & (val > 80) & (hue >= 145) & (hue < 172),
        "pink": (sat >= 35) & (sat < 145) & (val >= 175) & ((hue >= 155) | (hue <= 8)),
        "black": val < 65,
        "gray": (sat < 35) & (val >= 65) & (val < 225),
        "white": (sat < 35) & (val >= 225),
    }


def image_color_stats(path: str) -> dict:
    """Count coarse color evidence in a saved image.

    This is intentionally low-level. It does not say what was drawn; it only
    reports whether requested colors have visible pixels.
    """
    p = Path(path)
    if not p.exists():
        return {"exists": False, "path": str(p)}
    image = cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return {"exists": True, "path": str(p), "readable": False}
    h, w = image.shape[:2]
    masks = coarse_color_masks(image)
    total = int(h * w)
    colors: dict[str, dict[str, float | int | bool]] = {}
    for name, mask in masks.items():
        pixels = int(mask.sum())
        colors[name] = {
            "pixels": pixels,
            "ratio": round(pixels / total, 6) if total else 0.0,
            "visible": pixels >= max(30, int(total * 0.000005)),
        }
    return {
        "exists": True,
        "readable": True,
        "path": str(p),
        "width": int(w),
        "height": int(h),
        "colors": colors,
    }


def color_presence(image: np.ndarray) -> dict:
    """Coarse visible-color summary for a screen/window image.

    Returns only compact facts: per-color visibility, pixel count, and bounding
    box. This is meant for text-only agents that need to know whether their
    requested colored action actually appeared on screen.
    """
    if image is None or getattr(image, "size", 0) == 0:
        return {"readable": False, "colors": {}}
    h, w = image.shape[:2]
    masks = coarse_color_masks(image)
    total = int(h * w)
    threshold = max(30, int(total * 0.00002))
    colors: dict[str, dict] = {}
    for name, mask in masks.items():
        pixels = int(mask.sum())
        item: dict[str, object] = {
            "pixels": pixels,
            "ratio": round(pixels / total, 6) if total else 0.0,
            "visible": pixels >= threshold,
        }
        if pixels >= threshold:
            ys, xs = np.where(mask)
            item["bbox"] = [int(xs.min()), int(ys.min()), int(xs.max() + 1), int(ys.max() + 1)]
            num_labels, _labels, comp_stats, _centroids = cv2.connectedComponentsWithStats(
                mask.astype("uint8"), connectivity=8
            )
            components: list[dict] = []
            for label in range(1, num_labels):
                x, y, cw, ch, area = comp_stats[label].tolist()
                if int(area) < threshold:
                    continue
                components.append({
                    "bbox": [int(x), int(y), int(x + cw), int(y + ch)],
                    "area": int(area),
                })
            components.sort(key=lambda entry: int(entry["area"]), reverse=True)
            if components:
                item["largest_bbox"] = components[0]["bbox"]
                item["largest_area"] = components[0]["area"]
        colors[name] = item
    return {"readable": True, "width": int(w), "height": int(h), "colors": colors}


def likely_canvas_bbox(image: np.ndarray) -> list[int] | None:
    """Find a large light work surface inside a window screenshot.

    This is a generic heuristic for drawing/editor tasks: pick the largest
    near-white connected region. It deliberately avoids app names and gives
    text-only agents a better visual summary target than the whole toolbar.
    """
    if image is None or getattr(image, "size", 0) == 0:
        return None
    h, w = image.shape[:2]
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    sat = hsv[:, :, 1]
    mask = (gray > 235) & (sat < 45)
    num_labels, _labels, stats, _centroids = cv2.connectedComponentsWithStats(
        mask.astype("uint8"), connectivity=8
    )
    min_area = max(20_000, int(h * w * 0.08))
    best: tuple[int, list[int]] | None = None
    for label in range(1, num_labels):
        x, y, cw, ch, area = stats[label].tolist()
        if area < min_area or cw < max(120, w * 0.25) or ch < max(80, h * 0.18):
            continue
        bbox = [int(x), int(y), int(x + cw), int(y + ch)]
        if best is None or int(area) > best[0]:
            best = (int(area), bbox)
    return best[1] if best else None
