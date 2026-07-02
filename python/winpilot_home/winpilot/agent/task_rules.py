"""Task classification and guard rules for the WinPilot GUI runner.

These helpers are intentionally pure/string-based. Keep them out of
``loop.py`` so the runner remains focused on LLM orchestration.
"""
from __future__ import annotations

import re


_OPEN_INTENT = re.compile(r"^(打开|开启|启动|运行|玩|播放|关闭|切换|最小化|最大化|登录|搜索)")
_CODING_STRONG = (
    "调试", "debug", "重构", "refactor", "仓库", "repo", "git", "github", "clone",
    "克隆", "bug", "报错", "异常", "traceback", "编译", "build ", "单元测试", "pytest",
    "npm", "pnpm", "yarn", "cargo", "dotnet", ".net", "代码", "源码", "函数", "脚本",
    "这个项目", "项目里", "写代码", "写个程序", "写一个程序", "实现一个", "api 接口", "接口",
    "数据库", "前端", "后端", "部署",
)
_CODING_BUILD = re.compile(
    r"(做|写|开发|实现|生成|创建|编写|搭建|搭|建|帮我做|帮我写)"
    r"[^。,，;；\n]{0,12}"
    r"(游戏|小游戏|网页|网站|页面|程序|软件|脚本|应用|小程序|html|网址|界面|插件|工具|"
    r"爬虫|机器人|\bbot\b|\bapp\b|计算器|时钟|待办|todo|博客|商城|登录页|表单)",
    re.IGNORECASE,
)
_COMMUNICATION_HINTS = (
    "发信息", "发消息", "发送", "回复", "私信", "聊天", "邮件",
    "message", "send", "reply", "email", "chat",
)
_DRAWING_TASK_RE = re.compile(
    r"draw|drawing|paint|sketch|canvas|whiteboard|"
    r"\u753b|\u753b\u56fe|\u7ed8\u56fe|\u6d82\u9e26|\u753b\u677f|\u767d\u677f",
    re.IGNORECASE,
)
_DESKTOP_ICON_DRAG_RE = re.compile(
    r"desktop|drag|move|icon|folder|"
    r"\u684c\u9762|\u62d6\u52a8|\u79fb\u52a8|\u56fe\u6807|\u6587\u4ef6\u5939|\u6574\u7406|\u6392\u5217",
    re.IGNORECASE,
)
_DRAWING_COLOR_TERMS = {
    "red": ("red", "\u7ea2", "\u7ea2\u8272"),
    "blue": ("blue", "\u84dd", "\u84dd\u8272"),
    "green": ("green", "\u7eff", "\u7eff\u8272"),
    "yellow": ("yellow", "\u9ec4", "\u9ec4\u8272"),
    "orange": ("orange", "\u6a59", "\u6a59\u8272"),
    "purple": ("purple", "\u7d2b", "\u7d2b\u8272"),
    "black": ("black", "\u9ed1", "\u9ed1\u8272"),
}


def _looks_like_contact_list(text: str) -> bool:
    """Heuristic: contacts/session list rather than an open chat window."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return True
    long_lines = sum(1 for line in lines if len(line) > 30)
    return long_lines < 3


def _has_explicit_gui_only_marker(task: str) -> bool:
    text = (task or "").casefold()
    compact = re.sub(r"\s+", "", text)
    return (
        bool(re.search(r"\bgui[- ]only\b", text))
        or "must use gui" in text
        or "use gui" in text
        or "through gui" in text
        or "via gui" in text
        or "not use command line" in text
        or "do not use command line" in text
        or "no command line" in text
        or "\u5fc5\u987b\u8d70gui" in compact
        or "\u5fc5\u987b\u901agui" in compact
        or "\u901agui\u64cd\u4f5c" in compact
        or "\u901a\u8fc7gui\u64cd\u4f5c" in compact
        or "\u4e3b\u8981\u64cd\u4f5c\u5fc5\u987b\u8d70gui" in compact
        or "\u5168\u7a0b\u4e3b\u8981\u64cd\u4f5c\u5fc5\u987b\u8d70gui" in compact
        or "\u4e0d\u8981\u7528\u547d\u4ee4\u884c" in compact
        or "\u4e0d\u8981\u4f7f\u7528\u547d\u4ee4\u884c" in compact
        or "\u4e0d\u80fd\u7528\u547d\u4ee4\u884c" in compact
        or "\u4e0d\u4f7f\u7528\u547d\u4ee4\u884c" in compact
        or "\u4e0d\u8981\u7528\u811a\u672c" in compact
        or "\u4e0d\u80fd\u7528\u811a\u672c" in compact
        or "\u4e0d\u8981\u4f7f\u7528\u811a\u672c" in compact
    )


def _is_gui_only_task(task: str) -> bool:
    text = (task or "").casefold()
    return (
        _has_explicit_gui_only_marker(task)
        or
        bool(re.search(r"\bgui[- ]only\b", text))
        or "must use gui" in text
        or "gui方式" in text
        or "gui 方式" in text
        or "可见gui" in text
        or "可见 gui" in text
        or "通过可见" in text
        or "必须通过可见" in text
        or "必须通过gui" in text
        or "必须通过 gui" in text
        or "不要用脚本" in text
        or "不能用脚本" in text
        or "别用脚本" in text
        or "不要使用脚本" in text
    )


def _is_drawing_task(task: str) -> bool:
    return bool(_DRAWING_TASK_RE.search(task or ""))


def _is_desktop_icon_drag_task(task: str) -> bool:
    text = task or ""
    low = text.casefold()
    has_desktop = "desktop" in low or "\u684c\u9762" in text
    has_drag = any(
        term in low for term in ("drag", "move", "icon", "folder")
    ) or any(term in text for term in ("\u62d6\u52a8", "\u79fb\u52a8", "\u56fe\u6807", "\u6587\u4ef6\u5939", "\u6574\u7406", "\u6392\u5217"))
    return has_desktop and has_drag and bool(_DESKTOP_ICON_DRAG_RE.search(text))


def _desktop_icon_target_names(task: str) -> list[str]:
    text = task or ""
    names = _quoted_payloads(text)
    patterns = (
        r"\u684c\u9762\u4e0a\u7684\s*([^\s\uff0c,\u3002\uff1b;]{2,80})",
        r"desktop(?:\s+icon|\s+folder)?\s+([A-Za-z0-9_. -]{2,80})",
    )
    for pattern in patterns:
        for match in re.findall(pattern, text, flags=re.IGNORECASE):
            value = str(match).strip().strip('"')
            value = re.sub(r"(\u6587\u4ef6\u5939|\u56fe\u6807|folder|icon)$", "", value, flags=re.IGNORECASE).strip()
            if value:
                names.append(value)
    for token in re.findall(r"\b[A-Za-z0-9][A-Za-z0-9_.-]{2,}\b", text):
        if "_" in token or "." in token:
            names.append(token)
    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        key = name.casefold()
        if key not in seen:
            out.append(name)
            seen.add(key)
    return out


def _required_image_paths(task: str) -> list[str]:
    paths = re.findall(
        r"[A-Za-z]:\\[^\r\n\"'<>|]+?\.(?:png|jpg|jpeg|bmp|gif)",
        task or "",
        flags=re.IGNORECASE,
    )
    cleaned: list[str] = []
    seen: set[str] = set()
    for path in paths:
        value = path.strip().rstrip(".,;:，。；：)")
        key = value.casefold()
        if value and key not in seen:
            cleaned.append(value)
            seen.add(key)
    return cleaned


def _required_drawing_colors(task: str) -> list[str]:
    text = (task or "").casefold()
    colors: list[str] = []
    for color, terms in _DRAWING_COLOR_TERMS.items():
        if any(term in text for term in terms):
            colors.append(color)
    return colors


def _gui_only_shell_allowed(command: str) -> bool:
    low = (command or "").casefold()
    verification_markers = (
        "test-path", "get-content", "select-string",
        "get-item", "get-childitem", "resolve-path",
    )
    mutation_or_ui_automation = (
        "set-content", "add-content", "out-file", "new-item", "copy-item",
        "move-item", "remove-item", "start-process", "sendkeys", "clipboard",
        "windows.automation", "wscript.shell", "shell.application",
    )
    execution_or_automation = (
        "powershell -file", "-executionpolicy", ".ps1", ".bat", ".cmd",
        "python ", "python.exe", "py ", "node ", "node.exe", "wscript",
        "cscript", "mshta", "rundll32", "reg add", "reg delete",
        "pyautogui", "pywinauto", "subprocess", "import ",
    )
    return (
        any(marker in low for marker in verification_markers)
        and not any(marker in low for marker in mutation_or_ui_automation)
        and not any(marker in low for marker in execution_or_automation)
    )


def _is_coding_task(task: str) -> bool:
    text = (task or "").strip()
    low = text.lower()
    if _OPEN_INTENT.match(text):
        return False
    if any(k in low for k in _CODING_STRONG):
        return True
    return bool(_CODING_BUILD.search(text))


def _is_communication_task(task: str) -> bool:
    text = (task or "").lower()
    return any(hint in text for hint in _COMMUNICATION_HINTS)


def _quoted_payloads(task: str) -> list[str]:
    matches = re.findall(r'"([^"]{1,120})"|“([^”]{1,120})”|「([^」]{1,120})」', task or "")
    payloads: list[str] = []
    for groups in matches:
        value = next((group for group in groups if group), "").strip()
        if value:
            payloads.append(value)
    return payloads


def _required_visible_payloads(task: str) -> list[str]:
    """Extract literal text that a GUI writing/typing task must make visible."""
    text = task or ""
    low = text.lower()
    line_re = (
        r"\u7b2c[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341\d]+"
        r"\u884c\s*[=:\uff1a\uff1d]\s*([^\uff0c,\u3002\uff1b;\r\n]+)"
    )
    payloads = [match.strip() for match in re.findall(line_re, text) if match.strip()]
    if any(trigger in low for trigger in ("type", "input", "write", "fill")):
        payloads.extend(_quoted_payloads(text))
    seen: set[str] = set()
    unique: list[str] = []
    for payload in payloads:
        payload = payload.strip()
        if payload and payload not in seen:
            unique.append(payload)
            seen.add(payload)
    return unique


def _gui_search_payloads(task: str) -> list[str]:
    text = task or ""
    payloads: list[str] = []
    patterns = (
        r"(?:search|look up)\s+([A-Za-z0-9][^,.;\r\n]{1,80})",
        r"(?:\u641c\u7d22|\u67e5\u627e)\s*([^\uff0c,\u3002\uff1b;\r\n]{1,80})",
    )
    for pattern in patterns:
        for match in re.findall(pattern, text, flags=re.IGNORECASE):
            value = str(match).strip()
            value = re.split(
                r"\s+(?:and|then|in|on|at)\s+|(?:\u5e76|\u7136\u540e|\u6253\u5f00|\u64ad\u653e)",
                value,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0].strip()
            if value:
                payloads.append(value.strip('"“” '))
    seen: set[str] = set()
    out: list[str] = []
    for payload in payloads:
        key = payload.casefold()
        if key not in seen:
            out.append(payload)
            seen.add(key)
    return out


def _is_gui_search_task(task: str) -> bool:
    text = (task or "").lower()
    wants_open = any(k in text for k in ("open", "打开", "启动", "进入"))
    wants_search = any(k in text for k in ("search", "look up", "搜索", "查找"))
    app_surface = any(k in text for k in (
        "client", "store", "app", "steam", "douyin", "抖音", "网易云", "music",
        "客户端", "商店", "应用", "页面",
    ))
    return wants_open and wants_search and app_surface


def _is_store_search_task(task: str) -> bool:
    text = (task or "").lower()
    return _is_gui_search_task(task) and any(k in text for k in (
        "store", "shop", "\u5546\u5e97", "\u9875\u9762", "page",
    ))


def _blocks_store_search_action(task: str, tool_name: str, args: dict) -> str:
    if not _is_store_search_task(task):
        return ""
    command = str(args.get("command") or args.get("target") or "").casefold()
    if tool_name in {"launch", "run_shell"} and any(
        bad in command for bad in ("rungameid", "install/", "steam://run", "steam://install")
    ):
        return (
            "Store/search navigation task blocked a launch/install/run protocol. "
            "The user asked to search/open a store/page, not start or install the item. "
            "Open the app/store/search UI or a store/search deep link, then verify the "
            "requested query/page is visible."
        )
    return ""


def _is_upload_publish_task(task: str) -> bool:
    text = (task or "").casefold()
    explicit = any(k in text for k in (
        "upload", "publish", "post", "creator", "draft",
        "\u4e0a\u4f20", "\u53d1\u5e03", "\u6295\u7a3f", "\u4f5c\u54c1",
        "\u6807\u9898", "\u8349\u7a3f",
    ))
    media_file = any(ext in text for ext in (".mp4", ".mov", ".avi", ".mkv", ".webm"))
    platform_or_form = any(k in text for k in (
        "douyin", "tiktok", "creator", "upload", "publish", "post", "draft",
        "\u6296\u97f3", "\u521b\u4f5c\u8005", "\u53d1\u5e03", "\u4e0a\u4f20", "\u6807\u9898",
    ))
    return explicit or (media_file and platform_or_form)


def _blocks_upload_media_analysis(task: str, tool_name: str, args: dict) -> str:
    if not _is_upload_publish_task(task):
        return ""
    command = str(
        args.get("command")
        or args.get("script")
        or args.get("target")
        or args.get("name")
        or ""
    ).casefold()
    media_terms = (".mp4", ".mov", ".avi", ".mkv", ".webm")
    analysis_or_playback = (
        "ffprobe", "ffmpeg", "wmplayer", "windows media player", "opencv",
        "cv2", "extract frame", "showinfo", "start-process", "media player",
        "vlc", "potplayer", "mpv", "mplayer",
    )
    media_player_target = any(
        marker in command
        for marker in ("wmplayer", "windows media player", "media player", "vlc", "potplayer", "mpv")
    )
    has_media_file = any(term in command for term in media_terms)
    readonly_media_check = any(
        marker in command
        for marker in ("test-path", "get-item", "get-childitem", "resolve-path")
    )
    media_analysis_target = (
        has_media_file
        and any(marker in command for marker in analysis_or_playback)
    )
    if (
        tool_name in {"run_shell", "run_detached", "launch", "find_program"}
        and (
            media_player_target
            or media_analysis_target
            or (tool_name in {"run_shell", "run_detached", "launch"} and has_media_file and not readonly_media_check)
        )
    ):
        return (
            "Upload/publish task blocked media playback/analysis. The media file is an "
            "upload payload, not something to play, inspect, or understand. Verify the file "
            "exists if needed, then open the requested app/platform upload UI, choose the file, "
            "fill metadata, and stop before final publish."
        )
    return ""
