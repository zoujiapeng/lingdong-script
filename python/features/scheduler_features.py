"""
调度器管理功能注册
scheduler_list / scheduler_get / scheduler_create / scheduler_update / scheduler_delete
scheduler_condition_types / scheduler_trigger_types
"""
from __future__ import annotations
import json
import os
import time
from pathlib import Path
from features.registry import feature, P, TC, FeatureCategory as F


def _get_schedules_path() -> str:
    """获取 schedules.json 路径（环境变量 -> 默认路径）"""
    env = os.getenv("SCHEDULES_PATH", "")
    if env:
        return env
    # 与 Electron 主进程 app.getPath('userData') 一致
    appdata = os.getenv("APPDATA", "")
    if appdata:
        # Electron package.json name = "lingdong-script"
        electron_path = os.path.join(appdata, "lingdong-script", "schedules.json")
        if os.path.exists(electron_path):
            return electron_path
        # 旧版路径
        legacy_path = os.path.join(appdata, "灵动脚本", "schedules.json")
        if os.path.exists(legacy_path):
            return legacy_path
    # Fallback: 独立运行时使用 ~/.lingdong 目录
    fallback = os.path.join(Path.home(), ".lingdong", "schedules.json")
    os.makedirs(os.path.dirname(fallback), exist_ok=True)
    return fallback


def _read_schedules() -> list[dict]:
    path = _get_schedules_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _write_schedules(schedules: list[dict]) -> bool:
    path = _get_schedules_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(schedules, f, ensure_ascii=False, indent=2)
        return True
    except OSError:
        return False


def _gen_id() -> str:
    return f"sched_{int(time.time() * 1000)}_{os.urandom(2).hex()}"


TRIGGER_TYPES = [
    {"type": "cron", "description": "Cron 表达式定时触发",
     "params": [{"name": "cron_expr", "type": "string", "description": "5字段 cron 表达式（分 时 日 月 周）"}]},
    {"type": "interval", "description": "固定间隔触发",
     "params": [{"name": "interval_seconds", "type": "number", "description": "间隔秒数"}]},
    {"type": "startup", "description": "应用启动时触发",
     "params": []},
    {"type": "event", "description": "系统事件触发（窗口/进程/文件/剪贴板/网络）",
     "params": [
         {"name": "event_type", "type": "string",
          "description": "事件类型: window_open / process_start / file_created / clipboard_change / network_available"},
         {"name": "event_config", "type": "object", "description": "事件配置参数（如 title/name/path）"},
     ]},
]


@feature(
    name="scheduler_list",
    display_name="列出所有定时调度",
    description="【调度器】列出所有已注册的定时调度规则，包含触发器、守卫条件和启用状态。用于查看当前有哪些自动任务在运行",
    category=F.SYSTEM,
    params=[
        P("enabled_only", "boolean", "仅返回已启用的调度", required=False, default=False),
    ],
    returns="list[dict] - 调度规则列表",
    tags=["scheduler", "schedule"],
)
def scheduler_list(enabled_only: bool = False) -> list:
    schedules = _read_schedules()
    if enabled_only:
        return [s for s in schedules if s.get("enabled", False)]
    return schedules


@feature(
    name="scheduler_get",
    display_name="查看单个调度",
    description="【调度器】按 ID 查看单个调度规则的详细配置",
    category=F.SYSTEM,
    params=[
        P("id", "str", "调度 ID", example="schedule_001"),
    ],
    returns="dict - 调度详情（不存在时返回空字典）",
    tags=["scheduler", "schedule"],
)
def scheduler_get(id: str) -> dict:
    for s in _read_schedules():
        if s.get("id") == id:
            return s
    return {}


@feature(
    name="scheduler_create",
    display_name="创建定时调度",
    description="【调度器】创建一条新的定时调度规则，支持 ECA 模型：触发器（何时检查）+ 守卫条件（是否执行）+ 模板（执行什么）",
    category=F.SYSTEM,
    params=[
        P("name", "str", "调度名称，如「上班自动打卡」", example="上班打卡"),
        P("template_id", "str", "要执行的模板 ID", example="attendance_card"),
        P("template_name", "str", "模板显示名称（可选）", required=False, default=""),
        P("triggers", "list", '触发器列表', example=[{"type":"cron","cron_expr":"0 9 * * 1-5"}]),
        P("conditions", "object", '守卫条件组（可选），如 {"operator":"AND","rules":[{"type":"window_exists","params":{"title":"企业微信"}}]}', required=False, default=None),
        P("enabled", "boolean", "是否立即启用", required=False, default=True),
    ],
    returns="dict - 创建的调度规则（含 id）",
    examples=[
        'scheduler_create(name="上班打卡", template_id="checkin", triggers=[{"type":"cron","cron_expr":"0 9 * * 1-5"}], conditions={"operator":"AND","rules":[{"type":"window_exists","params":{"title":"企业微信"}}]})',
    ],
    tags=["scheduler", "schedule"],
)
def scheduler_create(name: str, template_id: str, triggers: list,
                     template_name: str = "", conditions: dict = None,
                     enabled: bool = True) -> dict:
    schedules = _read_schedules()
    entry = {
        "id": _gen_id(),
        "name": name,
        "template_id": template_id,
        "template_name": template_name or "",
        "triggers": triggers,
        "enabled": enabled,
        "last_run": None,
        "next_run": None,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if conditions:
        entry["conditions"] = conditions
    schedules.append(entry)
    _write_schedules(schedules)
    return entry


@feature(
    name="scheduler_update",
    display_name="更新定时调度",
    description="【调度器】更新已存在的调度规则（部分更新，仅修改提供的字段）",
    category=F.SYSTEM,
    params=[
        P("id", "str", "调度 ID", example="schedule_001"),
        P("name", "str", "调度名称", required=False, default=None),
        P("template_id", "str", "模板 ID", required=False, default=None),
        P("template_name", "str", "模板显示名称", required=False, default=None),
        P("triggers", "list", "触发器列表", required=False, default=None),
        P("conditions", "object", "守卫条件组（设为 {} 可清空条件）", required=False, default=None),
        P("enabled", "boolean", "是否启用", required=False, default=None),
    ],
    returns='dict - 更新后的调度（未找到时返回 {"error": "not found"}）',
    tags=["scheduler", "schedule"],
)
def scheduler_update(id: str, **kwargs) -> dict:
    schedules = _read_schedules()
    for i, s in enumerate(schedules):
        if s.get("id") == id:
            for key in ("name", "template_id", "template_name", "triggers", "conditions", "enabled"):
                val = kwargs.get(key)
                if val is not None:
                    schedules[i][key] = val
            _write_schedules(schedules)
            return schedules[i]
    return {"error": "not found"}


@feature(
    name="scheduler_delete",
    display_name="删除定时调度",
    description="【调度器】删除指定的调度规则。用于清理不再需要的自动任务",
    category=F.SYSTEM,
    params=[
        P("id", "str", "要删除的调度 ID", example="schedule_001"),
    ],
    returns="bool - 是否成功",
    tags=["scheduler", "schedule"],
)
def scheduler_delete(id: str) -> bool:
    schedules = _read_schedules()
    filtered = [s for s in schedules if s.get("id") != id]
    if len(filtered) == len(schedules):
        return False
    _write_schedules(filtered)
    return True


@feature(
    name="scheduler_condition_types",
    display_name="列出可用守卫条件类型",
    description="【调度器】列出所有可用于调度守卫条件的条件类型及参数说明。这些条件决定调度触发后是否真正执行模板",
    category=F.DEBUG,
    params=[
        P("include_builtin", "boolean", "是否包含内置条件（窗口/进程/时间）", required=False, default=True),
    ],
    returns="list[dict{type, description, params}] - 条件类型列表",
    tags=["scheduler", "condition"],
)
def scheduler_condition_types(include_builtin: bool = True) -> list:
    try:
        from engine.condition_evaluator import list_condition_types
        return list_condition_types()
    except ImportError:
        return []


@feature(
    name="scheduler_trigger_types",
    display_name="列出可用触发器类型",
    description="【调度器】列出所有可用的触发器类型及参数说明。触发器决定「什么时候检查」调度条件",
    category=F.DEBUG,
    params=[],
    returns="list[dict{type, description, params}] - 触发器类型列表",
    tags=["scheduler", "trigger"],
)
def scheduler_trigger_types() -> list:
    return TRIGGER_TYPES


@feature(
    name="quick_schedule",
    display_name="快速自然语言创建调度",
    description="【调度器】用自然语言一句话创建定时调度，无需手动填表。自动解析时间和动作，支持：提醒、打开软件、运行模板。示例：'今晚9点提醒我写周报并打开企业微信'",
    category=F.SYSTEM,
    params=[
        P("text", "str", "自然语言调度描述", example="今晚9点提醒我写周报"),
    ],
    returns="dict - 创建的调度规则（含 id、confidence、解析详情）",
    examples=[
        'quick_schedule(text="今晚9点提醒我写周报并打开企业微信")',
        'quick_schedule(text="每工作日上午9点打开企业微信")',
        'quick_schedule(text="5分钟后提醒我检查邮件")',
    ],
    tags=["scheduler", "quick", "nlp"],
)
def quick_schedule(text: str) -> dict:
    from engine.nlp_schedule_parser import parse, is_schedule_intent

    if not is_schedule_intent(text):
        return {"success": False, "error": "未识别为调度意图", "confidence": 0}

    parsed = parse(text)
    if not parsed.get("triggers"):
        return {"success": False, "error": "未能解析出时间触发条件", "confidence": parsed.get("confidence", 0)}

    # 构建完整 schedule entry（含内置动作字段）
    entry = {
        "id": _gen_id(),
        "name": parsed["name"],
        "triggers": parsed["triggers"],
        "template_id": parsed.get("template_id", "__quick_schedule__"),
        "template_name": "",
        "enabled": True,
        "last_run": None,
        "next_run": None,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if parsed.get("notify_message"):
        entry["notify_message"] = parsed["notify_message"]
    if parsed.get("launch_target"):
        entry["launch_target"] = parsed["launch_target"]

    # 写入 schedules.json
    schedules = _read_schedules()
    schedules.append(entry)
    _write_schedules(schedules)

    return {
        "success": True,
        "schedule": entry,
        "confidence": parsed.get("confidence", 0),
        "name": parsed["name"],
        "triggers": parsed["triggers"],
        "notify_message": parsed.get("notify_message", ""),
        "launch_target": parsed.get("launch_target", ""),
        "original": parsed.get("original", text),
    }
