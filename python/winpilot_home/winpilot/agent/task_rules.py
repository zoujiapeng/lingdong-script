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
_CODING_EXPLICIT = (
    "编程任务", "项目编程", "coding task", "programming task",
    "python 项目", "python项目", "node 项目", "node项目", "前端项目", "后端项目",
    "run_shell", "stdout", "stderr", "exit code", "退出码",
    "main.py", "package.json", "pyproject.toml", "requirements.txt", "vite.config",
)
_CODING_ARTIFACT_ACTION = re.compile(
    r"(创建|新建|修改|编辑|写入|生成|运行|执行|测试|验证|create|write|edit|modify|run|test|verify)"
    r"[^。,，;；\n]{0,80}"
    r"\b[\w.-]+\.(?:py|js|ts|tsx|jsx|html|css|rs|go|java|cs|cpp|c|sh|ps1)\b",
    re.IGNORECASE,
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
    r"\b(draw|drawing|paint|sketch|canvas|whiteboard|doodle)\b|"
    r"\u753b\u56fe|\u7ed8\u56fe|\u6d82\u9e26|\u753b\u677f|\u767d\u677f|\u7ed8\u5236|"
    r"\u753b(?:\u4e00|\u4e2a|\u53ea|\u5f20|\u5e45|\u6761|\u4e9b|\u51fa|\u5728|\u6210)",
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
        or bool(re.search(r"\u901a\u8fc7.{0,6}gui\u64cd\u4f5c", compact))
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


def _gui_only_forbids_shell(task: str) -> bool:
    text = (task or "").casefold()
    compact = re.sub(r"\s+", "", text)
    return (
        "do not use shell" in text
        or "don't use shell" in text
        or "no shell" in text
        or "without shell" in text
        or "do not use command line" in text
        or "no command line" in text
        or "\u4e0d\u8981\u7528shell" in compact
        or "\u4e0d\u80fd\u7528shell" in compact
        or "\u7981\u6b62\u7528shell" in compact
        or "\u4e0d\u8981\u7528\u547d\u4ee4\u884c" in compact
        or "\u4e0d\u8981\u4f7f\u7528\u547d\u4ee4\u884c" in compact
        or "\u4e0d\u80fd\u7528\u547d\u4ee4\u884c" in compact
    )


def _gui_only_launch_uses_shell(command: str) -> bool:
    low = (command or "").casefold().strip()
    return (
        bool(re.search(r"\b(?:powershell|pwsh|cmd(?:\.exe)?|wscript|cscript|mshta)\b", low))
        or "-command" in low
        or ".ps1" in low
        or "python -m http.server" in low
    )


def _is_coding_task(task: str) -> bool:
    text = (task or "").strip()
    low = text.lower()
    explicit_coding = (
        any(k in low for k in _CODING_EXPLICIT)
        or bool(_CODING_ARTIFACT_ACTION.search(text))
    )
    if _OPEN_INTENT.match(text) and not explicit_coding:
        return False
    if explicit_coding:
        return True
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


def _required_literal_tokens(task: str) -> list[str]:
    """Extract distinctive user literals that must be proven before done.

    This is deliberately broader than write/input payload extraction. If the
    user mentions a unique token such as FAST_WRAP_OK_071 or a long numeric
    result, accepting a "functionally equivalent" outcome is usually fake
    success.
    """
    text = task or ""
    text = re.sub(r"https?://\S+", " ", text)
    tokens: list[str] = []
    for token in re.findall(r"\b[A-Za-z][A-Za-z0-9_-]{3,80}\b|\b\d{4,18}\b", text):
        value = token.strip(".,;:!?()[]{}<>\"'")
        if not value:
            continue
        low = value.casefold()
        if low in {
            "http", "https", "localhost", "gui", "shell", "dom",
            "chrome", "edge", "windows", "winpilot",
        }:
            continue
        has_digit = any(ch.isdigit() for ch in value)
        distinctive = (
            "_" in value
            or has_digit
            or (len(value) >= 6 and value.upper() == value and any(ch.isalpha() for ch in value))
        )
        if distinctive:
            tokens.append(value)
    return _dedupe_targets(tokens)


def _dedupe_targets(targets: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for target in targets:
        target = target.strip()
        key = target.casefold()
        if target and key not in seen:
            seen.add(key)
            unique.append(target)
    return unique


def _historical_visible_state_targets(task: str) -> list[str]:
    text = task or ""
    targets: list[str] = []
    historical_patterns = (
        r"([A-Za-z0-9_\-\u3001,\uff0c\s]+?)(?:\u90fd|\u5747|all)?\s*(?:\u66fe\u7ecf?\u51fa\u73b0|\u51fa\u73b0\u8fc7)",
        r"([A-Za-z0-9_\-\u3001,\uff0c\s]+?)\s*(?:have|has|had|all)?\s*(?:appeared|been visible)",
    )
    for pattern in historical_patterns:
        for match in re.findall(pattern, text, flags=re.IGNORECASE):
            clause = re.split(r"[。；;\n]", match)[-1]
            for token in re.findall(r"\b[A-Za-z][A-Za-z0-9_\-]{2,80}\b", clause):
                if "_" in token or token.isupper() or any(ch.isdigit() for ch in token):
                    targets.append(token.strip(".,;:!?()[]{}"))
    return _dedupe_targets(targets)


def _visible_state_targets(task: str) -> list[str]:
    """Extract explicit labels/text the user asks to see on screen."""
    text = task or ""
    patterns = (
        r"(?:\u51fa\u73b0|\u663e\u793a|\u770b\u5230|\u53ef\u89c1|\u5305\u542b)\s*([A-Za-z0-9_\-]{3,80})",
        r"(?:appear|appears|show|shows|visible|contains?|confirm)\s+([A-Za-z0-9_\-]{3,80})",
        r"\b([A-Za-z][A-Za-z0-9_\-]{2,80})\b\s+(?:appear|appears|show|shows|is visible|are visible)",
    )
    targets: list[str] = []
    for pattern in patterns:
        for match in re.findall(pattern, text, flags=re.IGNORECASE):
            value = match if isinstance(match, str) else next((item for item in match if item), "")
            value = value.strip().strip(".,;:!?()[]{}")
            if value:
                targets.append(value)
    targets.extend(_historical_visible_state_targets(task))
    return _dedupe_targets(targets)


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
