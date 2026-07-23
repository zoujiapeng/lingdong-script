"""Exact private Windows fixture for visible-client target-acquisition E2E."""
from __future__ import annotations

import ctypes
import hashlib
import json
import ntpath
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import winreg
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psutil
import win32con
import win32gui
import win32process


APP_PATHS_ROOT = r"Software\Microsoft\Windows\CurrentVersion\App Paths"


class FixtureUnavailable(RuntimeError):
    """The independent fixture could not be established or observed."""


@dataclass(slots=True)
class _ShellForegroundGuard:
    """Keep the shell foreground while the Electron handoff is captured."""

    control_surface_hwnd: int
    shell_hwnd: int
    control_surface_pid: int = 0
    stop_event: threading.Event = field(default_factory=threading.Event)
    armed_event: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    shell_observation_count: int = 0
    failure: str = ""

    def start(self) -> None:
        if self.thread is not None:
            raise FixtureUnavailable("targetless shell guard already started")
        self.thread = threading.Thread(
            target=self._run,
            name="winpilot-targetless-shell-guard",
            daemon=True,
        )
        self.thread.start()
        if not self.armed_event.wait(2.0):
            self.stop_event.set()
            self.thread.join(2.0)
            raise FixtureUnavailable("targetless shell guard did not arm")
        if self.failure:
            raise FixtureUnavailable(self.failure)

    def _run(self) -> None:
        try:
            self.armed_event.set()
            while (
                not self.stop_event.is_set()
                and not win32gui.IsIconic(self.control_surface_hwnd)
            ):
                time.sleep(0.01)
            if self.stop_event.is_set():
                return
            while not self.stop_event.is_set():
                win32gui.SetForegroundWindow(self.shell_hwnd)
                time.sleep(0.02)
                if int(ctypes.windll.user32.GetForegroundWindow() or 0) == self.shell_hwnd:
                    self.shell_observation_count += 1
        except Exception as exc:
            self.failure = f"shell guard failed: {type(exc).__name__}"

    def stop(self) -> dict[str, Any]:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(2.0)
            if self.thread.is_alive():
                raise FixtureUnavailable("targetless shell guard did not stop")
        if self.failure:
            raise FixtureUnavailable(self.failure)
        if self.shell_observation_count <= 0:
            raise FixtureUnavailable("targetless shell guard observed no shell foreground")
        foreground = int(ctypes.windll.user32.GetForegroundWindow() or 0)
        if foreground != self.shell_hwnd:
            raise FixtureUnavailable("shell lost foreground before handoff completed")
        return {
            "shell_hwnd": self.shell_hwnd,
            "foreground_hwnd": foreground,
            "shell_observation_count": self.shell_observation_count,
        }


def same_path(left: object, right: object) -> bool:
    def normalized(value: object) -> str:
        return os.path.normcase(os.path.abspath(str(value))).casefold()
    return normalized(left) == normalized(right)


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def process_identity_for(
    *, process_id: int, executable_path: str, creation_time_ns: int,
) -> str:
    material = {
        "creation_time_ns": creation_time_ns,
        "executable_path": ntpath.normcase(
            ntpath.normpath(executable_path),
        ).casefold(),
        "process_id": process_id,
    }
    encoded = json.dumps(
        material, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return "process-" + hashlib.sha256(encoded).hexdigest()[:32]


def _window_region(hwnd: int) -> list[int]:
    rect = wintypes.RECT()
    result = ctypes.windll.dwmapi.DwmGetWindowAttribute(
        hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect),
    )
    if result and not ctypes.windll.user32.GetWindowRect(
        hwnd, ctypes.byref(rect),
    ):
        raise OSError("window region is unavailable")
    return [rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top]


def _csc_path() -> Path:
    windows = Path(os.environ.get("WINDIR", r"C:\Windows"))
    candidates = (
        shutil.which("csc.exe"),
        windows / r"Microsoft.NET\Framework64\v4.0.30319\csc.exe",
        windows / r"Microsoft.NET\Framework\v4.0.30319\csc.exe",
    )
    found = next(
        (Path(item) for item in candidates if item and Path(item).is_file()),
        None,
    )
    if found is None:
        raise FixtureUnavailable("csc.exe is unavailable")
    return found


def exact_instances(executable: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for process in psutil.process_iter(("pid", "exe", "create_time")):
        try:
            if not same_path(process.info.get("exe"), executable):
                continue
            pid = int(process.info["pid"])
            path = str(Path(process.info["exe"]).resolve())
            created = int(float(process.info["create_time"]) * 1_000_000_000)
            windows: list[dict[str, Any]] = []

            def collect(hwnd: int, _extra: object) -> bool:
                _thread, owner = win32process.GetWindowThreadProcessId(hwnd)
                if int(owner or 0) == pid and win32gui.IsWindowVisible(hwnd):
                    windows.append({
                        "hwnd": int(hwnd),
                        "root_hwnd": int(
                            win32gui.GetAncestor(hwnd, win32con.GA_ROOT) or 0
                        ),
                        "region": _window_region(hwnd),
                        "is_minimized": bool(win32gui.IsIconic(hwnd)),
                    })
                return True

            win32gui.EnumWindows(collect, None)
            rows.append({
                "exe_path": path, "exe_sha256": file_sha256(Path(path)),
                "process_id": pid, "create_time_ns": created,
                "process_identity": process_identity_for(
                    process_id=pid, executable_path=path,
                    creation_time_ns=created,
                ),
                "windows": sorted(windows, key=lambda item: item["hwnd"]),
            })
        except (psutil.Error, OSError, TypeError, ValueError):
            continue
    return sorted(
        rows, key=lambda item: (item["create_time_ns"], item["process_id"]),
    )


@dataclass(slots=True)
class AcquisitionFixture:
    root: Path
    executable: Path
    launch_log: Path
    public_name: str
    nonce: str
    registry_key: str
    registry_created: bool = False
    _handoff_guard: _ShellForegroundGuard | None = field(
        default=None, init=False, repr=False,
    )

    @classmethod
    def create(cls, seed: int) -> "AcquisitionFixture":
        nonce = f"{int(seed) & 0xFFFFFFFF:08x}-{uuid.uuid4().hex[:10]}"
        name = f"WinPilotAcquisition-{nonce}"
        root = Path(tempfile.mkdtemp(prefix="winpilot-target-acquisition-"))
        fixture = cls(
            root, root / f"{name}.exe", root / "launch.log",
            name, nonce, f"{APP_PATHS_ROOT}\\{name}.exe",
        )
        try:
            fixture._compile()
            fixture._register()
            return fixture
        except Exception:
            fixture.cleanup()
            raise

    def _compile(self) -> None:
        source = self.root / "Fixture.cs"
        literal = lambda value: json.dumps(str(value), ensure_ascii=False)
        source.write_text(
            "using System;using System.Diagnostics;using System.IO;"
            "using System.Security.Cryptography;using System.Windows.Forms;"
            "static class P{[STAThread]static void Main(){"
            "var p=Process.GetCurrentProcess();var e=p.MainModule.FileName;"
            "string h;using(var s=SHA256.Create())using(var f=File.OpenRead(e)){"
            "h=BitConverter.ToString(s.ComputeHash(f)).Replace(\"-\",\"\").ToLower();}"
            f"File.AppendAllText({literal(self.launch_log)},"
            f"{literal(self.nonce + '|')}+p.Id+'|'+DateTime.UtcNow.Ticks+'|'+"
            "e+'|'+h+Environment.NewLine);Application.EnableVisualStyles();"
            f"Application.Run(new Form{{Text={literal(self.public_name)},"
            "Width=720,Height=480,"
            "StartPosition=FormStartPosition.CenterScreen});}}",
            encoding="utf-8",
        )
        completed = subprocess.run(
            [
                str(_csc_path()), "/nologo", "/target:winexe",
                f"/out:{self.executable}", "/reference:System.Windows.Forms.dll",
                str(source),
            ],
            cwd=self.root, capture_output=True, text=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode or not self.executable.is_file():
            detail = (completed.stderr or completed.stdout)[-500:]
            raise FixtureUnavailable(f"fixture compilation failed: {detail}")

    def _register(self) -> None:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.registry_key):
                raise FixtureUnavailable("random App Paths key already exists")
        except FileNotFoundError:
            pass
        key = winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, self.registry_key, 0, winreg.KEY_WRITE,
        )
        # From this point cleanup owns the exact random key even if writing its
        # default value raises.  Setting this after SetValueEx leaked partial
        # keys on failed fixture setup.
        self.registry_created = True
        with key:
            winreg.SetValueEx(key, None, 0, winreg.REG_SZ, str(self.executable))

    def launch_records(self) -> list[dict[str, int | str]]:
        if not self.launch_log.is_file():
            return []
        records = []
        for line in self.launch_log.read_text(encoding="utf-8").splitlines():
            parts = line.split("|")
            if len(parts) != 5 or parts[0] != self.nonce:
                raise FixtureUnavailable("fixture launch log identity is invalid")
            records.append({
                "nonce": parts[0], "process_id": int(parts[1]),
                "utc_ticks": int(parts[2]), "exe_path": parts[3],
                "exe_sha256": parts[4],
            })
        return records

    def instances(self) -> list[dict[str, Any]]:
        return exact_instances(self.executable)

    def wait_instances(
        self, count: int, timeout: float = 12.0,
    ) -> list[dict[str, Any]]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rows = self.instances()
            if len(rows) == count and all(len(row["windows"]) == 1 for row in rows):
                return rows
            time.sleep(0.1)
        raise FixtureUnavailable(f"fixture instance count did not become {count}")

    def prepare(self, mode: str) -> dict[str, Any]:
        if mode not in {"existing", "cold"}:
            raise ValueError("mode must be existing or cold")
        if self.instances() or self.launch_records():
            raise FixtureUnavailable("fixture did not start from zero instances")
        process = (
            subprocess.Popen(
                [str(self.executable)], cwd=self.root,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if mode == "existing" else None
        )
        rows = self.wait_instances(1) if process is not None else []
        records = self.launch_records()
        if (
            len(records) != len(rows)
            or process is not None and rows[0]["process_id"] != process.pid
            or rows and not self.records_match(records, rows)
        ):
            raise FixtureUnavailable("fixture baseline identity is incomplete")
        return {"instances": rows, "launch_records": records}

    def stage_targetless_handoff(
        self,
        expected_rows: list[dict[str, Any]],
        expected_launch_records: list[dict[str, Any]],
        *,
        control_surface_hwnd: int,
        control_surface_pid: int,
    ) -> dict[str, Any]:
        """Keep shell foreground without hiding the discoverable fixture."""

        current = self.instances()
        current_records = self.launch_records()
        if (
            current != expected_rows
            or current_records != expected_launch_records
            or not self.records_match(current_records, current)
        ):
            raise FixtureUnavailable(
                "fixture identity changed before targetless handoff",
            )
        windows = self._verified_expected_windows(expected_rows)
        if any(
            not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd)
            for hwnd in windows
        ):
            raise FixtureUnavailable("fixture window is not visibly discoverable")
        if self._handoff_guard is not None:
            raise FixtureUnavailable("targetless shell guard is already active")
        control_surface_hwnd = int(control_surface_hwnd or 0)
        control_surface_pid = int(control_surface_pid or 0)
        self._verify_control_surface(
            control_surface_hwnd, control_surface_pid,
        )
        shell_hwnd = int(ctypes.windll.user32.GetShellWindow() or 0)
        if shell_hwnd <= 0 or not win32gui.IsWindow(shell_hwnd):
            raise FixtureUnavailable("GetShellWindow returned no exact HWND")
        guard = _ShellForegroundGuard(
            control_surface_hwnd, shell_hwnd, control_surface_pid,
        )
        self._handoff_guard = guard
        try:
            guard.start()
        except Exception:
            if guard.thread is None or not guard.thread.is_alive():
                self._handoff_guard = None
            raise
        return {
            "shell_hwnd": shell_hwnd,
            "guarded_fixture_hwnds": windows,
            "control_surface_hwnd": control_surface_hwnd,
            "control_surface_pid": control_surface_pid,
            "guard_armed": guard.armed_event.is_set(),
            "guard_started": True,
        }

    def finish_targetless_handoff(
        self,
        expected_rows: list[dict[str, Any]],
        expected_launch_records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Stop the shell guard and prove fixtures stayed discoverable."""

        guard = self._handoff_guard
        if guard is None:
            raise FixtureUnavailable("targetless shell guard is not active")
        self._verify_control_surface(
            guard.control_surface_hwnd,
            guard.control_surface_pid,
        )
        current = self.instances()
        records = self.launch_records()
        if (
            current != expected_rows
            or records != expected_launch_records
            or not self.records_match(records, current)
        ):
            raise FixtureUnavailable("fixture changed before handoff release")
        windows = self._verified_expected_windows(expected_rows)
        if any(
            not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd)
            for hwnd in windows
        ):
            raise FixtureUnavailable("fixture stopped being visibly discoverable")
        stopped = guard.stop()
        self._handoff_guard = None
        if self.instances() != expected_rows or self.launch_records() != expected_launch_records:
            raise FixtureUnavailable("fixture changed while releasing handoff")
        return {
            **stopped,
            "verified_fixture_hwnds": windows,
            "focus_strategy": "shell_foreground_guard",
        }

    def abort_targetless_handoff(self) -> None:
        """Stop a guard after a failed barrier without claiming handoff proof."""

        guard = self._handoff_guard
        if guard is None:
            return
        guard.stop_event.set()
        thread = guard.thread
        if thread is not None:
            thread.join(2.0)
            if thread.is_alive():
                raise FixtureUnavailable("targetless shell guard did not stop")
        self._handoff_guard = None

    @staticmethod
    def _verify_control_surface(hwnd: int, process_id: int) -> None:
        try:
            hwnd = int(hwnd)
            process_id = int(process_id)
            _thread, owner = win32process.GetWindowThreadProcessId(hwnd)
            root = int(win32gui.GetAncestor(hwnd, win32con.GA_ROOT) or 0)
        except (TypeError, ValueError, OSError) as exc:
            raise FixtureUnavailable("control surface identity is unavailable") from exc
        if (
            hwnd <= 0
            or process_id <= 0
            or not win32gui.IsWindow(hwnd)
            or int(owner or 0) != process_id
            or root != hwnd
        ):
            raise FixtureUnavailable("control surface identity changed")

    def _verified_expected_windows(
        self, expected_rows: list[dict[str, Any]],
    ) -> list[int]:
        windows: list[int] = []
        for row in expected_rows:
            try:
                process = psutil.Process(int(row["process_id"]))
            except (KeyError, TypeError, ValueError, psutil.Error) as exc:
                raise FixtureUnavailable("fixture process is unavailable") from exc
            if not self._same_process(process, row):
                raise FixtureUnavailable("fixture process identity changed")
            for window in row.get("windows") or []:
                try:
                    hwnd = int(window["hwnd"])
                    expected_root = int(window["root_hwnd"])
                    _thread, owner = win32process.GetWindowThreadProcessId(hwnd)
                    root = int(win32gui.GetAncestor(hwnd, win32con.GA_ROOT) or 0)
                except (KeyError, TypeError, ValueError, OSError) as exc:
                    raise FixtureUnavailable("fixture window is unavailable") from exc
                if (
                    not win32gui.IsWindow(hwnd)
                    or int(owner or 0) != process.pid
                    or root != expected_root
                    or not self._same_process(process, row)
                ):
                    raise FixtureUnavailable("fixture window identity changed")
                windows.append(hwnd)
        return windows

    @staticmethod
    def records_match(records: list[dict], rows: list[dict]) -> bool:
        try:
            by_pid = {int(row["process_id"]): row for row in rows}
            return len(records) == len(by_pid) and all(
                int(record["utc_ticks"]) > 0
                and int(record["process_id"]) in by_pid
                and same_path(
                    record["exe_path"],
                    by_pid[int(record["process_id"])]["exe_path"],
                )
                and record["exe_sha256"]
                == by_pid[int(record["process_id"])]["exe_sha256"]
                for record in records
            )
        except (KeyError, TypeError, ValueError):
            return False

    def _safe_scope(self) -> bool:
        try:
            root, temp_root = self.root.resolve(), Path(tempfile.gettempdir()).resolve()
            return bool(
                root.parent == temp_root
                and root.name.startswith("winpilot-target-acquisition-")
                and self.executable.resolve().parent == root
                and self.executable.name == f"{self.public_name}.exe"
                and self.launch_log.resolve().parent == root
                and self.registry_key
                == f"{APP_PATHS_ROOT}\\{self.public_name}.exe"
            )
        except OSError:
            return False

    @staticmethod
    def _same_process(process: psutil.Process, row: dict) -> bool:
        try:
            path = process.exe()
            created = int(float(process.create_time()) * 1_000_000_000)
            return bool(
                process.pid == row["process_id"]
                and same_path(path, row["exe_path"])
                and created == row["create_time_ns"]
                and process_identity_for(
                    process_id=process.pid, executable_path=path,
                    creation_time_ns=created,
                ) == row["process_identity"]
            )
        except (psutil.Error, OSError, KeyError, TypeError, ValueError):
            return False

    def _close_exact(self, row: dict) -> list[str]:
        errors: list[str] = []
        try:
            process = psutil.Process(row["process_id"])
        except psutil.Error:
            return errors
        if not self._same_process(process, row):
            return ["process:identity_changed"]
        for window in row["windows"]:
            try:
                _thread, owner = win32process.GetWindowThreadProcessId(window["hwnd"])
                root = win32gui.GetAncestor(window["hwnd"], win32con.GA_ROOT)
                exact = (
                    win32gui.IsWindow(window["hwnd"])
                    and int(owner or 0) == row["process_id"]
                    and int(root or 0) == window["root_hwnd"]
                    and self._same_process(process, row)
                )
                if not exact:
                    raise RuntimeError("window identity changed")
                win32gui.PostMessage(window["hwnd"], win32con.WM_CLOSE, 0, 0)
            except Exception as exc:
                errors.append(f"window:{type(exc).__name__}")
        for label, mutate in (
            ("terminate", process.terminate), ("kill", process.kill),
        ):
            try:
                process.wait(2)
                return errors
            except psutil.TimeoutExpired:
                if not self._same_process(process, row):
                    errors.append(f"process:identity_changed_before_{label}")
                    return errors
                mutate()
            except psutil.Error:
                return errors
        try:
            process.wait(2)
        except psutil.Error as exc:
            errors.append(f"process:{type(exc).__name__}")
        return errors

    def cleanup(self) -> dict[str, Any]:
        guard_error = []
        if self._handoff_guard is not None:
            try:
                self.abort_targetless_handoff()
            except FixtureUnavailable:
                guard_error.append("handoff_guard:still_running")
        if guard_error:
            return {"passed": False, "errors": guard_error}
        if not self._safe_scope():
            return {"passed": False, "errors": ["fixture scope is unsafe"]}
        errors = [
            error for row in exact_instances(self.executable)
            for error in self._close_exact(row)
        ]
        errors = [*guard_error, *errors]
        if self.registry_created:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.registry_key) as key:
                    try:
                        registered, _kind = winreg.QueryValueEx(key, None)
                    except FileNotFoundError:
                        registered = None
                if registered is not None and not same_path(
                    registered, self.executable,
                ):
                    raise RuntimeError("App Paths ownership changed")
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, self.registry_key)
                self.registry_created = False
            except Exception as exc:
                errors.append(f"registry:{type(exc).__name__}:{exc}")
        try:
            if self.root.exists():
                shutil.rmtree(self.root)
        except Exception as exc:
            errors.append(f"directory:{type(exc).__name__}:{exc}")
        return {"passed": not errors, "errors": errors}
__all__ = [
    "AcquisitionFixture", "FixtureUnavailable", "exact_instances",
    "file_sha256", "process_identity_for", "same_path",
]
