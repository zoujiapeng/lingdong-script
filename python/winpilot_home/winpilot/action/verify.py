"""Action→Verify→Retry engine.

Every action tool can carry an ``expect`` condition (a small DSL dict). After
executing, we poll the condition until verified or timeout; failed checks
escalate through perception dimensions (fused observe → window OCR →
full-screen OCR) and failed actions are re-executed up to ``max_retries``
times when safe. Results always report ``verified`` + a ``post_state``
summary so the model can self-correct.

Expect DSL (keys may be combined; all must pass):
    {"text_appears": "另存为"}
    {"text_gone": "正在加载"}
    {"window_appears": "另存为"}
    {"window_gone": "记事本"}
    {"screen_stable": true}                          # or {"stable_ms":600,"timeout_s":10}
    {"color_match": {"x":10,"y":20,"rgb":[0,120,215],"tolerance":40}}
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import win32gui

from ..config import CONFIG
from ..perception import fusion, groundtruth, ocr, uia, vision_cv, win32
from ..utils.log import BUS, logger
from ..utils.screenshot import Region, virtual_screen_region, window_region

_POLL_INTERVAL_S = 0.35
_FAST_POLL_INTERVAL_S = 0.08
_FAST_ESCALATION_S = 0.75
_DEFAULT_TIMEOUT_S = 6.0
_COLOR_DEFAULT_TOLERANCE = 40

SUPPORTED_CONDITIONS = (
    "text_appears", "text_gone", "window_appears", "window_gone", "window_title",
    "screen_stable", "color_match",
) + groundtruth.GROUNDTRUTH_KEYS

# Only checks that are intrinsically read-only may overlap UI perception.
# shell_true deliberately stays synchronous because an arbitrary PowerShell
# expression cannot be proven side-effect free.
_PARALLEL_GROUNDTRUTH_KEYS = (
    "file_exists",
    "file_contains",
    "process_running",
    "process_gone",
)


def _norm_visible_text(value: str) -> str:
    return re.sub(r"[\s_\-:\uff1a]+", "", (value or "").casefold())


def _match_visible_text_precise(needle: str, text: str) -> bool:
    needle_lower = (needle or "").casefold()
    text_lower = (text or "").casefold()
    norm_needle = _norm_visible_text(needle_lower)
    if not norm_needle:
        return False
    # Short numeric checks are too weak for GUI verification: they can match URL
    # ports, clocks, list indexes, or diagnostic text. Accept one/two digit
    # values only when they are the whole visible line. Longer numeric literals
    # still work for calculator/file-result tasks as bounded numeric tokens.
    if norm_needle.isdigit():
        for line in text_lower.splitlines():
            norm_line = _norm_visible_text(line)
            if norm_line == norm_needle:
                return True
            if len(norm_needle) >= 3 and re.search(rf"(?<!\d){re.escape(norm_needle)}(?!\d)", norm_line):
                return True
        return False
    if needle_lower in text_lower:
        return True
    norm_text = _norm_visible_text(text_lower)
    if norm_needle and norm_needle in norm_text:
        return True
    # OCR commonly confuses identifier digits with similarly shaped letters.
    # Keep correction restricted to long ASCII identifiers containing digits;
    # ordinary prose and short numeric checks remain exact.
    if (
        len(norm_needle) >= 8
        and any(ch.isdigit() for ch in norm_needle)
        and re.fullmatch(r"[a-z0-9]+", norm_needle)
    ):
        table = str.maketrans({"o": "0", "i": "1", "l": "1"})
        corrected_needle = norm_needle.translate(table)
        for line in text_lower.splitlines():
            norm_line = _norm_visible_text(line)
            if not re.fullmatch(r"[a-z0-9]+", norm_line):
                continue
            corrected_line = norm_line.translate(table)
            if corrected_needle == corrected_line:
                return True
            if (
                corrected_needle.endswith("1")
                and norm_line.endswith("t")
                and corrected_needle[:-1] == corrected_line[:-1]
            ):
                return True
    return False


def _match_visible_text(needle: str, text: str) -> bool:
    return _match_visible_text_precise(needle, text)


@dataclass
class VerifyOutcome:
    """Result of evaluating one expect dict."""

    verified: bool
    detail: str
    method: str = ""           # which dimension confirmed/refuted (uia/ocr/win32/cv)
    elapsed_ms: int = 0
    trace: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verified": self.verified,
            "detail": self.detail,
            "method": self.method,
            "elapsed_ms": self.elapsed_ms,
            "trace": dict(self.trace),
        }


class _AsyncTextProbe:
    """Run OCR on watcher-triggered ROIs while UIA verification continues."""

    def __init__(
        self,
        watcher: Any,
        after_change_ms: int = 0,
        needles: list[str] | None = None,
    ) -> None:
        self.watcher = watcher
        self.after_change_ms = max(0, int(after_change_ms))
        self.needles = [str(item) for item in (needles or []) if str(item).strip()]
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._started = False
        self._ready = False
        self._texts: list[str] = []
        self._elapsed_ms = 0
        self._evidence_count = 0
        self._processed_count = 0
        self._generation = 0
        self._source = ""
        self._sample_texts: list[str] = []
        self._error = ""

    def maybe_start(self) -> None:
        with self._lock:
            if self._started:
                return
        generation = 0
        change_generation = 0
        action_generation = 0
        status_reader = getattr(self.watcher, "status", None)
        if callable(status_reader):
            try:
                status = status_reader() or {}
                change_generation = int(status.get("last_change_ms") or 0)
                action_generation = int(status.get("last_action_change_ms") or 0)
                generation = max(change_generation, action_generation)
            except Exception:
                generation = 0
        if self.after_change_ms and generation <= self.after_change_ms:
            return
        crops: list[tuple[Any, Region]] = []
        source = ""
        selected_generation = 0
        action_is_fresh = (
            not self.after_change_ms
            or action_generation > self.after_change_ms
        )
        change_is_fresh = (
            not self.after_change_ms
            or change_generation > self.after_change_ms
        )
        action_reader = getattr(self.watcher, "action_evidence", None)
        if callable(action_reader) and action_is_fresh:
            try:
                action_crops, transient = action_reader(1500)
                if transient and action_crops:
                    crops = list(action_crops)
                    source = "action-transient"
                    selected_generation = action_generation
            except Exception:
                crops = []
        reader = getattr(self.watcher, "recent_evidence", None)
        if callable(reader) and change_is_fresh:
            if not crops:
                try:
                    crops = list(reader(1500) or [])
                    if crops:
                        source = "watcher-change"
                        selected_generation = change_generation
                except Exception:
                    crops = []
        if not crops:
            if callable(action_reader) and action_is_fresh:
                try:
                    action_crops, _transient = action_reader(1500)
                    crops = list(action_crops or [])
                    if crops:
                        source = "action-change"
                        selected_generation = action_generation
                except Exception:
                    crops = []
        if not crops:
            return
        with self._lock:
            if self._started:
                return
            self._started = True
            self._evidence_count = len(crops[:4])
            self._generation = selected_generation or generation
            self._source = source

        def worker() -> None:
            started = time.monotonic()
            texts: list[str] = []
            processed_count = 0
            error = ""
            try:
                for image, evidence_region in crops[:4]:
                    recognized = ocr.recognize(
                        image,
                        origin=(evidence_region.left, evidence_region.top),
                    )
                    crop_texts = [
                        str(element.text or "").strip()
                        for element in recognized
                        if str(element.text or "").strip()
                    ]
                    texts.extend(crop_texts)
                    for start in range(len(crop_texts)):
                        for width in (2, 3):
                            segment = crop_texts[start:start + width]
                            if len(segment) == width:
                                texts.append(" ".join(segment))
                    processed_count += 1
                    if any(
                        _match_visible_text(needle, candidate)
                        for needle in self.needles
                        for candidate in texts
                    ):
                        break
            except Exception as exc:
                error = str(exc)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            with self._lock:
                self._texts = list(dict.fromkeys(texts))
                self._sample_texts = self._texts[:8]
                self._processed_count = processed_count
                self._elapsed_ms = elapsed_ms
                self._error = error
                self._ready = True
                self._done.set()

        threading.Thread(
            target=worker,
            daemon=True,
            name="verify-predictive-ocr",
        ).start()

    def match(self, needle: str) -> bool:
        with self._lock:
            texts = list(self._texts) if self._ready else []
        return any(_match_visible_text(needle, text) for text in texts)

    def wait_match(self, needle: str, timeout_s: float = 0.45) -> bool:
        with self._lock:
            started = self._started
            ready = self._ready
        if started and not ready:
            self._done.wait(max(0.0, float(timeout_s)))
        return self.match(needle)

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "started": self._started,
                "ready": self._ready,
                "evidence_count": self._evidence_count,
                "processed_count": self._processed_count,
                "generation": self._generation,
                "after_change_ms": self.after_change_ms,
                "source": self._source,
                "text_count": len(self._texts),
                "sample_texts": list(self._sample_texts),
                "elapsed_ms": self._elapsed_ms,
                "error": self._error,
            }


class _AsyncGroundTruthProbe:
    """Overlap read-only deterministic checks with UI perception.

    Filesystem/process/shell checks do not touch the desktop, so they can run
    while UIA or OCR verifies the visible result. The final verdict still waits
    for every condition; this only removes avoidable serial latency.
    """

    def __init__(self, expect: dict[str, Any], timeout_s: float) -> None:
        self._checks = {
            key: value
            for key, value in expect.items()
            if key in _PARALLEL_GROUNDTRUTH_KEYS
        }
        self._timeout_s = max(1.0, float(timeout_s))
        self._lock = threading.Lock()
        self._events = {key: threading.Event() for key in self._checks}
        self._results: dict[str, tuple[bool, str]] = {}
        self._elapsed_ms: dict[str, int] = {}
        self._started = False

    def start(self) -> None:
        if not self._checks:
            return
        with self._lock:
            if self._started:
                return
            self._started = True
        for key, value in self._checks.items():
            threading.Thread(
                target=self._run_one,
                args=(key, value),
                daemon=True,
                name=f"verify-groundtruth-{key}",
            ).start()

    def _run_one(self, key: str, value: Any) -> None:
        started = time.monotonic()
        try:
            result = groundtruth.check(key, value, timeout_s=self._timeout_s)
        except Exception as exc:  # groundtruth checks should never raise
            result = (False, f"groundtruth check failed: {exc}")
        elapsed_ms = int((time.monotonic() - started) * 1000)
        with self._lock:
            self._results[key] = result
            self._elapsed_ms[key] = elapsed_ms
            self._events[key].set()

    def wait_result(
        self,
        key: str,
        deadline: float | None,
    ) -> tuple[bool, str] | None:
        event = self._events.get(key)
        if event is None:
            return None
        timeout = None if deadline is None else max(0.0, deadline - time.monotonic())
        if not event.wait(timeout=timeout):
            return None
        with self._lock:
            return self._results.get(key)

    def handles(self, key: str) -> bool:
        return key in self._checks

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "started": self._started,
                "check_count": len(self._checks),
                "completed_count": len(self._results),
                "pending": [key for key in self._checks if key not in self._results],
                "checks": {
                    key: {
                        "ok": result[0],
                        "detail": result[1][:160],
                        "elapsed_ms": self._elapsed_ms.get(key, 0),
                    }
                    for key, result in self._results.items()
                },
            }


# ------------------------------------------------------------ target window
def _related_window(hwnd: int | None, target: int | None) -> bool:
    if not hwnd or not target:
        return False
    if int(hwnd) == int(target):
        return True
    try:
        if win32gui.GetAncestor(int(hwnd), 2) == int(target):  # GA_ROOT
            return True
    except Exception:
        pass
    try:
        owner = win32gui.GetWindow(int(hwnd), 4)  # GW_OWNER
        while owner:
            if int(owner) == int(target):
                return True
            owner = win32gui.GetWindow(int(owner), 4)
    except Exception:
        pass
    return False


def _target_hwnd(hwnd: int | None) -> int | None:
    """Use foreground only when it belongs to the scoped target."""
    fg = win32gui.GetForegroundWindow()
    if fg and win32gui.IsWindowVisible(fg):
        if not hwnd or _related_window(fg, hwnd):
            return fg
    return hwnd


def _full_screen_region() -> Region:
    return virtual_screen_region()


# --------------------------------------------------------- condition checks
def _find_text(
    needle: str,
    hwnd: int | None,
    level: int,
    text_probe: _AsyncTextProbe | None = None,
) -> tuple[bool, str]:
    """Search for text with escalating perception. Returns (found, method)."""
    target = _target_hwnd(hwnd)

    if text_probe is not None:
        text_probe.maybe_start()
        if text_probe.match(needle):
            return True, "predictive-ocr"

    # Level 0a: a property-indexed UIA query avoids walking the entire tree and
    # skips all image dimensions. It covers native text plus browser DOM text
    # and input values, while the fused/OCR paths below retain broad recall.
    if target:
        try:
            for element in uia.find_text(target, needle):
                if _match_visible_text(needle, element.text):
                    return True, "uia-query"
        except Exception as exc:
            logger.debug("verify: UIA text query failed: %s", exc)
    if text_probe is not None:
        text_probe.maybe_start()
        if text_probe.match(needle):
            return True, "predictive-ocr"

    # Level 0b: fused observe with whatever dimensions are enabled.
    if target and (text_probe is None or level >= 1):
        if text_probe is not None and text_probe.wait_match(needle):
            return True, "predictive-ocr"
        try:
            snapshot = fusion.observe(target, include_icons=False)
            for element in snapshot.elements:
                if _match_visible_text(needle, element.text):
                    return True, element.source
        except Exception as exc:
            logger.debug("verify: fused observe 失败: %s", exc)
    if text_probe is not None and text_probe.match(needle):
        return True, "predictive-ocr"

    if level < 1 or not _ocr_enabled():
        return False, ""

    # Level 1: direct OCR over the target window (fresh capture).
    if target:
        try:
            if ocr.find_text(window_region(target), needle):
                return True, "ocr"
        except Exception as exc:
            logger.debug("verify: 窗口 OCR 失败: %s", exc)

    if level < 2:
        return False, ""

    # Level 2: full-screen OCR — catches toasts/dialogs outside a target only
    # when no target window was supplied. If an action was scoped to a window,
    # accepting full-screen text can produce false success from the agent UI,
    # logs, or other apps that happen to contain the same words.
    if target:
        return False, ""

    # Last resort, so trade precision for recall with a lowered threshold.
    try:
        if ocr.find_text(_full_screen_region(), needle, min_conf=0.4):
            return True, "ocr-fullscreen"
    except Exception as exc:
        logger.debug("verify: 全屏 OCR 失败: %s", exc)
    return False, ""


def _ocr_enabled() -> bool:
    return bool(CONFIG.get("perception", "ocr", "enabled", default=False))


def _check_window(needle: str, want_present: bool) -> tuple[bool, str]:
    found = win32.find_window(needle)
    if want_present:
        return (found is not None), (found["title"] if found else "")
    return (found is None), (found["title"] if found else "")


def _check_window_title(needle: str, hwnd: int | None) -> tuple[bool, str]:
    target = _target_hwnd(hwnd)
    title = win32._window_text(target) if target else ""
    return win32.title_contains(title, needle), title


def _check_color(spec: dict, _hwnd: int | None) -> tuple[bool, str]:
    x, y = int(spec["x"]), int(spec["y"])
    expected = spec.get("rgb") or spec.get("color")
    tolerance = int(spec.get("tolerance", _COLOR_DEFAULT_TOLERANCE))
    probe = vision_cv.color_probe(x, y)
    actual = probe["average_rgb"]
    if expected is None:
        # An expectation without a target colour is a malformed condition —
        # failing loudly tells the model to fix it (use color_probe to probe).
        return False, f"color_match 缺少 rgb 期望值（实际颜色 {probe['average_hex']}）"
    distance = max(abs(int(a) - int(b)) for a, b in zip(actual, expected))
    ok = distance <= tolerance
    return ok, f"期望RGB{tuple(expected)} 实际{actual} 最大偏差{distance}"


def _check_screen_stable(spec: Any, hwnd: int | None,
                         deadline: float | None = None) -> tuple[bool, str]:
    opts = spec if isinstance(spec, dict) else {}
    target = _target_hwnd(hwnd)
    region = window_region(target) if target else _full_screen_region()
    timeout = float(opts.get("timeout_s", 10.0))
    if deadline is not None:  # never overrun the outer verify budget
        timeout = max(0.5, min(timeout, deadline - time.monotonic()))
    result = vision_cv.wait_screen_stable(
        region,
        stable_ms=int(opts.get("stable_ms", 600)),
        timeout_s=timeout,
    )
    return result["stable"], f"等待{result['waited_s']}s"


# ----------------------------------------------------------------- evaluate
def _eval_once(expect: dict, hwnd: int | None, level: int,
               deadline: float | None = None,
               text_probe: _AsyncTextProbe | None = None,
               groundtruth_probe: _AsyncGroundTruthProbe | None = None) -> VerifyOutcome:
    """Evaluate every condition in the expect dict once (AND semantics)."""
    methods: list[str] = []
    # Deterministic checks may already be running in the background. Evaluate
    # visible conditions first so their UIA/OCR latency overlaps that work.
    items = [
        item for item in expect.items()
        if item[0] not in groundtruth.GROUNDTRUTH_KEYS
    ] + [
        item for item in expect.items()
        if item[0] in groundtruth.GROUNDTRUTH_KEYS
    ]
    for key, value in items:
        if key == "timeout_s":
            continue
        if key == "text_appears":
            ok, method = _find_text(
                str(value), hwnd, level, text_probe=text_probe,
            )
            if not ok:
                return VerifyOutcome(False, f'未找到文字 "{value}"', method)
            if method:
                methods.append(method)
        elif key == "text_gone":
            ok, method = _find_text(str(value), hwnd, max(level, 1))
            if ok:
                return VerifyOutcome(False, f'文字 "{value}" 仍存在', method)
        elif key == "window_appears":
            ok, title = _check_window(str(value), want_present=True)
            if not ok:
                return VerifyOutcome(False, f'窗口 "{value}" 未出现', "win32")
            methods.append("win32")
        elif key == "window_gone":
            ok, title = _check_window(str(value), want_present=False)
            if not ok:
                return VerifyOutcome(False, f'窗口 "{title}" 仍存在', "win32")
            methods.append("win32")
        elif key == "window_title":
            ok, title = _check_window_title(str(value), hwnd)
            if not ok:
                return VerifyOutcome(False, f'foreground window title "{title}" does not contain "{value}"', "win32")
            methods.append("win32")
        elif key == "screen_stable":
            ok, detail = _check_screen_stable(value, hwnd, deadline=deadline)
            if not ok:
                return VerifyOutcome(False, f"画面未稳定 ({detail})", "cv")
            methods.append("cv")
        elif key == "color_match":
            ok, detail = _check_color(value, hwnd)
            if not ok:
                return VerifyOutcome(False, f"颜色不符 ({detail})", "cv")
            methods.append("cv")
        elif key in groundtruth.GROUNDTRUTH_KEYS:
            # Deterministic ground truth (filesystem/process/shell) — the
            # strongest signal; no perception escalation needed. Bound any
            # shell_true to the remaining verify budget so it can't overrun.
            remaining = max(1.0, deadline - time.monotonic()) if deadline else 15.0
            use_parallel_result = (
                groundtruth_probe is not None
                and groundtruth_probe.handles(key)
            )
            async_result = (
                groundtruth_probe.wait_result(key, deadline)
                if use_parallel_result
                else None
            )
            if use_parallel_result and async_result is None:
                return VerifyOutcome(
                    False,
                    f"{key} asynchronous check did not finish before timeout",
                    "groundtruth-async",
                )
            ok, detail = async_result or groundtruth.check(
                key, value, timeout_s=remaining,
            )
            if not ok:
                return VerifyOutcome(False, f"{key} 未满足 ({detail})", "groundtruth")
            methods.append("groundtruth")
        else:
            return VerifyOutcome(False, f"不支持的验证条件: {key}", "")
    return VerifyOutcome(
        True,
        "全部条件满足",
        "+".join(dict.fromkeys(methods)),
    )


def check(expect: dict, hwnd: int | None = None,
          timeout_s: float = _DEFAULT_TIMEOUT_S,
          stop_event=None,
          watcher: Any = None,
          watcher_change_baseline: int = 0) -> VerifyOutcome:
    """Poll the expect conditions until verified or timeout, escalating
    perception level (0=fused → 1=window OCR → 2=full-screen OCR) over time.

    ``stop_event`` (threading.Event) lets a Stop abort the polling loop early
    instead of waiting out the timeout."""
    started = time.monotonic()
    deadline = started + timeout_s
    level = 0
    polls = 0
    text_probe = (
        _AsyncTextProbe(
            watcher,
            after_change_ms=watcher_change_baseline,
            needles=[str(expect.get("text_appears") or "")],
        )
        if watcher is not None and "text_appears" in expect
        else None
    )
    has_perception_condition = any(
        key not in groundtruth.GROUNDTRUTH_KEYS and key != "timeout_s"
        for key in expect
    )
    groundtruth_probe = (
        _AsyncGroundTruthProbe(expect, timeout_s)
        if has_perception_condition
        and any(key in _PARALLEL_GROUNDTRUTH_KEYS for key in expect)
        else None
    )
    if groundtruth_probe is not None:
        groundtruth_probe.start()
    last = VerifyOutcome(False, "未开始", "")

    def finish(outcome: VerifyOutcome) -> VerifyOutcome:
        outcome.trace = {
            "poll_count": polls,
            "final_level": level,
            "predictive_ocr": text_probe.to_dict() if text_probe is not None else {},
            "parallel_groundtruth": (
                groundtruth_probe.to_dict() if groundtruth_probe is not None else {}
            ),
        }
        return outcome

    while True:
        polls += 1
        last = _eval_once(
            expect,
            hwnd,
            level,
            deadline=deadline,
            text_probe=text_probe,
            groundtruth_probe=groundtruth_probe,
        )
        elapsed = time.monotonic() - started
        last.elapsed_ms = int(elapsed * 1000)
        if last.verified or time.monotonic() >= deadline:
            if last.verified and not last.method:
                last.method = "fused"
            return finish(last)
        if stop_event is not None and stop_event.is_set():
            return finish(last)  # Stop requested → don't keep polling
        # Watcher-backed checks let UIA and ROI OCR race first. Escalate to a
        # full fused/window pass only if neither cheap path resolves promptly.
        level_one_after = (
            min(timeout_s / 3, _FAST_ESCALATION_S)
            if text_probe else timeout_s / 3
        )
        level_two_after = (
            min(timeout_s * 2 / 3, _FAST_ESCALATION_S * 2)
            if text_probe else timeout_s * 2 / 3
        )
        if elapsed > level_two_after:
            level = 2
        elif elapsed > level_one_after:
            level = 1
        time.sleep(_FAST_POLL_INTERVAL_S if text_probe else _POLL_INTERVAL_S)


# --------------------------------------------------------------- post state
def post_state_summary() -> dict[str, Any]:
    """Cheap snapshot of "where are we now" for the model after each action."""
    fg = win32gui.GetForegroundWindow()
    title = win32._window_text(fg) if fg else ""
    return {"foreground_hwnd": fg, "foreground_title": title}


# ------------------------------------------------------------ run + verify
def run_with_verify(
    name: str,
    action: Callable[[], dict],
    expect: dict | None = None,
    hwnd: int | None = None,
    retryable: bool = True,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    stop_event=None,
    watcher: Any = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Execute an action, verify the expectation, retry on failure.

    Non-retryable actions (e.g. type_text — re-running duplicates input) are
    executed once; only verification is repeated. ``stop_event`` aborts the
    verify polling + retry loop promptly when a Stop is requested.
    """
    max_retries = int(CONFIG.get("agent", "max_retries", default=2))
    attempts = 0
    result: dict[str, Any] = {}
    outcome: VerifyOutcome | None = None
    # Hard wall-clock cap so a single action can never hang for minutes
    # (e.g. screen_stable on a never-settling canvas + slow OCR escalation
    # × retries). Once exceeded, stop retrying and report the last outcome.
    overall_deadline = time.monotonic() + max(timeout_s * (max_retries + 1), timeout_s) + 4.0

    while True:
        attempts += 1
        watcher_change_baseline = 0
        if watcher is not None:
            status_reader = getattr(watcher, "status", None)
            if callable(status_reader):
                try:
                    status = status_reader() or {}
                    watcher_change_baseline = max(
                        int(status.get("last_change_ms") or 0),
                        int(status.get("last_action_change_ms") or 0),
                    )
                except Exception:
                    watcher_change_baseline = 0
        try:
            result = action() or {}
        except Exception as exc:
            logger.warning("动作 %s 执行异常: %s", name, exc)
            result = {"ok": False, "error": str(exc)}
            # Don't verify a screen the action never touched — a stale match
            # would falsely report success. Retry the action or give up.
            if retryable and attempts <= max_retries and not (
                    stop_event is not None and stop_event.is_set()):
                continue
            outcome = VerifyOutcome(False, f"动作执行异常: {exc}", "") if expect else None
            break

        if not expect:
            outcome = None
            break

        # Shrink the verify budget to whatever's left of the overall cap.
        budget = max(1.0, min(timeout_s, overall_deadline - time.monotonic()))
        outcome = check(
            expect,
            hwnd=hwnd,
            timeout_s=budget,
            stop_event=stop_event,
            watcher=watcher,
            watcher_change_baseline=watcher_change_baseline,
        )
        BUS.publish(
            "verify",
            run_id=run_id,
            action=name,
            attempt=attempts,
            verified=outcome.verified,
            detail=outcome.detail,
            method=outcome.method,
            elapsed_ms=outcome.elapsed_ms,
            trace=outcome.trace,
        )
        if outcome.verified:
            break
        if (not retryable or attempts > max_retries or not result.get("ok", True)
                or time.monotonic() >= overall_deadline
                or (stop_event is not None and stop_event.is_set())):
            break
        logger.info("动作 %s 验证失败（第%d次），重试: %s", name, attempts, outcome.detail)

    response = dict(result)
    response["attempts"] = attempts
    if outcome is not None:
        response["verified"] = outcome.verified
        response["verify_detail"] = outcome.detail
        response["verify_method"] = outcome.method
        response["verify_trace"] = outcome.trace
    response["post_state"] = post_state_summary()
    return response
