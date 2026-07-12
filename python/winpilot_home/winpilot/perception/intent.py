"""Task-intent hints that change perception strategy.

Pure string heuristics, no heavy deps, so both the perception layer
(``fusion``) and the agent layer (``tools``) can import it without a layering
inversion.

Perception modes:
- "normal"  : default; UIA-rich windows may skip OCR/VLM for speed.
- "pixel_text": office/document surfaces; run UIA and OCR together, using a
                 VLM only when OCR supplies no readable content.
- "visual"  : chat/canvas/mirror tasks; OCR always runs; VLM runs as an
              assist dimension when configured (not just as rescue).
- "vlm_first": future; VLM leads, others fill gaps. Reserved for explicit
              user config or fully pixel-rendered surfaces.
"""
from __future__ import annotations

# Drawing / canvas: the meaningful output is painted pixels, invisible to UIA.
_DRAWING_TERMS = (
    "draw", "drawing", "paint", "sketch", "canvas", "whiteboard", "doodle",
    "画", "画图", "绘图", "涂鸦", "画板",
    "白板", "颜色", "color", "手绘",
)

# Chat / messaging: bubbles and composer text are often OCR-only on modern
# chat clients (QQNT / WeChat render messages on a custom surface).
_CHAT_TERMS = (
    "发信息", "发消息", "发送", "回复",
    "私信", "聊天", "邮件", "message", "send", "reply",
    "email", "chat", "微信", "qq", "会话",
)

# Phone mirror / cast: the phone screen is a single image to the host OS.
_MIRROR_TERMS = (
    "手机", "投屏", "镜像", "我的设备",
    "我的电脑", "mirror", "scrcpy", "cast",
)

# Media frames the user wants understood (only when paired with a look/read
# verb — handled by the caller; kept here for completeness/future use).
_MEDIA_TERMS = (
    "视频", "截图", "video", "screenshot",
)

# Office editors frequently expose ribbon chrome and stale document-window
# nodes through UIA while the actual page/sheet remains pixel-rendered.
_PIXEL_TEXT_TERMS = (
    "spreadsheet", "workbook", "worksheet", "excel", "wps spreadsheets",
    "电子表格", "工作簿", "wps表格", "wps 表格",
    "wps writer", "microsoft word", "word document", "wps文字", "wps 文字",
    "powerpoint", "wps presentation", "幻灯片", "wps演示", "wps 演示",
)


def perception_mode(task: str, window_title: str = "") -> str:
    """Return the perception strategy mode for a task.

    "normal"  — default; skip OCR/VLM on UIA-rich windows for speed.
    "pixel_text" — office/document UI: UIA and OCR run together; VLM is
                   reserved for an OCR-empty fallback.
    "visual"  — chat / canvas / mirror: OCR always runs; VLM assists when
                configured (not just as rescue ladder).
    "vlm_first" — reserved for future explicit VLM-led perception.

    Combines the task text with the current window title so that a generic
    follow-up like "继续" still keeps the right mode while a QQ/Paint window
    is in focus.
    """
    text = f"{task or ''} {window_title or ''}".casefold()
    if any(term in text for term in (*_DRAWING_TERMS, *_CHAT_TERMS, *_MIRROR_TERMS)):
        return "visual"
    if any(term in text for term in _PIXEL_TEXT_TERMS):
        return "pixel_text"
    return "normal"


def vision_priority_task(task: str, window_title: str = "") -> bool:
    """Legacy bool accessor — True when perception_mode returns 'visual'.

    Kept for call-sites that haven't migrated yet. New code should call
    perception_mode() directly.
    """
    return perception_mode(task, window_title) != "normal"
