"""
经验数据库 (Experience DB) — JSON 文件存储，映射步骤特征 → 视觉模板。

每次执行成功时记录：
  - 截图裁剪（按钮/输入框的小图）
  - 屏幕坐标
  - 窗口上下文
  - 置信度

后续相同步骤可先用 OpenCV 模板匹配定位，无需 VLM/LLM。
"""
import hashlib
import json
import os
import time
from typing import Optional

_EXP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "experience")
_DB_PATH = os.path.join(_EXP_DIR, "db.json")
_TEMPLATES_DIR = os.path.join(_EXP_DIR, "templates")

os.makedirs(_TEMPLATES_DIR, exist_ok=True)


def _load_db() -> dict:
    if os.path.exists(_DB_PATH):
        try:
            with open(_DB_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {"v1": {}}
    return {"v1": {}}


def _save_db(db: dict):
    with open(_DB_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


def _signature(step_type: str, target_text: str, window_title: str = "") -> str:
    """Generate a stable hash key for a step."""
    raw = f"{step_type}|{target_text}|{window_title}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def store(step_type: str, target_text: str, template_path: str,
          bbox: dict, confidence: float, method: str = "vlm",
          window_title: str = "") -> str:
    """Store an experience entry.

    Args:
        step_type: e.g. "click", "type", "launch"
        target_text: e.g. "搜索", "登录", "确定"
        template_path: path to the saved crop image
        bbox: {x, y, w, h} screen coordinates
        confidence: match confidence (0-1)
        method: "vlm" or "opencv"
        window_title: context window title

    Returns:
        The signature key
    """
    sig = _signature(step_type, target_text, window_title)
    db = _load_db()
    entry = db.setdefault("v1", {}).get(sig, {})
    entry.update({
        "template_path": template_path,
        "method": method,
        "confidence": max(confidence, entry.get("confidence", 0)),
        "bbox": bbox,
        "count": entry.get("count", 0) + 1,
        "last_used": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "step_type": step_type,
        "target_text": target_text,
        "window_title": window_title,
    })
    db["v1"][sig] = entry
    _save_db(db)
    return sig


def find(step_type: str, target_text: str, window_title: str = "",
         min_confidence: float = 0.6) -> Optional[dict]:
    """Look up an experience by step signature.

    Returns:
        {template_path, bbox, confidence, method, count} or None
    """
    sig = _signature(step_type, target_text, window_title)
    db = _load_db()
    entry = db.get("v1", {}).get(sig)
    if entry and entry.get("confidence", 0) >= min_confidence:
        return entry
    return None


def find_by_sig(signature: str) -> Optional[dict]:
    """Look up by exact signature hash."""
    db = _load_db()
    return db.get("v1", {}).get(signature)


def get_template_path(signature: str, filename: str = "") -> str:
    """Get or generate a template file path for a signature."""
    if filename and os.path.isabs(filename):
        return filename
    name = filename or f"{signature}.png"
    return os.path.join(_TEMPLATES_DIR, name)


def stats() -> dict:
    """Get experience DB statistics."""
    db = _load_db()
    entries = db.get("v1", {})
    return {
        "total": len(entries),
        "by_method": {
            "vlm": sum(1 for e in entries.values() if e.get("method") == "vlm"),
            "opencv": sum(1 for e in entries.values() if e.get("method") == "opencv"),
        },
        "total_uses": sum(e.get("count", 0) for e in entries.values()),
    }


def clear():
    """Clear all experience data and templates."""
    import shutil
    if os.path.exists(_EXP_DIR):
        shutil.rmtree(_EXP_DIR)
    os.makedirs(_TEMPLATES_DIR, exist_ok=True)
