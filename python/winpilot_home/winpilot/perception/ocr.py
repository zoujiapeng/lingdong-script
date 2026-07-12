"""OCR dimension: RapidOCR (PP-OCR v3) with DirectML GPU acceleration.

RapidOCR 1.2.x builds CPU-only sessions; we rebuild its internal
``ort.InferenceSession`` objects with DmlExecutionProvider (AMD 780M via
DirectML) and gracefully fall back to CPU if that fails. Recognition runs on
a window-region screenshot and bounding boxes are mapped back to absolute
screen coordinates.
"""
from __future__ import annotations

import re
import threading
import time
import unicodedata
from difflib import SequenceMatcher

import cv2
import numpy as np
import onnxruntime as ort

from ..config import CONFIG
from ..utils.log import logger
from ..utils.screenshot import Region, capture
from .model import UIElement

_engine = None
_engine_lock = threading.Lock()
_inference_lock = threading.Lock()
_warm_thread: threading.Thread | None = None
_warm_thread_lock = threading.Lock()
_active_provider = "uninitialized"
_control_cache_lock = threading.Lock()
_control_cache: dict[tuple, tuple[float, list[UIElement]]] = {}


def _match_text(needle: str, text: str) -> bool:
    needle_lower = (needle or "").casefold()
    text_lower = (text or "").casefold()
    if needle_lower in text_lower:
        return True
    norm_needle = re.sub(r"[\s_\-:：]+", "", needle_lower)
    norm_text = re.sub(r"[\s_\-:：]+", "", text_lower)
    if not norm_needle:
        return False
    if norm_needle in norm_text:
        return True
    # Correct digit/letter OCR confusion only for long machine-like identifiers.
    # Matching inside a longer UI line is intentional; short numbers and prose
    # remain exact to avoid false positive completion evidence.
    if (
        len(norm_needle) >= 8
        and any(ch.isdigit() for ch in norm_needle)
        and re.fullmatch(r"[a-z0-9]+", norm_needle)
    ):
        table = str.maketrans({"o": "0", "i": "1", "l": "1"})
        return norm_needle.translate(table) in norm_text.translate(table)
    # Status identifiers are especially prone to one-character OCR mistakes
    # (1/I/T, C/G). Keep this bounded to similarly-sized, non-trivial strings
    # so ordinary prose does not become a fuzzy match.
    if len(norm_needle) >= 8 and 0.65 <= len(norm_text) / len(norm_needle) <= 1.45:
        return SequenceMatcher(None, norm_needle, norm_text).ratio() >= 0.84
    return False


def _rebuild_with_dml(engine) -> bool:
    """Swap CPU InferenceSessions inside RapidOCR for DML ones. True if all swapped."""
    if "DmlExecutionProvider" not in ort.get_available_providers():
        return False
    options = ort.SessionOptions()
    # DirectML EP requirements: no mem pattern, sequential execution.
    options.enable_mem_pattern = False
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    providers = ["DmlExecutionProvider", "CPUExecutionProvider"]

    swapped = 0
    for part_name in ("text_detector", "text_recognizer", "text_cls"):
        part = getattr(engine, part_name, None)
        wrapper = getattr(part, "session", None)
        inner = getattr(wrapper, "session", None)
        if not isinstance(inner, ort.InferenceSession):
            continue
        model_path = getattr(inner, "_model_path", None)
        if not model_path:
            return False
        try:
            wrapper.session = ort.InferenceSession(
                model_path, sess_options=options, providers=providers
            )
            swapped += 1
        except Exception as exc:
            logger.warning("OCR: %s 切换 DML 失败: %s", part_name, exc)
            return False
    return swapped > 0


def get_engine():
    """Lazy-init the shared RapidOCR engine (thread-safe)."""
    global _engine, _active_provider
    with _engine_lock:
        if _engine is not None:
            return _engine
        from rapidocr_onnxruntime import RapidOCR

        started = time.monotonic()
        engine = RapidOCR()
        if CONFIG.get("ocr", "use_gpu", default=True) and _rebuild_with_dml(engine):
            _active_provider = "DmlExecutionProvider"
        else:
            _active_provider = "CPUExecutionProvider"
        # Warm up once so first real call isn't slow.
        try:
            engine(np.full((64, 256, 3), 255, dtype=np.uint8))
        except Exception as exc:
            logger.warning("OCR 预热失败: %s", exc)
        logger.info(
            "OCR 引擎就绪 provider=%s 耗时=%.1fs",
            _active_provider, time.monotonic() - started,
        )
        _engine = engine
        return _engine


def active_provider() -> str:
    return _active_provider


def warm_async() -> bool:
    """Warm OCR in the background so the first GUI action avoids cold start."""
    global _warm_thread
    with _warm_thread_lock:
        if _engine is not None:
            return False
        if _warm_thread is not None and _warm_thread.is_alive():
            return False

        def worker() -> None:
            try:
                get_engine()
            except Exception as exc:
                logger.warning("OCR 后台预热失败: %s", exc)

        _warm_thread = threading.Thread(target=worker, daemon=True, name="ocr-warmup")
        _warm_thread.start()
        return True


def recognize(image: np.ndarray, origin: tuple[int, int] = (0, 0),
              min_conf: float | None = None) -> list[UIElement]:
    """OCR an BGR image; map results to screen coords using `origin` offset.

    ``min_conf=None`` uses the configured threshold; escalation/retry paths
    pass a lower value so faint or small text isn't filtered out when the
    normal pass came back empty.
    """
    engine = get_engine()
    if min_conf is None:
        min_conf = float(CONFIG.get("ocr", "min_confidence", default=0.55))
    ox, oy = origin
    try:
        with _inference_lock:
            result, _elapsed = engine(image)
    except Exception as exc:
        logger.warning("OCR 识别异常: %s", exc)
        return []
    if not result:
        return []
    elements: list[UIElement] = []
    for box, text, score in result:
        score = float(score)
        if score < min_conf or not str(text).strip():
            continue
        xs = [int(p[0]) for p in box]
        ys = [int(p[1]) for p in box]
        left, top = min(xs) + ox, min(ys) + oy
        width, height = max(xs) - min(xs), max(ys) - min(ys)
        elements.append(
            UIElement(
                source="ocr",
                role="text",
                text=str(text).strip(),
                cx=left + width // 2,
                cy=top + height // 2,
                left=left,
                top=top,
                width=width,
                height=height,
                confidence=score,
            )
        )
    return elements


def _control_key(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    text = re.sub(r"\s+", "", text)
    aliases = {
        "−": "-", "–": "-", "—": "-", "一": "-",
        "×": "*", "x": "*", "X": "*", "✕": "*",
        "÷": "/", "／": "/",
        "＝": "=", "Ⅱ": "=",
        "·": ".", "。": ".",
    }
    return aliases.get(text, text).casefold()


def _runs(indices: np.ndarray) -> list[tuple[int, int]]:
    if indices.size == 0:
        return []
    out: list[tuple[int, int]] = []
    start = previous = int(indices[0])
    for raw in indices[1:]:
        value = int(raw)
        if value != previous + 1:
            out.append((start, previous))
            start = value
        previous = value
    out.append((start, previous))
    return out


def _control_glyph_crop(control: np.ndarray) -> tuple[np.ndarray, str]:
    """Tighten a control crop around its contrasting glyph.

    The optional geometry label covers universal line glyphs that OCR models
    commonly confuse (``+``, ``-``, ``=``, and division).
    """
    if control is None or control.size == 0:
        return control, ""
    gray = cv2.cvtColor(control, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape[:2]
    if height < 8 or width < 8:
        return control, ""
    border_parts = [
        gray[2:min(8, height), 2:max(3, width - 2)].ravel(),
        gray[max(0, height - 8):height - 2, 2:max(3, width - 2)].ravel(),
        gray[2:max(3, height - 2), 2:min(8, width)].ravel(),
        gray[2:max(3, height - 2), max(0, width - 8):width - 2].ravel(),
    ]
    border = np.concatenate([part for part in border_parts if part.size])
    background = float(np.median(border)) if border.size else float(np.median(gray))
    mask = (np.abs(gray.astype(np.float32) - background) >= 32).astype(np.uint8)
    count, labels, stats, centers = cv2.connectedComponentsWithStats(mask, connectivity=8)
    kept: list[int] = []
    for index in range(1, count):
        x, y, item_width, item_height, area = [int(v) for v in stats[index]]
        cx, cy = centers[index]
        if not 2 <= area <= max(500, int(width * height * 0.22)):
            continue
        if x <= 1 or y <= 1 or x + item_width >= width - 1 or y + item_height >= height - 1:
            continue
        if not (0.15 * width <= cx <= 0.85 * width):
            continue
        if not (0.10 * height <= cy <= 0.90 * height):
            continue
        kept.append(index)
    if not kept:
        return control, ""

    glyph_mask = np.zeros_like(mask)
    for index in kept:
        glyph_mask[labels == index] = 1
    ys, xs = np.where(glyph_mask > 0)
    if not len(xs):
        return control, ""
    left = max(0, int(xs.min()) - 5)
    top = max(0, int(ys.min()) - 5)
    right = min(width, int(xs.max()) + 6)
    bottom = min(height, int(ys.max()) + 6)
    tight = control[top:bottom, left:right]
    compact = glyph_mask[int(ys.min()):int(ys.max()) + 1, int(xs.min()):int(xs.max()) + 1]
    glyph_height, glyph_width = compact.shape[:2]
    if glyph_height <= 0 or glyph_width <= 0:
        return tight, ""

    row_hits = np.where(compact.sum(axis=1) >= max(3, int(glyph_width * 0.60)))[0]
    col_hits = np.where(compact.sum(axis=0) >= max(3, int(glyph_height * 0.60)))[0]
    row_runs = _runs(row_hits)
    col_runs = _runs(col_hits)
    row_centers = [((start + end) / 2) / max(1, glyph_height - 1) for start, end in row_runs]
    col_centers = [((start + end) / 2) / max(1, glyph_width - 1) for start, end in col_runs]
    central_rows = [value for value in row_centers if 0.25 <= value <= 0.75]
    central_cols = [value for value in col_centers if 0.25 <= value <= 0.75]
    components = [tuple(int(value) for value in stats[index]) for index in kept]
    horizontal = [
        item for item in components
        if item[2] >= 6 and item[2] >= item[3] * 3
    ]
    small_dots = [
        item for item in components
        if item[4] <= 12 and item[2] <= 5 and item[3] <= 5
    ]
    geometry = ""
    if len(horizontal) >= 2 and len(horizontal) == len(components):
        geometry = "="
    elif len(horizontal) == 1 and len(small_dots) >= 2:
        geometry = "/"
    elif len(components) == 1 and central_rows and central_cols:
        density = components[0][4] / max(1, components[0][2] * components[0][3])
        if density <= 0.42:
            geometry = "+"
    elif len(components) == 1 and len(horizontal) == 1 and central_rows:
        geometry = "-"
    return tight, geometry


def locate_control_text(
    region: Region,
    targets: list[str] | tuple[str, ...],
    max_controls: int = 120,
) -> list[UIElement]:
    """Locate short labels inside visually detected rectangular controls.

    This targeted fallback is not part of the normal OCR prompt payload. It is
    called only after semantic element lookup misses a short label.
    """
    from . import vision_cv

    wanted = {_control_key(value): str(value) for value in targets if _control_key(value)}
    if not wanted:
        return []
    region = region.clamp_to_screen()
    image = capture(region)
    controls = vision_cv._detect_control_candidates_from_image(
        image, region, max_items=max_controls,
    )
    if not controls:
        return []

    layout_key = (
        region.left,
        region.top,
        region.width,
        region.height,
        tuple(
            (item.left, item.top, item.width, item.height)
            for item in controls
        ),
    )
    ttl_s = max(0.0, min(30.0, float(
        CONFIG.get("ocr", "control_cache_ttl_s", default=8.0)
    )))
    cached: list[UIElement] | None = None
    with _control_cache_lock:
        entry = _control_cache.get(layout_key)
        if entry and time.monotonic() - entry[0] <= ttl_s:
            cached = entry[1]

    def matching(elements: list[UIElement], cache_hit: bool) -> list[UIElement]:
        out: list[UIElement] = []
        for element in elements:
            key = _control_key(element.text)
            if key not in wanted:
                continue
            out.append(UIElement(
                source=element.source,
                role=element.role,
                text=wanted[key],
                cx=element.cx,
                cy=element.cy,
                left=element.left,
                top=element.top,
                width=element.width,
                height=element.height,
                confidence=element.confidence,
                extra={**element.extra, "control_cache_hit": cache_hit},
            ))
        return out

    if cached is not None:
        return matching(cached, True)

    crops: list[np.ndarray] = []
    metadata: list[tuple[UIElement, str]] = []
    for control in controls:
        left = max(0, int(control.left - region.left))
        top = max(0, int(control.top - region.top))
        right = min(image.shape[1], left + int(control.width))
        bottom = min(image.shape[0], top + int(control.height))
        tight, geometry = _control_glyph_crop(image[top:bottom, left:right])
        if tight is None or tight.size == 0:
            continue
        enlarged = cv2.resize(tight, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
        crops.extend([enlarged, 255 - enlarged])
        metadata.append((control, geometry))

    if not crops:
        return []
    engine = get_engine()
    try:
        with _inference_lock:
            recognized, _elapsed = engine.text_recognizer(crops)
    except Exception as exc:
        logger.debug("targeted control OCR failed: %s", exc)
        return []

    recognized_controls: list[UIElement] = []
    for index, (control, geometry) in enumerate(metadata):
        variants = recognized[index * 2:index * 2 + 2]
        best_text = ""
        best_score = 0.0
        for text, score in variants:
            score = float(score)
            if score > best_score and str(text or "").strip():
                best_text, best_score = str(text).strip(), score
        recognized_text = ""
        score = 0.0
        if best_text and best_score >= 0.30:
            recognized_text, score = best_text, best_score
        elif geometry:
            recognized_text, score = geometry, 0.78
        elif best_text and best_score >= 0.14:
            recognized_text, score = best_text, best_score
        if not _control_key(recognized_text):
            continue
        recognized_controls.append(UIElement(
            source="ocr-control",
            role="button",
            text=recognized_text,
            cx=control.cx,
            cy=control.cy,
            left=control.left,
            top=control.top,
            width=control.width,
            height=control.height,
            confidence=max(float(control.confidence), float(score)),
            extra={
                "recognized_text": recognized_text,
                "recognizer_score": round(float(best_score), 3),
                "geometry": geometry,
            },
        ))
    with _control_cache_lock:
        _control_cache[layout_key] = (time.monotonic(), recognized_controls)
        if len(_control_cache) > 12:
            oldest = sorted(_control_cache.items(), key=lambda item: item[1][0])[:-12]
            for key, _value in oldest:
                _control_cache.pop(key, None)
    return matching(recognized_controls, False)


def collect(region: Region, min_conf: float | None = None) -> list[UIElement]:
    """OCR a screen region (e.g. the target window). Never raises."""
    try:
        region = region.clamp_to_screen()
        image = capture(region)
        return recognize(image, origin=(region.left, region.top), min_conf=min_conf)
    except Exception as exc:
        logger.warning("OCR collect 失败: %s", exc)
        return []


def find_text(region: Region, needle: str,
              min_conf: float | None = None) -> list[UIElement]:
    """All OCR hits containing the substring, tolerant of OCR separators."""
    return [e for e in collect(region, min_conf=min_conf)
            if _match_text(needle, e.text)]
