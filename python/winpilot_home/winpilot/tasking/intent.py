"""Low-latency structured intent extraction.

Regexes here create candidates.  Final intent fields are assigned only after a
relation has been found (operation -> media -> channel -> recipient), avoiding
the old behaviour where one unrelated keyword made the entire task a
communication task.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from .contracts import TaskIntent


_COMMUNICATION_VERB = re.compile(
    r"(?:发给|发送给|发送(?!给|到)|给.{0,24}?发|传给|分享到|发送到|发到|传到|send\s+to|share\s+to|send)",
    re.IGNORECASE,
)
_CHAT_OPERATION = re.compile(r"聊天|聊(?:天|聊|一下|一聊)?|回复|私信|消息|信息|chat\s+with|reply|message", re.IGNORECASE)
_CAPTURE_VERB = re.compile(r"截图|截取|捕获|capture|screenshot", re.IGNORECASE)
_DRAW_VERB = re.compile(r"绘制|(?<!刚)(?<!已)画(?!图(?:里|中|应用|窗口|软件)|板|好)|draw|paint|sketch", re.IGNORECASE)
_SAVE_VERB = re.compile(r"保存|另存为|导出|save|export", re.IGNORECASE)
_TEXT_MEDIA = re.compile(r"消息|信息|文字|文本|message|text", re.IGNORECASE)
_IMAGE_MEDIA = re.compile(r"截图|截取|图片|照片|图像|image|photo|screenshot|capture", re.IGNORECASE)
_FILE_MEDIA = re.compile(r"文件|附件|文档|file|attachment|document", re.IGNORECASE)
_DRAFT = re.compile(r"不要发送|别发送|只输入|草稿|draft\s+only|do\s+not\s+send", re.IGNORECASE)
_SHARE = re.compile(r"分享|share", re.IGNORECASE)

_CHANNEL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("QQ", re.compile(r"(?<![A-Za-z])QQ(?![A-Za-z])", re.IGNORECASE)),
    ("WeChat", re.compile(r"微信|WeChat", re.IGNORECASE)),
    ("Email", re.compile(r"邮件|邮箱|email|mail", re.IGNORECASE)),
)
_SOURCE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Paint", re.compile(r"画板|画图|MS\s*Paint|Paint(?:\s+app)?", re.IGNORECASE)),
    ("Whiteboard", re.compile(r"白板|Whiteboard", re.IGNORECASE)),
)


def _strip_recipient(value: str) -> str:
    value = value.strip(" \t\r\n，。,.：:;；的")
    value = value.strip("'\"“”‘’「」『』")
    value = re.split(r"(?:然后|并且|并|后|，|。|,|\.|截图|图片|照片)", value, maxsplit=1)[0]
    return value.strip(" \t\r\n，。,.：:;；的'\"“”‘’「」『』")[:80]


def _recipient_after_channel(text: str, channel: str) -> str:
    escaped = {
        "WeChat": r"(?:微信|WeChat)",
        "Email": r"(?:邮件|邮箱|email|mail)",
    }.get(channel, re.escape(channel))
    patterns = (
        rf"{escaped}\s*(?:的|里|中)\s*[‘'\"“]?([^’'\"”，。]{{1,40}})",
        rf"(?:发送到|发到|传到|分享到)\s*{escaped}\s*(?:的|里|中)?\s*[‘'\"“]?([^’'\"”，。]{{1,40}})",
        rf"(?:用|通过|via)\s*{escaped}\s*给\s*([^，。,.]{{1,30}}?)(?=发|发送|传|分享)",
        rf"(?:用|通过|via)\s*{escaped}\s*(?:发给|发送给|给|传给|分享到)\s*([^，。,.]{{1,40}})",
        rf"(?:用|通过|via)\s*{escaped}\s*(?:找)?\s*([^，。,.]{{1,30}}?)(?=聊天|聊|chat)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            candidate = _strip_recipient(match.group(1))
            if candidate:
                return candidate
    return ""


def _recipient_from_relation(text: str) -> str:
    patterns = (
        r"(?:发给|发送给|传给|分享到)\s*([^，。,.]{1,40})",
        r"给\s*([^，。,.]{1,30})\s*(?:发|发送|传|分享)",
        r"(?:send|share)\s+(?:it\s+|the\s+(?:image|file|screenshot)\s+)?to\s+([^,.;]{1,40})",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            candidate = _strip_recipient(match.group(1))
            if candidate:
                return candidate
    return ""


def _drawing_subject(text: str) -> str:
    match = re.search(
        r"(?:绘制|(?<!刚)(?<!已)画)\s*(?!板|图(?:里|中|应用|软件))([^，。,.]{1,48}?)(?=然后|并|再|后|截图|截取|保存|[，。,.]|$)",
        text,
        re.IGNORECASE,
    )
    if not match:
        match = re.search(r"(?:draw|paint|sketch)\s+(?:an?\s+|the\s+)?([^,.;]{1,48})", text, re.IGNORECASE)
    return _strip_recipient(match.group(1)) if match else ""


class TaskIntentParser:
    """Extract a stable semantic frame without adding an LLM round trip.

    An optional semantic extractor can fill fields that the structural grammar
    leaves ambiguous.  Its output is merged only into empty/ambiguous slots.
    """

    def __init__(self, semantic_extractor: Callable[[str], dict[str, Any]] | None = None) -> None:
        self.semantic_extractor = semantic_extractor

    def parse(self, task: str) -> TaskIntent:
        text = str(task or "").strip()
        operations: list[str] = []
        if _DRAW_VERB.search(text):
            operations.append("draw")
        if _CAPTURE_VERB.search(text):
            operations.append("capture")
        if _SAVE_VERB.search(text):
            operations.append("save")
        communication_relation = _COMMUNICATION_VERB.search(text)
        chat_relation = _CHAT_OPERATION.search(text)
        if communication_relation or chat_relation:
            operations.append("send")

        channel = ""
        for name, pattern in _CHANNEL_PATTERNS:
            if pattern.search(text):
                channel = name
                break
        source = ""
        for name, pattern in _SOURCE_PATTERNS:
            if pattern.search(text):
                source = name
                break
        # Keep capture intent separate from the source application.  A user
        # asking for the application/window gets the full window; a drawing
        # screenshot without that qualifier gets the detected work surface.
        capture_scope = "window" if re.search(
            r"窗口|应用(?:窗口)?|整个(?:画板|画图|应用)|window|app(?:lication)?\s+window",
            text,
            re.IGNORECASE,
        ) else ("canvas" if source and _CAPTURE_VERB.search(text) and _DRAW_VERB.search(text) else "window")

        if _IMAGE_MEDIA.search(text):
            media_type = "image"
        elif _FILE_MEDIA.search(text):
            media_type = "file"
        elif _TEXT_MEDIA.search(text) or communication_relation or chat_relation:
            media_type = "text"
        else:
            media_type = "none"

        recipient = _recipient_after_channel(text, channel) if channel else ""
        recipient = recipient or _recipient_from_relation(text)
        destination = channel
        send_mode = "draft" if _DRAFT.search(text) else ("share" if _SHARE.search(text) else "send")
        subject = _drawing_subject(text)
        quality = "detailed" if re.search(r"好看|精细|细致|漂亮|detailed|beautiful|polished", text, re.IGNORECASE) else "normal"

        ambiguous: list[str] = []
        if "send" in operations and not channel:
            ambiguous.append("channel")
        if "send" in operations and not recipient:
            ambiguous.append("recipient")
        if "draw" in operations and not subject:
            ambiguous.append("subject")

        provenance = {
            "operation": "structural_operation_relation",
            "media_type": "media_role",
            "channel": "destination_entity",
            "recipient": "communication_relation",
            "source": "source_application_entity",
        }
        extracted: dict[str, Any] = {}
        if ambiguous and self.semantic_extractor is not None:
            try:
                extracted = dict(self.semantic_extractor(text) or {})
            except Exception:
                extracted = {}
            for field in tuple(ambiguous):
                value = str(extracted.get(field) or "").strip()
                if not value:
                    continue
                if field == "channel":
                    channel = value
                    destination = destination or value
                elif field == "recipient":
                    recipient = value
                elif field == "subject":
                    subject = value
                ambiguous.remove(field)
                provenance[field] = "semantic_extractor"

        signals = sum(bool(value) for value in (operations, media_type != "none", channel, recipient, source, subject))
        confidence = min(0.99, 0.42 + signals * 0.09 - len(ambiguous) * 0.08)
        return TaskIntent(
            original_text=text,
            operation=list(dict.fromkeys(operations)),
            media_type=media_type,
            channel=channel,
            recipient=recipient,
            source=source,
            capture_scope=capture_scope,
            destination=destination,
            send_mode=send_mode,
            subject=subject,
            quality=quality,
            confidence=max(0.0, confidence),
            ambiguous_fields=ambiguous,
            provenance=provenance,
        )
