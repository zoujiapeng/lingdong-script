"""
AutoHotkey 管理器 — 自动检测/下载/启动 AHK v2 便携版
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
import time
import threading
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

_AHK_DIR = Path(__file__).resolve().parent.parent / ".ahk"
_AHK_EXE = _AHK_DIR / "AutoHotkey64.exe"
_RECORDER_SCRIPT = Path(__file__).resolve().parent / "ahk_recorder.ahk"
_AHK_DOWNLOAD_URL = "https://www.autohotkey.com/download/ahk-v2.exe.zip"

_lock = threading.Lock()
_proc: Optional[subprocess.Popen] = None
_buf: list[str] = []


def is_ahk_available() -> bool:
    """检查 AHK 是否可用（系统 PATH 或便携版）"""
    if _AHK_EXE.exists():
        return True
    try:
        subprocess.run(["AutoHotkey64", "--version"], capture_output=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        try:
            subprocess.run(["AutoHotkey32", "--version"], capture_output=True, timeout=5)
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False


def download_ahk(progress_callback=None) -> bool:
    """下载 AHK v2 便携版到项目目录"""
    _AHK_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = _AHK_DIR / "ahk-v2.zip"

    try:
        def report(b, d):
            if progress_callback:
                progress_callback(b, d if d else 1)

        req = urllib.request.Request(_AHK_DOWNLOAD_URL,
                                     headers={"User-Agent": "Lobster-AHK/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            with open(zip_path, "wb") as f:
                while True:
                    chunk = resp.read(8192)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    report(downloaded, total)

        # 解压
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(_AHK_DIR)
        zip_path.unlink()

        return _AHK_EXE.exists()
    except Exception as e:
        print(f"[AHK] 下载失败: {e}", file=sys.stderr)
        return False


def get_ahk_path() -> Optional[str]:
    """返回可用的 AHK exe 路径"""
    if _AHK_EXE.exists():
        return str(_AHK_EXE)
    for name in ["AutoHotkey64", "AutoHotkey32", "AutoHotkey"]:
        try:
            r = subprocess.run([name, "--version"], capture_output=True, timeout=5)
            if r.returncode == 0:
                return name
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


def start_recorder(
    name: str = "recording",
    include_mouse: bool = True,
    include_keys: bool = True,
) -> dict:
    """启动 AHK 录制进程。返回 {success, ...}"""
    global _proc, _buf

    with _lock:
        if _proc and _proc.poll() is None:
            return {"success": False, "error": "已有录制进程在运行"}

        ahk = get_ahk_path()
        if not ahk:
            if download_ahk():
                ahk = get_ahk_path()
            if not ahk:
                return {"success": False, "error": "AHK 不可用，请安装 AutoHotkey v2"}

        script = str(_RECORDER_SCRIPT)
        if not os.path.exists(script):
            return {"success": False, "error": f"录制脚本不存在: {script}"}

        try:
            proc = subprocess.Popen(
                [ahk, script],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True, bufsize=1,
            )
        except Exception as e:
            return {"success": False, "error": f"启动 AHK 失败: {e}"}

        _proc = proc
        _buf = []

        # 等待 READY 信号
        deadline = time.time() + 10
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            _buf.append(line.strip())
            if line.startswith("READY|"):
                break

        if not _buf or not _buf[-1].startswith("READY|"):
            try:
                proc.kill()
            except Exception:
                pass
            _proc = None
            return {"success": False, "error": "AHK 录制器启动超时"}

        # 发送 start 命令
        cmd = f"start|{name}|{1 if include_mouse else 0}|{1 if include_keys else 0}"
        proc.stdin.write(cmd + "\n")
        proc.stdin.flush()

        # 等待确认
        deadline = time.time() + 5
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            _buf.append(line.strip())
            if "RECORDING_START" in line:
                return {"success": True, "result": {"recording": True, "name": name}}

        return {"success": True, "result": {"recording": True, "name": name}}


def stop_recorder() -> dict:
    """停止 AHK 录制进程。返回录制结果"""
    global _proc, _buf

    with _lock:
        if not _proc or _proc.poll() is not None:
            return {"success": False, "error": "录制器未运行"}

        try:
            _proc.stdin.write("stop\n")
            _proc.stdin.flush()
        except Exception as e:
            _proc.kill()
            _proc = None
            return {"success": False, "error": f"停止录制失败: {e}"}

        # 读取 RESULT 行
        result = {"success": True, "name": "unknown", "events": 0, "duration": 0}
        deadline = time.time() + 10
        while time.time() < deadline:
            line = _proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            _buf.append(line)
            if line.startswith("RESULT|"):
                parts = line.split("|")
                if len(parts) >= 5:
                    result["name"] = parts[2]
                    result["events"] = int(parts[3])
                    result["duration"] = float(parts[4])

                # 读取缓冲区中累积的 EVENT 行
                events = []
                for l in _buf:
                    if l.startswith("EVENT|"):
                        ep = l.split("|")
                        if ep[1] == "click":
                            events.append({
                                "type": "click", "time": 0,
                                "x": int(ep[2]), "y": int(ep[3]),
                                "button": ep[4], "pressed": ep[5] == "true",
                            })
                        elif ep[1] == "key":
                            events.append({
                                "type": "key", "time": 0,
                                "key": ep[2], "pressed": ep[3] == "true",
                            })
                        elif ep[1] == "move":
                            events.append({
                                "type": "move", "time": 0,
                                "x": int(ep[2]), "y": int(ep[3]),
                            })
                result["event_data"] = events

                break

        # 清理进程
        try:
            _proc.wait(timeout=3)
        except Exception:
            _proc.kill()
        _proc = None

        return result


def recorder_status() -> dict:
    """获取录制器状态"""
    global _proc
    with _lock:
        if not _proc or _proc.poll() is not None:
            return {"recording": False, "available": False}
        try:
            _proc.stdin.write("ping\n")
            _proc.stdin.flush()
            deadline = time.time() + 2
            while time.time() < deadline:
                line = _proc.stdout.readline()
                if not line:
                    break
                if line.startswith("PONG|"):
                    recording = line.strip().split("|")[1] == "1"
                    return {"recording": recording, "available": True}
        except Exception:
            pass
        return {"recording": False, "available": False}
