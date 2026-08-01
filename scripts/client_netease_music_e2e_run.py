#!/usr/bin/env python3
"""Run one real NetEase Cloud Music task through the visible Electron client.

The harness never searches, clicks a result, starts playback, or changes
volume.  It only keeps the caller-supplied NetEase window in front during the
native Electron handoff.  Completion is decided by a separate read-only
oracle:

* the bound NetEase window exposes a stable current-track/player state;
* the visible player names Liang Jingru and exposes a pause action, which is
  the accessible state of a currently playing transport;
* the current track matches the first visible result when UI Automation
  exposes result rows; and
* Windows Core Audio independently reads an unmuted master level near 5%.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import sys
import threading
import time
from ctypes import POINTER, c_float, c_int, c_uint, c_void_p, cast
from ctypes.wintypes import BOOL, DWORD
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import comtypes
import psutil
import uiautomation as auto
import win32api
import win32con
import win32gui
import win32process
from comtypes import CLSCTX_ALL, COMMETHOD, GUID, HRESULT, IUnknown
from PIL import Image, ImageGrab


WORKSPACE = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import client_e2e_run as client_harness  # noqa: E402


TASK = "去网易云音乐搜索梁静茹并播放对应列表的第一首歌，保持音量百分之5"
TARGET_ARTIST = "梁静茹"
TARGET_VOLUME_PERCENT = 5.0
VOLUME_TOLERANCE_PERCENT = 0.75
DEFAULT_OUTPUT = (
    WORKSPACE / "output" / "playwright" / "client-netease-music-e2e"
)
ORACLE_SOURCE = "independent_netease_uia_and_coreaudio_readback"
_ROOT_ANCESTOR = win32con.GA_ROOT
_PLAYER_TOP_FRACTION = 0.72
_MAX_UIA_NODES = 1200


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(_json_safe(value), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _normal(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def _root_window(hwnd: int) -> int:
    return int(win32gui.GetAncestor(int(hwnd), _ROOT_ANCESTOR) or hwnd)


def _window_identity(hwnd: int) -> dict[str, Any]:
    if not hwnd or not win32gui.IsWindow(hwnd):
        raise RuntimeError("NetEase target window does not exist")
    root = _root_window(hwnd)
    _thread_id, process_id = win32process.GetWindowThreadProcessId(root)
    try:
        process = psutil.Process(int(process_id))
        executable = process.name()
        executable_path = process.exe()
    except psutil.Error:
        executable = ""
        executable_path = ""
    identity = {
        "hwnd": root,
        "process_id": int(process_id),
        "executable": executable,
        "executable_path": executable_path,
        "title": win32gui.GetWindowText(root),
        "window_rect": list(map(int, win32gui.GetWindowRect(root))),
    }
    if "cloudmusic" not in _normal(executable):
        raise RuntimeError(
            "supplied HWND is not owned by cloudmusic.exe: "
            f"{executable!r}"
        )
    return identity


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
            "could not focus supplied NetEase target: "
            + "; ".join(errors[-3:])
        )
    if tuple(map(int, win32gui.GetWindowRect(root))) != original_rect:
        raise RuntimeError("focusing NetEase changed its bound geometry")
    return {
        **identity,
        "foreground_root_hwnd": _root_window(
            int(win32gui.GetForegroundWindow() or 0)
        ),
        "focused": True,
        "attempt": attempt,
    }


def _start_target_handoff_watcher(
    *,
    electron_hwnd: int,
    target_hwnd: int,
    timeout_s: float = 12.0,
    poll_s: float = 0.01,
) -> dict[str, Any]:
    """Keep the known target visible while Electron yields the foreground."""

    stop = threading.Event()
    deadline = time.monotonic() + timeout_s
    evidence: dict[str, Any] = {
        "started": True,
        "electron_minimized_observed": False,
        "target_focused": False,
        "focus_attempts": 0,
        "errors": [],
    }

    def watch() -> None:
        while not stop.is_set() and time.monotonic() < deadline:
            try:
                if win32gui.IsIconic(int(electron_hwnd)):
                    evidence["electron_minimized_observed"] = True
                    foreground = _root_window(
                        int(win32gui.GetForegroundWindow() or 0)
                    )
                    if foreground != int(target_hwnd):
                        evidence["focus_attempts"] += 1
                        _focus_window(target_hwnd)
                    evidence["target_focused"] = (
                        _root_window(
                            int(win32gui.GetForegroundWindow() or 0)
                        )
                        == int(target_hwnd)
                    )
            except Exception as error:
                evidence["errors"].append(
                    f"{type(error).__name__}: {error}"
                )
            stop.wait(poll_s)
        evidence["finished"] = True

    thread = threading.Thread(
        target=watch,
        name="netease-electron-target-handoff",
        daemon=True,
    )
    thread.start()
    return {
        "stop": stop,
        "thread": thread,
        "evidence": evidence,
        "deadline": deadline,
    }


def _finish_target_handoff_watcher(
    watcher: dict[str, Any] | None,
) -> dict[str, Any]:
    if not watcher:
        return {"started": False}
    watcher["stop"].set()
    watcher["thread"].join(timeout=1.0)
    evidence = dict(watcher["evidence"])
    evidence["thread_alive"] = watcher["thread"].is_alive()
    return evidence


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
            == str(expected_goal).strip()
        ):
            return run_id, ""
    return "", ""


def _hold_target_until_admitted(
    driver: client_harness.ElectronClientDriver,
    client_result: dict[str, Any],
    *,
    target_hwnd: int,
    deadline: float,
) -> dict[str, Any]:
    before = client_result.get("submission_before") or {}
    before_job_ids = {
        str(value) for value in before.get("job_ids") or [] if str(value)
    }
    previous = (client_result.get("runtime_before") or {}).get("current_run")
    previous_run_id = str(
        previous.get("run_id") if isinstance(previous, dict) else ""
    )
    timeline: list[dict[str, Any]] = []
    admitted_run_id = ""
    admitted_job_id = ""
    refocus_count = 0
    first_sample = True
    while first_sample or time.monotonic() < deadline:
        first_sample = False
        foreground = _root_window(
            int(win32gui.GetForegroundWindow() or 0)
        )
        if foreground != int(target_hwnd):
            _focus_window(target_hwnd)
            refocus_count += 1
        status = driver.runtime_status()
        admitted_run_id, admitted_job_id = _new_admitted_run(
            status,
            previous_run_id=previous_run_id,
            before_job_ids=before_job_ids,
            expected_goal=TASK,
        )
        timeline.append(
            {
                "target_foreground": (
                    _root_window(
                        int(win32gui.GetForegroundWindow() or 0)
                    )
                    == int(target_hwnd)
                ),
                "running": status.get("running") is True,
                "current_run": status.get("current_run"),
                "admitted_run_id": admitted_run_id,
            }
        )
        if admitted_run_id:
            break
        time.sleep(0.1)
    if not admitted_run_id:
        raise RuntimeError(
            "Electron did not admit a new vNext run while the exact "
            "NetEase HWND remained foreground"
        )
    if (
        _root_window(int(win32gui.GetForegroundWindow() or 0))
        != int(target_hwnd)
    ):
        raise RuntimeError("NetEase target lost foreground during admission")
    return {
        "admitted_run_id": admitted_run_id,
        "admitted_job_id": admitted_job_id,
        "refocus_count": refocus_count,
        "timeline_sample_count": len(timeline),
        "timeline": timeline,
    }


# Minimal Core Audio interface declarations.  Setters are present only because
# COM vtable order is fixed; the oracle calls the two read methods below.
class IMMDevice(IUnknown):
    _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
    _methods_ = (
        COMMETHOD(
            [],
            HRESULT,
            "Activate",
            (["in"], POINTER(GUID), "iid"),
            (["in"], DWORD, "dwClsCtx"),
            (["in"], c_void_p, "pActivationParams"),
            (["out", "retval"], POINTER(POINTER(IUnknown)), "ppInterface"),
        ),
    )


class IMMDeviceEnumerator(IUnknown):
    _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
    _methods_ = (
        COMMETHOD(
            [],
            HRESULT,
            "EnumAudioEndpoints",
            (["in"], c_int, "dataFlow"),
            (["in"], DWORD, "stateMask"),
            (["out"], POINTER(POINTER(IUnknown)), "devices"),
        ),
        COMMETHOD(
            [],
            HRESULT,
            "GetDefaultAudioEndpoint",
            (["in"], c_int, "dataFlow"),
            (["in"], c_int, "role"),
            (["out", "retval"], POINTER(POINTER(IMMDevice)), "endpoint"),
        ),
    )


class IAudioEndpointVolume(IUnknown):
    _iid_ = GUID("{5CDF2C82-841E-4546-9722-0CF74078229A}")
    _methods_ = (
        COMMETHOD(
            [],
            HRESULT,
            "RegisterControlChangeNotify",
            (["in"], POINTER(IUnknown), "notify"),
        ),
        COMMETHOD(
            [],
            HRESULT,
            "UnregisterControlChangeNotify",
            (["in"], POINTER(IUnknown), "notify"),
        ),
        COMMETHOD(
            [],
            HRESULT,
            "GetChannelCount",
            (["out", "retval"], POINTER(c_uint), "count"),
        ),
        COMMETHOD(
            [],
            HRESULT,
            "SetMasterVolumeLevel",
            (["in"], c_float, "levelDb"),
            (["in"], POINTER(GUID), "eventContext"),
        ),
        COMMETHOD(
            [],
            HRESULT,
            "SetMasterVolumeLevelScalar",
            (["in"], c_float, "level"),
            (["in"], POINTER(GUID), "eventContext"),
        ),
        COMMETHOD(
            [],
            HRESULT,
            "GetMasterVolumeLevel",
            (["out", "retval"], POINTER(c_float), "levelDb"),
        ),
        COMMETHOD(
            [],
            HRESULT,
            "GetMasterVolumeLevelScalar",
            (["out", "retval"], POINTER(c_float), "level"),
        ),
        COMMETHOD(
            [],
            HRESULT,
            "SetChannelVolumeLevel",
            (["in"], c_uint, "channel"),
            (["in"], c_float, "levelDb"),
            (["in"], POINTER(GUID), "eventContext"),
        ),
        COMMETHOD(
            [],
            HRESULT,
            "SetChannelVolumeLevelScalar",
            (["in"], c_uint, "channel"),
            (["in"], c_float, "level"),
            (["in"], POINTER(GUID), "eventContext"),
        ),
        COMMETHOD(
            [],
            HRESULT,
            "GetChannelVolumeLevel",
            (["in"], c_uint, "channel"),
            (["out", "retval"], POINTER(c_float), "levelDb"),
        ),
        COMMETHOD(
            [],
            HRESULT,
            "GetChannelVolumeLevelScalar",
            (["in"], c_uint, "channel"),
            (["out", "retval"], POINTER(c_float), "level"),
        ),
        COMMETHOD(
            [],
            HRESULT,
            "SetMute",
            (["in"], BOOL, "mute"),
            (["in"], POINTER(GUID), "eventContext"),
        ),
        COMMETHOD(
            [],
            HRESULT,
            "GetMute",
            (["out", "retval"], POINTER(BOOL), "muted"),
        ),
    )


def _default_audio_endpoint() -> Any:
    enumerator = comtypes.CoCreateInstance(
        GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}"),
        interface=IMMDeviceEnumerator,
        clsctx=CLSCTX_ALL,
    )
    device = enumerator.GetDefaultAudioEndpoint(0, 1)
    interface = device.Activate(
        IAudioEndpointVolume._iid_,
        CLSCTX_ALL,
        None,
    )
    return cast(interface, POINTER(IAudioEndpointVolume))


def read_coreaudio_master_volume(
    endpoint_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Read, but never mutate, the default render endpoint volume."""

    initialize_com = endpoint_factory is None
    if initialize_com:
        comtypes.CoInitialize()
    try:
        endpoint = (
            endpoint_factory()
            if endpoint_factory is not None
            else _default_audio_endpoint()
        )
        scalar = float(endpoint.GetMasterVolumeLevelScalar())
        muted = bool(endpoint.GetMute())
        if not math.isfinite(scalar) or not 0.0 <= scalar <= 1.0:
            raise RuntimeError(f"invalid Core Audio scalar: {scalar!r}")
        return {
            "source": "windows_coreaudio_iaudioendpointvolume_readback",
            "level_scalar": round(scalar, 6),
            "level_percent": round(scalar * 100.0, 3),
            "muted": muted,
        }
    finally:
        if initialize_com:
            comtypes.CoUninitialize()


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


def _text_pattern_lines(control: Any) -> list[dict[str, Any]]:
    try:
        pattern = control.GetPattern(auto.PatternId.TextPattern)
        if pattern is None:
            return []
        document = pattern.DocumentRange
    except Exception:
        return []
    values: list[dict[str, Any]] = []
    for role, backward in (
        ("first_artist_line", False),
        ("last_artist_line", True),
    ):
        try:
            found = document.FindText(TARGET_ARTIST, backward, True)
            if found is None:
                continue
            found.ExpandToEnclosingUnit(auto.TextUnit.Line)
            rects = found.GetBoundingRectangles()
            if not rects:
                continue
            left = min(int(item.left) for item in rects)
            top = min(int(item.top) for item in rects)
            right = max(int(item.right) for item in rects)
            bottom = max(int(item.bottom) for item in rects)
            values.append(
                {
                    "name": str(found.GetText(800) or "").strip(),
                    "control_type": "TextRangeLine",
                    "automation_id": "",
                    "rect": [left, top, right, bottom],
                    "path": f"text-pattern:{role}",
                    "parent_path": "",
                    "depth": 0,
                    "semantic_role": role,
                }
            )
        except Exception:
            continue
    return values


def snapshot_accessible_state(hwnd: int) -> list[dict[str, Any]]:
    root = auto.ControlFromHandle(int(hwnd))
    if root is None:
        raise RuntimeError("UI Automation could not bind the NetEase HWND")
    queue: list[tuple[Any, str, str, int]] = [(root, "0", "", 0)]
    nodes: list[dict[str, Any]] = []
    visited = 0
    while queue and visited < _MAX_UIA_NODES:
        control, path, parent_path, depth = queue.pop(0)
        visited += 1
        try:
            name = str(control.Name or "").strip()
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
        node = {
            "name": name,
            "control_type": control_type,
            "automation_id": automation_id,
            "rect": _rect_list(control),
            "path": path,
            "parent_path": parent_path,
            "depth": depth,
        }
        if name or automation_id:
            nodes.append(node)
        if control_type in {"DocumentControl", "PaneControl"}:
            nodes.extend(_text_pattern_lines(control))
        if depth >= 16:
            continue
        try:
            children = control.GetChildren()
        except Exception:
            children = []
        for index, child in enumerate(children):
            queue.append((child, f"{path}.{index}", path, depth + 1))
    return nodes


def _in_player_region(
    node: dict[str, Any],
    window_rect: tuple[int, int, int, int],
) -> bool:
    rect = [int(value) for value in node.get("rect") or [0, 0, 0, 0]]
    if len(rect) != 4 or rect[3] <= rect[1]:
        return False
    top = window_rect[1] + (
        window_rect[3] - window_rect[1]
    ) * _PLAYER_TOP_FRACTION
    return (rect[1] + rect[3]) / 2 >= top


def _is_pause_signal(value: str) -> bool:
    normalized = _normal(value)
    ambiguous = (
        "播放/暂停",
        "播放或暂停",
        "play/pause",
        "playorpause",
    )
    return bool(
        normalized
        and not any(_normal(item) in normalized for item in ambiguous)
        and ("暂停" in normalized or "pause" in normalized)
    )


def _title_candidates(values: list[str], artist: str) -> list[str]:
    artist_normal = _normal(artist)
    generic = {
        "网易云音乐",
        "云音乐",
        "暂停",
        "pause",
        "上一首",
        "下一首",
        "喜欢",
        "收藏",
        "歌词",
        "音量",
        "播放列表",
    }
    candidates: list[str] = []
    for raw in values:
        value = re.sub(r"\s+", " ", str(raw or "")).strip()
        normalized = _normal(value)
        if not value or normalized in {_normal(item) for item in generic}:
            continue
        if artist_normal in normalized:
            pieces = [
                item.strip()
                for item in re.split(r"\s*[-—–·|]\s*", value)
                if item.strip()
            ]
            candidates.extend(
                item for item in pieces if artist_normal not in _normal(item)
            )
            continue
        if re.fullmatch(r"[\d:./]+", value):
            continue
        candidates.append(value)
    unique: list[str] = []
    for value in candidates:
        if _normal(value) and _normal(value) not in {
            _normal(item) for item in unique
        }:
            unique.append(value)
    return unique[:12]


def _first_result_titles(
    nodes: list[dict[str, Any]],
    window_rect: tuple[int, int, int, int],
    artist: str,
) -> list[str]:
    row_types = {"DataItemControl", "ListItemControl"}
    rows = [
        node
        for node in nodes
        if node.get("control_type") in row_types
        and not _in_player_region(node, window_rect)
    ]
    matching: list[tuple[int, list[str]]] = []
    artist_normal = _normal(artist)
    for row in rows:
        path = str(row.get("path") or "")
        members = [
            item
            for item in nodes
            if str(item.get("path") or "") == path
            or str(item.get("path") or "").startswith(path + ".")
        ]
        names = [str(item.get("name") or "") for item in members]
        if not any(artist_normal in _normal(value) for value in names):
            continue
        rect = row.get("rect") or [0, 0, 0, 0]
        matching.append((int(rect[1]), _title_candidates(names, artist)))
    if matching:
        return min(matching, key=lambda item: item[0])[1]
    # TextPattern exposes the first and last occurrence even when the custom
    # list does not publish row controls.
    lines = [
        str(node.get("name") or "")
        for node in nodes
        if node.get("semantic_role") == "first_artist_line"
        and not _in_player_region(node, window_rect)
    ]
    return _title_candidates(lines, artist)


def _titles_match(current: list[str], first: list[str]) -> bool:
    for current_value in current:
        left = _normal(current_value)
        if len(left) < 2:
            continue
        for first_value in first:
            right = _normal(first_value)
            if len(right) >= 2 and (
                left == right or left in right or right in left
            ):
                return True
    return False


def evaluate_music_state(
    nodes: list[dict[str, Any]],
    volume: dict[str, Any],
    window_rect: tuple[int, int, int, int],
    *,
    artist: str = TARGET_ARTIST,
    target_volume: float = TARGET_VOLUME_PERCENT,
    volume_tolerance: float = VOLUME_TOLERANCE_PERCENT,
) -> dict[str, Any]:
    player = [
        node for node in nodes if _in_player_region(node, window_rect)
    ]
    artist_nodes = [
        node
        for node in player
        if _normal(artist) in _normal(node.get("name"))
    ]
    pause_nodes = [
        node
        for node in player
        if _is_pause_signal(
            f"{node.get('name', '')} {node.get('automation_id', '')}"
        )
    ]
    player_values = [
        str(node.get("name") or "")
        for node in player
        if node.get("control_type")
        in {
            "TextControl",
            "HyperlinkControl",
            "ListItemControl",
            "DataItemControl",
            "TextRangeLine",
        }
    ]
    current_titles = _title_candidates(player_values, artist)
    first_titles = _first_result_titles(nodes, window_rect, artist)
    first_match = _titles_match(current_titles, first_titles)
    level = float(volume.get("level_percent", -1000.0))
    muted = bool(volume.get("muted", True))
    volume_matches = (
        not muted and abs(level - float(target_volume)) <= volume_tolerance
    )
    errors: list[str] = []
    if not artist_nodes:
        errors.append("player region does not identify Liang Jingru")
    if not pause_nodes:
        errors.append("player transport does not expose a playing/pause state")
    if not current_titles:
        errors.append("current track title is not independently observable")
    if not first_titles:
        errors.append("first visible search-result title is not observable")
    elif not first_match:
        errors.append("current track does not match the first visible result")
    if not volume_matches:
        errors.append(
            "Core Audio master volume is not unmuted at approximately 5%"
        )
    candidate = not errors
    return {
        "source": ORACLE_SOURCE,
        "candidate_passed": candidate,
        "passed": False,
        "status": "candidate" if candidate else "observing",
        "errors": errors,
        "artist": artist,
        "artist_player_nodes": artist_nodes[:12],
        "playing_pause_nodes": pause_nodes[:12],
        "current_track_candidates": current_titles,
        "first_result_track_candidates": first_titles,
        "first_result_match": first_match,
        "coreaudio": volume,
        "target_volume_percent": target_volume,
        "volume_tolerance_percent": volume_tolerance,
        "volume_matches": volume_matches,
    }


class NetEaseWindowOracle:
    def __init__(
        self,
        hwnd: int,
        output_dir: Path,
        *,
        stable_frames: int = 2,
        sample_interval_s: float = 0.3,
        capture: Callable[..., Image.Image] = ImageGrab.grab,
        uia_snapshot: Callable[[int], list[dict[str, Any]]] = (
            snapshot_accessible_state
        ),
        volume_reader: Callable[[], dict[str, Any]] = (
            read_coreaudio_master_volume
        ),
    ) -> None:
        if type(stable_frames) is not int or not 1 <= stable_frames <= 8:
            raise ValueError("stable_frames must be an integer in 1..8")
        self.identity = _window_identity(hwnd)
        self.hwnd = int(self.identity["hwnd"])
        self.window_rect = tuple(self.identity["window_rect"])
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.stable_frames = stable_frames
        self.sample_interval_s = max(0.05, float(sample_interval_s))
        self._capture_fn = capture
        self._uia_snapshot = uia_snapshot
        self._volume_reader = volume_reader
        self._stable_count = 0
        self._last_signature = ""
        self._last_verdict: dict[str, Any] = {
            "source": ORACLE_SOURCE,
            "passed": False,
            "status": "not_started",
        }
        self._last_probe_error = ""

    def _validate_target(self) -> None:
        current = _window_identity(self.hwnd)
        if (
            int(current["process_id"]) != int(self.identity["process_id"])
            or tuple(current["window_rect"]) != self.window_rect
        ):
            raise RuntimeError("bound NetEase identity or geometry changed")
        foreground = _root_window(
            int(win32gui.GetForegroundWindow() or 0)
        )
        if foreground != self.hwnd:
            raise RuntimeError("bound NetEase window is not the foreground")

    def _sample(self, prefix: str) -> tuple[dict[str, Any], Image.Image]:
        self._validate_target()
        image = self._capture_fn(
            bbox=self.window_rect,
            all_screens=True,
        ).convert("RGB")
        nodes = self._uia_snapshot(self.hwnd)
        volume = self._volume_reader()
        verdict = evaluate_music_state(
            nodes,
            volume,
            self.window_rect,
        )
        verdict.update(
            {
                "target": self.identity,
                "captured_at": datetime.now().astimezone().isoformat(),
                "frame_sha256": hashlib.sha256(image.tobytes()).hexdigest(),
                "accessible_node_count": len(nodes),
            }
        )
        image.save(self.output_dir / f"{prefix}.png")
        _write_json(self.output_dir / f"{prefix}-uia.json", nodes)
        _write_json(self.output_dir / f"{prefix}-coreaudio.json", volume)
        return verdict, image

    def capture_baseline(self) -> dict[str, Any]:
        verdict, _image = self._sample("baseline")
        if verdict.get("candidate_passed") is True:
            raise RuntimeError(
                "invalid test precondition: music task is already satisfied"
            )
        verdict["status"] = "baseline_ready"
        verdict["passed"] = False
        self._last_verdict = verdict
        self._last_probe_error = ""
        _write_json(self.output_dir / "oracle.json", verdict)
        return verdict

    def _observe_once(self, run_id: str) -> dict[str, Any]:
        verdict, _image = self._sample("latest")
        signature = json.dumps(
            {
                "track": verdict.get("current_track_candidates"),
                "first": verdict.get("first_result_track_candidates"),
                "volume": (verdict.get("coreaudio") or {}).get(
                    "level_percent"
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        if verdict.get("candidate_passed") is True:
            self._stable_count = (
                self._stable_count + 1
                if signature == self._last_signature
                else 1
            )
        else:
            self._stable_count = 0
        self._last_signature = signature
        passed = self._stable_count >= self.stable_frames
        verdict.update(
            {
                "run_id": run_id,
                "stable_frame_count": self._stable_count,
                "required_stable_frames": self.stable_frames,
                "passed": passed,
                "status": "passed" if passed else "observing",
            }
        )
        self._last_verdict = verdict
        _write_json(self.output_dir / "oracle.json", verdict)
        return verdict

    def probe(self, context: dict[str, Any]) -> dict[str, Any]:
        run_id = str(context.get("run_id") or "")
        try:
            verdict = self._observe_once(run_id)
            while (
                verdict.get("candidate_passed") is True
                and verdict.get("passed") is not True
            ):
                time.sleep(self.sample_interval_s)
                verdict = self._observe_once(run_id)
            return dict(verdict)
        except Exception as error:
            self._last_probe_error = f"{type(error).__name__}: {error}"
            verdict = {
                "source": ORACLE_SOURCE,
                "passed": False,
                "status": "capture_error",
                "run_id": run_id,
                "error": self._last_probe_error,
                "target": self.identity,
            }
            return verdict

    def final_verdict(self) -> dict[str, Any]:
        verdict = dict(self._last_verdict)
        verdict.setdefault("source", ORACLE_SOURCE)
        if self._last_probe_error:
            verdict["last_probe_error"] = self._last_probe_error
            if verdict.get("status") in {"not_started", "baseline_ready"}:
                verdict["status"] = "capture_error"
        verdict["passed"] = verdict.get("passed") is True
        return verdict


def _copy_bound_traces(
    result: dict[str, Any],
    destination: Path,
) -> list[str]:
    copied: list[str] = []
    for trace_id in result.get("bound_trace_ids") or []:
        source = client_harness.TRACE_ROOT / f"{trace_id}.jsonl"
        if not source.is_file():
            continue
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / source.name
        shutil.copy2(source, target)
        copied.append(str(target))
    return copied


def client_case_spec(*, timeout: int, max_steps: int) -> dict[str, Any]:
    """Return only values entered through the visible primary Agent UI."""

    return {
        "task": TASK,
        "max_steps": int(max_steps),
        "timeout": int(timeout),
        "runtime_version": "vnext",
        "expected": "independent_success",
    }


def run_netease_music_case(
    *,
    netease_hwnd: int,
    output_root: Path,
    timeout: int = 600,
    max_steps: int = 40,
    stable_frames: int = 2,
    sample_interval_s: float = 0.3,
) -> dict[str, Any]:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    case_dir = output_root / f"{timestamp}-netease-music"
    case_dir.mkdir(parents=True, exist_ok=True)
    oracle = NetEaseWindowOracle(
        netease_hwnd,
        case_dir / "oracle",
        stable_frames=stable_frames,
        sample_interval_s=sample_interval_s,
    )
    initial_focus = _focus_window(netease_hwnd)
    baseline = oracle.capture_baseline()
    handoff_watcher: dict[str, Any] | None = None

    def prepare_handoff(
        driver: client_harness.ElectronClientDriver,
        _client_result: dict[str, Any],
    ) -> dict[str, Any]:
        nonlocal handoff_watcher
        handoff_watcher = _start_target_handoff_watcher(
            electron_hwnd=int(driver.identity.hwnd),
            target_hwnd=int(oracle.hwnd),
        )
        return {
            "watcher_started": True,
            "target": oracle.identity,
        }

    def handoff(
        driver: client_harness.ElectronClientDriver,
        client_result: dict[str, Any],
    ) -> dict[str, Any]:
        watcher_evidence = _finish_target_handoff_watcher(handoff_watcher)
        focus = _focus_window(oracle.hwnd)
        admission = _hold_target_until_admitted(
            driver,
            client_result,
            target_hwnd=oracle.hwnd,
            deadline=float(
                (handoff_watcher or {}).get(
                    "deadline",
                    time.monotonic() + 12.0,
                )
            ),
        )
        return {
            **focus,
            **admission,
            "submit_watcher": watcher_evidence,
        }

    try:
        result = client_harness.run_case(
            "netease-liang-jingru-first-song-volume-5",
            client_case_spec(timeout=timeout, max_steps=max_steps),
            output_root=case_dir / "client",
            reset_session=True,
            before_submit=prepare_handoff,
            after_submit=handoff,
            independent_completion_probe=oracle.probe,
            independent_verifier=lambda _result: oracle.final_verdict(),
        )
    finally:
        _finish_target_handoff_watcher(handoff_watcher)
    copied_traces = _copy_bound_traces(result, case_dir / "traces")
    report = {
        "kind": "native_netease_music_e2e",
        "entrypoint": "electron_client_windows_uia",
        "task": TASK,
        "runtime_version": "vnext",
        "initial_focus": initial_focus,
        "baseline": baseline,
        "oracle": oracle.final_verdict(),
        "client_result": result,
        "copied_traces": copied_traces,
        "passed": result.get("passed") is True,
        "finished_at": datetime.now().astimezone().isoformat(),
    }
    report_path = case_dir / "netease-music-report.json"
    _write_json(report_path, report)
    report["report"] = str(report_path)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--netease-hwnd", type=int, required=True)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--max-steps", type=int, default=40)
    parser.add_argument("--stable-frames", type=int, default=2)
    parser.add_argument("--sample-interval", type=float, default=0.3)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = run_netease_music_case(
        netease_hwnd=args.netease_hwnd,
        output_root=args.output_root,
        timeout=args.timeout,
        max_steps=args.max_steps,
        stable_frames=args.stable_frames,
        sample_interval_s=args.sample_interval,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
