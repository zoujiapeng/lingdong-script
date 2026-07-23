#!/usr/bin/env python3
"""Drive the real Electron client through Windows UI Automation.

This is deliberately not an API-equivalent harness: goals, max steps, the
cold-start toggle, session reset, and the Run button are all operated through
the visible Electron window. Backend trace files are only collected after the
client submission so the result can be audited without stealing focus from the
desktop agent while it works.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import psutil
import win32api
import win32con
import win32gui
import uiautomation as auto
from PIL import ImageGrab


WORKSPACE = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = WORKSPACE / "output" / "playwright" / "client-e2e"
TRACE_ROOT = WORKSPACE / "python" / "winpilot_home" / "traces"

CLIENT_TITLE = "灵动脚本"
NAV_AGENT = "智能代理"
NAV_ASSETS = "技能与学习"
RUN_TASK = "运行任务"
STOP_TASK = "停止"
CONTINUE_TASK = "继续"
COLD_START = "冷启动（禁用 Skill/长期经验）"
START_PAUSED = "启动后暂停（确认环境后继续）"
RUNTIME_SELECTOR = "实验运行时"
RUNTIME_LABELS = {
    "legacy": "Legacy（默认）",
    "vnext": "vNext（实验）",
}
CLARIFICATION_HEADING = "任务尚未启动，请先补充必要信息"

ORIGINAL_CASES: dict[str, dict[str, Any]] = {
    "1": {
        "task": "打开QQ去QQ空间里面点赞看到的所有说说",
        "max_steps": 160,
        "timeout": 900,
    },
    "2": {"task": "用画板画一朵花", "max_steps": 100, "timeout": 600},
    "3": {
        "task": "把刚刚画的画截图发给我的手机",
        "max_steps": 100,
        "timeout": 600,
    },
    "4": {"task": "那你干嘛", "max_steps": 40, "timeout": 300},
    "5": {
        "task": "打开网易云音乐搜索周杰伦并播放列表的第一首歌",
        "max_steps": 140,
        "timeout": 900,
    },
    "6": {"task": "去抖音搜索kimi k3", "max_steps": 120, "timeout": 720},
    "7": {
        "task": "帮我把当前打开的onenote笔记转成world",
        "max_steps": 180,
        "timeout": 1200,
    },
    "8": {"task": "用kirta帮我画一部手机", "max_steps": 140, "timeout": 900},
    "9": {"task": "演示一下你有什么能力", "max_steps": 140, "timeout": 900},
    "10": {
        "task": "用Wireshark看看最近的包里面有什么",
        "max_steps": 180,
        "timeout": 1200,
    },
    "11": {
        "task": "去qq找我的手机持续聊天,他如果发@电脑就说明在命令你,你要照做",
        "max_steps": 100,
        "timeout": 240,
        "expected": "persistent",
        "persistent_probe_seconds": 120,
    },
}

MIXED_CASES: dict[str, dict[str, Any]] = {
    "mix-media-analysis": {
        "task": (
            "打开网易云音乐搜索周杰伦并播放列表的第一首歌，然后去抖音搜索kimi k3，"
            "最后用Wireshark看看最近的包里面有什么并总结结果"
        ),
        "max_steps": 300,
        "timeout": 1800,
    },
    "mix-drawing-handoff": {
        "task": "用kirta帮我画一部手机，再用画板画一朵花，然后把刚刚画的画截图发给我的手机",
        "max_steps": 260,
        "timeout": 1500,
    },
    "mix-office-demo": {
        "task": "帮我把当前打开的onenote笔记转成world，完成后演示一下你还有什么能力",
        "max_steps": 260,
        "timeout": 1800,
    },
    "mix-all-11": {
        "task": "；然后".join(
            str(ORIGINAL_CASES[str(index)]["task"])
            for index in range(1, 12)
        ),
        "max_steps": 400,
        "timeout": 3600,
        "expected": "persistent",
        "persistent_probe_seconds": 300,
    },
}

TERMINAL_STATUS = {
    "完成": "done",
    "已完成": "done",
    "成功": "done",
    "DONE": "done",
    "SUCCESS": "done",
    "失败": "failed",
    "FAIL": "failed",
    "FAILED": "failed",
    "已取消": "canceled",
    "取消": "canceled",
    "CANCELED": "canceled",
}


@dataclass
class ClientIdentity:
    pid: int
    hwnd: int
    executable: str
    command_line: list[str]
    workspace_verified: bool


def _slug(value: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z._-]+", "-", value).strip("-")
    return safe[:80] or "case"


def _visible_descendants(window: Any, control_type: str) -> list[Any]:
    expected_type = f"{control_type}Control"
    items: list[Any] = []
    for control, _depth in auto.WalkControl(window, includeTop=False):
        try:
            if control.ControlTypeName == expected_type and not control.IsOffscreen:
                items.append(control)
        except Exception:
            continue
    return items


def _control_value(control: Any) -> str:
    pattern = control.GetPattern(auto.PatternId.ValuePattern)
    if pattern is None:
        raise RuntimeError(f"控件不支持 ValuePattern: {control.ControlTypeName}")
    return str(pattern.Value or "")


def _set_control_value(control: Any, value: str) -> None:
    pattern = control.GetPattern(auto.PatternId.ValuePattern)
    if pattern is None:
        raise RuntimeError(f"控件不支持 ValuePattern: {control.ControlTypeName}")
    pattern.SetValue(value)


def _toggle_state(control: Any) -> int:
    pattern = control.GetPattern(auto.PatternId.TogglePattern)
    if pattern is None:
        raise RuntimeError(f"控件不支持 TogglePattern: {control.ControlTypeName}")
    return int(pattern.ToggleState)


def _toggle(control: Any) -> None:
    pattern = control.GetPattern(auto.PatternId.TogglePattern)
    if pattern is None:
        raise RuntimeError(f"控件不支持 TogglePattern: {control.ControlTypeName}")
    pattern.Toggle()


def _activate_run_control(control: Any, *, without_focus: bool) -> str:
    """Activate the visible Run control through one explicit UIA path."""

    if not without_focus:
        control.Click()
        return "click"
    pattern = control.GetPattern(auto.PatternId.InvokePattern)
    if pattern is None:
        raise RuntimeError("run control does not support UIA InvokePattern")
    pattern.Invoke()
    return "invoke_pattern"


class ElectronClientDriver:
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        hwnds: list[int] = []

        def collect(hwnd: int, _extra: Any) -> bool:
            if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd) == CLIENT_TITLE:
                hwnds.append(int(hwnd))
            return True

        win32gui.EnumWindows(collect, None)
        windows = [
            control
            for hwnd in hwnds
            if (control := auto.ControlFromHandle(hwnd)) is not None
        ]
        if not windows:
            raise RuntimeError("未找到正在运行的‘灵动脚本’ Electron 客户端")
        self.window = max(windows, key=lambda item: psutil.Process(item.ProcessId).create_time())
        process = psutil.Process(self.window.ProcessId)
        command_line = process.cmdline()
        executable = process.exe()
        workspace_verified = any(
            str(WORKSPACE).lower() in arg.lower() for arg in command_line
        ) or process.parent() is not None and any(
            str(WORKSPACE).lower() in arg.lower() for arg in process.parent().cmdline()
        )
        self.identity = ClientIdentity(
            pid=process.pid,
            hwnd=int(self.window.NativeWindowHandle),
            executable=executable,
            command_line=command_line,
            workspace_verified=workspace_verified,
        )
        if not workspace_verified:
            raise RuntimeError(f"客户端进程不属于当前工作区: {command_line!r}")
        self._runtime_url, self._runtime_token = self._discover_runtime_connection(
            process
        )
        self.renderer_reloaded = self.ensure_renderer_ready()

    @staticmethod
    def _discover_runtime_connection(
        electron_process: psutil.Process,
    ) -> tuple[str, str]:
        """Resolve the managed WinPilot child without printing its token."""
        for process in electron_process.children(recursive=True):
            try:
                command = process.cmdline()
                if not any(Path(item).name.casefold() == "serve.py" for item in command):
                    continue
                port = next(
                    (
                        int(item)
                        for item in command
                        if str(item).isdigit() and 1024 <= int(item) <= 65535
                    ),
                    0,
                )
                token = str(
                    process.environ().get("WINPILOT_CONTROL_TOKEN") or ""
                )
                if port and token:
                    return f"http://127.0.0.1:{port}", token
            except (psutil.Error, OSError, ValueError):
                continue
        raise RuntimeError("未找到 Electron 管理的 WinPilot 运行时连接")

    def runtime_status(self) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self._runtime_url}/api/status",
            headers={"Authorization": f"Bearer {self._runtime_token}"},
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = json.load(response)
        current = payload.get("current_run")
        compact_current = None
        if isinstance(current, dict):
            compact_current = {
                key: current.get(key)
                for key in (
                    "run_id", "kind", "status", "phase", "goal",
                    "manual_paused", "current_node_id", "current_tool",
                    "runtime_version", "error",
                )
            }
        runtime = payload.get("runtime") or {}
        runtime_git = runtime.get("git") or {}
        runtime_source = runtime.get("source") or {}
        return {
            "running": bool(payload.get("running")),
            "arp_running": bool(payload.get("arp_running")),
            "current_run": compact_current,
            "model_profile": dict(payload.get("model_profile") or {}),
            "desktop_action_queue": dict(
                payload.get("desktop_action_queue") or {}
            ),
            "runtime_identity": {
                "contract_version": runtime.get("contract_version"),
                "pid": runtime.get("pid"),
                "repo_root": runtime.get("repo_root"),
                "git": {
                    key: runtime_git.get(key)
                    for key in (
                        "branch", "loaded_commit", "current_commit", "dirty",
                    )
                },
                "source": {
                    key: runtime_source.get(key)
                    for key in ("loaded_stamp", "current_stamp", "drifted")
                },
            },
        }

    def run_detail(self, run_id: str) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self._runtime_url}/api/v2/runs/{run_id}",
            headers={"Authorization": f"Bearer {self._runtime_token}"},
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = json.load(response)
        return {
            "run": dict(payload.get("run") or {}),
            "arp": dict(payload.get("arp") or {}),
        }

    def restore_and_focus(self) -> None:
        win32gui.ShowWindow(self.identity.hwnd, win32con.SW_RESTORE)
        left, top, right, bottom = win32gui.GetWindowRect(self.identity.hwnd)
        # The renderer intentionally collapses the sidebar to icon-only at its
        # default small window size.  Maximize only the test client when that
        # responsive layout would otherwise remove the visible/UIA nav labels.
        if right - left < 1050 or bottom - top < 650:
            win32gui.ShowWindow(self.identity.hwnd, win32con.SW_MAXIMIZE)
        win32gui.BringWindowToTop(self.identity.hwnd)
        focused = False
        errors: list[str] = []
        for _attempt in range(5):
            try:
                # A balanced ALT tap unlocks SetForegroundWindow without
                # activating anything inside the client.  Verify the result;
                # the Win32 call can fail silently under foreground-lock rules.
                win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
                win32gui.SetForegroundWindow(self.identity.hwnd)
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
            finally:
                win32api.keybd_event(
                    win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0,
                )
            time.sleep(0.2)
            focused = int(win32gui.GetForegroundWindow() or 0) == int(
                self.identity.hwnd
            )
            if focused:
                break
            try:
                self.window.SetFocus()
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
            time.sleep(0.1)
            focused = int(win32gui.GetForegroundWindow() or 0) == int(
                self.identity.hwnd
            )
            if focused:
                break
        if not focused:
            raise RuntimeError(
                "无法验证 Electron 客户端已成为前台窗口: "
                + "; ".join(errors[-3:])
            )
        time.sleep(0.6)

    def ensure_renderer_ready(
        self,
        *,
        timeout: int = 120,
        initial_timeout: int = 120,
    ) -> bool:
        """Recover a dev-client window whose Vite document loaded blank."""

        def ready() -> bool:
            try:
                # Chromium replaces its UIA document provider during Ctrl+R;
                # rebind the control from the verified HWND on every probe.
                refreshed = auto.ControlFromHandle(self.identity.hwnd)
                if refreshed is not None and refreshed.Exists(maxSearchSeconds=0.2):
                    self.window = refreshed
                if NAV_AGENT in {
                    item.Name.strip() for item in self.buttons()
                }:
                    return True
                # Chromium may expose the Document text before its descendant
                # Button providers settle after a minimized window is restored.
                # The task editor and run control together distinguish the
                # actual agent page from a partial shell or a blank renderer.
                for document in _visible_descendants(self.window, "Document"):
                    value = str(document.Name or "")
                    if NAV_AGENT in value and RUN_TASK in value:
                        return True
                return False
            except Exception:
                return False

        def wait_ready(seconds: int) -> bool:
            deadline = time.time() + max(1, seconds)
            while time.time() < deadline:
                if ready():
                    return True
                time.sleep(0.25)
            return False

        self.restore_and_focus()
        # Restoring an occluded/minimized Chromium window can briefly leave the
        # UIA subtree empty even though the existing renderer is healthy.  Give
        # that provider time to settle before issuing a destructive reload.
        if wait_ready(initial_timeout):
            return False
        auto.SendKeys("{Ctrl}r")
        if wait_ready(timeout):
            return True
        try:
            self.capture_window("renderer-readiness-failed.png")
        except Exception:
            pass
        raise RuntimeError("Electron 客户端渲染器重载后仍未显示智能代理页面")

    def buttons(self) -> list[Any]:
        return _visible_descendants(self.window, "Button")

    def texts(self) -> list[str]:
        values: list[str] = []
        for control in _visible_descendants(self.window, "Text"):
            try:
                value = control.Name.strip()
                if value:
                    values.append(value)
            except Exception:
                continue
        return values

    def button(self, title: str, *, prefix: bool = False) -> Any:
        matches = []
        for control in self.buttons():
            name = control.Name.strip()
            if name == title or prefix and name.startswith(title):
                matches.append(control)
        if not matches:
            raise RuntimeError(f"客户端中未找到按钮: {title}")
        return matches[0]

    def navigate(self, title: str) -> None:
        self.button(title).Click()
        time.sleep(0.8)

    def capture_window(self, name: str) -> str:
        path = self.output_dir / name
        rect = self.window.BoundingRectangle
        ImageGrab.grab(
            bbox=(rect.left, rect.top, rect.right, rect.bottom),
            all_screens=True,
        ).save(path)
        return str(path)

    def capture_desktop(self, name: str) -> str:
        path = self.output_dir / name
        ImageGrab.grab(all_screens=True).save(path)
        return str(path)

    def clear_session_through_client(self) -> dict[str, Any]:
        self.restore_and_focus()
        self.navigate(NAV_ASSETS)
        session_button = self.button("会话 ", prefix=True)
        before_name = session_button.Name.strip()
        before_match = re.search(r"(\d+)$", before_name)
        before = int(before_match.group(1)) if before_match else -1
        session_button.Click()
        time.sleep(0.5)
        try:
            self.button("清空会话记忆").Click()
        except RuntimeError:
            # Chromium can omit title-only icon buttons from UIA names; reload
            # the tab once before treating it as a client defect.
            session_button.Click()
            time.sleep(0.3)
            self.button("清空会话记忆").Click()
        cleared = True
        deadline = time.time() + 15
        while time.time() < deadline:
            time.sleep(0.3)
            now = self.button("会话 ", prefix=True).Name.strip()
            if re.search(r"\b0$", now):
                break
        else:
            raise RuntimeError("通过客户端清空会话后，会话计数未归零")
        after_name = self.button("会话 ", prefix=True).Name.strip()
        after_match = re.search(r"(\d+)$", after_name)
        after = int(after_match.group(1)) if after_match else -1
        if after != 0:
            raise RuntimeError(f"冷启动前会话计数不是 0: {after_name}")
        proof = self.capture_window("session-reset.png")
        self.navigate(NAV_AGENT)
        return {"before": before, "after": after, "cleared": cleared, "proof": proof}

    def configure_goal(
        self,
        task: str,
        max_steps: int,
        runtime_version: str = "legacy",
        start_paused: bool = False,
    ) -> dict[str, Any]:
        self.restore_and_focus()
        try:
            self.navigate(NAV_AGENT)
        except RuntimeError:
            pass
        edits = _visible_descendants(self.window, "Edit")
        if not edits:
            raise RuntimeError("客户端目标输入框不可见")
        goal = next(
            (edit for edit in edits if "描述希望代理" in edit.Name),
            edits[0],
        )
        _set_control_value(goal, task)

        expected_runtime = str(runtime_version or "legacy").casefold()
        if expected_runtime not in RUNTIME_LABELS:
            raise RuntimeError(f"不支持的客户端实验运行时: {runtime_version!r}")
        runtime_select = next(
            (
                item for item in _visible_descendants(self.window, "ComboBox")
                if item.Name.strip() == RUNTIME_SELECTOR
            ),
            None,
        )
        if runtime_select is None:
            raise RuntimeError("客户端实验运行时选择器不可见")
        runtime_select.Click()
        auto.SendKeys("{Home}" if expected_runtime == "legacy" else "{End}")
        auto.SendKeys("{Enter}")
        auto.SendKeys("{Tab}")
        time.sleep(0.2)
        observed_runtime = _control_value(runtime_select).strip()
        accepted_values = {
            expected_runtime,
            RUNTIME_LABELS[expected_runtime].casefold(),
        }
        if observed_runtime.casefold() not in accepted_values:
            raise RuntimeError(
                "无法通过客户端选择实验运行时: "
                f"expected={expected_runtime!r}, observed={observed_runtime!r}"
            )

        checkboxes = _visible_descendants(self.window, "CheckBox")
        cold_start_checkbox = next(
            (item for item in checkboxes if item.Name == COLD_START),
            None,
        )
        if cold_start_checkbox is None:
            raise RuntimeError("客户端冷启动开关不可见")
        if _toggle_state(cold_start_checkbox) != 1:
            _toggle(cold_start_checkbox)
            time.sleep(0.2)
        if _toggle_state(cold_start_checkbox) != 1:
            raise RuntimeError("无法通过客户端开启冷启动")
        start_paused_checkbox = next(
            (item for item in checkboxes if item.Name == START_PAUSED),
            None,
        )
        if start_paused_checkbox is None:
            raise RuntimeError("客户端启动后暂停开关不可见")
        expected_pause_state = 1 if start_paused else 0
        if _toggle_state(start_paused_checkbox) != expected_pause_state:
            _toggle(start_paused_checkbox)
            time.sleep(0.2)
        if _toggle_state(start_paused_checkbox) != expected_pause_state:
            raise RuntimeError("无法通过客户端设置启动后暂停状态")

        spinners = _visible_descendants(self.window, "Spinner")
        if not spinners:
            raise RuntimeError("客户端步骤上限控件不可见")
        spinner = spinners[0]
        spinner.Click()
        auto.SendKeys("{Ctrl}a")
        auto.SendKeys(str(max_steps))
        auto.SendKeys("{Tab}")
        time.sleep(0.2)

        value = _control_value(goal)
        if value != task:
            raise RuntimeError(f"客户端目标输入不一致: {value!r}")
        return {
            "task_input": value,
            "cold_start_toggle": _toggle_state(cold_start_checkbox),
            "start_paused_toggle": _toggle_state(start_paused_checkbox),
            "max_steps": max_steps,
            "runtime_version": expected_runtime,
            "runtime_selector_value": observed_runtime,
        }

    def resume_paused_run(
        self,
        run_id: str,
        *,
        timeout: float = 20.0,
    ) -> dict[str, Any]:
        """Resume one exact run through the visible Continue control."""

        self.restore_and_focus()
        deadline = time.monotonic() + timeout
        control = None
        while time.monotonic() < deadline:
            try:
                control = self.button(CONTINUE_TASK)
                break
            except RuntimeError:
                time.sleep(0.1)
        if control is None:
            raise RuntimeError("客户端未显示可恢复当前运行的继续按钮")
        method = _activate_run_control(control, without_focus=False)
        while time.monotonic() < deadline:
            detail = self.run_detail(run_id)
            projection, arp = detail["run"], detail["arp"]
            if (
                str(projection.get("run_id") or "") != run_id
                or str(arp.get("run_id") or "") != run_id
            ):
                raise RuntimeError("继续操作返回了不同的运行身份")
            status = str(arp.get("status") or "")
            if arp.get("manual_paused") is False and status in {
                "queued", "running",
            }:
                iconic = bool(win32gui.IsIconic(self.identity.hwnd))
                foreground = int(win32gui.GetForegroundWindow() or 0)
                if not iconic or foreground == self.identity.hwnd:
                    raise RuntimeError("继续桌面运行前客户端没有让出前台")
                return {
                    "run_id": run_id,
                    "status": status,
                    "manual_paused": False,
                    "control_method": method,
                    "control_surface_iconic": iconic,
                    "foreground_hwnd": foreground,
                }
            if status not in {"paused", "queued", "running"}:
                raise RuntimeError("运行在继续完成前进入终态")
            time.sleep(0.05)
        raise RuntimeError("客户端继续操作没有恢复同一运行")

    def stop_active_run(self, *, timeout: int = 30) -> dict[str, Any]:
        """Stop a client-submitted run through the visible Electron UI."""

        before = {}
        try:
            before = self.runtime_status()
        except Exception:
            pass
        self.restore_and_focus()
        requested = False
        try:
            self.button(STOP_TASK).Click()
            requested = True
        except RuntimeError:
            # The run may have become terminal between the status poll and
            # foreground restoration.  Verify that state below.
            pass
        deadline = time.time() + max(1, timeout)
        run_button_restored = False
        runtime_stopped = False
        while time.time() < deadline:
            try:
                run_button_restored = RUN_TASK in {
                    button.Name.strip() for button in self.buttons()
                }
            except Exception:
                run_button_restored = False
            try:
                runtime_stopped = not bool(self.runtime_status().get("running"))
            except Exception:
                runtime_stopped = False
            if run_button_restored and runtime_stopped:
                break
            time.sleep(0.5)
        return {
            "requested_stop": requested,
            "run_button_restored": run_button_restored,
            "runtime_stopped": runtime_stopped,
            "runtime_before": before,
        }

    def _topbar_status(self, task: str) -> str:
        task_controls = []
        for control in _visible_descendants(self.window, "Text"):
            try:
                if control.Name.strip() == task:
                    task_controls.append(control)
            except Exception:
                continue
        if not task_controls:
            return ""
        window_top = self.window.BoundingRectangle.top
        topbar_task = min(task_controls, key=lambda item: item.BoundingRectangle.top)
        if topbar_task.BoundingRectangle.top > window_top + 150:
            return ""
        task_rect = topbar_task.BoundingRectangle
        candidates: list[tuple[int, str]] = []
        for control in _visible_descendants(self.window, "Text"):
            try:
                name = control.Name.strip()
                rect = control.BoundingRectangle
            except Exception:
                continue
            if (
                name in TERMINAL_STATUS
                and rect.left >= task_rect.left
                and abs(rect.top - task_rect.top) <= 12
            ):
                candidates.append((rect.left, name))
        return max(candidates, default=(0, ""))[1]

    def wait_for_result(
        self,
        task: str,
        timeout: int,
        *,
        expected: str = "terminal",
        persistent_probe_seconds: int = 120,
        previous_run_id: str = "",
    ) -> dict[str, Any]:
        started_at = time.time()
        deadline = time.time() + timeout
        bound_run_id = ""
        active_seen = False
        last_progress = 0.0
        last_status = ""
        runtime_status: dict[str, Any] = {}
        normalized_task = re.sub(r"\s+", " ", str(task or "")).strip()
        while time.time() < deadline:
            now = time.time()
            try:
                runtime_status = self.runtime_status()
            except Exception:
                runtime_status = {}
            current_runtime = runtime_status.get("current_run")
            current_run_id = str(
                current_runtime.get("run_id")
                if isinstance(current_runtime, dict) else ""
            )
            current_goal = str(
                current_runtime.get("goal")
                if isinstance(current_runtime, dict) else ""
            )
            normalized_goal = re.sub(r"\s+", " ", current_goal).strip()
            if (
                not bound_run_id
                and current_run_id
                and current_run_id != previous_run_id
            ):
                if normalized_goal == normalized_task:
                    bound_run_id = current_run_id
                elif normalized_goal and runtime_status.get("running"):
                    return {
                        "outcome": "submission_mismatch",
                        "status": "failed",
                        "run_id": current_run_id,
                        "expected_goal": task,
                        "observed_goal": current_goal,
                        "active_seen": False,
                        "runtime": runtime_status,
                    }
            if (
                bound_run_id
                and current_run_id
                and current_run_id != bound_run_id
            ):
                return {
                    "outcome": "run_replaced",
                    "status": "failed",
                    "run_id": bound_run_id,
                    "replacement_run_id": current_run_id,
                    "active_seen": active_seen,
                    "runtime": runtime_status,
                }
            if (
                bound_run_id
                and current_run_id == bound_run_id
                and runtime_status.get("running")
            ):
                active_seen = True

            # The client may be intentionally in the background while the
            # agent controls another application. UIA is best-effort during
            # that interval; the managed runtime status is the non-invasive
            # terminal signal.
            try:
                button_names = {
                    item.Name.strip() for item in self.buttons()
                }
                texts = self.texts()
            except Exception:
                button_names = set()
                texts = []
            if CLARIFICATION_HEADING in texts:
                questions = next(
                    (value for value in texts if value != CLARIFICATION_HEADING and "；" in value),
                    "",
                )
                return {
                    "outcome": "clarification",
                    "status": "needs_input",
                    "questions": questions,
                    "run_id": bound_run_id,
                    "active_seen": active_seen,
                    "visible_text": texts,
                    "runtime": runtime_status,
                }
            if bound_run_id and STOP_TASK in button_names:
                active_seen = True
            if (
                expected == "persistent"
                and bound_run_id
                and active_seen
                and current_run_id == bound_run_id
                and runtime_status.get("running")
                and now - started_at >= persistent_probe_seconds
            ):
                return {
                    "outcome": "persistent_active",
                    "status": "running",
                    "run_id": bound_run_id,
                    "active_seen": True,
                    "probe_seconds": round(now - started_at, 3),
                    "visible_text": texts,
                    "runtime": runtime_status,
                }
            if (
                bound_run_id
                and runtime_status
                and current_run_id == bound_run_id
                and not runtime_status.get("running")
            ):
                runtime_terminal = str(
                    current_runtime.get("status")
                    if isinstance(current_runtime, dict) else ""
                ).casefold()
                if runtime_terminal in {
                    "done", "completed", "success", "fail", "failed", "error",
                    "reply", "blocked", "canceled", "cancelled", "stopped",
                }:
                    return {
                        "outcome": "terminal",
                        "status": (
                            "done"
                            if runtime_terminal in {"done", "completed", "success"}
                            else "canceled"
                            if runtime_terminal in {"canceled", "cancelled", "stopped"}
                            else "failed"
                        ),
                        "status_label": runtime_terminal,
                        "run_id": bound_run_id,
                        "active_seen": active_seen,
                        "visible_text": texts,
                        "runtime": runtime_status,
                    }
            status = self._topbar_status(task)
            if status:
                last_status = status
            terminal = TERMINAL_STATUS.get(status)
            if bound_run_id and terminal and (active_seen or RUN_TASK in button_names):
                return {
                    "outcome": "terminal",
                    "status": terminal,
                    "status_label": status,
                    "run_id": bound_run_id,
                    "active_seen": active_seen,
                    "visible_text": texts,
                    "runtime": runtime_status,
                }
            if (
                bound_run_id
                and active_seen
                and RUN_TASK in button_names
                and STOP_TASK not in button_names
            ):
                return {
                    "outcome": "terminal",
                    "status": TERMINAL_STATUS.get(last_status, "unknown"),
                    "status_label": last_status,
                    "run_id": bound_run_id,
                    "active_seen": active_seen,
                    "visible_text": texts,
                    "runtime": runtime_status,
                }
            if not active_seen and now + 2 < deadline and now - last_progress > 15:
                # Progress output is intentionally stdout so a supervising Codex
                # turn can report status without focusing the client.
                print(
                    json.dumps(
                        {
                            "event": "waiting",
                            "elapsed_seconds": round(timeout - (deadline - now), 1),
                            "status_label": last_status,
                            "buttons": sorted(button_names),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                last_progress = now
            time.sleep(1.0)
        return {
            "outcome": "timeout",
            "status": "timeout",
            "run_id": bound_run_id,
            "status_label": last_status,
            "active_seen": active_seen,
            "visible_text": self.texts(),
            "runtime": runtime_status,
        }


def _trace_names() -> set[str]:
    if not TRACE_ROOT.exists():
        return set()
    return {
        item.stem
        for item in TRACE_ROOT.glob("arp-*.jsonl")
        if item.is_file()
    }


def _bound_trace_evidence(
    run_id: str,
    task: str,
    expected_runtime_version: str = "",
) -> dict[str, Any]:
    """Bind evidence to the exact outer run submitted by the visible client."""

    run_id = str(run_id or "").strip()
    exact_path = TRACE_ROOT / f"{run_id}.jsonl"
    paths = [exact_path] if exact_path.is_file() else []
    if TRACE_ROOT.is_dir():
        # The control-plane run id is allocated when the stage AgentRunner
        # starts, while the containing ARP trace file is named slightly earlier.
        # File timestamps are therefore only a search hint.  Identity comes
        # exclusively from the exact agent_start.run_id + original task events.
        recent = sorted(
            TRACE_ROOT.glob("arp-*.jsonl"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )[:64]
        paths.extend(path for path in recent if path not in paths)
    starts: list[dict[str, Any]] = []
    agent_starts: list[dict[str, Any]] = []
    runtime_selections: list[dict[str, Any]] = []
    matched_paths: list[Path] = []
    parse_errors: list[str] = []
    for path in paths:
        path_starts: list[dict[str, Any]] = []
        path_agent_starts: list[dict[str, Any]] = []
        path_runtime_selections: list[dict[str, Any]] = []
        try:
            for raw_line in path.read_text(encoding="utf-8").splitlines():
                if not raw_line.strip():
                    continue
                event = json.loads(raw_line)
                if isinstance(event, dict) and event.get("type") == "arp_run_start":
                    path_starts.append(event)
                if isinstance(event, dict) and event.get("type") == "agent_start":
                    path_agent_starts.append(event)
                if (
                    isinstance(event, dict)
                    and event.get("type") == "desktop_runtime_selected"
                ):
                    path_runtime_selections.append(event)
        except Exception as error:
            parse_errors.append(f"{path.name}: {type(error).__name__}: {error}")
            continue
        task_starts = [
            event for event in path_starts
            if str((event.get("task_contract") or {}).get("task") or "") == str(task or "")
        ]
        client_starts = [
            event for event in path_agent_starts
            if str(event.get("run_id") or "") == run_id
            and str(event.get("task") or "") == str(task or "")
        ]
        legacy_outer_start = any(
            str(event.get("run_id") or "") == run_id for event in task_starts
        )
        accepted_runtime_run_ids = {
            run_id,
            *(
                str(event.get("run_id") or "")
                for event in task_starts
                if str(event.get("run_id") or "")
            ),
        }
        runtime_matches = (
            not expected_runtime_version
            or any(
                str(event.get("run_id") or "") in accepted_runtime_run_ids
                and str(event.get("runtime_version") or "")
                == expected_runtime_version
                for event in path_runtime_selections
            )
        )
        if task_starts and (client_starts or legacy_outer_start) and runtime_matches:
            matched_paths.append(path)
            starts = path_starts
            agent_starts = path_agent_starts
            runtime_selections = path_runtime_selections
            break
    path = matched_paths[0] if len(matched_paths) == 1 else None
    return {
        "passed": bool(run_id and path is not None),
        "run_id": run_id,
        "trace_ids": [path.stem] if path is not None else [],
        "trace_file": str(path) if path is not None else "",
        "start_event_count": len(starts),
        "agent_start_count": len(agent_starts),
        "expected_runtime_version": expected_runtime_version,
        "runtime_versions": [
            str(event.get("runtime_version") or "")
            for event in runtime_selections
        ],
        "matching_trace_count": len(matched_paths),
        "candidate_trace_count": len(paths),
        "candidate_trace_files": [str(item) for item in paths[:8]],
        "parse_errors": parse_errors,
    }


def _memory_isolation_evidence(
    trace_ids: list[str],
    configuration: dict[str, Any],
    *,
    require_empty_session: bool = False,
    required_context_run_id: str = "",
) -> dict[str, Any]:
    """Verify that a client cold-start run did not replay long-term experience."""

    policies: list[str] = []
    agent_starts: list[dict[str, Any]] = []
    inspected: list[str] = []
    parse_errors: list[str] = []
    session_contexts: list[list[dict[str, Any]]] = []
    for trace_id in trace_ids:
        path = TRACE_ROOT / f"{trace_id}.jsonl"
        if not path.is_file():
            continue
        inspected.append(str(path))
        try:
            for raw_line in path.read_text(encoding="utf-8").splitlines():
                if not raw_line.strip():
                    continue
                event = json.loads(raw_line)
                if not isinstance(event, dict):
                    continue
                if event.get("type") == "arp_run_start":
                    contract = event.get("task_contract")
                    if isinstance(contract, dict):
                        policies.append(str(contract.get("experience_policy") or ""))
                        context = contract.get("session_context")
                        session_contexts.append([
                            dict(item) for item in (context or [])
                            if isinstance(item, dict)
                        ])
                if event.get("type") == "agent_start":
                    runtime = event.get("runtime")
                    agent_starts.append({
                        "run_id": str(event.get("run_id") or ""),
                        "has_experience": bool(event.get("has_experience")),
                        "experience_enabled": bool(
                            runtime.get("experience_enabled")
                            if isinstance(runtime, dict) else False
                        ),
                    })
        except Exception as error:
            parse_errors.append(f"{path.name}: {type(error).__name__}: {error}")

    toggle_on = int(configuration.get("cold_start_toggle") or 0) == 1
    contracts_disabled = bool(policies) and all(
        value.casefold() == "disabled" for value in policies
    )
    runners_clean = all(
        not item["has_experience"] and not item["experience_enabled"]
        for item in agent_starts
    )
    session_empty = bool(session_contexts) and all(
        not context for context in session_contexts
    )
    required_context_present = bool(session_contexts) and any(
        any(
            str(item.get("run_id") or "") == required_context_run_id
            for item in context
        )
        for context in session_contexts
    )
    context_ok = (
        session_empty
        if require_empty_session
        else required_context_present
        if required_context_run_id
        else True
    )
    return {
        "passed": bool(
            toggle_on
            and contracts_disabled
            and runners_clean
            and context_ok
            and not parse_errors
        ),
        "cold_start_toggle": int(configuration.get("cold_start_toggle") or 0),
        "contract_experience_policies": policies,
        "agent_starts": agent_starts,
        "require_empty_session": require_empty_session,
        "session_contexts": session_contexts,
        "session_empty": session_empty,
        "required_context_run_id": required_context_run_id,
        "required_context_present": required_context_present,
        "inspected_trace_files": inspected,
        "parse_errors": parse_errors,
    }


def _execution_evidence(trace_ids: list[str]) -> dict[str, Any]:
    """Summarize auditable client-run actions without deciding task semantics."""

    attempts: list[str] = []
    successful: list[str] = []
    terminal_events: list[str] = []
    parse_errors: list[str] = []
    conversation_bound = False
    monitoring_started = False
    monitor_tools = {"read_text", "screen_watch", "wait", "wait_for_visual_change"}
    for trace_id in trace_ids:
        path = TRACE_ROOT / f"{trace_id}.jsonl"
        if not path.is_file():
            continue
        try:
            for raw_line in path.read_text(encoding="utf-8").splitlines():
                if not raw_line.strip():
                    continue
                event = json.loads(raw_line)
                if not isinstance(event, dict):
                    continue
                event_type = str(event.get("type") or "")
                if event_type in {"arp_run_done", "arp_run_failed", "arp_run_canceled"}:
                    terminal_events.append(event_type)
                if event_type != "agent_tool":
                    continue
                tool = str(event.get("tool") or "")
                if not tool:
                    continue
                attempts.append(tool)
                meta = event.get("result_meta")
                ok = bool(meta.get("ok")) if isinstance(meta, dict) else False
                if not ok:
                    preview = str(event.get("result_preview") or "").lstrip()
                    if preview.startswith("{"):
                        try:
                            ok = json.loads(preview).get("ok") is True
                        except Exception:
                            ok = False
                if not ok:
                    continue
                successful.append(tool)
                if tool == "open_conversation":
                    conversation_bound = True
                if tool in monitor_tools:
                    monitoring_started = True
                if tool == "run_action_batch":
                    actions = (event.get("args") or {}).get("actions")
                    if isinstance(actions, list) and any(
                        str(item.get("action") or "") in {
                            "wait", "wait_change", "wait_for_visual_change", "wait_stable",
                        }
                        for item in actions
                        if isinstance(item, dict)
                    ):
                        monitoring_started = True
        except Exception as error:
            parse_errors.append(f"{path.name}: {type(error).__name__}: {error}")
    return {
        "tool_attempts": attempts,
        "successful_tools": successful,
        "terminal_events": terminal_events,
        "conversation_bound": conversation_bound,
        "monitoring_started": monitoring_started,
        "persistent_ready": bool(conversation_bound and monitoring_started),
        "parse_errors": parse_errors,
    }


def _case_execution_valid(result: dict[str, Any]) -> bool:
    """Check the client/run plumbing without claiming the GUI goal succeeded."""

    if result.get("runner_error"):
        return False
    run_result = result.get("result") or {}
    outcome = str(run_result.get("outcome") or "")
    status = str(run_result.get("status") or "")
    expected = str(result.get("expected") or "terminal")
    runtime_ok = (
        (
            outcome == "persistent_active"
            and (result.get("execution_evidence") or {}).get("persistent_ready") is True
        )
        if expected == "persistent"
        else outcome == "terminal" and status == "done"
    )
    return bool(
        runtime_ok
        and (result.get("memory_isolation") or {}).get("passed") is True
        and (result.get("trace_binding") or {}).get("passed") is True
    )


def _case_passed(result: dict[str, Any]) -> bool:
    """Require an oracle independent from the agent/runtime completion claim."""

    verdict = result.get("task_verdict") or {}
    source = str(verdict.get("source") or "").strip().casefold()
    independent_source = source not in {
        "", "agent", "agent_claim", "runtime", "runtime_status",
    }
    return bool(
        _case_execution_valid(result)
        and independent_source
        and verdict.get("passed") is True
    )


def run_case(
    name: str,
    spec: dict[str, Any],
    *,
    output_root: Path,
    reset_session: bool,
    before_submit: Callable[[ElectronClientDriver, dict[str, Any]], dict[str, Any] | None]
    | None = None,
    after_submit: Callable[[ElectronClientDriver, dict[str, Any]], dict[str, Any] | None]
    | None = None,
    independent_verifier: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    required_context_run_id: str = "",
    submit_without_focus: bool = False,
) -> dict[str, Any]:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = output_root / f"{timestamp}-{_slug(name)}"
    output_dir.mkdir(parents=True, exist_ok=True)
    driver = ElectronClientDriver(output_dir)
    result: dict[str, Any] = {
        "name": name,
        "task": spec["task"],
        "expected": spec.get("expected", "terminal"),
        "started_at": datetime.now().astimezone().isoformat(),
        "client": asdict(driver.identity),
        "client_renderer_reloaded": driver.renderer_reloaded,
        "entrypoint": "electron_client_windows_uia",
    }
    try:
        runtime_before = driver.runtime_status()
        result["runtime_before"] = runtime_before
        previous_current = runtime_before.get("current_run")
        previous_run_id = str(
            previous_current.get("run_id")
            if isinstance(previous_current, dict) else ""
        )
        if reset_session:
            result["session_reset"] = driver.clear_session_through_client()
        else:
            result["session_reset"] = {"skipped": True, "reason": "same scenario context"}
        start_paused = spec.get("start_paused", False)
        if type(start_paused) is not bool:
            raise RuntimeError("start_paused case option must be boolean")
        result["configuration"] = driver.configure_goal(
            spec["task"],
            int(spec.get("max_steps", 120)),
            str(spec.get("runtime_version") or "legacy"),
            start_paused,
        )
        result["screenshots"] = {
            "before_submit": driver.capture_window("before-submit.png")
        }
        run_control = driver.button(RUN_TASK)
        if before_submit is not None:
            handoff = before_submit(driver, result)
            if handoff is not None:
                result["before_submit_handoff"] = handoff
        result["submit_control_method"] = _activate_run_control(
            run_control, without_focus=submit_without_focus,
        )
        submitted_at = time.time()
        result["submitted_at"] = datetime.now().astimezone().isoformat()
        print(
            json.dumps(
                {
                    "event": "submitted",
                    "name": name,
                    "task": spec["task"],
                    "client_pid": driver.identity.pid,
                    "cold_start": True,
                    "runtime_version": result["configuration"]["runtime_version"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        if after_submit is not None:
            handoff = after_submit(driver, result)
            if handoff is not None:
                result["after_submit_handoff"] = handoff
        result["result"] = driver.wait_for_result(
            spec["task"],
            int(spec.get("timeout", 900)),
            expected=str(spec.get("expected") or "terminal"),
            persistent_probe_seconds=int(spec.get("persistent_probe_seconds") or 120),
            previous_run_id=previous_run_id,
        )
        result["duration_seconds"] = round(time.time() - submitted_at, 3)
        driver.restore_and_focus()
        result["screenshots"].update(
            {
                "desktop_terminal": driver.capture_desktop("desktop-terminal.png"),
                "client_terminal": driver.capture_window("client-terminal.png"),
            }
        )
        if result["result"].get("outcome") == "timeout":
            result["timeout_cleanup"] = driver.stop_active_run()
            result["timeout_cleanup"]["proof"] = driver.capture_window(
                "timeout-stopped.png"
            )
        if result["result"].get("outcome") == "persistent_active":
            result["persistent_test_cleanup"] = driver.stop_active_run()
            result["persistent_test_cleanup"]["proof"] = driver.capture_window(
                "persistent-test-stopped.png"
            )
    except Exception as error:
        result["runner_error"] = f"{type(error).__name__}: {error}"
        try:
            if driver.runtime_status().get("running"):
                result["error_cleanup"] = driver.stop_active_run()
        except Exception:
            pass
        try:
            result.setdefault("screenshots", {})["runner_error"] = driver.capture_window(
                "runner-error.png"
            )
        except Exception:
            pass
    finally:
        bound_run_id = str((result.get("result") or {}).get("run_id") or "")
        result["trace_binding"] = _bound_trace_evidence(
            bound_run_id,
            str(result.get("task") or ""),
            str((result.get("configuration") or {}).get("runtime_version") or ""),
        )
        result["bound_trace_ids"] = list(
            result["trace_binding"].get("trace_ids") or []
        )
        # Compatibility field: unlike the old directory set difference, this
        # now contains only the exact client-bound outer run.
        result["new_trace_ids"] = list(result["bound_trace_ids"])
        result["memory_isolation"] = _memory_isolation_evidence(
            list(result["bound_trace_ids"]),
            dict(result.get("configuration") or {}),
            require_empty_session=reset_session,
            required_context_run_id=required_context_run_id,
        )
        result["execution_evidence"] = _execution_evidence(
            list(result["bound_trace_ids"]),
        )
        if independent_verifier is None:
            result["task_verdict"] = {
                "source": "none",
                "status": "unverified",
                "passed": False,
                "reason": (
                    "No independent task-state oracle was supplied; runtime done is "
                    "only a candidate completion."
                ),
            }
        else:
            try:
                verdict = dict(independent_verifier(result) or {})
                verdict.setdefault("source", "external_verifier")
                verdict["passed"] = verdict.get("passed") is True
                result["task_verdict"] = verdict
            except Exception as error:
                result["task_verdict"] = {
                    "source": "external_verifier",
                    "status": "error",
                    "passed": False,
                    "error": f"{type(error).__name__}: {error}",
                }
        result["execution_valid"] = _case_execution_valid(result)
        result["passed"] = _case_passed(result)
        result["finished_at"] = datetime.now().astimezone().isoformat()
        report = output_dir / "report.json"
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["report"] = str(report)
        print(json.dumps({"event": "finished", **result}, ensure_ascii=False), flush=True)
    return result


def run_suite(
    suite: str,
    *,
    output_root: Path,
    fail_fast: bool = False,
    max_steps_override: int | None = None,
    timeout_override: int | None = None,
) -> dict[str, Any]:
    """Run fixtures through one real Electron client, never a backend shortcut."""

    selections: list[tuple[str, dict[str, Any], bool]] = []
    if suite in {"original", "all"}:
        for case_id in sorted(ORIGINAL_CASES, key=int):
            selections.append((
                f"original-{case_id}",
                dict(ORIGINAL_CASES[case_id]),
                case_id == "3",
            ))
    if suite in {"mixed", "all"}:
        for name, spec in MIXED_CASES.items():
            selections.append((name, dict(spec), False))

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suite_dir = output_root / f"{timestamp}-suite-{_slug(suite)}"
    suite_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "suite": suite,
        "entrypoint": "electron_client_windows_uia",
        "cold_start_required": True,
        "started_at": datetime.now().astimezone().isoformat(),
        "cases": [],
    }
    prior_results: dict[str, dict[str, Any]] = {}
    for name, source_spec, needs_prior_context in selections:
        spec = dict(source_spec)
        if max_steps_override is not None:
            spec["max_steps"] = max_steps_override
        if timeout_override is not None:
            spec["timeout"] = timeout_override
        preserve_context = bool(
            needs_prior_context
            and _case_execution_valid(prior_results.get("original-2") or {})
        )
        dependency = prior_results.get("original-2") or {}
        required_context_run_id = (
            str((dependency.get("result") or {}).get("run_id") or "")
            if preserve_context else ""
        )
        result = run_case(
            name,
            spec,
            output_root=suite_dir,
            reset_session=not preserve_context,
            required_context_run_id=required_context_run_id,
        )
        result["suite_context"] = {
            "preserved": preserve_context,
            "dependency": "original-2" if needs_prior_context else "",
        }
        report["cases"].append(result)
        prior_results[name] = result
        if fail_fast and not _case_passed(result):
            break

    passed_count = sum(_case_passed(item) for item in report["cases"])
    report.update({
        "finished_at": datetime.now().astimezone().isoformat(),
        "case_count": len(report["cases"]),
        "passed_count": passed_count,
        "failed_count": len(report["cases"]) - passed_count,
        "passed": passed_count == len(report["cases"]) and bool(report["cases"]),
    })
    path = suite_dir / "suite-report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["report"] = str(path)
    print(json.dumps({"event": "suite_finished", **report}, ensure_ascii=False), flush=True)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=sorted(ORIGINAL_CASES), help="run one original case")
    parser.add_argument("--mixed", choices=sorted(MIXED_CASES), help="run one mixed case")
    parser.add_argument("--task", help="run an ad-hoc task through the client")
    parser.add_argument(
        "--suite",
        choices=["original", "mixed", "all"],
        help="run the exact 11 cases, mixed long tasks, or both",
    )
    parser.add_argument("--name", default="ad-hoc")
    parser.add_argument(
        "--max-steps", type=int, default=None,
        help="override the selected case's step limit (ad-hoc default: 120)",
    )
    parser.add_argument(
        "--timeout", type=int, default=None,
        help="override the selected case's timeout seconds (ad-hoc default: 900)",
    )
    parser.add_argument("--no-reset", action="store_true", help="preserve same-scenario session context")
    parser.add_argument("--fail-fast", action="store_true", help="stop a suite after its first failed case")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--list", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.list:
        print(json.dumps({"original": ORIGINAL_CASES, "mixed": MIXED_CASES}, ensure_ascii=False, indent=2))
        return 0
    if args.suite:
        result = run_suite(
            args.suite,
            output_root=args.output_root.resolve(),
            fail_fast=args.fail_fast,
            max_steps_override=args.max_steps,
            timeout_override=args.timeout,
        )
        return 0 if result.get("passed") else 1
    if args.case:
        name, spec = f"original-{args.case}", dict(ORIGINAL_CASES[args.case])
    elif args.mixed:
        name, spec = args.mixed, dict(MIXED_CASES[args.mixed])
    elif args.task:
        name = args.name
        spec = {
            "task": args.task,
            "max_steps": args.max_steps if args.max_steps is not None else 120,
            "timeout": args.timeout if args.timeout is not None else 900,
        }
    else:
        raise SystemExit("必须指定 --case、--mixed、--suite、--task 或 --list")
    if args.case or args.mixed:
        if args.max_steps is not None:
            spec["max_steps"] = args.max_steps
        if args.timeout is not None:
            spec["timeout"] = args.timeout
    result = run_case(
        name,
        spec,
        output_root=args.output_root.resolve(),
        reset_session=not args.no_reset,
    )
    return 0 if _case_passed(result) else 1


if __name__ == "__main__":
    sys.exit(main())
