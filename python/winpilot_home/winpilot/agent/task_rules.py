"""Task classification and guard rules for the WinPilot GUI runner.

These helpers are intentionally pure/string-based. Keep them out of
``loop.py`` so the runner remains focused on LLM orchestration.
"""
from __future__ import annotations

import re
from typing import Any


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
_FILE_ARTIFACT_BUILD = re.compile(
    r"(写入|写到|保存到|输出到|导出到|生成|创建|新建|整理到|记录到|"
    r"write|save|export|generate|create)"
    r"[^。。，,;；\n]{0,40}"
    r"(?:\.[a-z0-9]{1,8}\b|(?:txt|csv|json|jsonl|md|html|xml|yaml|yml|log|文本)文件)",
    re.IGNORECASE,
)
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
        or "\u53ea\u7528gui" in compact
        or "\u4ec5\u7528gui" in compact
        or "\u53ea\u901a\u8fc7gui" in compact
        or "\u53ea\u7528\u56fe\u5f62\u754c\u9762" in compact
        or "\u4ec5\u7528\u56fe\u5f62\u754c\u9762" in compact
        or "not use command line" in text
        or "do not use command line" in text
        or "no command line" in text
        or "\u5fc5\u987b\u8d70gui" in compact
        or "\u5fc5\u987b\u901agui" in compact
        or "\u5fc5\u987b\u901a\u8fc7gui\u64cd\u4f5c" in compact
        or "\u901agui\u64cd\u4f5c" in compact
        or "\u901a\u8fc7gui\u64cd\u4f5c" in compact
        or bool(re.search(r"\u901a\u8fc7.{0,6}gui\u64cd\u4f5c", compact))
        or "蹇呴』閫氳繃gui鎿嶄綔" in compact
        or "閫氳繃gui鎿嶄綔" in compact
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
        or bool(re.search(r"\bgui[- ]only\b", text))
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


def _is_multi_source_research_task(task: str) -> bool:
    """Recognize cross-page collection where source/entity binding matters."""
    text = str(task or "").casefold()
    has_research = bool(re.search(
        r"\b(?:search|research|compare|find|inspect|product\s+page|source\s+url)\b|"
        r"搜索|调研|对比|查找|多个页面|产品页面|商品页面|来源|网址",
        text,
        re.IGNORECASE,
    ))
    has_multiple = bool(re.search(
        r"\b(?:two|multiple|several|each|both|separate)\b|"
        r"两个|多个|每个|各自|分别|独立|两款|多款",
        text,
        re.IGNORECASE,
    ))
    return has_research and has_multiple


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
        or "gui only" in text
        or "only use gui" in text
        or "use gui only" in text
        or "do not use command line" in text
        or "no command line" in text
        or "\u4e0d\u8981\u7528shell" in compact
        or "\u4e0d\u8981\u4f7f\u7528shell" in compact
        or "\u4e0d\u80fd\u7528shell" in compact
        or "\u4e0d\u80fd\u4f7f\u7528shell" in compact
        or "\u7981\u6b62\u7528shell" in compact
        or "\u7981\u6b62\u4f7f\u7528shell" in compact
        or "\u53ea\u7528gui" in compact
        or "\u4ec5\u7528gui" in compact
        or "\u53ea\u901a\u8fc7gui" in compact
        or "\u53ea\u7528\u56fe\u5f62\u754c\u9762" in compact
        or "\u4ec5\u7528\u56fe\u5f62\u754c\u9762" in compact
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

    # Prefer the shared typed contract.  A second open-verb keyword router
    # misclassified ordinary application work and could not generalize to
    # novel wording; only unresolved legacy calls fall through to heuristics.
    try:
        domain = str(_structured_task_intent(text).get("domain") or "").casefold()
    except Exception:
        domain = ""
    if domain in {"desktop", "communication"}:
        return False
    if domain == "workspace":
        return True

    def contains_keyword(keyword: str) -> bool:
        candidate = keyword.strip().lower()
        # Short ASCII coding tokens such as repo/git/bug must be lexical
        # words. Substring matching classified ordinary words like "report"
        # as repository work and exposed the wrong runtime/tool profile.
        if re.fullmatch(r"[a-z][a-z0-9_.-]*", candidate):
            return bool(re.search(
                rf"(?<![a-z0-9_]){re.escape(candidate)}(?![a-z0-9_])",
                low,
            ))
        return keyword.lower() in low

    explicit_coding = (
        any(contains_keyword(k) for k in _CODING_EXPLICIT)
        or bool(_CODING_ARTIFACT_ACTION.search(text))
        or bool(_FILE_ARTIFACT_BUILD.search(text))
    )
    if explicit_coding:
        return True
    if any(contains_keyword(k) for k in _CODING_STRONG):
        return True
    return bool(_CODING_BUILD.search(text))


def _structured_task_intent(task: Any) -> dict[str, Any]:
    if hasattr(task, "model_dump"):
        return dict(task.model_dump(mode="json"))
    if isinstance(task, dict):
        return dict(task)
    from ..tasking.intent import TaskIntentParser

    return TaskIntentParser().parse(str(task or "")).model_dump(mode="json")


def _is_communication_task(task: Any) -> bool:
    intent = _structured_task_intent(task)
    operations = intent.get("operation") or []
    if isinstance(operations, str):
        operations = [operations]
    return "send" in {str(item).casefold() for item in operations}


def _communication_mode(task: Any) -> str:
    """Return the compiler-owned delivery lifecycle without adding a tactic."""
    intent = _structured_task_intent(task)
    parsed_mode = str(intent.get("send_mode") or "send")
    return parsed_mode if parsed_mode in {
        "send", "draft", "share", "bounded_chat", "continuous_chat",
    } else "send"


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
        r"\u884c\s*(?:[=:\uff1a\uff1d]\s*)?([^\uff0c,\u3002\uff1b;\r\n]+)"
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
    generic_descriptions = {
        "a", "an", "the", "actually", "after", "again", "before", "finally", "first",
        "then", "when", "while", "app", "application", "browser", "button", "control", "dialog",
        "element", "field", "page", "popup", "screen", "text", "title",
        "window",
    }
    for pattern in patterns:
        for match in re.findall(pattern, text, flags=re.IGNORECASE):
            value = match if isinstance(match, str) else next((item for item in match if item), "")
            value = value.strip().strip(".,;:!?()[]{}")
            if value and value.casefold() not in generic_descriptions:
                targets.append(value)
    targets.extend(_historical_visible_state_targets(task))
    return _dedupe_targets(targets)

_UPLOAD_PUBLISH_OPERATION_RE = re.compile(
    r"\b(?:upload|publish|post|submit)\b|\u4e0a\u4f20|\u53d1\u5e03|\u6295\u7a3f|\u63d0\u4ea4",
    re.IGNORECASE,
)
_MEDIA_PAYLOAD_RE = re.compile(
    r"\.(?:mp4|mov|avi|mkv|webm|mp3|wav|flac|m4a|png|jpe?g|gif|webp)\b",
    re.IGNORECASE,
)
_MEDIA_UPLOAD_OBJECT_RE = re.compile(
    r"\b(?:video|image|photo|audio|media|clip|file)\b|"
    r"\u89c6\u9891|\u56fe\u7247|\u56fe\u50cf|\u7167\u7247|\u97f3\u9891|\u5a92\u4f53|\u6587\u4ef6|\u4f5c\u54c1",
    re.IGNORECASE,
)


def _is_upload_publish_task(task: Any) -> bool:
    """Recognize a transfer/publication operation, independent of platform."""
    intent = _structured_task_intent(task)
    operations = intent.get("operation") or []
    if isinstance(operations, str):
        operations = [operations]
    structured_operation = bool({str(item).casefold() for item in operations} & {
        "upload", "publish", "post", "submit",
    })
    text = str(intent.get("original_text") or (task if isinstance(task, str) else ""))
    has_media_payload = (
        str(intent.get("media_type") or "none").casefold() in {"image", "file"}
        or bool(_MEDIA_PAYLOAD_RE.search(text))
        or bool(_MEDIA_UPLOAD_OBJECT_RE.search(text))
    )
    return has_media_payload and (
        structured_operation or bool(_UPLOAD_PUBLISH_OPERATION_RE.search(text))
    )
