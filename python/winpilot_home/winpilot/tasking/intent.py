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

from .contracts import InteractionActor, InteractionIntent, TaskIntent


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

_CONTINUOUS_CHAT = re.compile(
    r"continuous\s+chat|keep\s+chatting|chat\s+continuously|"
    r"\u6301\u7eed\u804a\u5929|\u4e00\u76f4\u804a\u5929|\u7ee7\u7eed\u804a\u5929|"
    r"\u4e0d\u505c\u804a\u5929|\u4fdd\u6301\u804a\u5929",
    re.IGNORECASE,
)
_BOUNDED_CHAT = re.compile(
    r"chat\s+with|have\s+a\s+chat|chat\s+for\s+a\s+while|"
    r"\u804a\u5929|\u804a\u804a|\u804a\u4e00\u4e2a|\u804a\u4e00\u804a",
    re.IGNORECASE,
)

# Interaction expressions are only candidate signals.  Shared sessions require
# actor/turn relations plus independent evidence of an evolving visual state.
# Bounded visual commands use grammatical relations (imperative predicate with
# an object, or object -> spatial operation -> destination), rather than a list
# of application names, object names, games, or screen coordinates.
_ACTOR_RELATION = re.compile(
    r"(?:和|跟|与|陪)\s*(?:我|用户)|(?:我|用户)\s*(?:和|跟|与)\s*你|\bwith\s+me\b|\bplay\s+me\b",
    re.IGNORECASE,
)
_SESSION_CUE = re.compile(
    r"一起|玩|对战|互动|协作|配合|轮流|回合|继续(?:这个|当前)?|"
    r"\bplay\b|\bgame\b|interact|collaborat|cooperat|take\s+turns?",
    re.IGNORECASE,
)
_SHARED_STATE_CUE = re.compile(
    r"看看?.{0,24}(?:界面|屏幕|窗口|画面|内容|当前|就知道)|"
    r"(?:根据|观察|查看)(?:一下)?(?:当前|现有)?(?:界面|屏幕|窗口|画面|状态|内容)|"
    r"(?:界面|屏幕|窗口|画面|状态).{0,16}(?:已经|当前|变化|更新)|"
    r"look\s+at.{0,32}(?:screen|window|board|canvas|state)|"
    r"(?:inspect|observe).{0,20}(?:current|screen|window|state)",
    re.IGNORECASE,
)
_USER_STARTED = re.compile(
    r"我(?:已经|刚刚?|先)(?:开始|操作|点|按|拖|画|走|下|放)|"
    r"I(?:'ve|\s+have)?\s+(?:already\s+)?(?:started|moved|played|clicked|dragged|drawn)|"
    r"I\s+(?:went|go)\s+first",
    re.IGNORECASE,
)
_AGENT_TURN = re.compile(r"轮到你|该你了?|你先(?:来|开始|走|下|操作)?|your\s+turn|you\s+go\s+first", re.IGNORECASE)
_USER_TURN = re.compile(r"轮到我|该我了?|我先来|等我(?:先)?|my\s+turn|wait\s+for\s+me", re.IGNORECASE)
_TURN_BASED = re.compile(r"轮流|回合|一人一次|take\s+turns?|turn[- ]based|your\s+turn|my\s+turn", re.IGNORECASE)
_CONTINUOUS_INTERACTION = re.compile(r"同时|实时|跟着我|连续配合|simultaneous|real[- ]time|follow\s+me", re.IGNORECASE)
_DRAG_ACTION = re.compile(
    r"拖(?:动|拽|到|入|向)?|拉动|滑动|移动到|\b(?:drag|drop|swipe)\b",
    re.IGNORECASE,
)
_CLICK_ACTION = re.compile(
    r"点击(?!率|量|数)|点按|点一下|按一下|按下|单击|双击|"
    r"(?:^|[，,。.!！？；;])\s*(?:(?:请|麻烦)(?:你)?|帮(?:我)?|现在|然后|接着|再)?\s*点\s*|"
    r"\b(?:click|tap|press)\b",
    re.IGNORECASE,
)
_TYPE_ACTION = re.compile(r"输入|键入|填写|打字|type|enter|fill", re.IGNORECASE)
_VISUAL_MANIPULATION = re.compile(
    r"移动|选择|选中|排列|连接|连线|放置|调整|旋转|缩放|"
    r"\b(?:move|select|arrange|connect|place|adjust|rotate|resize)\b",
    re.IGNORECASE,
)
_VISUAL_CONTEXT_CUE = re.compile(
    r"当前|已打开|界面|窗口|屏幕|页面|画面|可见|这个|该(?:项|处|区域|位置)?|"
    r"\b(?:current|visible|screen|window|page|interface|canvas|this|that)\b|"
    r"\b(?:marked|labelled|labeled)\b",
    re.IGNORECASE,
)

# These predicates intrinsically describe pointer/UI input.  Ambiguous verbs
# such as generic "press", "move", or "place" require independent visual or
# spatial grounding below; this prevents physical and calendar/document verbs
# from becoming GUI facts merely because they share one word.
_INHERENT_UI_POINTER = re.compile(
    r"点击(?!率|量|数)|点按|点一下|单击|双击|右击|右键|勾选|取消勾选|悬停|框选|拖选|"
    r"(?:^|[，,。.!！？；;])\s*(?:(?:请|麻烦)(?:你)?|帮(?:我)?|现在|然后|接着|再)?\s*点\s*[^，,。.!！？；;]{1,48}|"
    r"\b(?:click|tap|double[- ]click|right[- ]click|hover|check|uncheck|box[- ]select)\b",
    re.IGNORECASE,
)
_CONTINUOUS_POINTER_PREDICATE = re.compile(
    r"拖(?:动|拽)?|拉动|滑动|推(?:动)?|框选|拖选|"
    r"\b(?:drag|drop|swipe|slide)\b",
    re.IGNORECASE,
)
_INTERIOR_SPATIAL_RELATION = re.compile(
    r"(?:放|拖|移|推|拉|滑)(?:置|动|拽)?\s*(?:进|入)|"
    r"\b(?:into|onto|inside|within|over)\b",
    re.IGNORECASE,
)
_DIRECTIONAL_SPATIAL_RELATION = re.compile(
    r"(?:向|往|到|至)\s*(?:左|右|上|下|前|后|里|内|外)(?:边|侧|方|部)?|"
    r"\b(?:left|right|up|down|forward|backward|inward|outward)\b",
    re.IGNORECASE,
)
_POINTER_LOCATIVE_RELATION = re.compile(
    r"(?:在|从)\s*[^，,。.!！？；;]{1,48}?(?:里|中|上|内)\s*"
    r"(?:点击|点按|点一下|按一下|按下|单击|双击|右击|右键|勾选|悬停|"
    r"长按|框选|拖选|拖(?:动|拽)?|拉动|滑动)|"
    r"\b(?:in|on|inside|within|from)\s+[^,.;!?]{1,48},?\s+"
    r"(?:click|tap|press|drag|swipe|hover|check)\b",
    re.IGNORECASE,
)
_EXPLICIT_LIVE_SURFACE_CUE = re.compile(
    r"界面|窗口|屏幕|页面|画面|可见|已打开|"
    r"\b(?:visible|on[- ]screen|screen|window|page|interface|canvas)\b",
    re.IGNORECASE,
)
_CONTEXTUAL_VISUAL_GOAL_RELATION = re.compile(
    r"(?:(?:^|[，,。.!！？；;])\s*"
    r"(?:(?:请|麻烦)(?:你)?|帮(?:我)?|现在|然后|接着|随后|再)?\s*"
    r"(?:(?:在|对)\s*[^，,。.!！？；;]{1,48}?(?:里|中|上|内)?\s*)?"
    r"(?:完成|解决|处理|操作|关闭|打开|匹配|排序|校准|对齐|组合|拼接|切换|展开|收起)\s*"
    r"[^，,。.!！？；;]{1,80}"
    r"|(?:^|[,.;!?])\s*"
    r"(?:(?:please|now|then)\s+|(?:(?:can|could|would)\s+you\s+))?"
    r"(?:(?:in|on|inside|within)\s+[^,.;!?]{1,48},?\s+)?"
    r"(?:complete|solve|handle|operate|close|open|match|sort|calibrate|align|"
    r"assemble|toggle|expand|collapse)\b\s+[^,.;!?]{1,80})",
    re.IGNORECASE,
)

# A command-shaped clause is stronger than a bag-of-words match.  It permits
# arbitrary target descriptions after the predicate, but rejects bare action
# words and mentions embedded in explanatory prose ("explain how to click").
_IMPERATIVE_POINTER_RELATION = re.compile(
    r"(?:"
    r"(?:^|[，,。.!！？；;])\s*"
    r"(?:(?:请|麻烦)(?:你)?|帮(?:我)?|现在|然后|接着|随后|再)?\s*"
    r"(?:(?:在|从)\s*[^，,。.!！？；;]{1,48}?(?:里|中|上|内)?\s*)?"
    r"(?:点击(?!率|量|数)|点按|点一下|点(?!击率|击量|击数)|按一下|按下|"
    r"单击|双击|右击|右键(?:点击|单击|一下)?|勾选|取消勾选|悬停|长按|"
    r"框选|拖选|拖(?:动|拽)?|拉动|滑动)\s*"
    r"[^，,。.!！？；;]{1,80}"
    r"|"
    r"(?:^|[,.;!?])\s*"
    r"(?:(?:please|now|then)\s+|(?:(?:can|could|would)\s+you\s+))?"
    r"(?:(?:in|on|inside|within|from)\s+[^,.;!?]{1,48},?\s+)?"
    r"(?:click|tap|press|drag|swipe|double[- ]click|right[- ]click|hover|"
    r"check|uncheck|long[- ]press|box[- ]select)\b\s+(?:on\s+)?[^,.;!?]{1,80}"
    r")",
    re.IGNORECASE,
)

# Object-transfer grammar captures continuous-pointer tasks without knowing
# whether the object is a block, diagram node, game piece, CAD handle, or some
# future UI element.  Requiring a clause boundary and a destination relation
# avoids routing quoted instructions and descriptive prose as live commands.
_SPATIAL_TRANSFER_RELATION = re.compile(
    r"(?:"
    r"(?:^|[，,。.!！？；;])\s*"
    r"(?:(?:请|麻烦)(?:你)?|帮(?:我)?|现在|然后|接着|随后|再)?\s*"
    r"(?:把|将)\s*[^，,。.!！？；;]{1,64}?\s*"
    r"(?:拖(?:动|拽)?|移(?:动)?|放(?:置)?|拉(?:动)?|滑(?:动)?|推(?:动)?|摆(?:放)?|置)\s*"
    r"(?:到|至|进|入|向|往|回到|于)\s*[^，,。.!！？；;]{1,64}"
    r"|"
    r"(?:^|[，,。.!！？；;])\s*"
    r"[^，,。.!！？；;]{1,48}?\s*(?:往|向)\s*[^，,。.!！？；;]{1,24}?\s*"
    r"(?:拖|拉|滑|移动)"
    r"|"
    r"(?:^|[，,。.!！？；;])\s*"
    r"(?:(?:请|麻烦)(?:你)?|帮(?:我)?|现在|然后|接着|随后|再)?\s*"
    r"(?:往|向)\s*[^，,。.!！？；;]{1,24}?\s*"
    r"(?:拖(?:动|拽)?|拉(?:动)?|滑(?:动)?|移动)(?:一下)?\s*"
    r"[^，,。.!！？；;]{1,64}"
    r"|"
    r"(?:^|[，,。.!！？；;])\s*"
    r"(?:(?:请|麻烦)(?:你)?|帮(?:我)?|现在|然后|接着|随后|再)?\s*"
    r"(?:拖(?:动|拽)?|拉(?:动)?|滑(?:动)?|推(?:动)?)\s*"
    r"[^，,。.!！？；;]{1,64}?\s*(?:到|至|进|入|向|往)\s*"
    r"[^，,。.!！？；;]{1,64}"
    r"|"
    r"(?:^|[,.;!?])\s*"
    r"(?:(?:please|now|then)\s+|(?:(?:can|could|would)\s+you\s+))?"
    r"(?:drag|move|place|put|slide|swipe|position|drop)\b\s+"
    r"(?!on\b|ahead\b|forward\b)[^,.;!?]{1,64}?\s+"
    r"(?:to|into|onto|inside|within|over|toward|towards|in)\s+[^,.;!?]{1,64}"
    r"|"
    r"(?:^|[,.;!?])\s*"
    r"(?:(?:please|now|then)\s+|(?:(?:can|could|would)\s+you\s+))?"
    r"(?:drag|move|slide|swipe)\b\s+[^,.;!?]{1,64}?\s+"
    r"(?:left|right|up|down|forward|backward|inward|outward)\b"
    r")",
    re.IGNORECASE,
)

_CONTEXTUAL_MANIPULATION_RELATION = re.compile(
    r"(?:"
    r"(?:^|[，,。.!！？；;])\s*(?:(?:请|麻烦)(?:你)?|帮(?:我)?|现在|然后|接着|再)?\s*"
    r"(?:(?:在|对)\s*[^，,。.!！？；;]{1,48}?(?:里|中|上|内)?\s*)"
    r"(?:移动|选择|选中|排列|连接|连线|放置|调整|旋转|缩放)\s*"
    r"[^，,。.!！？；;]{1,80}"
    r"|"
    r"(?:^|[,.;!?])\s*(?:(?:please|now|then)\s+|(?:(?:can|could|would)\s+you\s+))?"
    r"(?:(?:in|on|inside|within)\s+[^,.;!?]{1,48},?\s+)"
    r"(?:move|select|arrange|connect|place|adjust|rotate|resize)\b\s+[^,.;!?]{1,80}"
    r")",
    re.IGNORECASE,
)

_IMPERATIVE_MANIPULATION_RELATION = re.compile(
    r"(?:"
    r"(?:^|[，,。.!！？；;])\s*(?:(?:请|麻烦)(?:你)?|帮(?:我)?|现在|然后|接着|再)?\s*"
    r"(?:移动|选择|选中|排列|连接|连线|放置|调整|旋转|缩放)\s*"
    r"[^，,。.!！？；;]{1,80}"
    r"|"
    r"(?:^|[,.;!?])\s*(?:(?:please|now|then)\s+|(?:(?:can|could|would)\s+you\s+))?"
    r"(?:move|select|arrange|connect|place|adjust|rotate|resize)\b\s+[^,.;!?]{1,80}"
    r")",
    re.IGNORECASE,
)

_CHANNEL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("QQ", re.compile(r"(?<![A-Za-z])QQ(?![A-Za-z])", re.IGNORECASE)),
    ("WeChat", re.compile(r"微信|WeChat", re.IGNORECASE)),
    ("Email", re.compile(r"邮件|邮箱|email|mail", re.IGNORECASE)),
)
_SOURCE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Paint", re.compile(r"画板|画图|MS\s*Paint|Paint(?:\s+app)?", re.IGNORECASE)),
    ("Whiteboard", re.compile(r"白板|Whiteboard", re.IGNORECASE)),
)
_GENERIC_SOURCE_RELATION = re.compile(
    r"(?:在|查看|看看|观察|根据)\s*(?:当前|已打开的)?\s*"
    r"([^，,。.!！？]{1,32}?)(?:应用|程序|软件|窗口|界面|页面)(?:里|中|上)?",
    re.IGNORECASE,
)

# Launch commands need language-appropriate boundaries. ``\b`` cannot split
# two adjacent CJK word characters, so ``打开记事本`` used to miss the launch
# route and fall through to a generic artifact stage.  Match the complete
# command clause instead: English predicates retain word boundaries while CJK
# predicates use explicit clause delimiters.  The target is deliberately
# open-ended; no application catalogue is embedded here.
_OPEN_APPLICATION_RELATION = re.compile(
    r"(?:"
    r"^\s*(?:(?:请|麻烦|劳驾)(?:你)?\s*)?(?:(?:帮我|帮忙)\s*)?"
    r"(?P<zh_verb>打开|启动|运行)\s*(?:一下\s*)?"
    r"(?P<zh_target>[^，,。.!！？；;]{1,80})\s*[。.!！？]?\s*$"
    r"|"
    r"^\s*(?:please\s+)?(?:(?:can|could|would)\s+you\s+)?"
    r"(?P<en_verb>open|launch|start|run)\b\s+"
    r"(?P<en_target>[^,.;!?]{1,80})\s*[.!?]?\s*$"
    r")",
    re.IGNORECASE,
)
_NON_APPLICATION_OPEN_TARGET = re.compile(
    r"(?:文件|文档|附件|图片|照片|目录|文件夹|链接|网址|网页|"
    r"\b(?:file|document|attachment|image|photo|folder|directory|url|web\s*page)\b|"
    r"https?://|\.[A-Za-z0-9]{1,8}$)",
    re.IGNORECASE,
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


def _application_launch_target(text: str) -> str:
    """Return a target only for a complete, application-shaped launch clause.

    ``open`` is ambiguous for files and URLs; those media-shaped targets stay
    on the semantic task route.  ``launch/start/run`` and their Chinese
    equivalents are process predicates, so arbitrary application names remain
    supported without a product-name allowlist.
    """

    match = _OPEN_APPLICATION_RELATION.fullmatch(text)
    if not match:
        return ""
    verb = str(match.group("zh_verb") or match.group("en_verb") or "").casefold()
    target = str(match.group("zh_target") or match.group("en_target") or "")
    target = target.strip(" \t\r\n'\"“”‘’")[:80]
    if not target:
        return ""
    if verb in {"打开", "open"} and _NON_APPLICATION_OPEN_TARGET.search(target):
        return ""
    return target


def _actor_assignments(text: str) -> dict[str, dict[str, str]]:
    """Extract open-ended actor constraints without interpreting their value."""

    assignments: dict[str, dict[str, str]] = {}
    chinese = re.compile(
        r"(?P<actor>你|我)\s*(?:来)?\s*(?P<verb>用|使用|负责|控制|选择|选|扮演)\s*"
        r"(?P<value>[^，,；;。.!！？]{1,32}?)"
        r"(?=(?:你|我)\s*(?:来)?\s*(?:用|使用|负责|控制|选择|选|扮演)|[，,；;。.!！？]|$)",
        re.IGNORECASE,
    )
    english = re.compile(
        r"(?P<actor>you|I)\s+(?P<verb>use|control|choose|take|play\s+as|handle)\s+"
        r"(?P<value>[^,;.!?]{1,32}?)"
        r"(?=(?:you|I)\s+(?:use|control|choose|take|play\s+as|handle)|[,;.!?]|$)",
        re.IGNORECASE,
    )
    keys = {
        "负责": "responsibility", "控制": "control", "扮演": "role",
        "选择": "selection", "选": "selection", "用": "assignment", "使用": "assignment",
        "control": "control", "play as": "role", "handle": "responsibility",
        "choose": "selection", "take": "assignment", "use": "assignment",
    }
    for pattern in (chinese, english):
        for match in pattern.finditer(text):
            raw_actor = match.group("actor").casefold()
            actor = "agent" if raw_actor in {"你", "you"} else "user"
            verb = re.sub(r"\s+", " ", match.group("verb").casefold())
            value = re.sub(r"(?:\s+and|\s+以及|\s+并且?)$", "", match.group("value"), flags=re.IGNORECASE)
            value = value.strip(" \t\r\n'\"“”‘’")
            if value:
                assignments.setdefault(actor, {})[keys.get(verb, "assignment")] = value
    return assignments


def _primary_action_modality(modalities: list[str]) -> str:
    """Return a strict primary only when the contract is unambiguous."""
    concrete = list(dict.fromkeys(
        str(item or "").strip().casefold()
        for item in modalities
        if str(item or "").strip().casefold() not in {"", "infer_from_scene"}
    ))
    return concrete[0] if len(concrete) == 1 else "infer_from_scene"


def _interaction_candidate(text: str, source: str) -> InteractionIntent:
    actor_relation = bool(_ACTOR_RELATION.search(text))
    session_cue = bool(_SESSION_CUE.search(text))
    shared_state = bool(_SHARED_STATE_CUE.search(text))
    user_started = bool(_USER_STARTED.search(text))
    agent_turn = bool(_AGENT_TURN.search(text))
    user_turn = bool(_USER_TURN.search(text))
    assignments = _actor_assignments(text)
    paired_roles = {"agent", "user"} <= set(assignments)
    visual_grounding = bool(
        source
        or shared_state
        or _VISUAL_CONTEXT_CUE.search(text)
        or _POINTER_LOCATIVE_RELATION.search(text)
    )
    spatial_candidate = bool(_SPATIAL_TRANSFER_RELATION.search(text))
    spatial_transfer = bool(
        spatial_candidate
        and (
            visual_grounding
            or _CONTINUOUS_POINTER_PREDICATE.search(text)
            or _INTERIOR_SPATIAL_RELATION.search(text)
            or _DIRECTIONAL_SPATIAL_RELATION.search(text)
        )
    )
    imperative_candidate = bool(_IMPERATIVE_POINTER_RELATION.search(text))
    imperative_pointer = bool(
        imperative_candidate
        and (
            visual_grounding
            or spatial_transfer
            or _INHERENT_UI_POINTER.search(text)
        )
    )
    contextual_manipulation = bool(
        (
            _CONTEXTUAL_MANIPULATION_RELATION.search(text)
            or _IMPERATIVE_MANIPULATION_RELATION.search(text)
        )
        and (source or shared_state or _VISUAL_CONTEXT_CUE.search(text))
    )
    contextual_visual_goal = bool(
        _CONTEXTUAL_VISUAL_GOAL_RELATION.search(text)
        and _EXPLICIT_LIVE_SURFACE_CUE.search(text)
    )
    direct_manipulation = bool(
        imperative_pointer
        or spatial_transfer
        or contextual_manipulation
        or contextual_visual_goal
    )

    signals: list[str] = []
    for present, name in (
        (actor_relation, "actor_relation"),
        (session_cue, "shared_session"),
        (shared_state, "live_visual_state"),
        (user_started, "external_actor_started"),
        (agent_turn or user_turn, "explicit_turn"),
        (paired_roles, "paired_role_assignment"),
        (imperative_pointer, "imperative_pointer_relation"),
        (spatial_transfer, "spatial_transfer_relation"),
        (contextual_manipulation, "contextual_manipulation_relation"),
        (contextual_visual_goal, "contextual_visual_goal_relation"),
        (direct_manipulation, "direct_visual_manipulation"),
    ):
        if present:
            signals.append(name)

    # Turn words also occur in prose, documents, and physical activities.  A
    # persistent visual session therefore requires independent live-surface
    # or direct-manipulation grounding; one "your turn" phrase is not enough.
    shared_session = bool(
        ((agent_turn or user_turn) and (visual_grounding or direct_manipulation))
        or (
            actor_relation
            and (
                shared_state
                or user_started
                or paired_roles
                or (session_cue and visual_grounding)
            )
        )
        or (paired_roles and (session_cue or shared_state))
    )
    active = bool(shared_session or direct_manipulation)
    if _TURN_BASED.search(text) or (shared_session and user_started and session_cue):
        mode = "turn_based"
    elif shared_session and _CONTINUOUS_INTERACTION.search(text):
        mode = "continuous_cooperation"
    elif shared_session:
        mode = "shared_visual_session"
    elif direct_manipulation:
        mode = "direct_visual_manipulation"
    else:
        mode = "none"

    if direct_manipulation and not shared_session:
        turn_owner = "agent"
    elif agent_turn or (shared_session and user_started and not user_turn):
        turn_owner = "agent"
    elif user_turn:
        turn_owner = "user"
    else:
        turn_owner = "unknown"

    modalities: list[str] = []
    if active:
        if _DRAG_ACTION.search(text):
            modalities.append("drag")
        if _CLICK_ACTION.search(text):
            modalities.append("click")
        if _DRAW_VERB.search(text):
            modalities.append("draw")
        if _TYPE_ACTION.search(text):
            modalities.append("type")
        if not modalities:
            modalities.append("infer_from_scene")

    actors = []
    if shared_session:
        actors = [
            InteractionActor(
                id="agent", kind="agent", display_name="assistant",
                role="participant", attributes=assignments.get("agent", {}),
            ),
            InteractionActor(
                id="user", kind="human", display_name="user",
                role="participant", attributes=assignments.get("user", {}),
            ),
        ]
    elif direct_manipulation:
        actors = [
            InteractionActor(
                id="agent", kind="agent", display_name="assistant",
                role="operator", attributes=assignments.get("agent", {}),
            ),
        ]
    ambiguous: list[str] = []
    if active and turn_owner == "unknown":
        ambiguous.append("turn_owner")
    confidence = min(0.98, 0.40 + 0.09 * len(signals)) if active else min(0.45, 0.08 * len(signals))
    return InteractionIntent(
        active=active,
        mode=mode,
        actors=actors,
        role_assignments=assignments if active else {},
        turn_owner=turn_owner,
        session_mode="persistent" if shared_session else "bounded",
        continuation_policy=(
            "wait_for_external_change"
            if shared_session else "reobserve_until_goal_complete"
            if direct_manipulation else "finish_after_verified_effect"
        ),
        target=source or ("active_window" if active else ""),
        region_hint="existing_shared_state" if active and (shared_state or user_started) else (
            "infer_from_authoritative_scene" if active else ""
        ),
        action_modalities=modalities,
        primary_action_modality=_primary_action_modality(modalities),
        user_started=user_started,
        expected_external_change=shared_session,
        confidence=confidence,
        signals=signals,
        ambiguous_fields=ambiguous,
        provenance={
            "active": (
                "typed_command_relation"
                if direct_manipulation and not shared_session
                else "multi_signal_structural_candidate"
            ),
            "roles": "actor_assignment_relation",
            "turn_owner": "explicit_turn_or_external_progress",
        } if active else {},
    )


def _merge_semantic_interaction(
    candidate: InteractionIntent,
    extracted: dict[str, Any],
) -> InteractionIntent:
    raw = extracted.get("interaction")
    if not isinstance(raw, dict):
        raw = {
            key.removeprefix("interaction_"): value
            for key, value in extracted.items()
            if key.startswith("interaction_")
        }
    if not raw:
        return candidate
    try:
        semantic_confidence = float(raw.get("confidence") or 0.0)
    except (TypeError, ValueError):
        semantic_confidence = 0.0
    semantic_active = raw.get("active") is True and semantic_confidence >= 0.55
    semantic_veto = raw.get("active") is False and semantic_confidence >= 0.75
    if candidate.active and semantic_veto:
        return InteractionIntent(
            active=False,
            mode="none",
            confidence=min(0.99, semantic_confidence),
            signals=[*candidate.signals, "semantic_interaction_veto"],
            ambiguous_fields=[],
            provenance={
                "active": "semantic_extractor_veto",
                "candidate": candidate.provenance.get("active", "structural_candidate"),
            },
        )
    if not candidate.active and not semantic_active:
        return candidate

    payload = candidate.model_dump(mode="python")
    payload["active"] = bool(candidate.active or semantic_active)
    payload["confidence"] = max(candidate.confidence, min(0.99, semantic_confidence))
    for field in (
        "mode", "turn_owner", "session_mode", "continuation_policy",
        "target", "region_hint",
    ):
        value = str(raw.get(field) or "").strip()
        current = str(payload.get(field) or "")
        if value and current in {"", "none", "unknown", "bounded", "finish_after_verified_effect"}:
            payload[field] = value
    if isinstance(raw.get("action_modalities"), list) and raw["action_modalities"]:
        payload["action_modalities"] = list(dict.fromkeys([
            *payload.get("action_modalities", []),
            *(str(item) for item in raw["action_modalities"] if str(item).strip()),
        ]))
    payload["primary_action_modality"] = _primary_action_modality(
        list(payload.get("action_modalities") or []),
    )
    if isinstance(raw.get("role_assignments"), dict):
        merged_roles = dict(payload.get("role_assignments") or {})
        for actor, values in raw["role_assignments"].items():
            if isinstance(values, dict):
                merged_roles.setdefault(str(actor), {}).update({
                    str(key): str(value) for key, value in values.items() if str(value).strip()
                })
        payload["role_assignments"] = merged_roles
    for field in ("user_started", "expected_external_change"):
        if raw.get(field) is True:
            payload[field] = True
    if isinstance(raw.get("actors"), list) and raw["actors"]:
        semantic_actors: list[dict[str, Any]] = []
        for actor in raw["actors"]:
            if isinstance(actor, dict) and str(actor.get("id") or "").strip():
                semantic_actors.append(actor)
        if semantic_actors:
            payload["actors"] = semantic_actors
    payload["signals"] = list(dict.fromkeys([*payload.get("signals", []), "semantic_interaction_frame"]))
    payload["provenance"] = {**payload.get("provenance", {}), "semantic": "semantic_extractor"}
    if payload["active"] and not payload.get("actors"):
        payload["actors"] = [
            InteractionActor(id="agent", kind="agent", display_name="assistant", role="participant"),
            InteractionActor(id="user", kind="human", display_name="user", role="participant"),
        ]
    if payload["active"] and payload.get("role_assignments"):
        enriched_actors: list[dict[str, Any]] = []
        for actor in payload.get("actors") or []:
            actor_payload = actor.model_dump(mode="python") if isinstance(actor, InteractionActor) else dict(actor)
            actor_id = str(actor_payload.get("id") or "")
            actor_payload["attributes"] = {
                **dict(actor_payload.get("attributes") or {}),
                **dict(payload["role_assignments"].get(actor_id) or {}),
            }
            enriched_actors.append(actor_payload)
        payload["actors"] = enriched_actors
    try:
        return InteractionIntent.model_validate(payload)
    except Exception:
        return candidate


class TaskIntentParser:
    """Extract a stable semantic frame without adding an LLM round trip.

    An optional semantic extractor can fill fields that the structural grammar
    leaves ambiguous.  Its output is merged only into empty/ambiguous slots.
    """

    def __init__(self, semantic_extractor: Callable[[str], dict[str, Any]] | None = None) -> None:
        self.semantic_extractor = semantic_extractor

    def parse(self, task: str) -> TaskIntent:
        text = str(task or "").strip()
        extracted: dict[str, Any] = {}
        if self.semantic_extractor is not None:
            try:
                extracted = dict(self.semantic_extractor(text) or {})
            except Exception:
                extracted = {}
        operations: list[str] = []
        launch_target = _application_launch_target(text)
        if launch_target:
            operations.append("open")
        if _DRAW_VERB.search(text):
            operations.append("draw")
        if _CAPTURE_VERB.search(text):
            operations.append("capture")
        if _SAVE_VERB.search(text):
            operations.append("save")
        communication_relation = _COMMUNICATION_VERB.search(text)
        chat_relation = _CHAT_OPERATION.search(text)
        continuous_chat_relation = _CONTINUOUS_CHAT.search(text)
        bounded_chat_relation = _BOUNDED_CHAT.search(text)
        if (
            communication_relation or chat_relation
            or continuous_chat_relation or bounded_chat_relation
        ):
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
        if not source:
            generic_source = _GENERIC_SOURCE_RELATION.search(text)
            if generic_source:
                candidate_source = generic_source.group(1).strip(" \t\r\n'\"“”‘’")[:64]
                # Deictic qualifiers describe the authoritative live surface;
                # they are not application identities.  Keep these commands
                # target-bound to active_window instead of inventing a source
                # application named "current".
                if not re.fullmatch(r"(?:当前|现有|这个|该|已打开的?)", candidate_source):
                    source = candidate_source
        interaction = _merge_semantic_interaction(
            _interaction_candidate(text, source),
            extracted,
        )
        if interaction.active:
            operations.append("interact")
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
        destination = channel or launch_target
        if _DRAFT.search(text):
            send_mode = "draft"
        elif _SHARE.search(text):
            send_mode = "share"
        elif continuous_chat_relation:
            send_mode = "continuous_chat"
        elif bounded_chat_relation and not communication_relation:
            send_mode = "bounded_chat"
        else:
            send_mode = "send"
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
        if launch_target:
            provenance["destination"] = "application_launch_relation"
        if ambiguous and extracted:
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

        signals = sum(bool(value) for value in (
            operations, media_type != "none", channel, recipient, source, subject,
            interaction.active,
        ))
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
            interaction=interaction,
        )
