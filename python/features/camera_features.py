"""
摄像头功能
摄像头列表 / 帧抓拍 / 视频录制 / 运动检测
"""
import base64, io, os, time, threading
from datetime import datetime

from features.registry import feature, P, TC, FeatureCategory as F

# 全局录制状态
_recorder: dict = {"active": False, "process": None, "camera_id": -1, "output": "", "start_time": 0, "thread": None}
_recorder_lock = threading.Lock()


def _opencv_available() -> bool:
    try:
        import cv2
        return True
    except ImportError:
        return False


@feature(
    name="camera_list",
    display_name="列出摄像头",
    description="【摄像头】列出系统中所有可用的摄像头设备。通过 OpenCV 遍历设备索引 0-9 检测可用摄像头",
    category=F.PERCEPTION,
    params=[],
    returns="list[dict{id, name}] - 摄像头列表",
    tags=["camera", "hardware"],
)
def camera_list() -> dict:
    if not _opencv_available():
        return {"success": False, "error": "需要 opencv-python: pip install opencv-python"}

    import cv2
    cameras = []
    for i in range(10):
        try:
            cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
            if cap.isOpened():
                ret, _ = cap.read()
                if ret:
                    # 尝试获取摄像头名称（仅 Windows DirectShow 支持）
                    name = f"摄像头 {i}"
                    backend = cap.getBackendName()
                    cameras.append({"id": i, "name": name, "backend": backend})
                cap.release()
            else:
                cap.release()
        except Exception:
            pass

    return {"success": True, "result": cameras}


@feature(
    name="camera_capture",
    display_name="摄像头抓拍",
    description="【摄像头】从指定摄像头抓拍一帧图像并保存到文件或返回 base64。支持设置分辨率和自动曝光等待",
    category=F.PERCEPTION,
    params=[
        P("camera_id", "int", "摄像头编号（0=默认摄像头）", required=False, default=0),
        P("output_path", "str", "输出图片路径（可选，不填则返回 base64）", required=False, default=""),
        P("width", "int", "画面宽度（可选）", required=False, default=0),
        P("height", "int", "画面高度（可选）", required=False, default=0),
        P("format", "str", "图片格式 png/jpg", required=False, default="jpg"),
        P("wait_stable", "number", "等待画面稳定秒数（自动曝光/白平衡稳定时间）", required=False, default=0.5),
    ],
    returns="dict{file?, base64?, width, height} - 抓拍结果",
    tags=["camera", "capture"],
)
def camera_capture(camera_id: int = 0, output_path: str = "", width: int = 0, height: int = 0,
                   format: str = "jpg", wait_stable: float = 0.5) -> dict:
    if not _opencv_available():
        return {"success": False, "error": "需要 opencv-python: pip install opencv-python"}

    import cv2
    import numpy as np

    try:
        cap = cv2.VideoCapture(camera_id, cv2.CAP_DSHOW)
        if not cap.isOpened():
            return {"success": False, "error": f"无法打开摄像头 {camera_id}，请检查设备是否被占用"}

        if width > 0 and height > 0:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

        # 丢弃前几帧让自动曝光稳定
        for _ in range(5):
            cap.read()

        if wait_stable > 0:
            time.sleep(wait_stable)

        ret, frame = cap.read()
        cap.release()

        if not ret or frame is None:
            return {"success": False, "error": "抓拍失败，未获取到画面"}

        actual_h, actual_w = frame.shape[:2]
        ext = ".jpg" if format == "jpg" else ".png"
        quality_param = cv2.IMWRITE_JPEG_QUALITY if format == "jpg" else cv2.IMWRITE_PNG_COMPRESSION
        quality_val = 90 if format == "jpg" else 3

        if output_path:
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            save_path = output_path if output_path.endswith(ext) else output_path + ext
            cv2.imwrite(save_path, frame, [quality_param, quality_val])
            return {
                "success": True,
                "result": {
                    "file": save_path,
                    "width": actual_w,
                    "height": actual_h,
                    "format": format,
                }
            }
        else:
            _, buf = cv2.imencode(ext, frame, [quality_param, quality_val])
            b64 = base64.b64encode(buf).decode()
            return {
                "success": True,
                "result": {
                    "base64": b64,
                    "width": actual_w,
                    "height": actual_h,
                    "format": format,
                }
            }
    except Exception as e:
        return {"success": False, "error": f"摄像头抓拍失败: {e}"}


@feature(
    name="camera_record_start",
    display_name="开始摄像头录制",
    description="【摄像头·OpenCV】开始从摄像头录制视频到 AVI 文件。使用 OpenCV VideoWriter，性能稳定",
    category=F.SYSTEM,
    params=[
        P("camera_id", "int", "摄像头编号（0=默认摄像头）", required=False, default=0),
        P("output_path", "str", "输出视频文件路径（支持 .avi .mp4）", example="C:\\录制\\cam_demo.avi"),
        P("fps", "number", "录制帧率", required=False, default=15),
        P("duration", "number", "最大录制时长（秒，0=不限制）", required=False, default=0),
        P("width", "int", "画面宽度（可选，0=使用摄像头默认）", required=False, default=0),
        P("height", "int", "画面高度（可选，0=使用摄像头默认）", required=False, default=0),
    ],
    returns="dict{file, fps, camera_id} - 录制信息",
    tags=["camera", "recording"],
)
def camera_record_start(camera_id: int = 0, output_path: str = "", fps: float = 15,
                        duration: float = 0, width: int = 0, height: int = 0) -> dict:
    if not _opencv_available():
        return {"success": False, "error": "需要 opencv-python: pip install opencv-python"}

    import cv2

    global _recorder
    with _recorder_lock:
        if _recorder["active"]:
            return {"success": False, "error": "已有摄像头录制任务在进行中，请先停止"}

    if not output_path:
        return {"success": False, "error": "请指定输出路径"}

    try:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    except Exception as e:
        return {"success": False, "error": f"无法创建输出目录: {e}"}

    try:
        cap = cv2.VideoCapture(camera_id, cv2.CAP_DSHOW)
        if not cap.isOpened():
            return {"success": False, "error": f"无法打开摄像头 {camera_id}"}

        if width > 0 and height > 0:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

        # 获取实际分辨率
        actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        # 判断编码器
        ext = os.path.splitext(output_path)[1].lower()
        fourcc_code = cv2.VideoWriter_fourcc(*"MJPG") if ext == ".avi" else cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(output_path, fourcc_code, fps, (actual_w, actual_h))
        if not writer.isOpened():
            cap.release()
            return {"success": False, "error": "无法创建视频写入器，编码器不支持"}

        def _record_worker(cap, writer, output_path, camera_id, fps, duration):
            global _recorder
            start = time.time()
            frame_count = 0
            try:
                while True:
                    with _recorder_lock:
                        if not _recorder["active"]:
                            break
                    ret, frame = cap.read()
                    if not ret:
                        break
                    writer.write(frame)
                    frame_count += 1
                    if duration > 0 and (time.time() - start) >= duration:
                        break
            finally:
                cap.release()
                writer.release()
                with _recorder_lock:
                    _recorder["active"] = False
                    _recorder["thread"] = None

        thread = threading.Thread(
            target=_record_worker,
            args=(cap, writer, output_path, camera_id, fps, duration),
            daemon=True,
        )

        with _recorder_lock:
            _recorder["active"] = True
            _recorder["camera_id"] = camera_id
            _recorder["output"] = output_path
            _recorder["start_time"] = time.time()
            _recorder["thread"] = thread

        thread.start()

        return {
            "success": True,
            "result": {
                "file": output_path,
                "camera_id": camera_id,
                "fps": fps,
                "width": actual_w,
                "height": actual_h,
                "duration_limit": duration or "unlimited",
            }
        }
    except Exception as e:
        return {"success": False, "error": f"摄像头录制启动失败: {e}"}


@feature(
    name="camera_record_stop",
    display_name="停止摄像头录制",
    description="【摄像头】停止当前摄像头录制并保存视频文件",
    category=F.SYSTEM,
    params=[],
    returns="dict{file, duration, frame_count, size} - 录制结果",
    tags=["camera", "recording"],
)
def camera_record_stop() -> dict:
    global _recorder
    with _recorder_lock:
        if not _recorder["active"]:
            return {"success": False, "error": "没有正在进行的摄像头录制"}
        _recorder["active"] = False
        output = _recorder["output"]
        duration = time.time() - _recorder["start_time"]

    # 等待录制线程结束
    thread = _recorder.get("thread")
    if thread and thread.is_alive():
        thread.join(timeout=3)

    file_size = os.path.getsize(output) if os.path.exists(output) else 0
    return {
        "success": True,
        "result": {
            "file": output,
            "duration": round(duration, 1),
            "size_bytes": file_size,
        }
    }


@feature(
    name="camera_detect_motion",
    display_name="摄像头运动检测",
    description="【摄像头】检测摄像头画面中的运动。使用帧差法，返回是否有运动以及运动区域和幅度。可用于安防监控触发",
    category=F.PERCEPTION,
    params=[
        P("camera_id", "int", "摄像头编号（0=默认摄像头）", required=False, default=0),
        P("threshold", "number", "运动敏感度（越大越不敏感，建议 20-50）", required=False, default=25),
        P("min_area", "int", "最小运动区域面积（像素，用于过滤噪声）", required=False, default=500),
        P("timeout", "number", "检测超时秒数（0=只检测一次）", required=False, default=0),
        P("save_motion_frame", "boolean", "是否保存检测到运动时的画面", required=False, default=False),
        P("output_dir", "str", "运动画面保存目录（save_motion_frame=True 时必填）", required=False, default=""),
    ],
    returns="dict{motion_detected, area?, frames?, save_file?} - 检测结果",
    tags=["camera", "motion", "security"],
    examples=["camera_detect_motion(camera_id=0, threshold=25, min_area=500)"],
)
def camera_detect_motion(camera_id: int = 0, threshold: float = 25, min_area: int = 500,
                         timeout: float = 0, save_motion_frame: bool = False,
                         output_dir: str = "") -> dict:
    if not _opencv_available():
        return {"success": False, "error": "需要 opencv-python: pip install opencv-python"}

    import cv2
    import numpy as np

    try:
        cap = cv2.VideoCapture(camera_id, cv2.CAP_DSHOW)
        if not cap.isOpened():
            return {"success": False, "error": f"无法打开摄像头 {camera_id}"}

        # 读取首帧作为背景
        ret, frame1 = cap.read()
        if not ret:
            cap.release()
            return {"success": False, "error": "无法从摄像头读取画面"}
        gray1 = cv2.cvtColor(frame1, cv2.COLOR_BGR2GRAY)
        gray1 = cv2.GaussianBlur(gray1, (21, 21), 0)

        start = time.time()
        motion_detected = False
        max_area = 0
        motion_frames = 0
        saved_file = ""

        while True:
            ret, frame2 = cap.read()
            if not ret:
                break

            gray2 = cv2.cvtColor(frame2, cv2.COLOR_BGR2GRAY)
            gray2 = cv2.GaussianBlur(gray2, (21, 21), 0)

            # 帧差法
            diff = cv2.absdiff(gray1, gray2)
            thresh = cv2.threshold(diff, threshold, 255, cv2.THRESH_BINARY)[1]
            thresh = cv2.dilate(thresh, None, iterations=2)
            contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            frame_area = 0
            for c in contours:
                area = cv2.contourArea(c)
                if area > min_area:
                    frame_area += area
                    motion_detected = True

            if frame_area > max_area:
                max_area = int(frame_area)

            if motion_detected:
                motion_frames += 1
                if save_motion_frame and not saved_file and output_dir:
                    os.makedirs(output_dir, exist_ok=True)
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    save_path = os.path.join(output_dir, f"motion_{timestamp}.jpg")
                    cv2.imwrite(save_path, frame2, [cv2.IMWRITE_JPEG_QUALITY, 85])
                    saved_file = save_path
                if timeout == 0:
                    break

            gray1 = gray2

            if timeout > 0 and (time.time() - start) >= timeout:
                break

        cap.release()

        return {
            "success": True,
            "result": {
                "motion_detected": motion_detected,
                "max_area_px": max_area,
                "motion_frames": motion_frames,
                "saved_file": saved_file or None,
                "duration": round(time.time() - start, 1),
            }
        }
    except Exception as e:
        return {"success": False, "error": f"运动检测失败: {e}"}
