"""UIA dimension: walk the UI Automation tree of a window.

Covers modern apps (UWP/WinUI), WPF, WinForms, Qt and most Win32 apps that
expose UIA providers. Emits UIElement with role/text/coords/states and keeps
the live automation element handle in `extra` for fast Invoke/SetValue paths.
"""
from __future__ import annotations

import threading
import time

import uiautomation as auto
import uiautomation.uiautomation as auto_core

from ..utils.log import logger
from .model import UIElement

try:
    auto.SetGlobalSearchTimeout(1.2)
    auto.OPERATION_WAIT_TIME = 0.1
except Exception:
    pass

# Roles we surface to the model. Anything else is treated as container and recursed.
_INTERESTING = {
    "ButtonControl": "button",
    "SplitButtonControl": "button",
    "CheckBoxControl": "checkbox",
    "RadioButtonControl": "radio",
    "ComboBoxControl": "combobox",
    "EditControl": "edit",
    "DocumentControl": "document",
    "TextControl": "text",
    "HyperlinkControl": "link",
    "MenuItemControl": "menuitem",
    "MenuBarControl": "menubar",
    "TabItemControl": "tab",
    "ListItemControl": "listitem",
    "TreeItemControl": "treeitem",
    "DataItemControl": "dataitem",
    "HeaderItemControl": "headeritem",
    "SliderControl": "slider",
    "SpinnerControl": "spinner",
    "StatusBarControl": "statusbar",
    "ToolBarControl": "toolbar",
    "ImageControl": "image",
    "TitleBarControl": "titlebar",
    "WindowControl": "window",
    "PaneControl": "pane",
    "GroupControl": "group",
    "ListControl": "list",
    "ScrollBarControl": "scrollbar",
}
# Containers we recurse into but only emit if they carry their own name.
_CONTAINER_ROLES = {"window", "pane", "group", "list", "toolbar", "menubar"}

_MAX_ELEMENTS = 300
_MAX_DEPTH = 18
_TIME_BUDGET_S = 1.8
_thread_state = threading.local()


def _is_legacy_chromium_root_child(
    class_name: str,
    *,
    has_modern_browser_tree: bool,
) -> bool:
    """Identify Chromium's stale top-level accessibility mirror."""
    return (
        has_modern_browser_tree
        and str(class_name or "") == "Chrome_RenderWidgetHostHWND"
    )


def _control_class_name(control: auto.Control) -> str:
    try:
        return str(control.ClassName or "")
    except Exception:
        return ""


def _has_modern_chromium_tree(children: list[auto.Control]) -> bool:
    return any(_control_class_name(child) == "BrowserRootView" for child in children)


def _is_legacy_chromium_match(
    control: auto.Control,
    *,
    has_modern_browser_tree: bool,
) -> bool:
    """Return true when an exact UIA hit belongs to a direct stale renderer."""
    if not has_modern_browser_tree:
        return False
    current = control
    for _ in range(_MAX_DEPTH + 4):
        if _control_class_name(current) == "Chrome_RenderWidgetHostHWND":
            try:
                parent = current.GetParentControl()
                return bool(parent and parent.ControlTypeName == "WindowControl")
            except Exception:
                return False
        try:
            current = current.GetParentControl()
        except Exception:
            return False
        if current is None:
            return False
    return False


def _ensure_thread_initialized() -> None:
    """Keep COM/UIA initialized for every worker that owns live controls."""
    if getattr(_thread_state, "initializer", None) is None:
        try:
            _thread_state.initializer = auto.UIAutomationInitializerInThread()
        except Exception as exc:
            # Some host threads already own a different COM apartment. UIA
            # may still be usable there; preserve the dimension's never-raise
            # contract and let the normal collection fallback decide.
            _thread_state.initializer = False
            logger.debug("UIA thread initialization skipped: %s", exc)


def _states_of(ctrl: auto.Control) -> list[str]:
    states: list[str] = []
    try:
        if not ctrl.IsEnabled:
            states.append("disabled")
    except Exception:
        pass
    try:
        if ctrl.IsOffscreen:
            states.append("offscreen")
    except Exception:
        pass
    try:
        toggle = ctrl.GetTogglePattern()
        if toggle:
            states.append("checked" if toggle.ToggleState == 1 else "unchecked")
    except Exception:
        pass
    try:
        sel = ctrl.GetSelectionItemPattern()
        if sel and sel.IsSelected:
            states.append("selected")
    except Exception:
        pass
    try:
        if ctrl.HasKeyboardFocus:
            states.append("focused")
    except Exception:
        pass
    return states


def _value_of(ctrl: auto.Control) -> str:
    try:
        vp = ctrl.GetValuePattern()
        if vp and vp.Value:
            return str(vp.Value)
    except Exception:
        pass
    return ""


def _to_element(ctrl: auto.Control) -> UIElement | None:
    try:
        ctype = ctrl.ControlTypeName
        role = _INTERESTING.get(ctype)
        rect = ctrl.BoundingRectangle
        width, height = rect.width(), rect.height()
        name = (ctrl.Name or "").strip()
        if not role or width <= 0 or height <= 0:
            return None
        value = _value_of(ctrl)
        text = name or value
        if value and name and value != name:
            text = f"{name}={value}"
        if role in _CONTAINER_ROLES and not text:
            return None
        states = _states_of(ctrl)
        if "offscreen" in states:
            return None
        return UIElement(
            source="uia",
            role=role,
            text=text,
            cx=(rect.left + rect.right) // 2,
            cy=(rect.top + rect.bottom) // 2,
            left=rect.left,
            top=rect.top,
            width=width,
            height=height,
            states=states,
            extra={
                "automation_id": ctrl.AutomationId or "",
                "class_name": ctrl.ClassName or "",
                "control_type": ctype,
                "uia_ctrl": ctrl,
            },
        )
    except Exception:
        return None


def collect(hwnd: int) -> list[UIElement]:
    """Collect interesting UIA elements of the window. Never raises."""
    _ensure_thread_initialized()
    started = time.monotonic()
    elements: list[UIElement] = []
    try:
        root = auto.ControlFromHandle(hwnd)
        if root is None:
            return []
    except Exception as exc:
        logger.warning("UIA: ControlFromHandle 失败 hwnd=%s: %s", hwnd, exc)
        return []

    try:
        root_children = root.GetChildren()
    except Exception:
        root_children = []
    # Chromium can expose cached tabs as direct renderer children while its
    # modern BrowserRootView contains the real active document. Those mirrors
    # report plausible, non-offscreen coordinates and otherwise outrank the
    # live page, so ignore only the direct copies when the modern tree exists.
    has_modern_browser_tree = _has_modern_chromium_tree(root_children)

    def walk(
        ctrl: auto.Control,
        depth: int,
        children_override: list[auto.Control] | None = None,
    ) -> None:
        if depth > _MAX_DEPTH or len(elements) >= _MAX_ELEMENTS:
            return
        if time.monotonic() - started > _TIME_BUDGET_S:
            return
        if children_override is not None:
            children = children_override
        else:
            try:
                children = ctrl.GetChildren()
            except Exception:
                children = []
        for child in children:
            if len(elements) >= _MAX_ELEMENTS:
                return
            try:
                if depth == 0 and _is_legacy_chromium_root_child(
                    _control_class_name(child),
                    has_modern_browser_tree=has_modern_browser_tree,
                ):
                    continue
                element = _to_element(child)
                if element is not None:
                    elements.append(element)
                walk(child, depth + 1)
            except Exception:
                continue

    try:
        walk(root, 0, root_children)
    except Exception as exc:
        logger.warning("UIA: 遍历异常 hwnd=%s: %s", hwnd, exc)
    return elements


def find_by_text(hwnd: int, text: str, limit: int = 40) -> list[UIElement]:
    """Query exact UIA names without walking and materializing the full tree."""
    needle = str(text or "").strip()
    if not needle:
        return []
    _ensure_thread_initialized()
    try:
        root = auto.ControlFromHandle(hwnd)
        if root is None:
            return []
        try:
            root_children = root.GetChildren()
        except Exception:
            root_children = []
        has_modern_browser_tree = _has_modern_chromium_tree(root_children)
        client = auto_core._AutomationClient.instance()
        condition = client.IUIAutomation.CreatePropertyCondition(
            auto_core.PropertyId.NameProperty,
            needle,
        )
        matches = root.Element.FindAll(
            client.UIAutomationCore.TreeScope_Descendants,
            condition,
        )
        elements: list[UIElement] = []
        for index in range(min(int(matches.Length), max(1, int(limit)))):
            control = auto.Control.CreateControlFromElement(matches.GetElement(index))
            if _is_legacy_chromium_match(
                control,
                has_modern_browser_tree=has_modern_browser_tree,
            ):
                continue
            element = _to_element(control)
            if element is not None:
                elements.append(element)
        return elements
    except Exception as exc:
        logger.debug("UIA exact-name query failed hwnd=%s text=%r: %s", hwnd, needle, exc)
        return []


def find_by_automation_id(hwnd: int, automation_id: str) -> UIElement | None:
    for element in collect(hwnd):
        if element.extra.get("automation_id") == automation_id:
            return element
    return None
