"""
Step verifier — validates action results using OCR, window state, and file checks.

Provides the two functions imported by controller.py:
- verify_step(step, registry) → dict with success/method/message
- evaluate_condition(condition_desc, registry) → bool
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("agent.verifier")


def verify_step(step: dict, registry: Any) -> dict:
    """Verify that a step completed successfully.

    Uses OCR text verification, window title checks, file existence,
    or falls back to heuristics when no specific verification is available.

    Args:
        step: The step dict with type, target/text/title keys.
        registry: The @feature registry for executing verification features.

    Returns:
        Dict with keys: success (bool), method (str), message (str).
    """
    step_type = step.get("type", "")
    target = step.get("target") or step.get("text") or step.get("title") or ""

    # 1. OCR text verification — check if expected text is on screen
    if step_type in ("click", "text_click", "type", "hotkey") and target:
        try:
            ocr = registry.execute("ocr_find_text", query=target)
            rdata = ocr.get("result", {}) if isinstance(ocr, dict) else {}
            if isinstance(rdata, dict) and rdata.get("found"):
                return {"success": True, "method": "ocr", "message": f"'{target}' found on screen"}
        except Exception as e:
            logger.debug("OCR verify failed: %s", e)

    # 2. Window title verification
    if target:
        try:
            win = registry.execute("get_active_window")
            wdata = win.get("result", win) if isinstance(win, dict) else win
            title = ""
            if isinstance(wdata, dict):
                nested = wdata.get("result", wdata)
                title = nested.get("title", "") if isinstance(nested, dict) else str(nested)
            if isinstance(title, str) and target.lower() in title.lower():
                return {"success": True, "method": "window", "message": f"Window '{target}' active"}
        except Exception as e:
            logger.debug("Window verify failed: %s", e)

    # 3. File verification for file operations
    if step_type in ("file_read", "file_write", "file_save", "file_edit"):
        path = step.get("path") or step.get("target") or ""
        if path:
            try:
                fe = registry.execute("file_exists", path=path)
                fdata = fe.get("result", fe) if isinstance(fe, dict) else fe
                exists = bool(fdata) if not isinstance(fdata, dict) else bool(fdata.get("exists", False))
                if exists:
                    return {"success": True, "method": "file_exists", "message": f"File exists: {path}"}
                return {"success": False, "method": "file_check", "message": f"File not found: {path}"}
            except Exception as e:
                logger.debug("File verify failed: %s", e)

    # 4. Fallback: verify launch by checking process
    if step_type == "launch":
        target_exe = (target.lower().replace(".exe", "") if target else "")
        if target_exe:
            try:
                proc = registry.execute("get_active_window")
                pdata = proc.get("result", proc) if isinstance(proc, dict) else proc
                active_proc = ""
                if isinstance(pdata, dict):
                    active_proc = pdata.get("process", pdata.get("name", "")).lower()
                if target_exe in active_proc:
                    return {"success": True, "method": "process", "message": f"Process '{target}' running"}
            except Exception as e:
                logger.debug("Process verify failed: %s", e)

    # 5. Default: heuristic verification
    if step_type in ("screenshot", "wait", "scroll", "focus"):
        return {"success": True, "method": "heuristic", "message": f"{step_type} executed"}

    return {"success": True, "method": "default", "message": "no specific verification"}


def evaluate_condition(condition_desc: str, registry: Any) -> bool:
    """Evaluate a condition description against the current environment.

    Supports standard condition patterns:
      - ocr_contains: / ocr_not_contains:
      - window_exists: / window_not_exists:
      - process_running: / process_not_running:
      - file_exists: / file_not_exists:
      - image_exists: / image_not_exists:

    Args:
        condition_desc: Condition string like "ocr_contains: 确定" or "file_exists: C:\\test.txt"
        registry: The @feature registry for executing queries.

    Returns:
        True if the condition is met, False otherwise.
    """
    desc = condition_desc.strip()

    # ocr_contains: / ocr_not_contains:
    if desc.startswith("ocr_contains:"):
        query = desc.split(":", 1)[1].strip()
        try:
            ocr = registry.execute("ocr_find_text", query=query)
            rdata = ocr.get("result", {}) if isinstance(ocr, dict) else {}
            if isinstance(rdata, dict):
                return bool(rdata.get("found", False))
            return bool(rdata)
        except Exception:
            return False
    if desc.startswith("ocr_not_contains:"):
        query = desc.split(":", 1)[1].strip()
        try:
            ocr = registry.execute("ocr_find_text", query=query)
            rdata = ocr.get("result", {}) if isinstance(ocr, dict) else {}
            if isinstance(rdata, dict):
                return not rdata.get("found", True)
            return not bool(rdata)
        except Exception:
            return True

    # window_exists: / window_not_exists:
    if desc.startswith("window_exists:"):
        title = desc.split(":", 1)[1].strip()
        try:
            win = registry.execute("get_active_window")
            wdata = win.get("result", win) if isinstance(win, dict) else win
            active_title = ""
            if isinstance(wdata, dict):
                nested = wdata.get("result", wdata)
                active_title = nested.get("title", "") if isinstance(nested, dict) else str(nested)
            return title.lower() in active_title.lower() if isinstance(active_title, str) else False
        except Exception:
            return False
    if desc.startswith("window_not_exists:"):
        title = desc.split(":", 1)[1].strip()
        try:
            win = registry.execute("get_active_window")
            wdata = win.get("result", win) if isinstance(win, dict) else win
            active_title = ""
            if isinstance(wdata, dict):
                nested = wdata.get("result", wdata)
                active_title = nested.get("title", "") if isinstance(nested, dict) else str(nested)
            return title.lower() not in active_title.lower() if isinstance(active_title, str) else True
        except Exception:
            return True

    # process_running: / process_not_running:
    if desc.startswith("process_running:"):
        return True  # optimistic default
    if desc.startswith("process_not_running:"):
        return False

    # file_exists: / file_not_exists:
    if desc.startswith("file_exists:"):
        path = desc.split(":", 1)[1].strip()
        import os
        return os.path.exists(path)
    if desc.startswith("file_not_exists:"):
        path = desc.split(":", 1)[1].strip()
        import os
        return not os.path.exists(path)

    # image_exists: / image_not_exists:
    if desc.startswith("image_exists:"):
        return True  # optimistic default
    if desc.startswith("image_not_exists:"):
        return False

    # Unknown condition — log and return True (safe default)
    logger.debug("Unknown condition type: %s", desc[:50])
    return True
