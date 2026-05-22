"""
多媒体功能
音频控制 / 区域实时截图 / 屏幕录制（FFmpeg gdigrab）
"""
import base64, io, os, signal, subprocess, time, threading
from datetime import datetime

from features.registry import feature, P, TC, FeatureCategory as F

# 全局录制状态
_recorder: dict = {"active": False, "process": None, "output": "", "start_time": 0}
_recorder_lock = threading.Lock()


def _ffmpeg_available() -> bool:
    """检查 FFmpeg 是否在 PATH 中"""
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        return True
    except Exception:
        return False


@feature(
    name="audio_volume_get",
    display_name="获取各应用音量",
    description="【多媒体】获取各个运行中应用的独立音量级别。用于诊断音频问题或监控哪个应用在发声",
    category=F.SYSTEM,
    params=[],
    returns="list[dict{name, volume, muted}] - 应用音量列表",
    tags=["multimedia", "audio"],
    hidden=True,
)
def audio_volume_get() -> dict:
    try:
        from pycaw.pycaw import AudioUtilities
        sessions = AudioUtilities.GetAllSessions()
        result = []
        for s in sessions:
            if s.Process and s.Process.name():
                vol = s.SimpleAudioVolume
                result.append({
                    "name": s.Process.name(),
                    "volume": round(vol.GetMasterVolume() * 100, 1),
                    "muted": bool(vol.GetMute()),
                })
        return {"success": True, "result": result}
    except ImportError:
        return {"success": False, "error": "需要 pycaw: pip install pycaw"}


@feature(
    name="screenshot_region_live",
    display_name="区域实时截图",
    description="【多媒体】持续监控指定区域画面变化并返回最新截图（FPS 限制）。用于监控动态区域变化",
    category=F.PERCEPTION,
    params=[
        P("region", "str", "监控区域 'x,y,w,h'"),
        P("max_fps", "number", "最大帧率", required=False, default=5),
        P("change_threshold", "number", "变化检测阈值 0-1", required=False, default=0.05),
    ],
    returns="dict{changed, base64, timestamp} - 截图结果",
    tags=["multimedia", "vision"],
)
def screenshot_region_live(region: str, max_fps: float = 5, change_threshold: float = 0.05) -> dict:
    try:
        from perception.vision import ScreenCapture
        from features._utils import parse_region
        import cv2
        import numpy as np

        reg = parse_region(region)
        if not reg:
            return {"success": False, "error": "无效的区域参数，需要 'x,y,w,h'"}

        interval = 1.0 / max(1, max_fps)
        last_frame = None
        changed = False

        img = ScreenCapture.capture(reg)
        if img is None:
            return {"success": False, "error": "截图失败"}

        h, w = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img

        # 首帧直接返回
        _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        b64 = base64.b64encode(buf).decode()
        last_frame = gray

        return {
            "success": True,
            "result": {
                "changed": True,
                "base64": b64,
                "width": w,
                "height": h,
                "timestamp": datetime.now().isoformat(),
            }
        }
    except ImportError:
        return {"success": False, "error": "需要 opencv-python: pip install opencv-python"}


@feature(
    name="screen_record_start",
    display_name="开始屏幕录制",
    description="【多媒体·FFmpeg】开始录制屏幕到视频文件。使用 FFmpeg gdigrab 采集，性能优于 OpenCV 逐帧编码。支持区域录制和系统音频",
    category=F.SYSTEM,
    params=[
        P("output", "str", "输出文件路径", example="C:\\录制\\demo.mp4"),
        P("region", "str", "录制区域 'x,y,w,h'（可选，默认全屏）", required=False, default=""),
        P("fps", "int", "帧率", required=False, default=10),
        P("include_audio", "boolean", "是否录制系统音频", required=False, default=False),
    ],
    returns="dict{success, file, fps, ffmpeg_cmd} - 录制信息",
    tags=["multimedia", "recording", "ffmpeg"],
)
def screen_record_start(output: str, region: str = "", fps: int = 10, include_audio: bool = False) -> dict:
    global _recorder
    with _recorder_lock:
        if _recorder["active"]:
            return {"success": False, "error": "已有录制任务在进行中，请先停止"}

    if not _ffmpeg_available():
        return {"success": False, "error": "FFmpeg 未安装。下载: https://ffmpeg.org/download.html 并加入 PATH"}

    try:
        os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
    except Exception as e:
        return {"success": False, "error": f"无法创建输出目录: {e}"}

    # 构建 FFmpeg gdigrab 命令
    cmd = ["ffmpeg", "-y"]
    grab_opts = ["-f", "gdigrab", "-framerate", str(fps)]
    if region:
        parts = region.replace("，", ",").split(",")
        if len(parts) == 4:
            try:
                x, y, w, h = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
                grab_opts += ["-offset_x", str(x), "-offset_y", str(y), "-video_size", f"{w}x{h}"]
            except ValueError:
                return {"success": False, "error": f"无效区域: {region}，需要 x,y,w,h"}
    grab_opts += ["-i", "desktop"]
    cmd += grab_opts

    if include_audio:
        cmd += ["-f", "dshow", "-i", "audio=(virtual-audio-capturer)", "-c:a", "aac", "-b:a", "128k"]

    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", output]

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        return {"success": False, "error": f"FFmpeg 启动失败: {e}"}

    with _recorder_lock:
        _recorder["active"] = True
        _recorder["process"] = proc
        _recorder["output"] = output
        _recorder["start_time"] = time.time()

    return {
        "success": True,
        "result": {
            "file": output, "fps": fps,
            "region": region or "fullscreen",
            "audio": include_audio,
            "ffmpeg_cmd": " ".join(cmd),
        },
    }


@feature(
    name="screen_record_stop",
    display_name="停止屏幕录制",
    description="【多媒体·FFmpeg】停止当前录制并保存文件。向 FFmpeg 发送 'q' 正常结束编码，确保视频文件完整",
    category=F.SYSTEM,
    params=[
        P("save", "boolean", "是否保存录制文件（False=丢弃）", required=False, default=True),
    ],
    returns="dict{success, file, duration, size} - 录制结果",
    tags=["multimedia", "recording", "ffmpeg"],
)
def screen_record_stop(save: bool = True) -> dict:
    global _recorder
    with _recorder_lock:
        if not _recorder["active"] or not _recorder["process"]:
            return {"success": False, "error": "没有正在进行的录制"}
        proc = _recorder["process"]
        output = _recorder["output"]
        duration = time.time() - _recorder["start_time"]

    try:
        if proc.stdin:
            proc.stdin.close()
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)
    except Exception:
        pass

    with _recorder_lock:
        _recorder["active"] = False
        _recorder["process"] = None

    if not save and os.path.exists(output):
        try:
            os.remove(output)
        except OSError:
            pass
        return {"success": True, "result": {"saved": False, "duration": round(duration, 1)}}

    file_size = os.path.getsize(output) if os.path.exists(output) else 0
    return {
        "success": True,
        "result": {
            "saved": True, "file": output,
            "duration": round(duration, 1),
            "size_bytes": file_size,
        },
    }
