"""
视觉宏录制 — 录制时截屏，后期从点击坐标自动提取按钮小图

流程:
  1. macro_start_visual_recording
     → pynput 监听鼠标 + 每次按下截全屏到缓存目录
     返回时与普通录制相同（非阻塞）
  2. macro_stop_visual_recording
     → 停止监听，保存事件 JSON（含截图引用路径）
     返回文件路径和事件统计
  3. macro_extract_visual_templates
     → 手动触发（耗时 ~200ms/click）
     遍历每个 click 事件 → 加载对应截图
     → floodFill/contour 检测按钮边界
     → 裁剪小图 → 可选 OCR 提取文字
     → 存入 experience/db.json + templates/
     返回提取报告

视觉宏文件格式:
  {
    "name": "...",
    "events": [
      {"type": "click", "time": 1.5, "x": 500, "y": 400,
       "screenshot_file": "C:/tmp/lingdong_vis_xxx/shot_0.jpg", ...}
    ],
    "visual": true,
    "window_title": "...",
    "screenshot_dir": "C:/tmp/lingdong_vis_xxx"
  }

无 VLM 依赖，模板提取后可完全离线匹配。
"""
import json
import logging
import os
import shutil
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from features.registry import feature, P, FeatureCategory as F

logger = logging.getLogger("visual_macro")

# ── 路径 ──────────────────────────────────────────────────────────
_MACRO_DIR = Path(__file__).resolve().parent.parent / "macros"
_EXP_DIR = Path(__file__).resolve().parent.parent / "experience"
_TEMPLATES_DIR = _EXP_DIR / "templates"
_EXP_DIR.mkdir(exist_ok=True)
_TEMPLATES_DIR.mkdir(exist_ok=True)
_MACRO_DIR.mkdir(exist_ok=True)

# ── 依赖检查 ──────────────────────────────────────────────────────

_HAS_OPENCV = False
try:
    import cv2
    import numpy as np
    _HAS_OPENCV = True
except ImportError:
    pass

_HAS_MSS = False
try:
    import mss
    _HAS_MSS = True
except ImportError:
    pass

_HAS_EASYOCR = False
try:
    import easyocr
    _HAS_EASYOCR = True
except ImportError:
    pass

# ── 录制状态（单例） ─────────────────────────────────────────────
_vis_state = {
    "active": False,
    "events": [],
    "start": 0,
    "screenshot_dir": None,
    "screenshot_count": 0,
    "listeners": [],
    "name": "",
    "window_title": "",
}

# lazy easyocr reader (singleton, expensive to init)
_easyocr_reader = None


def _get_easyocr_reader():
    global _easyocr_reader
    if _easyocr_reader is None and _HAS_EASYOCR:
        try:
            import easyocr
            _easyocr_reader = easyocr.Reader(["ch_sim", "en"], gpu=False)
        except Exception as e:
            logger.warning("EasyOCR init failed: %s", e)
    return _easyocr_reader


# ── 工具函数 ──────────────────────────────────────────────────────

def _capture_screen(output_path: str) -> bool:
    """截全屏，mss 优先 → PIL 回退。"""
    try:
        if _HAS_MSS:
            import mss
            with mss.mss() as sct:
                sct.shot(output=output_path)
        else:
            from PIL import ImageGrab
            ImageGrab.grab().save(output_path)
        return True
    except Exception as e:
        logger.warning("screenshot failed: %s", e)
        return False


def _active_window_title() -> str:
    """当前活动窗口标题。"""
    try:
        import ctypes
        h = ctypes.windll.user32.GetForegroundWindow()
        length = ctypes.windll.user32.GetWindowTextLengthW(h)
        buf = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(h, buf, length + 1)
        return buf.value or ""
    except Exception:
        return ""


def _safe_name(raw: str) -> str:
    return "".join(c if c.isalnum() or c in " _-" else "_" for c in raw).strip() or f"vis_{int(time.time())}"


# ── 智能按钮边界检测 ──────────────────────────────────────────────

def _smart_crop_around_click(screenshot_path: str, click_x: int, click_y: int,
                              margin: int = 150) -> dict:
    """从 click 点出发，自动检测按钮边界。

    策略递进:
      1. floodFill 递增容差（15→30→50）找连通域
      2. Canny + findContours 兜底（找包含点击点的轮廓）
      3. 固定 40×40 兜底

    Returns: {x, y, w, h, center_x, center_y} or {error: "..."}
    """
    if not _HAS_OPENCV:
        return _fixed_crop(click_x, click_y, 20)

    import cv2
    import numpy as np

    img = cv2.imread(screenshot_path)
    if img is None:
        return {"error": f"cannot read {screenshot_path}"}

    h, w = img.shape[:2]

    # ── 局部 300×300 ──
    x1 = max(0, click_x - margin)
    y1 = max(0, click_y - margin)
    x2 = min(w, click_x + margin)
    y2 = min(h, click_y + margin)
    local = img[y1:y2, x1:x2]
    lh, lw = local.shape[:2]
    sx, sy = click_x - x1, click_y - y1

    best_rect = None
    best_area = 0

    # ── 阶段 1: floodFill 递增容差 ──
    for tolerance in (15, 30, 50):
        mask = np.zeros((lh + 2, lw + 2), np.uint8)
        copy = local.copy()
        try:
            _, _, rect, area = cv2.floodFill(
                copy, mask, (int(sx), int(sy)), 128,
                loDiff=(tolerance, tolerance, tolerance),
                upDiff=(tolerance, tolerance, tolerance),
                flags=cv2.FLOODFILL_FIXED_RANGE,
            )
        except Exception:
            continue

        rx, ry, rw, rh = rect
        # 过滤明显不合理的结果
        if rw < 10 or rh < 10:
            continue
        # floodFill 漫出局部区域 → 点击点在背景上，结果不可靠
        if rw >= margin * 1.5 or rh >= margin * 1.5:
            continue

        if rw * rh > best_area:
            best_area = rw * rh
            best_rect = (x1 + rx, y1 + ry, rw, rh)

        # 面积在合理按钮范围内（>800 即约 28×28，<50000 即约 220×220）提前退出
        if 800 <= rw * rh <= 50000:
            break

    # ── 阶段 2: Canny + contour 兜底 ──
    if best_rect is None or best_area < 300:
        gray = cv2.cvtColor(local, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (3, 3), 0)
        edges = cv2.Canny(blurred, 30, 100)
        try:
            contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        except Exception:
            contours = []

        for cnt in contours:
            if cv2.pointPolygonTest(cnt, (float(sx), float(sy)), False) >= 0:
                rx, ry, rw, rh = cv2.boundingRect(cnt)
                if 15 <= rw <= margin and 10 <= rh <= margin:
                    if rw * rh > best_area:
                        best_area = rw * rh
                        best_rect = (x1 + rx, y1 + ry, rw, rh)

    # ── 阶段 3: 固定 40×40 兜底 ──
    if best_rect is None:
        best_rect = _fixed_crop(click_x, click_y, 20)
        rx, ry, rw, rh = best_rect["x"], best_rect["y"], best_rect["w"], best_rect["h"]
        best_rect = (rx, ry, rw, rh)

    rx, ry, rw, rh = best_rect
    return {
        "x": rx, "y": ry, "w": rw, "h": rh,
        "center_x": rx + rw // 2,
        "center_y": ry + rh // 2,
    }


def _fixed_crop(click_x: int, click_y: int, half: int = 20) -> dict:
    """固定 40×40 裁剪兜底。"""
    return {
        "x": max(0, click_x - half),
        "y": max(0, click_y - half),
        "w": half * 2,
        "h": half * 2,
        "center_x": click_x,
        "center_y": click_y,
    }


def _store_template(screenshot_path: str, bbox: dict, macro_name: str,
                    step_index: int, window_title: str) -> Optional[dict]:
    """裁剪截图区域 → 保存小图 → 存入 experience_db。

    Returns {target_text, template_path, bbox} or None 如果失败。
    """
    if not _HAS_OPENCV:
        return None

    import cv2

    # 裁剪（带边界保护）
    try:
        img = cv2.imread(screenshot_path)
        if img is None:
            return None
        ih, iw = img.shape[:2]
        x, y, w, h = bbox["x"], bbox["y"], bbox["w"], bbox["h"]
        # clamp to image bounds
        x2 = min(x + w, iw)
        y2 = min(y + h, ih)
        x = max(0, x)
        y = max(0, y)
        if x2 <= x or y2 <= y:
            return None
        crop = img[y:y2, x:x2]
    except Exception as e:
        logger.warning("crop failed at step %d: %s", step_index, e)
        return None

    # OCR 提取文字
    label = f"vis_{_safe_name(macro_name)}_{step_index}"
    ocr_text = _maybe_ocr_text_img(crop)
    if ocr_text:
        label = ocr_text
        logger.info("OCR detected: '%s' at step %d", ocr_text, step_index)

    # 保存小图
    safe_label = "".join(c if c.isalnum() or c in " _-" else "_" for c in label).strip() or f"step_{step_index}"
    tmpl_name = f"vis_{safe_label}.png"
    tmpl_path = _TEMPLATES_DIR / tmpl_name
    try:
        cv2.imwrite(str(tmpl_path), crop)
    except Exception as e:
        logger.warning("save template failed: %s", e)
        return None

    # 存入 experience_db
    try:
        from agent import experience_db as exp
        sig = exp.store(
            step_type="click",
            target_text=label,
            template_path=str(tmpl_path),
            bbox=bbox,
            confidence=0.9,
            method="visual_macro",
            window_title=window_title,
        )
    except ImportError:
        sig = ""

    return {
        "target_text": label,
        "template_path": str(tmpl_path),
        "bbox": bbox,
        "signature": sig,
    }


def _maybe_ocr_text_img(crop_img) -> Optional[str]:
    """对 OpenCV 裁剪图像做 OCR。"""
    if not _HAS_EASYOCR:
        return None
    reader = _get_easyocr_reader()
    if reader is None:
        return None
    try:
        results = reader.readtext(crop_img)
        if results:
            best = max(results, key=lambda r: r[2])
            text, conf = best[1], best[2]
            if conf >= 0.4 and text.strip():
                return text.strip()
    except Exception as e:
        logger.debug("OCR on crop img failed: %s", e)
    return None


# ── @feature 工具 ──────────────────────────────────────────────────

@feature(
    name="macro_start_visual_recording",
    display_name="开始视觉宏录制",
    description="【视觉宏】启动带截图功能的宏录制。每次点击自动截全屏，录制后可用 macro_extract_visual_templates 提取按钮模板。非阻塞，立即返回",
    category=F.MACRO,
    params=[
        P("name", "str", "视觉宏名称", required=False, default=None),
        P("include_mouse", "boolean", "是否录制鼠标", required=False, default=True),
        P("include_keys", "boolean", "是否录制键盘", required=False, default=False),
    ],
    returns="dict{success, recording, name} 录制状态",
    tags=["macro", "visual", "recording"],
)
def macro_start_visual_recording(name: Optional[str] = None,
                                  include_mouse: bool = True,
                                  include_keys: bool = False) -> dict:
    if _vis_state["active"]:
        return {"success": False, "error": "已有录制在进行中"}

    if not name:
        name = f"visual_{int(time.time())}"

    # pynput 检查
    try:
        from pynput import mouse
    except ImportError:
        return {"success": False, "error": "需要 pynput: pip install pynput"}

    _vis_state["active"] = True
    _vis_state["events"] = []
    _vis_state["start"] = time.time()
    _vis_state["name"] = name
    _vis_state["screenshot_count"] = 0
    _vis_state["window_title"] = _active_window_title()

    # 截图缓存目录
    safe = _safe_name(name)
    _vis_state["screenshot_dir"] = os.path.join(
        tempfile.gettempdir(), f"lingdong_vis_{safe}_{int(time.time())}"
    )
    os.makedirs(_vis_state["screenshot_dir"], exist_ok=True)

    def _on_click(x, y, button, pressed):
        if not _vis_state["active"]:
            return False
        if not include_mouse:
            return True

        t = round(time.time() - _vis_state["start"], 3)
        event = {
            "type": "click",
            "time": t,
            "x": int(x),
            "y": int(y),
            "button": str(button),
            "pressed": bool(pressed),
        }
        # 按下时截屏
        if pressed:
            idx = _vis_state["screenshot_count"]
            shot = os.path.join(_vis_state["screenshot_dir"], f"shot_{idx:04d}.jpg")
            _capture_screen(shot)
            event["screenshot_file"] = shot
            _vis_state["screenshot_count"] += 1

        _vis_state["events"].append(event)

    listener = mouse.Listener(on_click=_on_click)
    _vis_state["listeners"] = [listener]
    listener.start()

    return {"success": True, "result": {"recording": True, "name": name}}


@feature(
    name="macro_stop_visual_recording",
    display_name="停止视觉宏录制",
    description="【视觉宏】停止视觉宏录制，保存事件 JSON 文件（含截图引用），返回文件路径和统计",
    category=F.MACRO,
    params=[],
    returns="dict{success, name, file, events, duration, screenshots, window_title}",
    tags=["macro", "visual", "recording"],
)
def macro_stop_visual_recording() -> dict:
    if not _vis_state["active"]:
        return {"success": False, "error": "录制器未运行"}

    _vis_state["active"] = False

    for lst in _vis_state["listeners"]:
        try:
            lst.stop()
        except Exception:
            pass
    _vis_state["listeners"] = []

    duration = round(time.time() - _vis_state["start"], 1)
    events = _vis_state["events"]
    name = _vis_state["name"]
    shot_dir = _vis_state["screenshot_dir"]
    win_title = _vis_state["window_title"] or _active_window_title()
    shot_count = _vis_state["screenshot_count"]

    # 保存 JSON
    safe = _safe_name(name)
    data = {
        "name": name,
        "events": events,
        "duration": duration,
        "created": datetime.now().isoformat(),
        "visual": True,
        "window_title": win_title,
        "screenshot_dir": shot_dir,
        "screenshot_count": shot_count,
    }
    file_path = _MACRO_DIR / f"{safe}.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # 清理状态
    _vis_state["events"] = []

    return {
        "success": True,
        "result": {
            "name": name,
            "file": str(file_path),
            "events": len(events),
            "duration": duration,
            "screenshots": shot_count,
            "window_title": win_title,
        },
    }


@feature(
    name="macro_extract_visual_templates",
    display_name="提取视觉宏模板",
    description="【视觉宏】从已录制的视觉宏中提取按钮小图。遍历每个 click 事件 → 智能检测按钮边界 → 裁剪小图 → OCR 识别文字 → 存入经验库。完成后该宏的截图缓存可安全删除",
    category=F.MACRO,
    params=[
        P("macro_name", "str", "视觉宏名称（不含 .json）", example="visual_login"),
        P("cleanup_screenshots", "boolean", "提取后是否清理原始截图", required=False, default=True),
        P("margin", "int", "按钮检测区域半径（像素）", required=False, default=150),
    ],
    returns="dict{success, total, extracted, skipped, templates[]}",
    tags=["macro", "visual", "template", "extraction"],
)
def macro_extract_visual_templates(macro_name: str,
                                    cleanup_screenshots: bool = True,
                                    margin: int = 150) -> dict:
    safe = _safe_name(macro_name)
    if not safe:
        # too aggressive — restore original stem logic
        safe = macro_name.strip()
    path = _MACRO_DIR / f"{safe}.json"
    if not path.exists():
        # try as-is
        path = _MACRO_DIR / f"{macro_name.strip()}.json"
    if not path.exists():
        return {"success": False, "error": f"宏 '{macro_name}' 不存在"}

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return {"success": False, "error": f"读取失败: {e}"}

    if not data.get("visual"):
        return {"success": False, "error": f"'{macro_name}' 不是视觉宏（缺少截图数据）"}

    events = data.get("events", [])
    if not events:
        return {"success": False, "error": "宏没有事件数据"}

    click_events = [e for e in events if e.get("type") == "click" and e.get("pressed") and e.get("screenshot_file")]
    if not click_events:
        return {"success": False, "error": "没有带截图的 click 事件"}

    window_title = data.get("window_title", "")

    templates = []
    skipped = 0
    total = len(click_events)

    for i, ev in enumerate(click_events):
        shot = ev["screenshot_file"]
        if not os.path.exists(shot):
            logger.warning("screenshot missing: %s", shot)
            skipped += 1
            continue

        # 检测按钮边界
        bbox = _smart_crop_around_click(shot, ev["x"], ev["y"], margin=margin)
        if "error" in bbox:
            logger.warning("boundary detection failed at step %d: %s", i, bbox["error"])
            skipped += 1
            continue

        # 裁剪 → 入库
        result = _store_template(shot, bbox, macro_name, i, window_title)
        if result is None:
            skipped += 1
            continue

        templates.append({
            "step": i,
            "target_text": result["target_text"],
            "template_path": result["template_path"],
            "bbox": result["bbox"],
            "signature": result.get("signature", ""),
            "click_x": ev["x"],
            "click_y": ev["y"],
        })

    # 清理截图缓存
    shot_dir = data.get("screenshot_dir", "")
    if cleanup_screenshots and shot_dir and os.path.isdir(shot_dir):
        try:
            shutil.rmtree(shot_dir)
            logger.info("cleaned up screenshot dir: %s", shot_dir)
        except Exception as e:
            logger.warning("cleanup failed: %s", e)

    return {
        "success": True,
        "result": {
            "total": total,
            "extracted": len(templates),
            "skipped": skipped,
            "templates": templates,
        },
    }
