"""Run-scoped Studio action-batch controller.

The controller owns only the small amount of state that must survive between
Studio nodes: the current target window, a semantic element cache, and managed
artifact handles.  Physical input is executed exactly once through the typed
RawDevice boundary while the desktop queue provides the process-wide
single-writer lease.
"""
from __future__ import annotations

import base64
from io import BytesIO
import json
import math
from pathlib import Path
import shlex
import time
from typing import Any, Callable

from PIL import Image
import win32con
import win32gui

from ..action import executor as action_executor
from ..action.desktop_queue import (
    DESKTOP_ACTION_QUEUE,
    DesktopActionCanceled,
)
from ..action.raw_device import (
    ClickCount,
    ClickMechanism,
    HotkeyChord,
    InputEventKind,
    MouseButton,
    PhysicalInputEvent,
    RawActionContext,
    RawActionReceipt,
    RawDeviceBackends,
    RawDeviceExecutor,
)
from ..perception import apps, fusion, win32
from ..tasking.artifacts import ArtifactStore
from ..tasking.evidence import EvidenceRegistry
from ..utils.log import BUS
from ..utils.screenshot import Region, capture, window_region
from ..visual_runtime.keyboard_contracts import (
    canonical_hotkey_args,
    normalize_hotkey_args,
)
from .action_contract import (
    AtomicAction,
    BATCH_CLIPBOARD_ACTIONS,
    BATCH_MUTATING_ACTIONS,
    BATCH_OBSERVE_ACTIONS,
    canonicalize_batch_actions,
    validate_batch_actions,
)
from .element_cache import ElementCache, anchor_from_dict
from .input_locator import locate_input
from .types import BatchMetrics, ScreenDelta
from .vision import changed_regions
from .watcher import ScreenWatcher


_SENSITIVE_TRACE_ACTIONS = frozenset({
    "type_text",
    "type_into_element",
    "set_value",
    "clipboard_read",
    "clipboard_write",
    "clipboard_paste",
    "launch",
})


def _now_ms() -> int:
    return int(time.monotonic() * 1000)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _text_metadata(value: Any) -> dict[str, int]:
    text = str(value or "")
    return {
        "text_length": len(text),
        "text_codepoint_count": len(text),
    }


def _redact_action_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Keep credentials and clipboard contents out of traces.

    This is deliberately a projection helper, not a policy gate.  Execution
    receives the original arguments; only BUS/preview metadata is redacted.
    """

    projected = dict(args or {})
    if name in {"type_text", "type_into_element", "set_value", "clipboard_write"}:
        text = projected.pop("text", projected.pop("text_input", projected.pop("value", "")))
        projected.update(_text_metadata(text))
        projected["text_redacted"] = True
    if name in {"clipboard_read", "clipboard_paste"}:
        projected.pop("text", None)
        projected.pop("text_input", None)
        projected["clipboard_content_redacted"] = True
    if name == "launch":
        command = projected.pop("command", "")
        arguments = projected.pop("arguments", "")
        projected.update({
            "command_length": len(str(command or "")),
            "arguments_length": len(str(arguments or "")),
            "command_redacted": True,
        })
    return projected


def _safe_event_preview(name: str, data: dict[str, Any], args: dict[str, Any]) -> str:
    if name not in _SENSITIVE_TRACE_ACTIONS:
        return json.dumps(data, ensure_ascii=False, default=str)[:800]
    safe = {
        "ok": bool(data.get("ok")),
        "input_completed": data.get("input_completed"),
        "outcome": data.get("outcome"),
        "error_kind": data.get("error_kind"),
        **(
            _text_metadata(args.get("text", args.get("text_input", args.get("value", ""))))
            if name in {"type_text", "type_into_element", "set_value", "clipboard_write"}
            else {}
        ),
    }
    return json.dumps(safe, ensure_ascii=False, default=str)


def _default_raw_device() -> RawDeviceExecutor:
    """Compose the same admitted physical primitives as the online runtime."""

    return RawDeviceExecutor(RawDeviceBackends(
        admitted_physical_click=action_executor.ADMITTED_PHYSICAL_CLICK_BACKEND,
        admitted_physical_drag=action_executor.ADMITTED_PHYSICAL_DRAG_BACKEND,
        admitted_physical_move=action_executor.ADMITTED_PHYSICAL_MOVE_BACKEND,
        admitted_physical_sequence=action_executor.ADMITTED_PHYSICAL_SEQUENCE_BACKEND,
        admitted_physical_scroll=action_executor.ADMITTED_PHYSICAL_SCROLL_BACKEND,
        admitted_physical_type_text=action_executor.ADMITTED_PHYSICAL_TYPE_TEXT_BACKEND,
        admitted_physical_hotkey=action_executor.ADMITTED_PHYSICAL_HOTKEY_BACKEND,
    ))


def _receipt_payload(receipt: RawActionReceipt) -> dict[str, Any]:
    data = receipt.to_dict()
    data["ok"] = receipt.input_completed is True
    if receipt.input_completed is not True:
        data["error"] = (
            "physical input outcome is unknown"
            if receipt.input_completed is None
            else "physical input was not completed"
        )
        if receipt.error_kind:
            data["error"] += f": {receipt.error_kind}"
    return data


def _region_from_value(value: Any) -> Region | None:
    if not isinstance(value, dict):
        return None
    try:
        left = int(value.get("x", value.get("left")))
        top = int(value.get("y", value.get("top")))
        width = int(value["width"])
        height = int(value["height"])
    except (KeyError, TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return Region(left, top, width, height).clamp_to_screen()


def _region_payload(region: Region) -> dict[str, int]:
    return {
        "x": int(region.left),
        "y": int(region.top),
        "width": int(region.width),
        "height": int(region.height),
    }


class FastGuiController:
    """Execute Studio action batches as the direct run-scoped owner."""

    def __init__(
        self,
        *,
        run_id: str = "",
        stop_event: Any = None,
        task: str = "",
        raw_device: RawDeviceExecutor | None = None,
        artifact_store: ArtifactStore | None = None,
        evidence_registry: EvidenceRegistry | None = None,
        observe_fn: Callable[..., Any] | None = None,
        capture_fn: Callable[[Region | None], Any] | None = None,
        watcher: ScreenWatcher | None = None,
    ) -> None:
        self.run_id = str(run_id or "")
        self.stop_event = stop_event
        self.task = str(task or "")
        self.hwnd: int | None = None
        self.target_title = ""
        self.snapshot = None
        self.raw_device = raw_device or _default_raw_device()
        self.artifact_store = artifact_store
        self.evidence_registry = evidence_registry
        self._observe_fn = observe_fn or fusion.observe
        self._capture_fn = capture_fn or capture
        self.cache = ElementCache()
        self.watcher = watcher or ScreenWatcher(
            region_provider=self._target_region,
            capture_fn=self._capture_fn,
        )
        self._closed = False

    def _stopped(self) -> bool:
        return bool(
            self.stop_event is not None
            and getattr(self.stop_event, "is_set", lambda: False)()
        )

    def _wait(self, seconds: float) -> bool:
        delay = max(0.0, float(seconds))
        wait = getattr(self.stop_event, "wait", None)
        if callable(wait):
            return bool(wait(delay))
        if self._stopped():
            return True
        time.sleep(delay)
        return self._stopped()

    def _target_region(self) -> Region | None:
        hwnd = int(self.hwnd or 0)
        if not hwnd:
            return None
        try:
            if not win32gui.IsWindow(hwnd):
                return None
            return window_region(hwnd)
        except Exception:
            return None

    def _ensure_target(self) -> int:
        hwnd = int(self.hwnd or 0)
        try:
            if hwnd and win32gui.IsWindow(hwnd):
                return hwnd
        except Exception:
            pass
        try:
            hwnd = int(win32gui.GetForegroundWindow() or 0)
        except Exception:
            hwnd = 0
        if not hwnd:
            raise RuntimeError("action requires a visible target window")
        self.hwnd = hwnd
        try:
            self.target_title = str(win32._window_text(hwnd) or "")
        except Exception:
            self.target_title = ""
        return hwnd

    def _context(self) -> RawActionContext:
        target = int(self.hwnd or 0) or None
        return RawActionContext(target_hwnd=target, current_hwnd=target)

    def _observe(self, args: dict[str, Any] | None = None) -> dict[str, Any]:
        args = dict(args or {})
        hwnd = self._ensure_target()
        snapshot = self._observe_fn(
            hwnd,
            include_icons=bool(args.get("include_icons", True)),
            perception_mode=str(args.get("perception_mode") or "normal"),
            stop_event=self.stop_event,
        )
        self.snapshot = snapshot
        self.target_title = str(getattr(snapshot, "title", "") or self.target_title)
        query = str(args.get("query") or "").strip().casefold()
        elements = []
        for element in getattr(snapshot, "scoped_elements", lambda: snapshot.elements)():
            text = str(getattr(element, "text", "") or "")
            if query and query not in text.casefold():
                continue
            elements.append(element.to_dict())
        return {
            "ok": True,
            "hwnd": hwnd,
            "title": self.target_title,
            "region": _region_payload(snapshot.region),
            "elements": elements[:200],
            "element_count": len(elements),
            "query": str(args.get("query") or ""),
        }

    def _fresh_snapshot(self) -> Any:
        result = self._observe({"include_icons": True})
        if result.get("ok") is not True or self.snapshot is None:
            raise RuntimeError(str(result.get("error") or "target observation failed"))
        return self.snapshot

    def _click_receipt(
        self,
        x: int,
        y: int,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            button = MouseButton(str(args.get("button") or "left").casefold())
        except ValueError:
            return {"ok": False, "error": "click button must be left, right, or middle"}
        count = ClickCount.DOUBLE if bool(args.get("double")) else ClickCount.SINGLE
        receipt = self.raw_device.click(
            self._context(),
            int(x),
            int(y),
            button,
            count,
            ClickMechanism.PHYSICAL,
            press_duration_ms=int(args.get("press_duration_ms") or 35),
            enforce_foreground=False,
            stop_event=self.stop_event,
        )
        return {**_receipt_payload(receipt), "x": int(x), "y": int(y)}

    def _click(self, args: dict[str, Any]) -> dict[str, Any]:
        if args.get("x") is not None and args.get("y") is not None:
            return self._click_receipt(int(args["x"]), int(args["y"]), args)
        if args.get("element_id") is not None:
            snapshot = self._fresh_snapshot()
            element = snapshot.by_id(int(args["element_id"]))
            if element is None:
                return {"ok": False, "error": "element_id is not present in the fresh target observation"}
            return {
                **self._click_receipt(int(element.cx), int(element.cy), args),
                "resolved_element": element.to_dict(),
            }
        return self._click_element(args)

    def _anchor_args(self, args: dict[str, Any]) -> dict[str, Any]:
        nested = args.get("anchor")
        data = dict(nested) if isinstance(nested, dict) else dict(args)
        if not str(data.get("label") or "").strip() and str(args.get("text_query") or "").strip():
            data["label"] = str(args["text_query"])
        return data

    def _click_element(self, args: dict[str, Any]) -> dict[str, Any]:
        snapshot = self._fresh_snapshot()
        anchor = anchor_from_dict(self._anchor_args(args))
        located = self.cache.locate_with_trace(snapshot, anchor)
        if located.element is None:
            return {
                "ok": False,
                "error": "semantic target was not found in the fresh target observation",
                "locator": located.to_dict(),
            }
        element = located.element
        return {
            **self._click_receipt(int(element.cx), int(element.cy), args),
            "locator": located.to_dict(),
            "resolved_element": element.to_dict(),
        }

    def _type_text(self, args: dict[str, Any]) -> dict[str, Any]:
        text = str(args.get("text") or "")
        if not text:
            return {"ok": True, "input_completed": True, **_text_metadata(text)}
        receipt = self.raw_device.type_text(
            self._context(),
            text,
            enforce_foreground=False,
            stop_event=self.stop_event,
        )
        return {**_receipt_payload(receipt), **_text_metadata(text)}

    def _hotkey(self, args: dict[str, Any]) -> dict[str, Any]:
        keys = canonical_hotkey_args(normalize_hotkey_args(args.get("keys") or args.get("value")))
        try:
            chord = HotkeyChord(tuple(keys))
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc), "keys": keys}
        receipt = self.raw_device.hotkey(
            self._context(),
            chord,
            repeat=max(1, int(args.get("repeat") or 1)),
            enforce_foreground=False,
            stop_event=self.stop_event,
        )
        return {**_receipt_payload(receipt), "keys": keys}

    def _type_into_element(self, args: dict[str, Any]) -> dict[str, Any]:
        snapshot = self._fresh_snapshot()
        fallback = args.get("fallback_text") or []
        if isinstance(fallback, str):
            fallback = [fallback]
        located = locate_input(
            snapshot,
            label=str(args.get("label") or args.get("text_query") or ""),
            expected_region=str(args.get("expected_region") or ""),
            fallback_text=[str(value) for value in fallback],
            task="",
            communication_context=None,
        )
        candidate = located.candidate
        if not located.found or candidate is None:
            return {
                "ok": False,
                "error": "input target was not found in the fresh target observation",
                "locator": located.to_dict(),
            }
        x, y = candidate.center
        click = self._click_receipt(int(x), int(y), args)
        if click.get("ok") is not True:
            return {"ok": False, "error": "input target click failed", "click": click}
        if _truthy(args.get("replace")) or _truthy(args.get("clear_before")):
            select = self._hotkey({"keys": ["ctrl", "a"]})
            if select.get("ok") is not True:
                return {"ok": False, "error": "input selection failed", "click": click, "select": select}
        typed = self._type_text({"text": str(args.get("text") or "")})
        return {
            "ok": typed.get("ok") is True,
            "input_completed": typed.get("input_completed"),
            "error": typed.get("error", ""),
            **_text_metadata(args.get("text")),
            "target": {"x": int(x), "y": int(y)},
            "locator": located.to_dict(),
            "click_receipt": click.get("outcome"),
            "type_receipt": typed.get("outcome"),
        }

    def _resolve_action_hwnd(self, args: dict[str, Any]) -> tuple[int, str]:
        """Resolve the window an action acts on, preferring an explicit handle."""
        raw_hwnd = args.get("hwnd")
        try:
            hwnd = int(raw_hwnd) if raw_hwnd not in (None, "") else 0
        except (TypeError, ValueError):
            hwnd = 0
        if hwnd and win32gui.IsWindow(hwnd):
            return hwnd, "hwnd"
        query = str(args.get("title") or args.get("window") or "").strip()
        if query:
            match = win32.find_window(query)
            if match:
                return int(match.get("hwnd") or 0), "title"
        current = int(self.hwnd or 0)
        if current and win32gui.IsWindow(current):
            return current, "target_window"
        return 0, ""

    def _uia_value_target(
        self,
        args: dict[str, Any],
        hwnd: int,
    ) -> tuple[Any | None, str]:
        """Find the live accessibility control whose value a write should replace.

        The perception collector is the owner of "what controls does this window
        have"; this only chooses one of them, and it never guesses: an
        ``automation_id`` must match exactly, a point must fall inside the
        control, a label must appear in its text.
        """
        from ..perception import uia

        try:
            controls = uia.collect(int(hwnd))
        except Exception as exc:
            return None, f"accessibility collection failed: {exc}"
        if not controls:
            return None, "no accessibility elements were readable for the target window"

        wanted_id = str(args.get("automation_id") or "").strip()
        if wanted_id:
            for item in controls:
                if str((item.extra or {}).get("automation_id") or "") == wanted_id:
                    return item, "automation_id"
            return None, f"no element carries automation_id {wanted_id!r}"

        if args.get("x") is not None and args.get("y") is not None:
            try:
                x, y = int(args["x"]), int(args["y"])
            except (TypeError, ValueError):
                return None, "x and y must be integers"
            inside = [
                item for item in controls
                if item.left <= x < item.left + item.width
                and item.top <= y < item.top + item.height
            ]
            if inside:
                smallest = min(inside, key=lambda item: item.width * item.height)
                return smallest, "point"

        needle = str(args.get("label") or args.get("text_query") or "").strip().casefold()
        if needle:
            for item in controls:
                if needle in str(item.text or "").casefold():
                    return item, "label"
            return None, f"no element text contains {needle!r}"

        return None, "no value-capable target matched"

    def _set_value(self, args: dict[str, Any]) -> dict[str, Any]:
        """Replace a control's value through its accessibility value pattern.

        This is deliberately not a click-and-type: nothing is aimed, nothing is
        synthesized, and the target never has to be in front. It applies where a
        control exposes a value pattern and refuses by name where it does not, so
        a caller can fall back to ``type_into_element`` instead of wondering
        whether a keystroke went somewhere unexpected.
        """
        text = args.get("text")
        if text is None:
            text = args.get("value")
        text = str(text if text is not None else "")

        if args.get("element_id") is not None and args.get("x") is None:
            snapshot = self._fresh_snapshot()
            try:
                element = snapshot.by_id(int(args["element_id"]))
            except (TypeError, ValueError):
                element = None
            if element is None:
                return {"ok": False, "error": "element_id is not present in the fresh target observation"}
            args = {**args, "x": int(element.cx), "y": int(element.cy)}

        hwnd, resolved_by = self._resolve_action_hwnd(args)
        if not hwnd:
            return {"ok": False, "error": "set_value requires an hwnd, a matching title, or a focused target window"}

        element, how = self._uia_value_target(args, hwnd)
        if element is None:
            return {"ok": False, "error": how, "hwnd": hwnd, "resolved_by": resolved_by}
        control = (element.extra or {}).get("uia_ctrl")
        if control is None:
            return {"ok": False, "error": "the matched element carries no live accessibility control", "target": element.to_dict()}
        try:
            pattern = control.GetValuePattern()
        except Exception as exc:
            return {
                "ok": False,
                "error": f"the matched element exposes no value pattern: {exc}",
                "target": element.to_dict(),
                "resolved_by": how,
            }
        try:
            before = str(pattern.Value or "")
        except Exception:
            before = ""
        try:
            pattern.SetValue(text)
        except Exception as exc:
            return {
                "ok": False,
                "error": f"value write failed: {exc}",
                "target": element.to_dict(),
                "resolved_by": how,
                "before_length": len(before),
            }
        try:
            after = str(control.GetValuePattern().Value or "")
        except Exception as exc:
            return {
                "ok": False,
                "verified": False,
                "error": f"the value was written but could not be read back: {exc}",
                "target": element.to_dict(),
                "resolved_by": how,
            }
        matched = after == text
        return {
            "ok": matched,
            "verified": matched,
            "value_matches": matched,
            "before_length": len(before),
            "read_back_length": len(after),
            "target": element.to_dict(),
            "resolved_by": how,
            "hwnd": hwnd,
            **_text_metadata(text),
        }

    @staticmethod
    def _window_placement_state(hwnd: int) -> str:
        """Report minimized / maximized / normal, or ``unknown`` when unreadable.

        pywin32 exposes no ``IsZoomed``, and a maximized window silently refuses
        a frame change, so this reads the placement through the API that does
        report it. Returning ``unknown`` rather than assuming ``normal`` keeps a
        failed read visible in the result instead of changing behaviour quietly.
        """
        try:
            if win32gui.IsIconic(hwnd):
                return "minimized"
            if int(win32gui.GetWindowPlacement(hwnd)[1]) == win32con.SW_SHOWMAXIMIZED:
                return "maximized"
            return "normal"
        except Exception:
            return "unknown"

    def _set_window_frame(self, args: dict[str, Any]) -> dict[str, Any]:
        """Move and resize one window, then read the result back independently.

        The frame is set without activating and without changing z-order, so the
        window someone is looking at stays where it is. A window is only restored
        out of a minimized or maximized placement when it actually refuses the
        request - Windows reports a remembered placement for windows that are
        already normal, and restoring one of those would move a window nobody
        asked to move.
        """
        hwnd, resolved_by = self._resolve_action_hwnd(args)
        if not hwnd:
            return {"ok": False, "error": "set_window_frame requires an hwnd, a matching title, or a focused target window"}

        def rect() -> tuple[int, int, int, int] | None:
            try:
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            except Exception:
                return None
            return int(left), int(top), int(right), int(bottom)

        def restore(placement: str) -> str:
            """Return "" when the window is normal afterwards, else the reason."""
            try:
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            except Exception as exc:
                return f"could not restore a {placement} window: {exc}"
            # Restoring is asynchronous; wait for a normal placement rather than
            # sleeping a fixed amount and hoping.
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if self._window_placement_state(hwnd) == "normal":
                    return ""
                time.sleep(0.05)
            return f"the window stayed {placement} after a restore request"

        def attempt(base: tuple[int, int, int, int]) -> dict[str, Any]:
            base_left, base_top, base_right, base_bottom = base
            x = int(args["x"]) if args.get("x") is not None else base_left
            y = int(args["y"]) if args.get("y") is not None else base_top
            width = int(args["width"]) if args.get("width") is not None else base_right - base_left
            height = int(args["height"]) if args.get("height") is not None else base_bottom - base_top
            if width < 1 or height < 1:
                return {"error": "requested frame has a non-positive size"}
            # pywin32's SetWindowPos returns None, not the Win32 BOOL, so its
            # return value says nothing; the readback below is the only claim.
            try:
                win32gui.SetWindowPos(
                    hwnd,
                    0,
                    x,
                    y,
                    width,
                    height,
                    win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE,
                )
            except Exception as exc:
                return {"error": f"frame change failed: {exc}"}
            after = rect()
            if after is None:
                return {"error": "the frame changed but could not be read back"}
            # A one-pixel tolerance absorbs the rounding a scaled display adds.
            verified = (
                abs(after[0] - x) <= 1 and abs(after[1] - y) <= 1
                and abs((after[2] - after[0]) - width) <= 1
                and abs((after[3] - after[1]) - height) <= 1
            )
            return {
                "requested": {"x": x, "y": y, "width": width, "height": height},
                "after": {"left": after[0], "top": after[1], "width": after[2] - after[0], "height": after[3] - after[1]},
                "verified": verified,
            }

        foreground_before = int(win32gui.GetForegroundWindow() or 0)
        before = rect()
        if before is None:
            return {"ok": False, "error": "target window has no readable rectangle", "hwnd": hwnd}
        placement = self._window_placement_state(hwnd)

        restored_from = ""
        # A minimized window has no frame to change until it is restored, so
        # that one is restored up front rather than discovered by failure.
        if placement == "minimized":
            failure = restore("minimized")
            if failure:
                return {"ok": False, "error": failure, "hwnd": hwnd, "restored_from": "minimized"}
            restored_from = "minimized"
            before = rect() or before

        outcome = attempt(before)
        if "error" not in outcome and outcome["verified"] is not True and placement == "maximized":
            # It really is maximized: the request landed on the restored
            # geometry while the window kept its maximized frame.
            failure = restore("maximized")
            if failure:
                return {"ok": False, "error": failure, "hwnd": hwnd, "restored_from": "maximized"}
            restored_from = "maximized"
            before = rect() or before
            outcome = attempt(before)
        if "error" in outcome:
            return {"ok": False, "error": outcome["error"], "hwnd": hwnd, "restored_from": restored_from or None}

        foreground_after = int(win32gui.GetForegroundWindow() or 0)
        self.snapshot = None
        self.cache.clear()
        return {
            "ok": outcome["verified"],
            "verified": outcome["verified"],
            "hwnd": hwnd,
            "resolved_by": resolved_by,
            "title": str(win32._window_text(hwnd) or ""),
            "requested": outcome["requested"],
            "before": {"left": before[0], "top": before[1], "width": before[2] - before[0], "height": before[3] - before[1]},
            "after": outcome["after"],
            "restored_from": restored_from or None,
            "placement_unknown": placement == "unknown",
            "foreground_before": foreground_before,
            "foreground_after": foreground_after,
            "activated": foreground_after != foreground_before,
        }

    def _scroll(self, args: dict[str, Any]) -> dict[str, Any]:
        receipt = self.raw_device.scroll(
            self._context(),
            int(args["x"]),
            int(args["y"]),
            int(args["amount"]),
            enforce_foreground=False,
            stop_event=self.stop_event,
        )
        return {
            **_receipt_payload(receipt),
            "x": int(args["x"]),
            "y": int(args["y"]),
            "amount": int(args["amount"]),
        }

    def _move_mouse(self, args: dict[str, Any]) -> dict[str, Any]:
        duration_ms = int(args.get("duration_ms") or round(float(args.get("duration") or 0) * 1000))
        receipt = self.raw_device.move_mouse(
            self._context(),
            int(args["x"]),
            int(args["y"]),
            duration_ms=max(0, duration_ms),
            enforce_foreground=False,
            stop_event=self.stop_event,
        )
        return {
            **_receipt_payload(receipt),
            "x": int(args["x"]),
            "y": int(args["y"]),
            "duration_ms": max(0, duration_ms),
        }

    def _drag(self, args: dict[str, Any]) -> dict[str, Any]:
        duration_ms = int(args.get("duration_ms") or round(float(args.get("duration", 0.4)) * 1000))
        points: list[tuple[int, int]] = []
        for point in args.get("points") or []:
            if isinstance(point, (list, tuple)) and len(point) == 2:
                points.append((int(point[0]), int(point[1])))
        held_keys = tuple(canonical_hotkey_args(normalize_hotkey_args(args.get("held_keys"))))
        try:
            button = MouseButton(str(args.get("button") or "left").casefold())
        except ValueError:
            return {"ok": False, "error": "drag button must be left, right, or middle"}
        receipt = self.raw_device.drag(
            self._context(),
            int(args["x1"]),
            int(args["y1"]),
            int(args["x2"]),
            int(args["y2"]),
            points=points,
            button=button,
            held_keys=held_keys,
            duration_ms=max(0, duration_ms),
            enforce_foreground=False,
            stop_event=self.stop_event,
        )
        return {
            **_receipt_payload(receipt),
            "from": [int(args["x1"]), int(args["y1"])],
            "to": [int(args["x2"]), int(args["y2"])],
            "points": [list(point) for point in points],
            "duration_ms": max(0, duration_ms),
        }

    def _draw_strokes(self, args: dict[str, Any]) -> dict[str, Any]:
        strokes = args.get("strokes") or []
        if not isinstance(strokes, list) or not strokes:
            return {"ok": False, "error": "draw_strokes requires non-empty strokes"}
        receipts: list[dict[str, Any]] = []
        for stroke in strokes:
            if not isinstance(stroke, list) or len(stroke) < 2:
                return {"ok": False, "error": "each stroke requires at least two points", "receipts": receipts}
            points = [(int(point[0]), int(point[1])) for point in stroke]
            result = self._drag({
                "x1": points[0][0],
                "y1": points[0][1],
                "x2": points[-1][0],
                "y2": points[-1][1],
                "points": [list(point) for point in points[1:-1]],
                "duration": args.get("duration", 0.4),
                "button": args.get("button", "left"),
            })
            receipts.append(result)
            if result.get("ok") is not True:
                return {"ok": False, "error": result.get("error") or "stroke input failed", "receipts": receipts}
        return {"ok": True, "stroke_count": len(strokes), "receipts": receipts}

    def _hold_key(self, args: dict[str, Any]) -> dict[str, Any]:
        keys = canonical_hotkey_args(normalize_hotkey_args(args.get("keys") or args.get("value")))
        duration_ms = int(args.get("duration_ms") or 0)
        try:
            events = tuple(
                [PhysicalInputEvent(InputEventKind.KEY_DOWN, key=key) for key in keys]
                + [PhysicalInputEvent(InputEventKind.WAIT, duration_ms=duration_ms)]
                + [PhysicalInputEvent(InputEventKind.KEY_UP, key=key) for key in reversed(keys)]
            )
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        receipt = self.raw_device.input_sequence(
            self._context(),
            events,
            enforce_foreground=False,
            stop_event=self.stop_event,
        )
        return {
            **_receipt_payload(receipt),
            "keys": keys,
            "duration_ms": duration_ms,
            "events": [
                {
                    "kind": event.kind.value,
                    "key": event.key,
                    "duration_ms": event.duration_ms,
                }
                for event in events
            ],
        }

    def _focus_window(self, args: dict[str, Any]) -> dict[str, Any]:
        raw_hwnd = args.get("hwnd")
        query = str(args.get("title") or args.get("query") or args.get("name") or "").strip()
        try:
            hwnd = int(raw_hwnd) if raw_hwnd not in (None, "") else 0
        except (TypeError, ValueError):
            hwnd = 0
        resolved_by = "hwnd"
        if not hwnd and query:
            folded = query.casefold()
            windows = win32.list_windows()
            exact = [item for item in windows if str(item.get("title") or "").casefold() == folded]
            partial = [
                item for item in windows
                if folded in str(item.get("title") or "").casefold()
                or folded in str(item.get("class_name") or "").casefold()
            ]
            matches = exact or partial
            if matches:
                hwnd = int(matches[0].get("hwnd") or 0)
                resolved_by = "exact_title" if exact else "title_contains"
        try:
            live = bool(hwnd and win32gui.IsWindow(hwnd))
        except Exception:
            live = False
        if not live:
            return {"ok": False, "error": "focus_window requires a live hwnd or matching title", "query": query}
        result = dict(action_executor.focus_window(hwnd) or {})
        result.update({
            "hwnd": hwnd,
            "title": str(win32._window_text(hwnd) or query),
            "resolved_by": resolved_by,
        })
        if result.get("ok") is True:
            self.hwnd = hwnd
            self.target_title = str(result["title"])
            self.snapshot = None
            self.cache.clear()
        return result

    @staticmethod
    def _split_launch_command(command: str) -> tuple[str, str]:
        try:
            parts = shlex.split(command, posix=False)
        except ValueError:
            parts = []
        if not parts:
            return command.strip(), ""
        app = str(parts[0]).strip().strip('"')
        arguments = " ".join(str(part) for part in parts[1:])
        return app, arguments

    def _launch(self, args: dict[str, Any]) -> dict[str, Any]:
        command = str(args.get("command") or "").strip()
        app, arguments = self._split_launch_command(command)
        try:
            result = dict(apps.launch_application(app, arguments) or {})
        except Exception as exc:
            return {
                "ok": False,
                "error": "application launch failed",
                "error_kind": type(exc).__name__,
                "command_length": len(command),
                "arguments_length": len(arguments),
            }
        try:
            activated = int(result.get("activated_hwnd") or 0)
        except (TypeError, ValueError):
            activated = 0
        if result.get("ok") is True and activated:
            self.hwnd = activated
            self.target_title = str(win32._window_text(activated) or app)
            self.snapshot = None
            self.cache.clear()
        safe = {
            "ok": result.get("ok") is True,
            "command_length": len(command),
            "arguments_length": len(arguments),
            "target_adopted": bool(activated),
        }
        via = str(result.get("via") or "")
        if via in {"existing", "path", "resolved", "startfile", "url"}:
            safe["via"] = via
        if activated:
            safe["activated_hwnd"] = activated
        if safe["ok"] is not True:
            safe.update({
                "error": "application launch failed",
                "error_kind": "launch_failed",
            })
        return safe

    def _wait_condition(self, args: dict[str, Any]) -> dict[str, Any]:
        condition = args.get("condition")
        if not isinstance(condition, dict):
            return {"ok": False, "error": "wait condition must be an object"}
        try:
            timeout_s = float(args.get("timeout_s", 10.0))
        except (TypeError, ValueError):
            timeout_s = float("nan")
        if not math.isfinite(timeout_s) or not 0 < timeout_s <= 120:
            return {"ok": False, "error": "wait timeout_s must be in (0, 120]"}
        window_query = str(condition.get("window_appears") or condition.get("window_title") or "").strip()
        text_appears = str(condition.get("text_appears") or "").strip()
        text_gone = str(condition.get("text_gone") or "").strip()
        if not any((window_query, text_appears, text_gone)):
            return {"ok": False, "error": "direct wait supports window_appears/window_title/text_appears/text_gone"}
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self._stopped():
                return {"ok": False, "canceled": True, "error": "wait canceled"}
            if window_query:
                folded = window_query.casefold()
                match = next((
                    item for item in win32.list_windows()
                    if folded in str(item.get("title") or "").casefold()
                    or folded in str(item.get("class_name") or "").casefold()
                ), None)
                if match is not None:
                    return {"ok": True, "condition": "window_appears", "window": match}
            else:
                try:
                    observed = self._observe({"include_icons": False})
                    visible = "\n".join(
                        str(item.get("text") or "")
                        for item in observed.get("elements") or []
                    )
                except Exception:
                    visible = ""
                if text_appears and text_appears in visible:
                    return {"ok": True, "condition": "text_appears"}
                if text_gone and text_gone not in visible:
                    return {"ok": True, "condition": "text_gone"}
            if self._wait(0.1):
                return {"ok": False, "canceled": True, "error": "wait canceled"}
        return {"ok": False, "timed_out": True, "error": "wait condition timed out"}

    def _pause(self, args: dict[str, Any]) -> dict[str, Any]:
        if isinstance(args.get("duration_s"), (int, float)):
            duration_s = float(args["duration_s"])
        elif isinstance(args.get("seconds"), (int, float)):
            duration_s = float(args["seconds"])
        elif isinstance(args.get("time"), (int, float)):
            duration_s = float(args["time"])
        elif isinstance(args.get("duration_ms"), (int, float)):
            duration_s = float(args["duration_ms"]) / 1000.0
        elif isinstance(args.get("ms"), (int, float)):
            duration_s = float(args["ms"]) / 1000.0
        else:
            duration_s = float(args.get("value") or 0) / 1000.0
        duration_s = max(0.0, min(duration_s, 120.0))
        started = time.monotonic()
        interrupted = self._wait(duration_s)
        return {
            "ok": not interrupted,
            "canceled": interrupted,
            "waited_ms": int((time.monotonic() - started) * 1000),
        }

    def _wait_change(self, args: dict[str, Any]) -> dict[str, Any]:
        region = _region_from_value(args.get("region") or args.get("effect_region"))
        kwargs = {
            "timeout_s": float(args.get("timeout_s", 3.0)),
            "min_ratio": float(args.get("min_ratio", 0.0005)),
            "stop_event": self.stop_event,
        }
        if region is not None:
            delta = self.watcher.wait_for_region_change(
                region,
                **kwargs,
                require_persistent=_truthy(args.get("require_persistent")),
                stable_ms=int(args.get("stable_ms", 350)),
                stable_max_ratio=float(args.get("stable_max_ratio", 0.0008)),
            )
        else:
            delta = self.watcher.wait_for_change(**kwargs)
        canceled = self._stopped()
        required = _truthy(args.get("required", True))
        return {
            "ok": not canceled and (bool(delta.changed) or not required),
            "changed": bool(delta.changed),
            "required": required,
            "canceled": canceled,
            "delta": delta.to_dict(),
            **({"region": _region_payload(region)} if region is not None else {}),
        }

    def _wait_stable(self, args: dict[str, Any]) -> dict[str, Any]:
        region = _region_from_value(args.get("region") or args.get("effect_region")) or self._target_region()
        stable_ms = max(0, int(args.get("stable_ms", 500)))
        timeout_s = max(0.0, float(args.get("timeout_s", 5.0)))
        max_ratio = max(0.0, float(args.get("max_ratio", 0.0008)))
        deadline = time.monotonic() + timeout_s
        stable_since: float | None = None
        before = self._capture_fn(region)
        delta = ScreenDelta(False, 0.0, [], 0)
        while time.monotonic() < deadline:
            if self._wait(1.0 / self.watcher.fps):
                return {"ok": False, "stable": False, "canceled": True, "error": "stable wait canceled"}
            after = self._capture_fn(region)
            delta = changed_regions(before, after, root_region=region, pixel_thresh=self.watcher.pixel_thresh)
            if delta.ratio <= max_ratio:
                stable_since = stable_since or time.monotonic()
                if (time.monotonic() - stable_since) * 1000 >= stable_ms:
                    return {"ok": True, "stable": True, "delta": delta.to_dict()}
            else:
                stable_since = None
            before = after
        required = _truthy(args.get("required", True))
        return {
            "ok": not required,
            "stable": False,
            "required": required,
            "delta": delta.to_dict(),
            "error": "screen did not become stable" if required else "",
        }

    def _read_text(self, args: dict[str, Any]) -> dict[str, Any]:
        observed = self._observe(args)
        query = str(args.get("query") or args.get("text") or "").strip().casefold()
        values = [
            str(item.get("text") or "").strip()
            for item in observed.get("elements") or []
            if str(item.get("text") or "").strip()
        ]
        if query:
            values = [value for value in values if query in value.casefold()]
        return {"ok": True, "text": "\n".join(values), "matches": len(values)}

    def _zoom(self, args: dict[str, Any]) -> dict[str, Any]:
        region = _region_from_value(args.get("region"))
        if region is None and all(args.get(key) is not None for key in ("x", "y", "width", "height")):
            region = _region_from_value(args)
        frame = self._capture_fn(region)
        if getattr(frame, "size", 0) <= 0:
            return {"ok": False, "error": "zoom capture was empty"}
        image = Image.fromarray(frame[:, :, ::-1].copy())
        payload = BytesIO()
        image.save(payload, format="JPEG", quality=max(30, min(95, int(args.get("quality", 75)))))
        return {
            "ok": True,
            "image_base64": base64.b64encode(payload.getvalue()).decode("ascii"),
            "width": image.width,
            "height": image.height,
            "region": _region_payload(region) if region is not None else None,
        }

    def _screenshot(self, args: dict[str, Any]) -> dict[str, Any]:
        if args.get("managed_artifact") is True:
            if not isinstance(self.artifact_store, ArtifactStore):
                return {"ok": False, "error": "managed artifact store is unavailable"}
            scope = str(args.get("scope") or "screen").strip().casefold()
            source_hwnd = int(args.get("hwnd") or self.hwnd or 0)
            artifact = self.artifact_store.capture_artifact(
                scope=scope,
                source_hwnd=source_hwnd,
                region=args.get("region"),
                source_window=str(args.get("source_window") or self.target_title),
                purpose=str(args.get("purpose") or "user_output"),
                sendable=bool(args.get("sendable", True)),
                metadata={"flow_managed_artifact": True},
            )
            if isinstance(self.evidence_registry, EvidenceRegistry):
                self.evidence_registry.record(
                    "artifact.current_run_valid",
                    provider="artifact_store_capture",
                    verified=True,
                    artifact_id=artifact.artifact_id,
                    metadata={"flow_managed_artifact": True, "sha256": artifact.sha256},
                )
            return {
                "ok": True,
                "artifact_id": artifact.artifact_id,
                "path": artifact.managed_path,
                "managed_path": artifact.managed_path,
                "sha256": artifact.sha256,
                "mime_type": artifact.mime_type,
                "width": artifact.width,
                "height": artifact.height,
                "size_bytes": artifact.size_bytes,
                "scope": artifact.source_scope,
                "region": artifact.metadata.get("region"),
            }
        path_text = str(args.get("path") or "").strip()
        if not path_text:
            return dict(action_executor.screenshot_base64(quality=int(args.get("quality", 75))) or {})
        path = Path(path_text).expanduser()
        if not path.is_absolute():
            return {"ok": False, "error": "screenshot path must be absolute"}
        if path.suffix.casefold() not in {".png", ".jpg", ".jpeg"}:
            return {"ok": False, "error": "screenshot path must end in .png, .jpg, or .jpeg"}
        if path.exists() and not bool(args.get("overwrite")):
            return {"ok": False, "error": f"screenshot path already exists: {path}"}
        if not path.parent.is_dir():
            return {"ok": False, "error": f" screenshot directory does not exist: {path.parent}"}
        region = _region_from_value(args)
        if bool(args.get("window")):
            region = window_region(int(args.get("hwnd") or self._ensure_target()))
        frame = self._capture_fn(region)
        image = Image.fromarray(frame[:, :, ::-1].copy())
        image.save(path)
        return {"ok": True, "path": str(path), "width": image.width, "height": image.height}

    def _clipboard_read(self, _args: dict[str, Any]) -> dict[str, Any]:
        text = str(action_executor.clipboard_read() or "")
        return {"ok": True, "text": text, **_text_metadata(text)}

    @staticmethod
    def _clipboard_write(args: dict[str, Any]) -> dict[str, Any]:
        text = str(args.get("text") or "")
        return {**dict(action_executor.clipboard_write(text) or {}), **_text_metadata(text)}

    def _clipboard_paste(self, _args: dict[str, Any]) -> dict[str, Any]:
        result = self._hotkey({"keys": ["ctrl", "v"]})
        result["clipboard_content_redacted"] = True
        return result

    def _routes(self) -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
        return {
            "observe": self._observe,
            "click": self._click,
            "click_element": self._click_element,
            "type_into_element": self._type_into_element,
            "type_text": self._type_text,
            "set_value": self._set_value,
            "hotkey": self._hotkey,
            "scroll": self._scroll,
            "move_mouse": self._move_mouse,
            "hold_key": self._hold_key,
            "drag": self._drag,
            "draw_strokes": self._draw_strokes,
            "focus_window": self._focus_window,
            "set_window_frame": self._set_window_frame,
            "launch": self._launch,
            "wait": lambda value: self._wait_condition(value) if isinstance(value.get("condition"), dict) else self._pause(value),
            "wait_change": self._wait_change,
            "wait_for_visual_change": self._wait_change,
            "wait_stable": self._wait_stable,
            "read_text": self._read_text,
            "zoom": self._zoom,
            "screenshot": self._screenshot,
            "clipboard_read": self._clipboard_read,
            "clipboard_write": self._clipboard_write,
            "clipboard_paste": self._clipboard_paste,
        }

    def _dispatch(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        route = self._routes().get(name)
        if route is None:
            return {"ok": False, "error": f"batch action not allowed: {name}"}
        try:
            result = route(dict(args))
        except Exception as exc:
            return {"ok": False, "error": f"{name} failed: {exc}"}
        return result if isinstance(result, dict) else {"ok": False, "error": f"{name} returned an invalid result"}

    def _replay_for(self, name: str, args: dict[str, Any], data: dict[str, Any]) -> dict[str, Any] | None:
        # Text/clipboard values are deliberately not replayable from BUS.  A
        # redacted executable step would silently perform the wrong action.
        if name in _SENSITIVE_TRACE_ACTIONS:
            return None
        if name in {"click", "click_element"}:
            if data.get("x") is None or data.get("y") is None:
                return None
            return {
                "tool": "click",
                "coords": [int(data["x"]), int(data["y"])],
                "button": str(args.get("button") or "left"),
                "double": bool(args.get("double")),
            }
        if name == "move_mouse":
            return {"tool": "move_mouse", "coords": [int(args["x"]), int(args["y"])]}
        if name == "scroll":
            return {
                "tool": "scroll",
                "coords": [int(args["x"]), int(args["y"])],
                "amount": int(args["amount"]),
            }
        if name == "drag":
            return {
                "tool": "drag",
                "from": [int(args["x1"]), int(args["y1"])],
                "to": [int(args["x2"]), int(args["y2"])],
                "points": args.get("points") or [],
                "duration": float(args.get("duration", 0.4)),
            }
        if name == "draw_strokes":
            return {
                "tool": "draw_strokes",
                "strokes": args.get("strokes") or [],
                "duration": float(args.get("duration", 0.4)),
            }
        if name == "hotkey":
            return {
                "tool": "hotkey",
                "keys": data.get("keys") or [],
                "repeat": max(1, int(args.get("repeat") or 1)),
            }
        if name == "hold_key":
            return {"tool": "input_sequence", "events": data.get("events") or []}
        if name == "focus_window" and self.target_title:
            return {"tool": "focus_window", "title": self.target_title}
        # ``set_window_frame`` is a live action today. Its offline step shape is
        # not emitted here on purpose: the replay vocabulary would have to learn
        # the tool first, and a step the replayer cannot execute is worse than no
        # step at all. Add it there, then return the shape below.
        if name == "set_window_frame":
            return None
        if name == "wait" and isinstance(args.get("condition"), dict):
            return {
                "tool": "wait",
                "condition": dict(args["condition"]),
                "timeout_s": float(args.get("timeout_s", 10.0)),
            }
        return None

    def _publish_child_start(
        self,
        batch_id: str,
        index: int,
        name: str,
        args: dict[str, Any],
    ) -> None:
        BUS.publish(
            "agent_batch_action_start",
            run_id=self.run_id,
            event_name="action.started",
            action_id=f"{batch_id}:{index}",
            parent_tool="run_action_batch",
            batch_id=batch_id,
            batch_index=index,
            tool=name,
            args=_redact_action_args(name, args),
            trace_screenshot=name not in _SENSITIVE_TRACE_ACTIONS,
        )

    def _publish_child_done(
        self,
        batch_id: str,
        index: int,
        name: str,
        args: dict[str, Any],
        data: dict[str, Any],
        elapsed_ms: int,
    ) -> None:
        replay = self._replay_for(name, args, data)
        meta = {
            "input_completed": data.get("input_completed"),
            "mutation_applied": bool(
                name in BATCH_MUTATING_ACTIONS and data.get("ok") is True
            ),
            "target_window": (
                {"title": self.target_title}
                if self.target_title and name not in _SENSITIVE_TRACE_ACTIONS
                else None
            ),
        }
        if name in {"type_text", "type_into_element", "clipboard_write"}:
            meta.update(_text_metadata(args.get("text", args.get("text_input", ""))))
            meta["text_redacted"] = True
        BUS.publish(
            "agent_batch_action",
            run_id=self.run_id,
            event_name="action.finished",
            action_id=f"{batch_id}:{index}",
            parent_tool="run_action_batch",
            batch_id=batch_id,
            batch_index=index,
            tool=name,
            args=_redact_action_args(name, args),
            ok=data.get("ok") is True,
            result_preview=_safe_event_preview(name, data, args),
            result_meta=meta,
            elapsed_ms=elapsed_ms,
            replay=replay,
            trace_screenshot=name not in _SENSITIVE_TRACE_ACTIONS,
        )

    def _execute_in_lease(
        self,
        actions: tuple[AtomicAction, ...],
        *,
        stop_on_error: bool,
        sync: dict[str, Any] | bool | None,
        subgoal: str,
    ) -> dict[str, Any]:
        metrics = BatchMetrics(started_ms=_now_ms())
        batch_id = f"batch-{metrics.started_ms}"
        results: list[dict[str, Any]] = []
        failed_at: int | None = None
        last_action = ""
        for index, action in enumerate(actions, start=1):
            if self._stopped():
                failed_at = index
                results.append({
                    "index": index,
                    "action": action.name,
                    "ok": False,
                    "canceled": True,
                    "error": "action batch canceled before the next child",
                })
                break
            name = action.name
            args = dict(action.inputs)
            last_action = name
            self._publish_child_start(batch_id, index, name, args)
            started = _now_ms()
            data = self._dispatch(name, args)
            elapsed_ms = max(0, _now_ms() - started)
            metrics.action_count += 1
            metrics.tool_latencies_ms.append(elapsed_ms)
            if name in BATCH_OBSERVE_ACTIONS:
                metrics.observe_count += 1
            if name.startswith("wait"):
                metrics.screen_wait_count += 1
            ok = data.get("ok") is True
            item = {
                "index": index,
                "action": name,
                "elapsed_ms": elapsed_ms,
                "ok": ok,
                "preview": _safe_event_preview(name, data, args),
                "data": data,
            }
            if not ok:
                item["error"] = str(data.get("error") or f"{name} failed")
            results.append(item)
            self._publish_child_done(batch_id, index, name, args, data, elapsed_ms)
            if not ok and failed_at is None:
                failed_at = index
                if stop_on_error:
                    break

        if failed_at is None and sync:
            sync_args = dict(sync) if isinstance(sync, dict) else {}
            sync_data = (
                self._wait_change(sync_args)
                if str(sync_args.get("mode") or "stable").casefold() == "change"
                else self._wait_stable({
                    "stable_ms": sync_args.get("stable_ms", 250),
                    "timeout_s": sync_args.get("timeout_s", 2.0),
                    "max_ratio": sync_args.get("max_ratio", 0.0008),
                    "required": bool(sync_args.get("required", False)),
                })
            )
            if sync_data.get("ok") is not True and bool(sync_args.get("required", False)):
                failed_at = len(actions) + 1
            results.append({
                "index": len(actions) + 1,
                "action": "sync",
                "ok": sync_data.get("ok") is True,
                "data": sync_data,
            })

        metrics.failed_at = failed_at
        metrics.elapsed_ms = max(0, _now_ms() - metrics.started_ms)
        metrics.controller_round_trips_saved = max(0, metrics.action_count - 1)
        payload: dict[str, Any] = {
            "ok": failed_at is None,
            "verified": False,
            "failed_at": failed_at,
            "subgoal": str(subgoal or ""),
            "controller_state": {
                "phase": "blocked" if failed_at is not None else "executed",
                "verified": False,
                "needs_observe": bool(failed_at is None and last_action not in BATCH_OBSERVE_ACTIONS),
                "failed_at": failed_at,
                "last_action": last_action,
            },
            "results": results,
            "metrics": metrics.to_dict(),
            "element_cache": self.cache.stats(),
            "last_action": last_action,
            "needs_observe": bool(failed_at is None and last_action not in BATCH_OBSERVE_ACTIONS),
            "transaction": {
                "planned_action_count": len(actions),
                "attempted_action_count": metrics.action_count,
                "committed_action_count": sum(
                    1 for item in results[:len(actions)] if item.get("ok") is True
                ),
                "next_uncommitted_action_index": failed_at,
                "remaining_action_count": (
                    max(0, len(actions) - failed_at + 1) if failed_at is not None else 0
                ),
            },
        }
        if failed_at is not None:
            failed_item = next(
                (item for item in results if item.get("index") == failed_at),
                {},
            )
            payload.update({
                "blocked": True,
                "failure_type": str(
                    (failed_item.get("data") or {}).get("failure_type")
                    or "CONTROLLER_ACTION_FAILED"
                ),
                "error": str(
                    failed_item.get("error")
                    or (failed_item.get("data") or {}).get("error")
                    or "controller action failed"
                ),
            })
        return payload

    def execute(
        self,
        actions: list[dict[str, Any]],
        stop_on_error: bool = True,
        sync: dict[str, Any] | bool | None = None,
        subgoal: str = "",
    ) -> dict[str, Any]:
        canonical, parse_errors = canonicalize_batch_actions(actions)
        validation_errors = [*parse_errors, *validate_batch_actions(canonical)] if canonical else list(parse_errors)
        if not canonical and not validation_errors:
            validation_errors = ["config.actions must be a non-empty array"]
        if validation_errors:
            return {
                "ok": False,
                "verified": False,
                "blocked": True,
                "failure_type": "INVALID_ACTION_TRANSACTION",
                "failed_at": 1,
                "validation_errors": validation_errors,
                "error": "; ".join(validation_errors),
                "results": [{"index": 1, "ok": False, "error": validation_errors[0]}],
            }

        has_mutation = any(action.name in BATCH_MUTATING_ACTIONS for action in canonical)
        has_clipboard = any(action.name in BATCH_CLIPBOARD_ACTIONS for action in canonical)
        scope = (
            "physical_clipboard"
            if has_mutation and has_clipboard
            else "physical"
            if has_mutation
            else "clipboard"
            if has_clipboard
            else None
        )

        def run_batch() -> dict[str, Any]:
            if not action_executor.release_held_input_state():
                return {
                    "ok": False,
                    "verified": False,
                    "blocked": True,
                    "failure_type": "STALE_CONTINUOUS_INPUT",
                    "failed_at": 1,
                    "error": "previous controller input could not be released",
                    "results": [],
                }
            payload: dict[str, Any]
            try:
                payload = self._execute_in_lease(
                    canonical,
                    stop_on_error=bool(stop_on_error),
                    sync=sync,
                    subgoal=subgoal,
                )
            finally:
                cleanup_ok = action_executor.release_held_input_state()
            if not cleanup_ok:
                payload.update({
                    "ok": False,
                    "verified": False,
                    "blocked": True,
                    "failure_type": "INPUT_RELEASE_INCOMPLETE",
                    "error": "controller input release cleanup was incomplete",
                })
            return payload

        if scope is None:
            return run_batch()
        try:
            return DESKTOP_ACTION_QUEUE.run(
                "controller_batch",
                run_batch,
                target_hwnd=int(self.hwnd or 0) or None,
                scope=scope,
                metadata={"subgoal": str(subgoal or ""), "action_count": len(canonical)},
                coalesce_nested=True,
                cancel_event=self.stop_event,
            )
        except DesktopActionCanceled:
            return {
                "ok": False,
                "verified": False,
                "blocked": True,
                "canceled": True,
                "failure_type": "ACTION_BATCH_CANCELED",
                "failed_at": 1,
                "error": "action batch canceled before acquiring the desktop",
                "results": [],
            }

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.watcher.stop()
        finally:
            action_executor.release_held_input_state()


__all__ = ["FastGuiController"]
