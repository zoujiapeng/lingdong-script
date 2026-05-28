"""
视觉定位器 (Visual Locator) — 两步定位策略：

1. 经验优先: 先查 ExperienceDB，如果有模板就用 OpenCV 匹配屏幕，返回坐标
2. VLM 兜底: 如果无经验或匹配失败 + 多模态已开启，调用 VLM API 分析截图定位
3. 学习保存: VLM 定位成功后自动裁剪小图存入 ExperienceDB

依赖: opencv-python (pip install opencv-python)
"""
import base64
import json
import logging
import os
import time
from typing import Optional

logger = logging.getLogger("agent.visual_locator")

# ── OpenCV 模板匹配 ──────────────────────────────────────────

_HAS_CV2 = False
try:
    import cv2
    import numpy as np
    _HAS_CV2 = True
except ImportError:
    pass


def template_match(template_path: str, screenshot_path: str,
                   threshold: float = 0.75) -> Optional[dict]:
    """Use OpenCV template matching to find template in screenshot.

    Returns:
        {x, y, w, h, center_x, center_y, confidence} or None
    """
    if not _HAS_CV2:
        logger.warning("OpenCV not installed, cannot use template matching")
        return None
    if not os.path.exists(template_path) or not os.path.exists(screenshot_path):
        return None

    try:
        screen = cv2.imread(screenshot_path)
        template = cv2.imread(template_path)
        if screen is None or template is None:
            return None

        h, w = template.shape[:2]
        result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        if max_val < threshold:
            logger.debug("Template match below threshold: %.3f < %.2f", max_val, threshold)
            return None

        x, y = max_loc
        return {
            "x": x,
            "y": y,
            "w": w,
            "h": h,
            "center_x": x + w // 2,
            "center_y": y + h // 2,
            "confidence": round(float(max_val), 4),
            "method": "opencv",
        }
    except Exception as e:
        logger.warning("Template match failed: %s", e)
        return None


# ── VLM 视觉定位 ─────────────────────────────────────────────

def vlm_locate(screenshot_path: str, description: str,
               api_key: str,
               api_base: str = "",
               model: str = "",
               timeout: int = 30) -> Optional[dict]:
    api_base = api_base or os.environ.get("OPENAI_API_BASE", "https://api.deepseek.com/v1")
    model = model or os.environ.get("VLM_MODEL", "deepseek-chat")
    """Use VLM to locate an element by description in a screenshot.

    Sends the screenshot image + text description to a multimodal LLM
    and asks it to return the element's bounding box.

    Returns:
        {x, y, w, h, center_x, center_y, confidence} or None
    """
    if not os.path.exists(screenshot_path):
        return None

    try:
        import requests as _req
    except ImportError:
        logger.warning("requests not installed, cannot call VLM API")
        return None

    # Read and encode screenshot
    with open(screenshot_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode("utf-8")

    # Determine image mime type
    ext = os.path.splitext(screenshot_path)[1].lower()
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "bmp": "image/bmp"}.get(ext, "image/png")

    system_prompt = """你是一个屏幕分析助手。给定一张屏幕截图和一段元素描述，你需要返回该元素的精确位置。

分析规则:
1. 仔细查看截图，找到描述的目标元素
2. 返回该元素在截图中的精确边界框 (bounding box) 坐标
3. 坐标单位是像素，原点在截图左上角

请以 JSON 格式回复，不要包含其他文字:
{"x": 100, "y": 200, "w": 80, "h": 30, "label": "搜索按钮", "confidence": 0.95}"""

    try:
        resp = _req.post(
            f"{api_base.rstrip('/')}/chat/completions",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": [
                        {"type": "text", "text": f"请找到截图中的: {description}"},
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{img_b64}"}},
                    ]},
                ],
                "response_format": {"type": "json_object"},
                "max_tokens": 256,
                "temperature": 0.1,
            },
            timeout=timeout,
        )
        if resp.status_code == 401:
            logger.error("VLM API key invalid")
            return None
        if resp.status_code == 400 and "not support" in (resp.text or "").lower():
            logger.warning("Model does not support multimodal input")
            return None
        resp.raise_for_status()

        raw = resp.json()["choices"][0]["message"]["content"]
        parsed = json.loads(raw)
        x = parsed.get("x", 0)
        y = parsed.get("y", 0)
        w = parsed.get("w", 0)
        h = parsed.get("h", 0)

        if w <= 0 or h <= 0:
            logger.warning("VLM returned invalid bbox: %s", raw)
            return None

        return {
            "x": int(x),
            "y": int(y),
            "w": int(w),
            "h": int(h),
            "center_x": int(x + w // 2),
            "center_y": int(y + h // 2),
            "confidence": parsed.get("confidence", 0.7),
            "method": "vlm",
            "label": parsed.get("label", description),
        }
    except _req.exceptions.Timeout:
        logger.warning("VLM API timeout (%ds)", timeout)
        return None
    except json.JSONDecodeError:
        logger.warning("VLM returned invalid JSON")
        return None
    except Exception as e:
        logger.warning("VLM locate failed: %s", e)
        return None


# ── 截图裁剪 ─────────────────────────────────────────────────

def crop_and_save(screenshot_path: str, bbox: dict,
                  output_path: str) -> Optional[str]:
    """Crop a region from screenshot and save as template image.

    Args:
        screenshot_path: path to full screenshot
        bbox: {x, y, w, h} region to crop
        output_path: where to save the crop

    Returns:
        output_path if successful, None otherwise
    """
    if not _HAS_CV2:
        # Fallback: use PIL
        try:
            from PIL import Image
            img = Image.open(screenshot_path)
            crop = img.crop((bbox["x"], bbox["y"],
                             bbox["x"] + bbox["w"],
                             bbox["y"] + bbox["h"]))
            crop.save(output_path)
            return output_path
        except ImportError:
            logger.warning("Neither OpenCV nor PIL available for cropping")
            return None

    try:
        img = cv2.imread(screenshot_path)
        if img is None:
            return None
        x, y, w, h = int(bbox["x"]), int(bbox["y"]), int(bbox["w"]), int(bbox["h"])
        crop = img[y:y+h, x:x+w]
        cv2.imwrite(output_path, crop)
        return output_path
    except Exception as e:
        logger.warning("Crop failed: %s", e)
        return None


# ── 统一入口 ──────────────────────────────────────────────────

def locate(target_text: str, step_type: str, screenshot_path: str,
           use_vlm: bool = False, vlm_config: Optional[dict] = None,
           window_title: str = "", threshold: float = 0.75) -> dict:
    """Unified locate: experience first → VLM fallback → learn.

    Args:
        target_text: element to find (e.g. "搜索", "登录按钮")
        step_type: step type (e.g. "click", "type")
        screenshot_path: current screenshot for matching/cropping
        use_vlm: whether to fall back to VLM
        vlm_config: {api_key, api_base, model} for VLM
        window_title: context for experience lookup
        threshold: OpenCV match threshold

    Returns:
        {success, result: {x, y, w, h, center_x, center_y, confidence, method}}
        or {success: false, error: "..."}
    """
    from agent import experience_db as exp

    # 1. Try experience DB
    experience = exp.find(step_type, target_text, window_title, min_confidence=threshold)
    if experience:
        tmpl_path = experience.get("template_path", "")
        if os.path.exists(tmpl_path):
            logger.info("Experience hit: %s -> %s (conf=%.3f)", target_text, tmpl_path, experience.get("confidence", 0))
            result = template_match(tmpl_path, screenshot_path, threshold=threshold)
            if result:
                result["experience_sig"] = experience.get("signature", "")
                return {"success": True, "result": result}

    # 2. VLM fallback
    if use_vlm and vlm_config and vlm_config.get("api_key"):
        logger.info("VLM locating: %s", target_text)
        result = vlm_locate(screenshot_path, target_text, **vlm_config)
        if result:
            # Save as experience
            tmpl_path = exp.get_template_path("", f"learn_{step_type}_{int(time.time())}.png")
            crop_and_save(screenshot_path, result, tmpl_path)
            sig = exp.store(step_type, target_text, tmpl_path, result,
                            result.get("confidence", 0.7), method="vlm",
                            window_title=window_title)
            result["experience_sig"] = sig
            logger.info("VLM located + saved experience: %s (sig=%s)", target_text, sig)
            return {"success": True, "result": result}

    # 3. Not found
    return {"success": False, "error": f"无法定位: {target_text}"}


def has_opencv() -> bool:
    return _HAS_CV2
