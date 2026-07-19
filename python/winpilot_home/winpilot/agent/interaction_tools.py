"""Input and visible-action tool handlers for ToolSession."""
from __future__ import annotations

import json
import hashlib
import inspect
import math
import sys
import time
from typing import Any

import win32gui

from ..action import executor, verify
from ..config import CONFIG
from ..perception import vision_cv
from ..utils.log import BUS, logger
from ..utils.screenshot import Region, capture, window_region
from .tool_result import ToolResult


_INPUT_ROLES = {"edit", "combobox", "\u7f16\u8f91", "\u7ec4\u5408\u6846"}
_ACTIONABLE_HIT_ROLES = (
    "button", "link", "hyperlink", "menuitem", "menu item", "tabitem",
    "tab item", "checkbox", "radio", "listitem", "treeitem", "splitbutton",
    "\u6309\u94ae", "\u94fe\u63a5", "\u83dc\u5355\u9879", "\u9009\u9879\u5361", "\u590d\u9009", "\u5355\u9009",
)
def _call_with_optional_hwnd(fn, *args, hwnd: int | None = None):
    """Keep test/adaptor compatibility while passing scope to new executors."""
    try:
        parameters = inspect.signature(fn).parameters.values()
        accepts_hwnd = any(
            parameter.name == "hwnd"
            or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )
    except (TypeError, ValueError):
        accepts_hwnd = True
    if accepts_hwnd:
        return fn(*args, hwnd=hwnd)
    return fn(*args)


def _clipboard_sequence_number() -> int | None:
    """Return the Windows clipboard generation without reading its payload."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        return int(ctypes.windll.user32.GetClipboardSequenceNumber())
    except (AttributeError, OSError, ValueError):
        return None


def _looks_like_input_element(role: str) -> bool:
    role_text = (role or "").strip().casefold()
    return role_text in _INPUT_ROLES or "edit" in role_text or "combo" in role_text


def _actionable_semantic_hit(
    element: Any,
    *,
    authored_point: tuple[int, int] | None = None,
) -> bool:
    """Whether a fresh point can be refined to this structured visual target.

    This is intentionally based on element provenance, geometry and confidence,
    never an application or label allow-list.  Native actionable controls are
    accepted, as are compact high-confidence OCR controls/text that lie under
    the model's already-authored click.  Broad containers and owner-drawn
    canvases keep the exact visual coordinate: snapping those to their center
    can move a correct click hundreds of pixels away from a child affordance.
    """
    source = str(getattr(element, "source", "") or "").strip().casefold()
    role = str(getattr(element, "role", "") or "").strip().casefold()
    text = str(getattr(element, "text", "") or "").strip()
    try:
        elem_id = int(getattr(element, "elem_id", -1) or -1)
        width = int(getattr(element, "width", 0) or 0)
        height = int(getattr(element, "height", 0) or 0)
        cx = int(getattr(element, "cx", 0) or 0)
        cy = int(getattr(element, "cy", 0) or 0)
        confidence = float(getattr(element, "confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        return False
    if elem_id <= 0 or width <= 0 or height <= 0:
        return False

    native_actionable = bool(
        source in {"uia", "win32"}
        and any(marker in role for marker in _ACTIONABLE_HIT_ROLES)
    )
    visual_control = bool(
        source in {"ocr", "ocr-control"}
        and text
        and confidence >= (0.48 if source == "ocr-control" else 0.68)
        and width <= 320
        and height <= 96
        and (
            source == "ocr-control"
            or role in {"text", "label", "statictext", "static text"}
            or any(marker in role for marker in _ACTIONABLE_HIT_ROLES)
        )
    )
    if not native_actionable and not visual_control:
        return False

    structural_role = any(marker in role for marker in (
        "listitem", "list item", "treeitem", "tree item", "menuitem", "menu item",
    ))
    if structural_role and (not text or width > 280 or height > 140):
        return False
    if not text and (width > 128 or height > 128):
        return False

    if authored_point is not None:
        authored_x, authored_y = authored_point
        center_distance = math.hypot(cx - authored_x, cy - authored_y)
        # A snap is a local correction, not a second target decision.  The cap
        # rejects large parent containers while still allowing an edge click on
        # an ordinary button/link to settle at its stable center.
        max_center_shift = max(
            24.0,
            min(96.0, 0.55 * math.hypot(float(width), float(height))),
        )
        if center_distance > max_center_shift:
            return False
    return True


def _prefer_physical_text_input(element: Any) -> bool:
    """Use real keyboard input where UIA SetValue is not a reliable commit.

    Composite controls frequently expose a writable wrapper even though changing
    that wrapper does not update the focused child editor.  This is a fixed
    control-shape property; task text and the value being entered never alter
    the execution path.
    """
    role = str(getattr(element, "role", "") or "").strip().casefold()
    return "combo" in role or "\u7ec4\u5408\u6846" in role


def _normalize_draw_strokes(raw: object) -> list[list[list[int]]]:
    """Normalize common model stroke encodings to ``[[[x, y], ...], ...]``.

    Tool schemas describe canonical polylines, but text models frequently emit
    either one unwrapped polyline (``[[x, y], ...]``) or line segments encoded
    as ``[x1, y1, x2, y2]``.  Both are unambiguous and safe to normalize in the
    controller instead of wasting LLM retries on array nesting.
    """
    if not isinstance(raw, list) or not raw:
        return []

    def number(value: object) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    def point(value: object) -> bool:
        return (
            isinstance(value, (list, tuple))
            and len(value) >= 2
            and number(value[0])
            and number(value[1])
        )

    # A model often omits the outer list for a single polyline.
    candidates: list[object]
    if all(point(item) and len(item) == 2 for item in raw):
        candidates = [raw]
    else:
        candidates = list(raw)

    normalized: list[list[list[int]]] = []
    for stroke in candidates:
        if not isinstance(stroke, (list, tuple)) or not stroke:
            continue
        if all(number(value) for value in stroke):
            values = [int(value) for value in stroke]
            if len(values) < 4 or len(values) % 2:
                continue
            pts = [[values[index], values[index + 1]] for index in range(0, len(values), 2)]
        else:
            pts = [
                [int(value[0]), int(value[1])]
                for value in stroke
                if point(value)
            ]
        if len(pts) >= 2:
            normalized.append(pts)
    return normalized


def _validate_normalized_draw_strokes(
    raw: object,
) -> tuple[list[list[list[float]]], str]:
    """Validate canonical 0..1000 work-surface paths without partial repair."""
    if not isinstance(raw, list) or not raw:
        return [], "normalized_strokes must contain at least one stroke"
    normalized: list[list[list[float]]] = []
    for stroke_index, stroke in enumerate(raw):
        if not isinstance(stroke, (list, tuple)) or len(stroke) < 2:
            return [], (
                f"normalized_strokes[{stroke_index}] must contain at least two [x,y] points"
            )
        points: list[list[float]] = []
        for point_index, point in enumerate(stroke):
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                return [], (
                    f"normalized_strokes[{stroke_index}][{point_index}] must be exactly [x,y]"
                )
            if any(
                isinstance(value, bool) or not isinstance(value, (int, float))
                for value in point
            ):
                return [], (
                    f"normalized_strokes[{stroke_index}][{point_index}] coordinates must be numeric"
                )
            x, y = float(point[0]), float(point[1])
            if not math.isfinite(x) or not math.isfinite(y):
                return [], (
                    f"normalized_strokes[{stroke_index}][{point_index}] coordinates must be finite"
                )
            if not (0.0 <= x <= 1000.0 and 0.0 <= y <= 1000.0):
                return [], (
                    f"normalized_strokes[{stroke_index}][{point_index}] is outside 0..1000; "
                    "the whole action was rejected instead of clamping"
                )
            points.append([x, y])
        normalized.append(points)
    return normalized, ""


def _tools_attr(name: str, default):
    """Preserve historical monkeypatch paths on winpilot.agent.tools."""
    tools_mod = sys.modules.get("winpilot.agent.tools")
    return getattr(tools_mod, name, default) if tools_mod is not None else default


def _expect_timeout(args: dict, default: float = 6.0) -> float:
    expect = args.get("expect")
    if isinstance(expect, dict):
        try:
            return float(expect.get("timeout_s", default))
        except (TypeError, ValueError):
            return default
    return default


def _printable_text_sequence(keys: list[str]) -> str:
    if len(keys) <= 1:
        return ""
    aliases = {"space": " ", "plus": "+", "equals": "=", "equal": "="}
    chars: list[str] = []
    for key in keys:
        value = str(key)
        low = value.casefold()
        if low in aliases:
            chars.append(aliases[low])
        elif len(value) == 1 and value.isprintable():
            chars.append(value)
        else:
            return ""
    return "".join(chars)


def normalize_key_sequence(raw: Any) -> list[str]:
    """Normalize model-produced hotkey args without treating JSON as text.

    Some providers occasionally return an array-looking string for a JSON
    schema array, and in rare cases append an extra brace. Shortcut tools are
    too risky to interpret such strings as printable text.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        text = raw.strip()
        parsed: Any = None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            if "[" in text and "]" in text:
                bracketed = text[text.find("["):text.rfind("]") + 1]
                try:
                    parsed = json.loads(bracketed)
                except json.JSONDecodeError:
                    parsed = None
        if isinstance(parsed, (list, tuple)):
            return [str(key) for key in parsed]
        if isinstance(parsed, str):
            text = parsed.strip()
        if "+" in text:
            parts = [part.strip() for part in text.split("+")]
            return [part for part in parts if part]
        if "," in text:
            parts = [part.strip(" \t\r\n'\"") for part in text.split(",")]
            return [part for part in parts if part]
        return [text] if text else []
    if isinstance(raw, (list, tuple)):
        out: list[str] = []
        for item in raw:
            if isinstance(item, str) and item.strip().startswith("["):
                parsed = normalize_key_sequence(item)
                if len(parsed) > 1:
                    out.extend(parsed)
                    continue
            out.append(str(item))
        return out
    return [str(raw)]


def canonical_hotkey_order(keys: list[str]) -> list[str]:
    """Place chord modifiers first while preserving all relative ordering."""
    aliases = {
        "control": "ctrl",
        "ctl": "ctrl",
        "option": "alt",
        "command": "meta",
        "cmd": "meta",
        "super": "meta",
    }
    normalized = [aliases.get(str(key).casefold(), str(key).casefold()) for key in keys]
    modifiers = {"ctrl", "alt", "shift", "meta", "control_l", "control_r", "alt_l", "alt_r", "shift_l", "shift_r"}
    chord_modifiers = [key for key in normalized if key in modifiers]
    ordinary = [key for key in normalized if key not in modifiers]
    return [*chord_modifiers, *ordinary] if chord_modifiers else normalized


class InteractionToolMixin:
    def _element_at_point(self, x: int, y: int):
        snapshot = getattr(self, "snapshot", None)
        elements = getattr(snapshot, "elements", None) or []
        hits = []
        for element in elements:
            try:
                left = int(getattr(element, "left", 0))
                top = int(getattr(element, "top", 0))
                width = int(getattr(element, "width", 0))
                height = int(getattr(element, "height", 0))
            except (TypeError, ValueError):
                continue
            if width <= 0 or height <= 0:
                continue
            if left <= x <= left + width and top <= y <= top + height:
                hits.append((width * height, element))
        if not hits:
            return None
        hits.sort(key=lambda item: item[0])
        return hits[0][1]

    def _semantic_element_near_point(self, x: int, y: int):
        """Return a compact fresh semantic target at or just beside a point.

        OCR boxes often cover only glyph ink while a user/model clicks the icon
        or padding beside it.  A small edge tolerance makes that grounding useful
        without letting an unrelated distant control replace the visual target.
        """
        snapshot = getattr(self, "snapshot", None)
        elements = getattr(snapshot, "elements", None) or []
        candidates: list[tuple[float, float, int, Any]] = []
        for element in elements:
            try:
                left = int(getattr(element, "left", 0))
                top = int(getattr(element, "top", 0))
                width = int(getattr(element, "width", 0))
                height = int(getattr(element, "height", 0))
                cx = int(getattr(element, "cx", left + width // 2))
                cy = int(getattr(element, "cy", top + height // 2))
            except (TypeError, ValueError):
                continue
            if width <= 0 or height <= 0:
                continue
            right = left + width
            bottom = top + height
            edge_dx = max(left - x, 0, x - right)
            edge_dy = max(top - y, 0, y - bottom)
            edge_distance = math.hypot(edge_dx, edge_dy)
            if edge_distance > 24.0:
                continue
            # Plain OCR describes glyph geometry, not control ownership.  A
            # single OCR box can merge several adjacent actions into one text
            # row; centering an authored point that is already inside that box
            # can silently replace the selected left/right sub-action with a
            # different neighbour.  Preserve such exact visual coordinates.
            # OCR-control and native elements may still center because their
            # boxes represent one structured control, while plain OCR remains
            # useful for correcting a click just outside the glyph bounds.
            source = str(
                getattr(element, "source", "") or ""
            ).strip().casefold()
            if source == "ocr" and edge_distance <= 1e-6:
                continue
            if not _actionable_semantic_hit(
                element,
                authored_point=(x, y),
            ):
                continue
            center_distance = math.hypot(cx - x, cy - y)
            # Containing boxes win over nearby boxes; then prefer the smallest
            # local correction and the most precise geometry.
            candidates.append((edge_distance, center_distance, width * height, element))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        return candidates[0][3]

    def _action_hwnd(self) -> int | None:
        # Action dispatch must not resolve/adopt a new foreground window.  The
        # remembered target is established by launch/focus/observe workflows;
        # mutating actions merely consume that binding.
        try:
            if self._window_alive(self.hwnd):
                return int(self.hwnd)
        except Exception:
            pass
        if CONFIG.get("controller", "multi_input_cooperation", default=False):
            return None
        try:
            fg = win32gui.GetForegroundWindow()
            if fg and win32gui.IsWindowVisible(fg):
                return int(fg)
        except Exception:
            pass
        return self.hwnd

    @staticmethod
    def _spatial_constraint_region(
        args: dict,
    ) -> tuple[Region | None, str]:
        raw = args.get("_constraint_region")
        if raw is None:
            raw = args.get("constraint_region")
        if raw is None:
            return None, ""
        if isinstance(raw, Region):
            region = raw
        elif isinstance(raw, dict):
            try:
                region = Region(
                    int(raw.get("x", raw.get("left"))),
                    int(raw.get("y", raw.get("top"))),
                    int(raw["width"]),
                    int(raw["height"]),
                )
            except (KeyError, TypeError, ValueError):
                return None, "constraint_region requires integer x/y/width/height"
        else:
            return None, "constraint_region must be an object"
        if region.width <= 0 or region.height <= 0:
            return None, "constraint_region width and height must be positive"
        return region, ""

    @staticmethod
    def _point_in_spatial_constraint(
        region: Region,
        x: int,
        y: int,
    ) -> bool:
        return bool(
            region.left <= int(x) < region.right
            and region.top <= int(y) < region.bottom
        )

    def _spatial_constraint_block(
        self,
        args: dict,
        points: list[tuple[int, int]],
        *,
        action: str,
    ) -> ToolResult | None:
        region, error = self._spatial_constraint_region(args)
        if error:
            return ToolResult(self._fmt({
                "ok": False,
                "blocked": True,
                "failure_type": "INVALID_SPATIAL_CONSTRAINT",
                "error": error,
            }))
        if region is None:
            return None
        for index, (x, y) in enumerate(points):
            if self._point_in_spatial_constraint(region, x, y):
                continue
            return ToolResult(self._fmt({
                "ok": False,
                "verified": False,
                "blocked": True,
                "failure_type": "SPATIAL_CONSTRAINT_VIOLATION",
                "action": action,
                "constraint_region": {
                    "x": region.left,
                    "y": region.top,
                    "width": region.width,
                    "height": region.height,
                },
                "rejected_point": [int(x), int(y)],
                "rejected_point_index": index,
                "error": (
                    "The requested spatial action leaves its declared action "
                    "region. Nothing was executed."
                ),
            }))
        return None

    def _verification_watcher(self):
        try:
            return self._fast_controller().watcher
        except Exception:
            return None

    def _t_double_click(self, args: dict) -> ToolResult:
        """Execute the explicit model primitive through the proven click path."""
        click_args = dict(args)
        click_args["double"] = True
        result = self._t_click(click_args)
        if isinstance(result.replay, dict):
            result.replay["tool"] = "double_click"
            result.replay["double"] = True
        return result

    def _t_click(self, args: dict) -> ToolResult:
        semantic_keys = (
            "label", "text_query", "fallback_text", "role",
            "expected_region", "visual_anchor", "anchor",
        )
        semantic_target = bool(
            args.get("element_id") is None
            and not (args.get("x") is not None and args.get("y") is not None)
            and any(args.get(key) not in (None, "", []) for key in semantic_keys)
        )
        if semantic_target:
            # Direct native-vision turns should not have to detour through a
            # special subgoal just to use the same semantic locator already
            # available to controller batches.  The controller performs a
            # fresh UIA/OCR observe when necessary, resolves the anchor, applies
            # visual servo immediately before mouse-down, and returns its real
            # effect evidence.  Its eventual physical click bypasses this
            # ToolSession handler, so this route cannot recurse.
            payload = self._fast_controller().execute(
                [{"action": "click", **dict(args)}],
                stop_on_error=True,
                subgoal="click the currently grounded visible target",
            )
            return ToolResult(
                self._fmt(payload),
                replay={
                    "tool": "click",
                    "semantic_target": {
                        key: args.get(key)
                        for key in semantic_keys
                        if args.get(key) not in (None, "", [])
                    },
                    "controller_routed": True,
                },
            )
        executor_mod = _tools_attr("executor", executor)
        verify_mod = _tools_attr("verify", verify)
        element_id = args.get("element_id")
        button = args.get("button", "left")
        double = bool(args.get("double", False))
        physical = bool(args.get("physical") or args.get("force_physical"))
        prepositioned = bool(args.get("_prepositioned"))
        replay: dict[str, Any] = {
            "tool": "click", "button": button, "double": double,
            "expect": args.get("expect"),
        }

        if element_id is not None:
            element, err = self._resolve_element(int(element_id))
            if element is None:
                return ToolResult(err)
            x, y = element.cx, element.cy
            replay.update({
                "text": element.text, "role": element.role,
                "coords": [x, y], "source": element.source,
            })
            if _looks_like_input_element(element.role):
                self._last_input_element = element
            def action() -> dict:
                if button == "left" and not double and not physical and executor_mod.uia_invoke(element):
                    BUS.publish("action", action="uia_invoke", elem_id=element.elem_id,
                                text=element.text)
                    return {"ok": True, "via": "uia_invoke"}
                click_kwargs = {"prepositioned": True} if prepositioned else {}
                if physical:
                    # ``physical`` is an execution guarantee, not merely a UIA
                    # preference.  Owner-drawn Chromium/DirectUI surfaces may
                    # accept PostMessage syntactically while ignoring it, so a
                    # grounded physical request must reach SendInput.
                    click_kwargs["force_physical"] = True
                return executor_mod.click(
                    x, y, button=button, double=double,
                    hwnd=self._action_hwnd(), **click_kwargs,
                )
        elif "x" in args and "y" in args:
            x, y = int(args["x"]), int(args["y"])
            authored_x, authored_y = x, y
            semantic_hit = None
            semantic_candidate = self._semantic_element_near_point(x, y)
            if (
                semantic_candidate is not None
                and bool(CONFIG.get(
                    "controller", "coordinate_semantic_snap", default=False,
                ))
                and not bool(args.get("disable_semantic_snap"))
            ):
                resolved, _ = self._resolve_element(
                    int(getattr(semantic_candidate, "elem_id", -1) or -1),
                )
                if resolved is not None:
                    semantic_hit = resolved
                    resolved_source = str(
                        getattr(resolved, "source", "") or ""
                    ).strip().casefold()
                    try:
                        resolved_left = int(getattr(resolved, "left", 0) or 0)
                        resolved_top = int(getattr(resolved, "top", 0) or 0)
                        resolved_width = int(getattr(resolved, "width", 0) or 0)
                        resolved_height = int(getattr(resolved, "height", 0) or 0)
                    except (TypeError, ValueError):
                        resolved_left = resolved_top = 0
                        resolved_width = resolved_height = 0
                    broad_plain_ocr = bool(
                        resolved_source == "ocr"
                        and resolved_width > 0
                        and resolved_height > 0
                        and (
                            resolved_width >= 72
                            or resolved_width >= resolved_height * 3
                        )
                    )
                    if broad_plain_ocr:
                        # A plain OCR rectangle can be one merged row containing
                        # several independent actions.  If an authored point is
                        # just outside the glyph ink, projecting it to the nearest
                        # interior pixel preserves the intended left/right
                        # subtarget.  Re-centering the whole row can silently turn
                        # "first action" into a neighbouring action.
                        inset_x = max(1, min(3, resolved_width // 12))
                        inset_y = max(1, min(3, resolved_height // 6))
                        min_x = resolved_left + inset_x
                        max_x = resolved_left + resolved_width - inset_x
                        min_y = resolved_top + inset_y
                        max_y = resolved_top + resolved_height - inset_y
                        x = max(min_x, min(authored_x, max_x))
                        y = max(min_y, min(authored_y, max_y))
                        snap_mode = "nearest_plain_ocr_subtarget"
                    else:
                        x, y = int(resolved.cx), int(resolved.cy)
                        snap_mode = "semantic_center"
                    replay["semantic_hit_snap"] = {
                        "element_id": int(resolved.elem_id),
                        "text": str(resolved.text or "")[:120],
                        "role": str(resolved.role or ""),
                        "source": str(resolved.source or ""),
                        "mode": snap_mode,
                        "authored_coords": [authored_x, authored_y],
                        "snapped_coords": [x, y],
                    }
            replay["coords"] = [x, y]

            def action() -> dict:
                if (
                    semantic_hit is not None
                    and button == "left"
                    and not double
                    and not physical
                    and executor_mod.uia_invoke(semantic_hit)
                ):
                    BUS.publish(
                        "action",
                        action="semantic_hit_uia_invoke",
                        elem_id=semantic_hit.elem_id,
                        text=semantic_hit.text,
                        authored_coords=[authored_x, authored_y],
                        snapped_coords=[x, y],
                    )
                    return {
                        "ok": True,
                        "via": "semantic_hit_uia_invoke",
                        "semantic_hit_snap": replay["semantic_hit_snap"],
                    }
                click_kwargs = {"prepositioned": True} if prepositioned else {}
                if physical:
                    click_kwargs["force_physical"] = True
                clicked = executor_mod.click(
                    x, y, button=button, double=double,
                    hwnd=self._action_hwnd(), **click_kwargs,
                )
                if semantic_hit is not None and isinstance(clicked, dict):
                    clicked.setdefault("semantic_hit_snap", replay["semantic_hit_snap"])
                return clicked
        else:
            return ToolResult("click 需要 element_id 或 x,y 坐标")

        constraint_block = self._spatial_constraint_block(
            args,
            [(x, y)],
            action="click",
        )
        if constraint_block is not None:
            return constraint_block

        result = verify_mod.run_with_verify(
            "click", action, expect=args.get("expect"), hwnd=self.hwnd,
            retryable=bool(args.get("retryable", False)),
            timeout_s=_expect_timeout(args), stop_event=self.stop_event,
            watcher=self._verification_watcher(),
            run_id=getattr(self, "run_id", None))
        if not args.get("expect"):
            result.setdefault("verified", False)
            result.setdefault("verify_detail", "未提供 expect；ok 只表示点击已执行，必须 observe 确认界面变化")
        return ToolResult(self._fmt(result), replay=replay)

    def _t_type_text(self, args: dict) -> ToolResult:
        executor_mod = _tools_attr("executor", executor)
        verify_mod = _tools_attr("verify", verify)
        win32gui_mod = _tools_attr("win32gui", win32gui)
        text = str(args["text"])
        element_id = args.get("element_id")
        native_visual = bool(getattr(self, "native_visual_runtime", False))
        replay: dict[str, Any] = {
            "tool": "type_text", "text_input": text, "expect": args.get("expect"),
        }
        if element_id is not None:
            element, err = self._resolve_element(int(element_id))
            if element is None:
                return ToolResult(err)
            replay.update({
                "element_text": element.text, "coords": [element.cx, element.cy],
            })
        elif not native_visual:
            element = getattr(self, "_last_input_element", None)
            if element is not None:
                replay.update({
                    "element_text": getattr(element, "text", ""),
                    "coords": [getattr(element, "cx", 0), getattr(element, "cy", 0)],
                    "source": "last_clicked_input",
                })
        else:
            element = None

        def action() -> dict:
            target = self._action_hwnd()
            if element is not None and not native_visual:
                prefer_physical = _prefer_physical_text_input(element)
                if not prefer_physical and executor_mod.uia_set_value(element, text):
                    return {"ok": True, "via": "uia_set_value"}
            cooperative_guard = getattr(
                executor_mod, "cooperative_input_guard", lambda *_args, **_kwargs: None,
            )
            cooperative_block = cooperative_guard("type_text", target)
            if cooperative_block is not None:
                return cooperative_block
            if element is not None:
                click_result = executor_mod.click(
                    element.cx, element.cy, hwnd=self._action_hwnd(),
                )
                if click_result.get("ok") is not True:
                    return click_result
                time.sleep(0.15)
            if target and win32gui_mod.GetForegroundWindow() != target:
                return {
                    "ok": False,
                    "verified": False,
                    "blocked": True,
                    "action_executed": False,
                    "error": "foreground window no longer matches the bound input target",
                    "target_hwnd": int(target),
                    "foreground_hwnd": int(win32gui_mod.GetForegroundWindow() or 0),
                }
            return executor_mod.type_text(text, hwnd=target)

        result = verify_mod.run_with_verify(
            "type_text", action, expect=args.get("expect"),
            hwnd=self.hwnd, retryable=False, timeout_s=_expect_timeout(args),
            stop_event=self.stop_event, watcher=self._verification_watcher(),
            run_id=getattr(self, "run_id", None))
        if not args.get("expect"):
            result.setdefault("verified", False)
            result.setdefault("verify_detail", "未提供 expect；ok 只表示输入动作已执行，必须 observe/read_text 确认文本位置")
        return ToolResult(self._fmt(result), replay=replay)

    def _t_hotkey(self, args: dict) -> ToolResult:
        executor_mod = _tools_attr("executor", executor)
        verify_mod = _tools_attr("verify", verify)
        win32gui_mod = _tools_attr("win32gui", win32gui)
        keys = canonical_hotkey_order(normalize_key_sequence(args.get("keys")))
        if not keys:
            return ToolResult("hotkey requires keys[]")
        printable_text = _printable_text_sequence(keys)
        if printable_text:
            result = self._t_type_text({
                "text": printable_text,
                "expect": args.get("expect"),
            })
            try:
                data = json.loads(result.text or "{}")
                if isinstance(data, dict):
                    data["coerced_from_hotkey"] = keys
                    result.text = self._fmt(data)
            except (json.JSONDecodeError, TypeError):
                pass
            return result

        key_set = {str(key).strip().casefold() for key in keys}
        verifies_text_copy = (
            "c" in key_set
            and bool(key_set.intersection({"ctrl", "control", "ctl"}))
        )
        clipboard_sequence_before = (
            _clipboard_sequence_number() if verifies_text_copy else None
        )

        def action() -> dict:
            target = self._action_hwnd()
            cooperative_guard = getattr(
                executor_mod, "cooperative_input_guard", lambda *_args, **_kwargs: None,
            )
            cooperative_block = cooperative_guard("hotkey", target)
            if cooperative_block is not None:
                return cooperative_block
            if target and win32gui_mod.GetForegroundWindow() != target:
                return {
                    "ok": False,
                    "verified": False,
                    "blocked": True,
                    "action_executed": False,
                    "error": "foreground window no longer matches the bound input target",
                    "target_hwnd": int(target),
                    "foreground_hwnd": int(win32gui_mod.GetForegroundWindow() or 0),
                }
            result = _call_with_optional_hwnd(
                executor_mod.press_keys, keys, hwnd=target,
            )
            if not verifies_text_copy:
                return result
            time.sleep(0.08)
            sequence_after = _clipboard_sequence_number()
            clipboard_text = executor_mod.clipboard_read()
            sequence_changed = (
                clipboard_sequence_before is not None
                and sequence_after is not None
                and sequence_after != clipboard_sequence_before
            )
            copied_text = sequence_changed and bool(clipboard_text)
            result.update({
                "clipboard_sequence_changed": sequence_changed,
                "clipboard_text_length": len(clipboard_text),
                "clipboard_text_fingerprint": (
                    hashlib.sha256(clipboard_text.encode("utf-8")).hexdigest()
                    if clipboard_text
                    else ""
                ),
                "verified": copied_text,
                "verify_method": "clipboard_sequence_and_nonempty_text",
                "verify_detail": (
                    "Ctrl+C changed the Windows clipboard generation and produced non-empty text."
                    if copied_text
                    else "Ctrl+C was sent, but no new non-empty clipboard text was observed."
                ),
            })
            return result

        result = verify_mod.run_with_verify(
            "hotkey", action,
            expect=args.get("expect"), hwnd=self.hwnd, retryable=False,
            timeout_s=_expect_timeout(args), stop_event=self.stop_event,
            watcher=self._verification_watcher(),
            run_id=getattr(self, "run_id", None))
        if not args.get("expect"):
            result.setdefault("verified", False)
            result.setdefault("verify_detail", "未提供 expect；ok 只表示快捷键已发送，必须 observe/read_text 确认结果")
        return ToolResult(
            self._fmt(result),
            replay={"tool": "hotkey", "keys": keys, "expect": args.get("expect")},
        )

    def _t_scroll(self, args: dict) -> ToolResult:
        executor_mod = _tools_attr("executor", executor)
        x, y, amount = int(args["x"]), int(args["y"]), int(args["amount"])
        result = _call_with_optional_hwnd(
            executor_mod.scroll,
            x,
            y,
            amount,
            hwnd=self._action_hwnd(),
        )
        return ToolResult(
            self._fmt(result),
            replay={"tool": "scroll", "coords": [x, y], "amount": amount},
        )

    def _t_drag(self, args: dict) -> ToolResult:
        executor_mod = _tools_attr("executor", executor)
        capture_fn = _tools_attr("capture", capture)
        x1, y1 = int(args["x1"]), int(args["y1"])
        x2, y2 = int(args["x2"]), int(args["y2"])
        points = args.get("points") if isinstance(args.get("points"), list) else None
        duration = float(args.get("duration", 0.4))
        constraint_points = [(x1, y1)]
        for point in points or []:
            try:
                constraint_points.append((int(point[0]), int(point[1])))
            except (TypeError, ValueError, IndexError, KeyError):
                continue
        constraint_points.append((x2, y2))
        constraint_block = self._spatial_constraint_block(
            args,
            constraint_points,
            action="drag",
        )
        if constraint_block is not None:
            return constraint_block
        region = self._drag_feedback_region(x1, y1, x2, y2, points)
        before = None
        try:
            before = capture_fn(region)
        except Exception as exc:
            logger.debug("drag pre-capture failed: %s", exc)
        result = executor_mod.drag(x1, y1, x2, y2, duration_s=duration,
                                   points=points, hwnd=self.hwnd)
        if before is not None:
            try:
                time.sleep(0.12)
                after = capture_fn(region)
                ratio = vision_cv.diff_ratio(before, after)
                result["visual_change_ratio"] = round(float(ratio), 5)
                changed = bool(ratio >= 0.0008)
                result["visual_change_detected"] = changed
            except Exception as exc:
                logger.debug("drag post-capture/diff failed: %s", exc)
        return ToolResult(
            self._fmt(result),
            replay={"tool": "drag", "from": [x1, y1], "to": [x2, y2],
                    "points": points, "duration": duration},
        )

    @staticmethod
    def _drag_feedback_region(
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        points: list | None,
    ) -> Region:
        coords: list[tuple[int, int]] = [(x1, y1), (x2, y2)]
        for point in points or []:
            try:
                coords.append((int(point[0]), int(point[1])))
            except (TypeError, ValueError, IndexError, KeyError):
                continue
        xs = [x for x, _ in coords]
        ys = [y for _, y in coords]
        pad = 28
        left = min(xs) - pad
        top = min(ys) - pad
        right = max(xs) + pad
        bottom = max(ys) + pad
        return Region(left, top, max(1, right - left), max(1, bottom - top)).clamp_to_screen()

    @staticmethod
    def _draw_region_payload(region: Region) -> dict[str, int]:
        return {
            "x": int(region.left),
            "y": int(region.top),
            "width": int(region.width),
            "height": int(region.height),
        }

    def _draw_target_geometry(self) -> tuple[int | None, Region | None, str]:
        """Return the explicitly bound, visible target window geometry."""
        target = getattr(self, "hwnd", None)
        try:
            target = int(target) if target else None
        except (TypeError, ValueError):
            target = None
        if not target:
            return None, None, (
                "normalized_strokes requires an explicitly focused/observed target window"
            )
        try:
            if not self._window_alive(target):
                return target, None, "the bound target window is no longer visible"
            if bool(win32gui.IsIconic(target)):
                return target, None, "the bound target window is minimized"
        except Exception:
            return target, None, "the bound target window could not be validated"
        try:
            region_fn = _tools_attr("window_region", window_region)
            region = region_fn(target).clamp_to_screen()
        except Exception as exc:
            return target, None, f"target-window geometry could not be read: {exc}"
        if region.width < 160 or region.height < 120:
            return target, None, "the visible target window is too small to ground a work surface"
        return target, region, ""

    def _cached_draw_surface(
        self,
        target: int,
        window: Region,
    ) -> tuple[Region | None, dict[str, Any] | None]:
        cache = getattr(self, "_draw_surface_cache", None)
        if not isinstance(cache, dict):
            return None, None
        expected_window = self._draw_region_payload(window)
        if cache.get("hwnd") != target or cache.get("window_region") != expected_window:
            self._draw_surface_cache = None
            return None, None
        raw = cache.get("surface_region")
        if not isinstance(raw, dict):
            self._draw_surface_cache = None
            return None, None
        try:
            surface = Region(
                int(raw["x"]), int(raw["y"]),
                int(raw["width"]), int(raw["height"]),
            )
        except (KeyError, TypeError, ValueError):
            self._draw_surface_cache = None
            return None, None
        if (
            surface.width < 120
            or surface.height < 80
            or surface.left < window.left
            or surface.top < window.top
            or surface.right > window.right
            or surface.bottom > window.bottom
        ):
            self._draw_surface_cache = None
            return None, None
        evidence = dict(cache)
        evidence["mapping_source"] = "same_hwnd_and_window_geometry_cache"
        return surface, evidence

    def _locate_draw_surface(
        self,
        target: int,
        window: Region,
        capture_fn,
    ) -> tuple[Region | None, dict[str, Any] | None, str]:
        cached, evidence = self._cached_draw_surface(target, window)
        if cached is not None:
            return cached, evidence, ""
        try:
            image = capture_fn(window)
        except Exception as exc:
            return None, None, f"could not capture current target-window pixels: {exc}"
        if image is None or getattr(image, "size", 0) == 0:
            return None, None, "current target-window capture was empty"
        try:
            image_height, image_width = image.shape[:2]
        except (AttributeError, TypeError, ValueError):
            return None, None, "current target-window capture had invalid pixel geometry"
        if image_width != window.width or image_height != window.height:
            return None, None, (
                "target-window capture dimensions did not match its physical-pixel geometry"
            )
        detection = vision_cv.credible_light_work_surface(image)
        if not isinstance(detection, dict) or detection.get("ok") is not True:
            reason = (
                str(detection.get("reason") or "surface detector returned no grounded region")
                if isinstance(detection, dict)
                else "surface detector returned invalid evidence"
            )
            return None, detection if isinstance(detection, dict) else None, reason
        bbox = detection.get("bbox")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            return None, detection, "surface detector returned invalid bounding geometry"
        try:
            x1, y1, x2, y2 = [int(value) for value in bbox]
        except (TypeError, ValueError):
            return None, detection, "surface detector returned non-integer bounding geometry"
        if not (0 <= x1 < x2 <= image_width and 0 <= y1 < y2 <= image_height):
            return None, detection, "surface detector returned out-of-frame bounding geometry"
        surface = Region(
            window.left + x1,
            window.top + y1,
            x2 - x1,
            y2 - y1,
        )
        if surface.width < 120 or surface.height < 80:
            return None, detection, "detected work surface was too small for safe path mapping"
        payload: dict[str, Any] = {
            "hwnd": target,
            "window_region": self._draw_region_payload(window),
            "surface_region": self._draw_region_payload(surface),
            "detected_from": "pre_action_current_target_pixels",
            "mapping_source": "pre_action_current_target_pixels",
            "detection": {
                key: value for key, value in detection.items()
                if key != "bbox"
            },
        }
        self._draw_surface_cache = payload
        return surface, dict(payload), ""

    @staticmethod
    def _draw_prepare_error(failure_type: str, error: str) -> dict[str, Any]:
        return {
            "ok": False,
            "verified": False,
            "blocked": True,
            "mutation_applied": False,
            "failure_type": failure_type,
            "error": error,
            "needs_observe": True,
            "recovery": (
                "Focus and uncover exactly one editable light work surface, observe the target "
                "again, then retry normalized_strokes. Do not guess physical coordinates."
            ),
        }

    def _park_pointer_for_draw_verification(
        self,
        executor_mod: Any,
        effect_region: Region,
    ) -> dict[str, Any]:
        """Move transient cursor/hover pixels away from a gesture proof ROI.

        Pointer effects can cover a thin authored stroke and can also dominate
        the changed-pixel denominator.  The Controller therefore parks the
        pointer at a non-mutating point in the same bound window before taking
        its post-action frame.  This is not execution evidence: the subsequent
        current-pixel path proof is still required in full.
        """
        move_mouse = getattr(executor_mod, "move_mouse", None)
        if not callable(move_mouse) or not self.hwnd:
            return {"attempted": False, "reason": "move_capability_unavailable"}
        try:
            window = window_region(int(self.hwnd)).clamp_to_screen()
        except Exception as exc:
            return {
                "attempted": False,
                "reason": "target_window_geometry_unavailable",
                "error": str(exc),
            }
        if window.width < 80 or window.height < 60:
            return {"attempted": False, "reason": "target_window_too_small"}

        parking_region = window
        parking_source = "window_interior_fallback"
        draw_surface_cache = getattr(self, "_draw_surface_cache", None)
        cached_surface = (
            draw_surface_cache.get("surface_region")
            if isinstance(draw_surface_cache, dict)
            else None
        )
        if isinstance(cached_surface, dict):
            try:
                proposed = Region(
                    int(cached_surface["x"]),
                    int(cached_surface["y"]),
                    int(cached_surface["width"]),
                    int(cached_surface["height"]),
                ).clamp_to_screen()
                if proposed.width >= 80 and proposed.height >= 60:
                    parking_region = proposed
                    parking_source = "current_pixel_work_surface"
            except (KeyError, TypeError, ValueError):
                pass
        # Prefer the proven work surface.  The fallback deliberately excludes
        # title bars and window edges, where a non-clicking pointer can still
        # open snap layouts, tooltips, menus, or resize affordances.
        horizontal_inset = min(32, max(12, parking_region.width // 10))
        vertical_inset = min(32, max(12, parking_region.height // 10))
        if parking_source == "window_interior_fallback":
            vertical_inset = min(
                max(56, parking_region.height // 7),
                max(24, parking_region.height // 3),
            )
        left = parking_region.left + horizontal_inset
        right = parking_region.right - horizontal_inset
        top = parking_region.top + vertical_inset
        bottom = parking_region.bottom - vertical_inset
        candidates = [
            (left, top), (right, top), (left, bottom), (right, bottom),
        ]
        exclusion = 24
        candidates = [
            point for point in candidates
            if not (
                effect_region.left - exclusion
                <= point[0]
                <= effect_region.right + exclusion
                and effect_region.top - exclusion
                <= point[1]
                <= effect_region.bottom + exclusion
            )
        ]
        if not candidates:
            return {"attempted": False, "reason": "no_safe_window_point"}
        center_x, center_y = effect_region.center
        point = max(candidates, key=lambda candidate: (
            (candidate[0] - center_x) ** 2
            + (candidate[1] - center_y) ** 2
        ))
        try:
            outcome = _call_with_optional_hwnd(
                move_mouse, point[0], point[1], hwnd=self.hwnd,
            )
        except Exception as exc:
            return {
                "attempted": True,
                "ok": False,
                "point": {"x": point[0], "y": point[1]},
                "error": str(exc),
            }
        ok = not isinstance(outcome, dict) or outcome.get("ok") is not False
        if ok:
            # Let the application's hover state follow the pointer.  This is a
            # bounded local settle, not another model round.
            time.sleep(0.08)
        return {
            "attempted": True,
            "ok": bool(ok),
            "point": {"x": point[0], "y": point[1]},
            "method": "same-window-nonmutating-pointer-park",
            "parking_source": parking_source,
        }

    def _prepare_draw_strokes_args(self, args: dict, capture_fn=None) -> dict:
        """Resolve one physical or surface-normalized stroke source exactly once."""
        prepared = dict(args or {})
        if prepared.get("_draw_strokes_prepared") is True:
            return prepared
        prepared["_draw_strokes_prepared"] = True
        has_physical = "strokes" in prepared and prepared.get("strokes") is not None
        has_normalized = (
            "normalized_strokes" in prepared
            and prepared.get("normalized_strokes") is not None
        )
        if has_physical == has_normalized:
            prepared["_draw_strokes_error"] = self._draw_prepare_error(
                "DRAW_COORDINATE_SOURCE_INVALID",
                "draw_strokes requires exactly one of strokes or normalized_strokes",
            )
            return prepared

        capture_fn = capture_fn or _tools_attr("capture", capture)
        target, window, target_error = self._draw_target_geometry()
        if has_physical:
            # Preserve the legacy physical-pixel contract. Surface grounding is
            # best-effort here solely to prime a complete pre-draw cache for a
            # later normalized call; failure must not block grounded px input.
            if target is not None and window is not None:
                surface, evidence, _error = self._locate_draw_surface(
                    target, window, capture_fn,
                )
                if surface is not None and evidence is not None:
                    prepared["_draw_surface_evidence"] = evidence
            return prepared

        normalized, validation_error = _validate_normalized_draw_strokes(
            prepared.get("normalized_strokes"),
        )
        if validation_error:
            prepared["_draw_strokes_error"] = self._draw_prepare_error(
                "INVALID_NORMALIZED_STROKES", validation_error,
            )
            return prepared
        if target is None or window is None:
            prepared["_draw_strokes_error"] = self._draw_prepare_error(
                "DRAW_SURFACE_NOT_GROUNDED", target_error,
            )
            return prepared
        surface, evidence, surface_error = self._locate_draw_surface(
            target, window, capture_fn,
        )
        if surface is None:
            prepared["_draw_strokes_error"] = self._draw_prepare_error(
                "DRAW_SURFACE_NOT_GROUNDED",
                f"normalized paths were not executed: {surface_error}",
            )
            if evidence:
                prepared["_draw_strokes_error"]["surface_detection"] = evidence
            return prepared

        x_span = max(0, surface.width - 1)
        y_span = max(0, surface.height - 1)
        prepared["strokes"] = [
            [
                [
                    surface.left + int(round(point[0] * x_span / 1000.0)),
                    surface.top + int(round(point[1] * y_span / 1000.0)),
                ]
                for point in stroke
            ]
            for stroke in normalized
        ]
        prepared.pop("normalized_strokes", None)
        prepared["mapped_from_coordinate_space"] = "work_surface_normalized_0_1000"
        prepared["_draw_surface_evidence"] = evidence
        return prepared

    def _t_draw_strokes(self, args: dict) -> ToolResult:
        executor_mod = _tools_attr("executor", executor)
        capture_fn = _tools_attr("capture", capture)
        args = self._prepare_draw_strokes_args(args, capture_fn=capture_fn)
        prepare_error = args.get("_draw_strokes_error")
        if isinstance(prepare_error, dict):
            return ToolResult(self._fmt(prepare_error))
        raw = args.get("strokes")
        if not isinstance(raw, list) or not raw:
            return ToolResult("draw_strokes 需要 strokes: [[[x,y],...], ...]（至少一条笔画）")
        strokes = _normalize_draw_strokes(raw)
        duration_defaulted = args.get("duration") is None
        try:
            duration = (
                max(0.08, min(0.24, 1.2 / max(1, len(strokes))))
                if duration_defaulted
                else float(args.get("duration"))
            )
        except (TypeError, ValueError):
            return ToolResult(self._fmt({
                "ok": False,
                "blocked": True,
                "failure_type": "INVALID_DRAW_DURATION",
                "error": "draw_strokes duration must be a finite positive number",
            }))
        if not math.isfinite(duration) or duration <= 0:
            return ToolResult(self._fmt({
                "ok": False,
                "blocked": True,
                "failure_type": "INVALID_DRAW_DURATION",
                "error": "draw_strokes duration must be a finite positive number",
            }))
        all_coords: list[tuple[int, int]] = []
        for stroke in strokes:
            all_coords.extend((point[0], point[1]) for point in stroke)
        if not strokes:
            return ToolResult("draw_strokes 没有有效笔画（每条至少需要2个点）")
        constraint_block = self._spatial_constraint_block(
            args,
            all_coords,
            action="draw_strokes",
        )
        if constraint_block is not None:
            return constraint_block
        xs = [c[0] for c in all_coords]
        ys = [c[1] for c in all_coords]
        pad = 28
        region = Region(min(xs) - pad, min(ys) - pad,
                        max(1, max(xs) - min(xs) + 2 * pad),
                        max(1, max(ys) - min(ys) + 2 * pad)).clamp_to_screen()
        deferred_verify = bool(args.get("defer_visual_verify"))
        before = None
        if not deferred_verify:
            try:
                before = capture_fn(region)
            except Exception as exc:
                logger.debug("draw_strokes pre-capture failed: %s", exc)
        result = executor_mod.draw_strokes(strokes, duration_s=duration, hwnd=self.hwnd)
        result["motion_duration_per_stroke_s"] = round(duration, 4)
        result["motion_duration_defaulted"] = duration_defaulted
        surface_evidence = args.get("_draw_surface_evidence")
        if isinstance(surface_evidence, dict):
            result["surface_region"] = surface_evidence.get("surface_region")
            result["surface_mapping_source"] = surface_evidence.get("mapping_source")
        if args.get("mapped_from_coordinate_space"):
            result["mapped_from_coordinate_space"] = args["mapped_from_coordinate_space"]
        frame_stroke_preparation = args.get("frame_stroke_preparation")
        if isinstance(frame_stroke_preparation, dict):
            # Keep the safety transform auditable without echoing the often
            # large point arrays back to the model.  This proves whether the
            # runtime used direct frame mapping, one whole-composition affine
            # fit, and/or conservative dense-anchor simplification.
            result["frame_stroke_preparation"] = frame_stroke_preparation
        if deferred_verify:
            result["visual_verify_deferred"] = True
            result["verify_method"] = "batch-final-visual-verify"
        if before is not None:
            try:
                if result.get("ok") is not False:
                    result["verification_cursor_park"] = (
                        self._park_pointer_for_draw_verification(
                            executor_mod, region,
                        )
                    )
                time.sleep(0.12)
                after = capture_fn(region)
                ratio = vision_cv.diff_ratio(before, after)
                result["visual_change_ratio"] = round(float(ratio), 5)
                changed = bool(ratio >= 0.0008)
                result["visual_change_detected"] = changed
                # A broad ROI delta can be only the moving cursor, a hover
                # highlight, or an unrelated repaint.  Share the deterministic
                # path provider used by the typed post-action verifier and
                # require pixels along every declared pointer path.
                from ..tasking.visual_effects import verify_pointer_paths_in_frames

                local_paths = [
                    [
                        [point[0] - region.left, point[1] - region.top]
                        for point in stroke
                    ]
                    for stroke in strokes
                ]
                path_realization = verify_pointer_paths_in_frames(
                    before,
                    after,
                    local_paths,
                )
                result["path_realization"] = path_realization
                path_verified = path_realization.get("verified") is True
                result["path_realization_detected"] = path_verified
                if path_verified and result.get("ok") is not False:
                    result["verified"] = True
                    result["mutation_applied"] = True
                    result["verify_method"] = "pointer-path-realization"
                    result["verify_detail"] = (
                        "current pixels trace every declared pointer path"
                    )
                if not path_verified:
                    # Device input being accepted is not proof that it landed
                    # on the intended drawable surface.  Keep this fail-closed:
                    # callers must recover focus/tool/canvas grounding instead
                    # of treating a no-op stroke as completed work.
                    result.update({
                        "ok": False,
                        "verified": False,
                        "mutation_applied": bool(
                            path_realization.get("mutation_evidence") is True
                        ),
                        "failure_type": (
                            "DRAW_PATH_NOT_REALIZED"
                            if changed else "DRAW_NO_VISIBLE_CHANGE"
                        ),
                        "error": (
                            "pixels changed, but they do not trace every requested pointer path"
                            if changed else
                            "draw strokes produced no visible pixel change in the intended region"
                        ),
                    })
                result["visual_feedback"] = (
                    f"drew {result.get('strokes_drawn', 0)} strokes; "
                    + ("visible pixels changed across the figure region — good evidence "
                       "the strokes landed on the canvas. Re-observe/zoom once if needed; "
                       "when a requested simple visible shape is confirmed, call done "
                       "instead of redrawing for thickness or aesthetics."
                       if path_verified else
                       "the region barely changed; the paths may have hit interface chrome, "
                       "an inactive surface, or an incompatible input mode. Re-bind the "
                       "target surface and prove the required pointer mechanism before retrying.")
                )
            except Exception as exc:
                logger.debug("draw_strokes post-capture/diff failed: %s", exc)
        elif not deferred_verify:
            # Capture failure must not silently become visual success. The
            # caller can observe or retry, but cannot mark the stroke verified.
            result.setdefault("verified", False)
            result["needs_observe"] = True
            result["verify_detail"] = "draw result has no visual capture evidence"
        return ToolResult(
            self._fmt(result),
            replay={
                "tool": "draw_strokes",
                "strokes": strokes,
                "duration": duration,
                **({
                    "surface_region": surface_evidence.get("surface_region"),
                    "mapped_from_coordinate_space": args.get("mapped_from_coordinate_space"),
                } if isinstance(surface_evidence, dict) else {}),
                **({
                    "frame_stroke_preparation": frame_stroke_preparation,
                } if isinstance(frame_stroke_preparation, dict) else {}),
            },
        )

    def _t_wait(self, args: dict) -> ToolResult:
        verify_mod = _tools_attr("verify", verify)
        if args.get("until_change") is True:
            try:
                timeout_s = float(args.get("timeout_s", 30.0))
                min_ratio = float(args.get("min_change_ratio", 0.0005))
            except (TypeError, ValueError):
                timeout_s = float("nan")
                min_ratio = float("nan")
            if (
                not math.isfinite(timeout_s)
                or timeout_s <= 0
                or timeout_s > 120
                or not math.isfinite(min_ratio)
                or min_ratio < 0
                or min_ratio > 1
            ):
                return ToolResult(self._fmt({
                    "ok": False,
                    "blocked": True,
                    "action_executed": False,
                    "error": (
                        "visual-change wait requires timeout_s in (0, 120] "
                        "and min_change_ratio in [0, 1]"
                    ),
                }))
            result = self._fast_controller()._wait_for_visual_change({
                "timeout_s": timeout_s,
                "min_ratio": min_ratio,
                "required": True,
            })
            return ToolResult(
                result.text,
                replay={
                    "tool": "wait",
                    "until_change": True,
                    "timeout_s": timeout_s,
                    "min_change_ratio": min_ratio,
                },
            )

        condition = args.get("condition")
        if condition not in (None, {}):
            if not isinstance(condition, dict):
                return ToolResult(self._fmt({
                    "ok": False,
                    "blocked": True,
                    "action_executed": False,
                    "error": "wait condition must be an object",
                }))
            try:
                timeout_s = float(args.get("timeout_s", 10.0))
            except (TypeError, ValueError):
                timeout_s = float("nan")
            if not math.isfinite(timeout_s) or timeout_s <= 0 or timeout_s > 120:
                return ToolResult(self._fmt({
                    "ok": False,
                    "blocked": True,
                    "action_executed": False,
                    "error": "wait timeout_s must be a finite number in (0, 120]",
                }))
            outcome = verify_mod.check(
                condition,
                hwnd=self.hwnd,
                timeout_s=timeout_s,
                stop_event=self.stop_event,
            )
            return ToolResult(
                self._fmt(outcome.to_dict()),
                replay={"tool": "wait", "condition": condition, "timeout_s": timeout_s},
            )

        if "duration_s" not in args:
            return ToolResult(self._fmt({
                "ok": False,
                "blocked": True,
                "action_executed": False,
                "error": "wait requires duration_s or condition",
            }))
        try:
            duration_s = float(args.get("duration_s"))
        except (TypeError, ValueError):
            duration_s = float("nan")
        if not math.isfinite(duration_s) or duration_s < 0 or duration_s > 120:
            return ToolResult(self._fmt({
                "ok": False,
                "blocked": True,
                "action_executed": False,
                "error": "wait duration_s must be a finite number in [0, 120]",
            }))

        started = time.monotonic()
        interrupted = bool(self.stop_event.wait(duration_s))
        waited_s = max(0.0, time.monotonic() - started)
        return ToolResult(
            self._fmt({
                "ok": not interrupted,
                "interrupted": interrupted,
                "action_executed": not interrupted,
                "requested_duration_s": duration_s,
                "waited_s": round(waited_s, 3),
            }),
            replay={"tool": "wait", "duration_s": duration_s},
        )
