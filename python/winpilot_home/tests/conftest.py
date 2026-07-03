"""Pytest bootstrap for the WinPilot package.

winpilot_home is not itself a package (the package is ``winpilot`` inside it),
so put winpilot_home on sys.path to make ``import winpilot...`` resolve no
matter where pytest is invoked from. Mirrors what agent/__main__.py does at
runtime.

Some unit tests only exercise task routing/runtime selection. On non-Windows
CI those tests should not fail just because GUI-only optional dependencies such
as pywin32, mss, UIAutomation, OCR, or OpenCV are unavailable, so lightweight
stubs are installed only when the real modules are missing.
"""
import ctypes
import importlib.util
import os
import subprocess
import sys
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class _WinDll:
    def __getattr__(self, _name):
        return self

    def __call__(self, *args, **kwargs):
        return 1


class _NoopModule(types.ModuleType):
    def __getattr__(self, _name):
        return lambda *args, **kwargs: 0


class _ConstModule(_NoopModule):
    def __getattr__(self, _name):
        return 0


class _Mss:
    monitors = [{"left": 0, "top": 0, "width": 1, "height": 1}]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def grab(self, _region):
        return types.SimpleNamespace(size=(1, 1), rgb=b"\x00\x00\x00")


def _missing(name: str) -> bool:
    return name not in sys.modules and importlib.util.find_spec(name) is None


if not hasattr(ctypes, "windll"):
    ctypes.windll = _WinDll()

if not hasattr(subprocess, "CREATE_NO_WINDOW"):
    subprocess.CREATE_NO_WINDOW = 0

if _missing("mss"):
    sys.modules["mss"] = types.SimpleNamespace(mss=_Mss)

if _missing("win32con"):
    mod = _ConstModule("win32con")
    mod.GA_ROOT = 2
    mod.GW_OWNER = 4
    mod.SW_RESTORE = 9
    mod.SW_SHOW = 5
    sys.modules["win32con"] = mod

if _missing("win32api"):
    sys.modules["win32api"] = _NoopModule("win32api")

if _missing("win32process"):
    mod = _NoopModule("win32process")
    mod.GetWindowThreadProcessId = lambda hwnd: (0, 0)
    sys.modules["win32process"] = mod

if _missing("win32gui"):
    mod = _NoopModule("win32gui")
    mod.GetForegroundWindow = lambda: 0
    mod.GetClassName = lambda hwnd: ""
    mod.GetWindowText = lambda hwnd: ""
    mod.GetWindowRect = lambda hwnd: (0, 0, 1, 1)
    mod.IsWindow = lambda hwnd: True
    mod.IsWindowVisible = lambda hwnd: True
    mod.GetAncestor = lambda hwnd, flag: hwnd
    mod.GetWindow = lambda hwnd, flag: 0
    sys.modules["win32gui"] = mod

if _missing("onnxruntime"):
    class _SessionOptions:
        enable_mem_pattern = False
        execution_mode = 0

    class _ExecutionMode:
        ORT_SEQUENTIAL = 0

    mod = types.ModuleType("onnxruntime")
    mod.get_available_providers = lambda: []
    mod.SessionOptions = _SessionOptions
    mod.ExecutionMode = _ExecutionMode
    mod.InferenceSession = type("InferenceSession", (), {})
    sys.modules["onnxruntime"] = mod

if _missing("uiautomation"):
    mod = types.ModuleType("uiautomation")
    mod.SetGlobalSearchTimeout = lambda *_args, **_kwargs: None
    mod.OPERATION_WAIT_TIME = 0.1
    mod.Control = type("Control", (), {})
    sys.modules["uiautomation"] = mod

if _missing("cv2"):
    mod = _NoopModule("cv2")
    mod.COLOR_BGR2GRAY = 0
    mod.TM_CCOEFF_NORMED = 0
    mod.RETR_EXTERNAL = 0
    mod.CHAIN_APPROX_SIMPLE = 0
    mod.cvtColor = lambda image, code: image
    mod.Canny = lambda *args, **kwargs: []
    mod.dilate = lambda image, *args, **kwargs: image
    mod.findContours = lambda *args, **kwargs: ([], None)
    mod.imencode = lambda *_args, **_kwargs: (False, b"")
    sys.modules["cv2"] = mod
