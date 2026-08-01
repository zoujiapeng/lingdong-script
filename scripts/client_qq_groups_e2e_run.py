#!/usr/bin/env python3
"""Run the real QQ group-summary goal through the visible Electron client.

The harness does not click, scroll, read QQ databases, or call a QQ API.  It
only focuses a caller-supplied QQ NT window immediately after the user-visible
Electron submission.  A separate read-only oracle samples that visible window
through screenshots and UI Automation.

QQ screenshots and the base client report may contain private conversation
content.  They are therefore kept under the case's ``private`` directory and
are never printed.  The shareable report contains only counts, one-run opaque
fingerprints, action names, hashes, and blocker codes.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import hmac
import io
import json
import re
import secrets
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

import psutil
import uiautomation as auto
import win32api
import win32con
import win32gui
import win32process
from PIL import Image, ImageGrab


WORKSPACE = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import client_e2e_run as client_harness  # noqa: E402


TASK = "去QQ看看所有的群最近都有什么消息，你汇总给我看他们在聊什么"
DEFAULT_OUTPUT = WORKSPACE / "output" / "playwright" / "client-qq-groups-e2e"
ORACLE_SOURCE = "independent_visible_qq_browse_and_client_delivery_oracle"
MIN_GROUPS = 2
_ROOT_ANCESTOR = win32con.GA_ROOT
_MAX_UIA_NODES = 1600
_MAX_PRIVATE_SCREENSHOTS = 24
_PUBLIC_QQ_LABELS = frozenset(
    {
        "qq",
        "消息",
        "联系人",
        "群聊",
        "群组",
        "我的群聊",
        "群通知",
        "搜索",
        "设置",
        "更多",
        "发送",
        "表情",
        "文件",
        "语音",
        "视频",
        "聊天记录",
    }
)
_CLIENT_SHELL_LABELS = frozenset(
    {
        "运行任务",
        "停止",
        "暂停",
        "继续",
        "必须使用gui",
        "自动选择",
        "任务目标",
        "智能代理指令",
        "就绪",
        "运行中",
        "已完成",
        "失败",
    }
)
_TRACE_UNSAFE_TEXT_FIELDS = frozenset(
    {
        "concise_claim",
        "message_text",
        "visible_text",
        "ocr_text",
        "result_preview",
        "prompt",
        "response",
    }
)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_json_safe(value), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _normal(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def _clean_label(value: Any) -> str:
    text = re.sub(r"[\u200b-\u200f\ufeff]+", "", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def _opaque(value: str, key: bytes) -> str:
    return hmac.new(
        key,
        _clean_label(value).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:20]


def _root_window(hwnd: int) -> int:
    return int(win32gui.GetAncestor(int(hwnd), _ROOT_ANCESTOR) or hwnd)


def _window_identity(hwnd: int) -> dict[str, Any]:
    if not hwnd or not win32gui.IsWindow(hwnd):
        raise RuntimeError("QQ target window does not exist")
    root = _root_window(hwnd)
    _thread_id, process_id = win32process.GetWindowThreadProcessId(root)
    try:
        process = psutil.Process(int(process_id))
        executable = process.name()
        executable_path = process.exe()
    except psutil.Error:
        executable = ""
        executable_path = ""
    if _normal(executable) != "qq.exe":
        raise RuntimeError(
            "supplied HWND is not owned by QQ.exe: "
            f"{executable!r}"
        )
    return {
        "hwnd": root,
        "process_id": int(process_id),
        "executable": executable,
        "executable_path": executable_path,
        "title": win32gui.GetWindowText(root),
        "window_rect": list(map(int, win32gui.GetWindowRect(root))),
    }


def _focus_window(hwnd: int) -> dict[str, Any]:
    identity = _window_identity(hwnd)
    root = int(identity["hwnd"])
    original_rect = tuple(identity["window_rect"])
    if win32gui.IsIconic(root):
        win32gui.ShowWindow(root, win32con.SW_RESTORE)
    win32gui.BringWindowToTop(root)
    errors: list[str] = []
    for attempt in range(1, 6):
        try:
            win32api.keybd_event(
                win32con.VK_MENU,
                0,
                0,
                client_harness.HARNESS_FOCUS_INPUT_TAG,
            )
            win32gui.SetForegroundWindow(root)
        except Exception as error:
            errors.append(f"{type(error).__name__}: {error}")
        finally:
            win32api.keybd_event(
                win32con.VK_MENU,
                0,
                win32con.KEYEVENTF_KEYUP,
                client_harness.HARNESS_FOCUS_INPUT_TAG,
            )
        time.sleep(0.12)
        if _root_window(int(win32gui.GetForegroundWindow() or 0)) == root:
            break
    else:
        raise RuntimeError(
            "could not focus supplied QQ target: "
            + "; ".join(errors[-3:])
        )
    if tuple(map(int, win32gui.GetWindowRect(root))) != original_rect:
        raise RuntimeError("focusing QQ changed its bound geometry")
    return {
        **identity,
        "foreground_root_hwnd": _root_window(
            int(win32gui.GetForegroundWindow() or 0)
        ),
        "focused": True,
        "attempt": attempt,
    }


def _new_admitted_run(
    status: dict[str, Any],
    *,
    previous_run_id: str,
    before_job_ids: set[str],
    expected_goal: str = TASK,
) -> tuple[str, str]:
    for job in status.get("runtime_jobs") or []:
        if not isinstance(job, dict):
            continue
        metadata = job.get("metadata") or {}
        job_id = str(job.get("job_id") or "")
        run_id = str(metadata.get("run_id") or "")
        if (
            job_id
            and job_id not in before_job_ids
            and str(job.get("kind") or "") == "arp"
            and str(metadata.get("runtime_version") or "") == "vnext"
            and run_id
            and run_id != previous_run_id
        ):
            return run_id, job_id
    current = status.get("current_run")
    if isinstance(current, dict):
        run_id = str(current.get("run_id") or "")
        if (
            run_id
            and run_id != previous_run_id
            and str(current.get("runtime_version") or "") == "vnext"
            and str(current.get("goal") or "").strip()
            == expected_goal.strip()
        ):
            return run_id, ""
    return "", ""


def _hold_target_until_admitted(
    driver: client_harness.ElectronClientDriver,
    client_result: dict[str, Any],
    *,
    target_hwnd: int,
    timeout_s: float = 15.0,
) -> dict[str, Any]:
    before = client_result.get("submission_before") or {}
    before_job_ids = {
        str(value) for value in before.get("job_ids") or [] if str(value)
    }
    previous = (client_result.get("runtime_before") or {}).get("current_run")
    previous_run_id = str(
        previous.get("run_id") if isinstance(previous, dict) else ""
    )
    deadline = time.monotonic() + timeout_s
    admitted_run_id = ""
    admitted_job_id = ""
    refocus_count = 0
    samples = 0
    while time.monotonic() < deadline:
        if (
            _root_window(int(win32gui.GetForegroundWindow() or 0))
            != int(target_hwnd)
        ):
            _focus_window(target_hwnd)
            refocus_count += 1
        status = driver.runtime_status()
        admitted_run_id, admitted_job_id = _new_admitted_run(
            status,
            previous_run_id=previous_run_id,
            before_job_ids=before_job_ids,
        )
        samples += 1
        if admitted_run_id:
            break
        time.sleep(0.1)
    if not admitted_run_id:
        raise RuntimeError(
            "Electron did not admit a new vNext run while the exact QQ "
            "HWND remained foreground"
        )
    if (
        _root_window(int(win32gui.GetForegroundWindow() or 0))
        != int(target_hwnd)
    ):
        raise RuntimeError("QQ target lost foreground during admission")
    return {
        "admitted_run_id": admitted_run_id,
        "admitted_job_id": admitted_job_id,
        "target_hwnd": int(target_hwnd),
        "target_process_id": _window_identity(target_hwnd)["process_id"],
        "refocus_count": refocus_count,
        "sample_count": samples,
        "target_foreground_at_admission": True,
    }


def _rect_list(control: Any) -> list[int]:
    try:
        rect = control.BoundingRectangle
        return [
            int(rect.left),
            int(rect.top),
            int(rect.right),
            int(rect.bottom),
        ]
    except Exception:
        return [0, 0, 0, 0]


def _selection_state(control: Any) -> bool | None:
    try:
        pattern = control.GetSelectionItemPattern()
        if pattern is None:
            return None
        return bool(pattern.IsSelected)
    except Exception:
        return None


def _scroll_state(control: Any) -> tuple[bool | None, float | None]:
    try:
        pattern = control.GetScrollPattern()
        if pattern is None:
            return None, None
        scrollable = bool(pattern.VerticallyScrollable)
        percent = float(pattern.VerticalScrollPercent)
        if not -1.0 <= percent <= 100.0:
            percent = None
        return scrollable, percent
    except Exception:
        return None, None


def snapshot_accessible_state(hwnd: int) -> list[dict[str, Any]]:
    """Read the visible QQ accessibility tree without persisting its text."""

    root = auto.ControlFromHandle(int(hwnd))
    if root is None:
        raise RuntimeError("UI Automation could not bind the QQ HWND")
    queue: list[tuple[Any, str, str, int]] = [(root, "0", "", 0)]
    nodes: list[dict[str, Any]] = []
    visited = 0
    while queue and visited < _MAX_UIA_NODES:
        control, path, parent_path, depth = queue.pop(0)
        visited += 1
        try:
            name = _clean_label(control.Name)
        except Exception:
            name = ""
        try:
            control_type = str(control.ControlTypeName or "")
        except Exception:
            control_type = ""
        try:
            automation_id = str(control.AutomationId or "")
        except Exception:
            automation_id = ""
        scrollable, scroll_percent = _scroll_state(control)
        node = {
            "name": name,
            "control_type": control_type,
            "automation_id": automation_id,
            "rect": _rect_list(control),
            "path": path,
            "parent_path": parent_path,
            "depth": depth,
            "selected": _selection_state(control),
            "vertically_scrollable": scrollable,
            "vertical_scroll_percent": scroll_percent,
        }
        if (
            name
            or automation_id
            or scrollable is not None
            or node["selected"] is not None
        ):
            nodes.append(node)
        if depth >= 18:
            continue
        try:
            children = control.GetChildren()
        except Exception:
            children = []
        for index, child in enumerate(children):
            queue.append((child, f"{path}.{index}", path, depth + 1))
    return nodes


def _valid_private_label(value: str) -> bool:
    text = _clean_label(value)
    normalized = _normal(text)
    if (
        not text
        or normalized in {_normal(item) for item in _PUBLIC_QQ_LABELS}
        or len(text) > 80
        or re.fullmatch(r"\d{1,2}:\d{2}", text)
        or re.fullmatch(r"(?:昨天|星期[一二三四五六日天]|\d{1,2}月\d{1,2}日)", text)
    ):
        return False
    return bool(re.search(r"[\w\u3400-\u9fff]", text))


def analyze_visible_qq_state(
    nodes: list[dict[str, Any]],
    window_rect: tuple[int, int, int, int],
) -> dict[str, Any]:
    """Return in-memory semantics; callers must redact ``*_names`` fields."""

    left, top, right, bottom = map(int, window_rect)
    width = max(1, right - left)
    height = max(1, bottom - top)
    public_names = {
        _normal(node.get("name"))
        for node in nodes
        if _clean_label(node.get("name"))
    }
    group_page_seen = bool(
        public_names
        & {
            _normal("群聊"),
            _normal("群组"),
            _normal("我的群聊"),
        }
    )

    row_nodes: list[dict[str, Any]] = []
    header_names: set[str] = set()
    selected_names: set[str] = set()
    scroll_states: list[tuple[bool, float | None]] = []
    row_types = {
        "ListItemControl",
        "DataItemControl",
        "TreeItemControl",
        "ButtonControl",
        "TextControl",
    }
    for node in nodes:
        name = _clean_label(node.get("name"))
        rect = list(node.get("rect") or [0, 0, 0, 0])
        if len(rect) != 4:
            continue
        center_x = (int(rect[0]) + int(rect[2])) / 2
        center_y = (int(rect[1]) + int(rect[3])) / 2
        in_group_list = (
            left + width * 0.10 <= center_x <= left + width * 0.48
            and top + height * 0.08 <= center_y <= bottom - height * 0.04
        )
        if (
            in_group_list
            and node.get("vertically_scrollable") is not None
        ):
            scroll_states.append(
                (
                    bool(node.get("vertically_scrollable")),
                    node.get("vertical_scroll_percent"),
                )
            )
        if (
            group_page_seen
            and in_group_list
            and str(node.get("control_type") or "") in row_types
            and _valid_private_label(name)
        ):
            row_nodes.append(node)
            if node.get("selected") is True:
                selected_names.add(name)
        in_header = (
            left + width * 0.36 <= center_x <= right - width * 0.04
            and top + height * 0.04 <= center_y <= top + height * 0.25
        )
        if in_header and _valid_private_label(name):
            header_names.add(name)

    # Prefer explicit list-item labels.  When QQ exposes only Text children,
    # retain one top/left label per visual row to avoid treating previews as
    # separate groups.
    explicit = [
        node
        for node in row_nodes
        if str(node.get("control_type") or "")
        in {"ListItemControl", "DataItemControl", "TreeItemControl"}
    ]
    if explicit:
        group_names = {_clean_label(node.get("name")) for node in explicit}
    else:
        by_band: dict[int, list[dict[str, Any]]] = {}
        band_height = max(24, int(height * 0.045))
        for node in row_nodes:
            rect = list(node.get("rect") or [0, 0, 0, 0])
            band = max(0, (int(rect[1]) - top) // band_height)
            by_band.setdefault(band, []).append(node)
        group_names = set()
        for candidates in by_band.values():
            chosen = min(
                candidates,
                key=lambda item: (
                    int((item.get("rect") or [0, 0, 0, 0])[1]),
                    int((item.get("rect") or [0, 0, 0, 0])[0]),
                    len(_clean_label(item.get("name"))),
                ),
            )
            group_names.add(_clean_label(chosen.get("name")))

    active_names = {
        group_name
        for group_name in group_names
        if group_name in selected_names
        or any(
            _normal(group_name) == _normal(header)
            or _normal(group_name) in _normal(header)
            for header in header_names
        )
    }
    scrollable_values = [value for value, _percent in scroll_states]
    scroll_percents = [
        float(percent)
        for _scrollable, percent in scroll_states
        if percent is not None and float(percent) >= 0.0
    ]
    return {
        "group_page_seen": group_page_seen,
        "group_names": group_names,
        "active_group_names": active_names,
        "header_names": header_names,
        "list_scrollability_observed": bool(scroll_states),
        "list_vertically_scrollable": (
            any(scrollable_values) if scroll_states else None
        ),
        "list_vertical_scroll_percent": (
            max(scroll_percents) if scroll_percents else None
        ),
        "uia_node_count": len(nodes),
    }


def trace_privacy_audit(
    trace_ids: Iterable[str],
    *,
    private_needles: Iterable[str] = (),
) -> dict[str, Any]:
    """Detect, without returning values, sensitive text in ordinary traces."""

    needles = {
        _clean_label(value)
        for value in private_needles
        if len(_clean_label(value)) >= 2
    }
    unsafe_fields: set[str] = set()
    private_match_count = 0
    inspected = 0
    parse_errors: list[str] = []
    for trace_id in trace_ids:
        path = client_harness.TRACE_ROOT / f"{trace_id}.jsonl"
        if not path.is_file():
            continue
        inspected += 1
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            parse_errors.append(f"{path.name}: {type(error).__name__}")
            continue
        for raw_line in lines:
            private_match_count += sum(
                1 for needle in needles if needle and needle in raw_line
            )
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                parse_errors.append(f"{path.name}: invalid_json")
                continue
            if not isinstance(event, dict):
                continue
            for key in _TRACE_UNSAFE_TEXT_FIELDS:
                value = event.get(key)
                if isinstance(value, str) and value.strip():
                    unsafe_fields.add(key)
    passed = bool(
        inspected
        and not unsafe_fields
        and private_match_count == 0
        and not parse_errors
    )
    return {
        "passed": passed,
        "inspected_trace_count": inspected,
        "unsafe_text_field_names": sorted(unsafe_fields),
        "private_value_match_count": private_match_count,
        "parse_errors": parse_errors,
    }


class BrowsingEvidence:
    """Accumulate coverage without exposing QQ labels or message text."""

    def __init__(self, secret: bytes, *, min_groups: int = MIN_GROUPS) -> None:
        self.secret = bytes(secret)
        self.min_groups = max(2, int(min_groups))
        self.group_page_seen = False
        self.discovered_names: set[str] = set()
        self.visited_names: set[str] = set()
        self.last_active_names: set[str] = set()
        self.switch_count = 0
        self.scroll_transition_count = 0
        self.last_row_names: set[str] = set()
        self.last_scroll_percent: float | None = None
        self.first_scroll_percent: float | None = None
        self.list_exhausted = False
        self.sample_count = 0

    def observe(
        self,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        self.sample_count += 1
        self.group_page_seen = bool(
            self.group_page_seen or state.get("group_page_seen")
        )
        rows = {
            _clean_label(value)
            for value in state.get("group_names") or set()
            if _clean_label(value)
        }
        active = {
            _clean_label(value)
            for value in state.get("active_group_names") or set()
            if _clean_label(value)
        }
        self.discovered_names.update(rows)
        if active:
            self.visited_names.update(active)
            if (
                self.last_active_names
                and active != self.last_active_names
            ):
                self.switch_count += 1
            self.last_active_names = set(active)
        percent = state.get("list_vertical_scroll_percent")
        percent_value = float(percent) if percent is not None else None
        row_changed = bool(self.last_row_names and rows != self.last_row_names)
        percent_changed = bool(
            self.last_scroll_percent is not None
            and percent_value is not None
            and abs(percent_value - self.last_scroll_percent) >= 1.0
        )
        if percent_changed and row_changed:
            self.scroll_transition_count += 1
        if state.get("list_vertically_scrollable") is False and rows:
            self.list_exhausted = True
        if percent_value is not None and percent_value >= 99.0:
            self.list_exhausted = True
        if rows:
            self.last_row_names = set(rows)
        if percent_value is not None:
            if self.first_scroll_percent is None:
                self.first_scroll_percent = percent_value
            self.last_scroll_percent = percent_value
        return self.public_evidence()

    def public_evidence(self) -> dict[str, Any]:
        discovered = {
            _opaque(value, self.secret) for value in self.discovered_names
        }
        visited = {
            _opaque(value, self.secret) for value in self.visited_names
        }
        covered = discovered & visited
        visible_scroll_distance = (
            abs(self.last_scroll_percent - self.first_scroll_percent)
            if (
                self.last_scroll_percent is not None
                and self.first_scroll_percent is not None
            )
            else 0.0
        )
        coverage_complete = bool(
            discovered and discovered.issubset(visited)
        )
        browse_complete = bool(
            self.group_page_seen
            and len(discovered) >= self.min_groups
            and coverage_complete
            and self.switch_count >= 1
            and self.scroll_transition_count >= 1
            and visible_scroll_distance >= 1.0
            and self.list_exhausted
        )
        return {
            "group_page_seen": self.group_page_seen,
            "discovered_group_count": len(discovered),
            "visited_group_count": len(visited),
            "covered_group_count": len(covered),
            "discovered_group_fingerprints": sorted(discovered),
            "visited_group_fingerprints": sorted(visited),
            "coverage_complete": coverage_complete,
            "switch_count": self.switch_count,
            "visible_scroll_distance_percent": round(
                visible_scroll_distance,
                3,
            ),
            "scroll_transition_count": self.scroll_transition_count,
            "list_exhausted": self.list_exhausted,
            "sample_count": self.sample_count,
            "browse_complete": browse_complete,
        }


def evaluate_client_delivery(
    visible_texts: Iterable[str],
    group_names: Iterable[str],
    *,
    secret: bytes,
) -> dict[str, Any]:
    """Verify a visible per-group summary while returning no summary text."""

    texts = [
        _clean_label(value)
        for value in visible_texts
        if _clean_label(value)
    ]
    groups = sorted(
        {
            _clean_label(value)
            for value in group_names
            if _clean_label(value)
        },
        key=len,
        reverse=True,
    )
    candidates = [
        text
        for text in texts
        if text != TASK
        and _normal(text) not in {_normal(item) for item in _CLIENT_SHELL_LABELS}
        and len(text) >= 12
    ]
    combined = "\n".join(candidates)
    mentioned: list[str] = []
    substantive: list[str] = []
    for group in groups:
        index = combined.find(group)
        if index < 0:
            continue
        mentioned.append(group)
        tail = combined[index + len(group) : index + len(group) + 180]
        tail = re.split(
            "|".join(re.escape(other) for other in groups if other != group)
            or r"$^",
            tail,
            maxsplit=1,
        )[0]
        content = re.sub(
            r"[\s:：\-—|、，。；;（）()\[\]【】]+",
            "",
            tail,
        )
        if len(content) >= 4:
            substantive.append(group)
    mentioned_hashes = sorted(_opaque(value, secret) for value in mentioned)
    substantive_hashes = sorted(
        _opaque(value, secret) for value in substantive
    )
    passed = bool(
        len(groups) >= MIN_GROUPS
        and len(mentioned) == len(groups)
        and len(substantive) == len(groups)
        and candidates
    )
    return {
        "passed": passed,
        "visible_candidate_count": len(candidates),
        "expected_group_count": len(groups),
        "mentioned_group_count": len(mentioned),
        "substantive_group_count": len(substantive),
        "mentioned_group_fingerprints": mentioned_hashes,
        "substantive_group_fingerprints": substantive_hashes,
        "delivery_sha256": (
            hashlib.sha256(combined.encode("utf-8")).hexdigest()
            if combined
            else ""
        ),
    }


def _visible_texts_for_window(hwnd: int) -> list[str]:
    return [
        str(node.get("name") or "")
        for node in snapshot_accessible_state(hwnd)
        if str(node.get("name") or "").strip()
    ]


def audit_fact_delivery_projection() -> dict[str, Any]:
    """Read-only audit of the current vNext claim-to-client projection."""

    runtime_path = (
        WORKSPACE
        / "python"
        / "winpilot_home"
        / "winpilot"
        / "vnext"
        / "runtime"
        / "mission_runtime.py"
    )
    canary_path = (
        WORKSPACE
        / "python"
        / "winpilot_home"
        / "winpilot"
        / "orchestrator"
        / "live_agent.py"
    )
    client_path = (
        WORKSPACE
        / "src"
        / "renderer"
        / "components"
        / "platform"
        / "PlatformWorkbench.tsx"
    )
    try:
        runtime = runtime_path.read_text(encoding="utf-8")
        canary = canary_path.read_text(encoding="utf-8")
        client = client_path.read_text(encoding="utf-8")
    except OSError as error:
        return {
            "passed": False,
            "status": "audit_error",
            "error": f"{type(error).__name__}: {error}",
        }

    claim_start = runtime.find("def _claim_and_verify")
    claim_end = runtime.find("\n    def ", claim_start + 1)
    claim_block = runtime[claim_start:claim_end]
    completion_start = canary.find("def _public_completion_snapshot")
    completion_end = canary.find("\ndef ", completion_start + 1)
    completion_block = canary[completion_start:completion_end]
    cockpit_start = client.find("export function AgentCockpit")
    cockpit_end = client.find("\nexport function ", cockpit_start + 1)
    cockpit_block = client[cockpit_start:cockpit_end]
    checks = {
        "claim_argument_discarded_before_verification": (
            "del proposal" in claim_block
        ),
        "public_completion_excludes_claim_text": (
            "concise_claim" not in completion_block
            and '"result"' not in completion_block
        ),
        "agent_cockpit_has_no_run_result_surface": not bool(
            re.search(r"\b(?:run|arp)\??\.result\b", cockpit_block)
        ),
    }
    blocker_present = all(checks.values())
    return {
        "passed": not blocker_present,
        "status": (
            "delivery_projection_available"
            if not blocker_present
            else "claim_text_not_projected_to_visible_client"
        ),
        "checks": checks,
        "blocker_present": blocker_present,
    }


class QqWindowOracle:
    def __init__(
        self,
        hwnd: int,
        private_dir: Path,
        *,
        min_groups: int = MIN_GROUPS,
        capture: Callable[..., Image.Image] = ImageGrab.grab,
        snapshot: Callable[[int], list[dict[str, Any]]] = snapshot_accessible_state,
    ) -> None:
        self.identity = _window_identity(hwnd)
        self.hwnd = int(self.identity["hwnd"])
        self.process_id = int(self.identity["process_id"])
        self.window_rect = tuple(self.identity["window_rect"])
        self.private_dir = private_dir
        self.private_screens = private_dir / "qq-screens"
        self.private_screens.mkdir(parents=True, exist_ok=True)
        self.secret = secrets.token_bytes(32)
        self.browsing = BrowsingEvidence(
            self.secret,
            min_groups=min_groups,
        )
        self._capture_fn = capture
        self._snapshot_fn = snapshot
        self._last_frame_sha256 = ""
        self._private_screenshot_count = 0
        self._foreground_sample_count = 0
        self._last_public: dict[str, Any] = {
            "source": ORACLE_SOURCE,
            "passed": False,
            "status": "not_started",
        }

    def _validate_target(self) -> None:
        current = _window_identity(self.hwnd)
        if (
            int(current["hwnd"]) != self.hwnd
            or int(current["process_id"]) != self.process_id
            or tuple(current["window_rect"]) != self.window_rect
        ):
            raise RuntimeError(
                "bound QQ window identity or geometry changed"
            )
        if (
            _root_window(int(win32gui.GetForegroundWindow() or 0))
            != self.hwnd
        ):
            raise RuntimeError("bound QQ window is not the visible foreground")

    def _capture_private_if_changed(self) -> str:
        image = self._capture_fn(
            bbox=self.window_rect,
            all_screens=True,
        ).convert("RGB")
        digest = hashlib.sha256(image.tobytes()).hexdigest()
        if (
            digest != self._last_frame_sha256
            and self._private_screenshot_count < _MAX_PRIVATE_SCREENSHOTS
        ):
            self._private_screenshot_count += 1
            image.save(
                self.private_screens
                / f"qq-state-{self._private_screenshot_count:03d}.png"
            )
        self._last_frame_sha256 = digest
        return digest

    def probe(self, context: dict[str, Any]) -> dict[str, Any]:
        run_id = str(context.get("run_id") or "")
        try:
            self._validate_target()
            self._foreground_sample_count += 1
            nodes = self._snapshot_fn(self.hwnd)
            state = analyze_visible_qq_state(nodes, self.window_rect)
            browsing = self.browsing.observe(state)
            public = {
                "source": ORACLE_SOURCE,
                "passed": False,
                "status": (
                    "browse_evidence_complete_delivery_pending"
                    if browsing["browse_complete"]
                    else "observing_visible_qq"
                ),
                "run_id": run_id,
                "captured_at": datetime.now().astimezone().isoformat(),
                "frame_sha256": self._capture_private_if_changed(),
                "target_hwnd": self.hwnd,
                "target_process_id": self.process_id,
                "target_foreground_sample_count": (
                    self._foreground_sample_count
                ),
                "uia_node_count": int(state.get("uia_node_count") or 0),
                "browsing": browsing,
                "private_screenshot_count": self._private_screenshot_count,
            }
        except Exception as error:
            public = {
                "source": ORACLE_SOURCE,
                "passed": False,
                "status": "capture_error",
                "run_id": run_id,
                "error": f"{type(error).__name__}: {error}",
                "target_hwnd": self.hwnd,
                "target_process_id": self.process_id,
            }
        self._last_public = public
        _write_json(self.private_dir.parent / "oracle-progress.json", public)
        # A visible user summary can only be inspected after the client returns
        # to the foreground, so the active-run probe never declares success.
        return dict(public)

    def final_verdict(self, result: dict[str, Any]) -> dict[str, Any]:
        browsing = self.browsing.public_evidence()
        client_hwnd = int((result.get("client") or {}).get("hwnd") or 0)
        try:
            client_texts = _visible_texts_for_window(client_hwnd)
            delivery = evaluate_client_delivery(
                client_texts,
                self.browsing.discovered_names,
                secret=self.secret,
            )
        except Exception as error:
            delivery = {
                "passed": False,
                "status": "client_read_error",
                "error": f"{type(error).__name__}: {error}",
            }
        handoff = result.get("after_submit_handoff") or {}
        target_binding_exact = bool(
            int(handoff.get("target_hwnd") or 0) == self.hwnd
            and int(handoff.get("target_process_id") or 0)
            == self.process_id
            and handoff.get("target_foreground_at_admission") is True
            and str(handoff.get("admitted_run_id") or "")
            == str((result.get("result") or {}).get("run_id") or "")
            and self._foreground_sample_count > 0
        )
        privacy = trace_privacy_audit(
            result.get("bound_trace_ids") or [],
            private_needles=self.browsing.discovered_names,
        )
        architecture = audit_fact_delivery_projection()
        blockers: list[str] = []
        if not target_binding_exact:
            blockers.append("qq_target_binding_not_proven")
        if not browsing["group_page_seen"]:
            blockers.append("group_list_entry_not_observed")
        if browsing["visited_group_count"] < MIN_GROUPS:
            blockers.append("multiple_group_switches_not_observed")
        if browsing["scroll_transition_count"] < 1:
            blockers.append("group_list_scroll_not_observed")
        if not browsing["list_exhausted"]:
            blockers.append("all_groups_traversal_not_proven")
        if not browsing["coverage_complete"]:
            blockers.append("not_every_discovered_group_was_visited")
        if delivery.get("passed") is not True:
            blockers.append("per_group_summary_not_visible_in_client")
        if privacy.get("passed") is not True:
            blockers.append("ordinary_trace_privacy_not_proven")
        if architecture.get("blocker_present") is True:
            blockers.append("vnext_claim_not_projected_to_client")
        passed = bool(
            target_binding_exact
            and browsing["browse_complete"]
            and delivery.get("passed") is True
            and privacy.get("passed") is True
        )
        verdict = {
            "source": ORACLE_SOURCE,
            "passed": passed,
            "status": "passed" if passed else "blocked",
            "blockers": blockers,
            "target": {
                "hwnd": self.hwnd,
                "process_id": self.process_id,
                "executable": self.identity["executable"],
                "window_rect": list(self.window_rect),
            },
            "target_binding_exact": target_binding_exact,
            "browsing": browsing,
            "delivery": delivery,
            "trace_privacy": privacy,
            "architecture_audit": architecture,
            "private_screenshot_count": self._private_screenshot_count,
        }
        self._last_public = verdict
        return verdict


def client_case_spec(*, timeout: int, max_steps: int) -> dict[str, Any]:
    """Return only values submitted through the visible primary Agent entry."""

    return {
        "task": TASK,
        "max_steps": int(max_steps),
        "timeout": int(timeout),
        "runtime_version": "vnext",
        "expected": "terminal",
    }


def _sanitized_client_result(result: dict[str, Any]) -> dict[str, Any]:
    run_result = result.get("result") or {}
    return {
        "name": result.get("name"),
        "expected": result.get("expected"),
        "entrypoint": result.get("entrypoint"),
        "configuration": result.get("configuration"),
        "duration_seconds": result.get("duration_seconds"),
        "runner_error": result.get("runner_error"),
        "run": {
            key: run_result.get(key)
            for key in (
                "outcome",
                "status",
                "status_label",
                "run_id",
                "active_seen",
            )
        },
        "bound_trace_ids": list(result.get("bound_trace_ids") or []),
        "execution_valid": result.get("execution_valid") is True,
        "passed": result.get("passed") is True,
    }


def run_qq_groups_case(
    *,
    qq_hwnd: int,
    output_root: Path,
    timeout: int = 600,
    max_steps: int = 30,
    min_groups: int = MIN_GROUPS,
) -> dict[str, Any]:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    case_dir = output_root / f"{timestamp}-qq-groups"
    private_dir = case_dir / "private"
    private_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        private_dir / "privacy-scope.json",
        {
            "classification": "local_private_e2e_artifact",
            "reason": (
                "QQ screenshots and the base client report may contain "
                "conversation content; do not publish this directory."
            ),
            "shareable_report": "../qq-groups-report.json",
        },
    )
    oracle = QqWindowOracle(
        qq_hwnd,
        private_dir,
        min_groups=min_groups,
    )

    def handoff(
        driver: client_harness.ElectronClientDriver,
        client_result: dict[str, Any],
    ) -> dict[str, Any]:
        focus = _focus_window(qq_hwnd)
        admission = _hold_target_until_admitted(
            driver,
            client_result,
            target_hwnd=int(focus["hwnd"]),
        )
        return {**focus, **admission}

    # client_harness intentionally prints a complete diagnostic result.  QQ
    # diagnostics can contain the visible final summary, so keep that stream
    # in memory and discard it rather than exposing it to an ordinary console.
    with contextlib.redirect_stdout(io.StringIO()):
        result = client_harness.run_case(
            "qq-groups-summary",
            client_case_spec(timeout=timeout, max_steps=max_steps),
            output_root=private_dir / "client",
            reset_session=True,
            after_submit=handoff,
            independent_completion_probe=oracle.probe,
            independent_verifier=oracle.final_verdict,
        )

    verdict = dict(result.get("task_verdict") or oracle.final_verdict(result))
    report = {
        "kind": "native_qq_groups_summary_e2e",
        "entrypoint": "electron_client_windows_uia",
        "task": TASK,
        "runtime_version": "vnext",
        "oracle": verdict,
        "client_result": _sanitized_client_result(result),
        "private_artifacts": {
            "directory": "private",
            "classification": "local_private_do_not_publish",
            "base_client_report": (
                "present_under_private_directory"
                if result.get("report")
                else "absent"
            ),
        },
        "copied_traces": [],
        "passed": result.get("passed") is True,
        "finished_at": datetime.now().astimezone().isoformat(),
    }
    report_path = case_dir / "qq-groups-report.json"
    _write_json(report_path, report)
    report["report"] = report_path.relative_to(output_root).as_posix()
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the unchanged QQ all-groups summary goal against a visible "
            "caller-supplied QQ NT window."
        )
    )
    parser.add_argument("--qq-hwnd", type=int, required=True)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--max-steps", type=int, default=30)
    parser.add_argument("--min-groups", type=int, default=MIN_GROUPS)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = run_qq_groups_case(
        qq_hwnd=args.qq_hwnd,
        output_root=args.output_root,
        timeout=args.timeout,
        max_steps=args.max_steps,
        min_groups=args.min_groups,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
