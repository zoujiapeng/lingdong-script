"""
自然语言调度解析器 — 纯规则匹配，无需 LLM

将中文自然语言调度请求解析为结构化调度数据。

示例:
  "今晚9点提醒我写周报并打开企业微信"
  → { triggers: [{type: "cron", cron_expr: "0 21 * * *"}],
      notify_message: "写周报", launch_target: "企业微信" }
"""
from __future__ import annotations
import re
from datetime import datetime, timedelta
from typing import Optional

WEEKDAY_NAMES = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7,
}

DAY_OFFSET = {
    "今晚": 0, "今天": 0, "今夜": 0,
    "明早": 1, "明天": 1, "明晚": 1, "明日": 1,
    "后天": 2, "後天": 2,
    "大后天": 3, "大後天": 3,
}

PERIOD_HOUR_OFFSET = {
    "凌晨": 0, "清晨": 0, "早上": 0, "上午": 0, "早晨": 0,
    "中午": 0,
    "下午": 12, "傍晚": 12, "晚上": 12, "夜晚": 12,
}

# Chinese numeral to digit mapping for time expressions
_CN_TO_DIGIT = {
    "零": "0", "一": "1", "二": "2", "三": "3", "四": "4",
    "五": "5", "六": "6", "七": "7", "八": "8", "九": "9",
}

_WEEKDAY_CN = set("一二三四五六日天")


def _normalize_cn_numbers(text: str) -> str:
    """将中文数字转换为阿拉伯数字（仅限时间相关上下文）。

    "今晚十一点半" → "今晚11点半"
    "零点三十分" → "0点30分"
    """
    # Step 1: Chinese compound tens for minute/second (三十分→30分, 三十五分→35分)
    # Must run BEFORE single-digit conversion so "五" in "三十五" is still Chinese
    text = re.sub(
        r"([一二三四五])十([一二三四五六七八九])?\s*(?=分|秒)",
        lambda m: str({
            "一": 10, "二": 20, "三": 30, "四": 40, "五": 50
        }[m.group(1)] + ({
            "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
            "六": 6, "七": 7, "八": 8, "九": 9,
        }.get(m.group(2), 0) if m.group(2) else 0)),
        text
    )

    # Step 2: Single digits 一~九 before time units
    for cn, d in _CN_TO_DIGIT.items():
        text = re.sub(f"{cn}(?=点|分|秒|时|半|后|刻|号)", d, text)

    # Step 3: Compound tens + ones: 十N → 1N (e.g. 十二→12, 十一→11, 十五号→15号)
    text = re.sub(
        r"十(\d)(?=点|分|秒|时|半|号)",
        lambda m: str(10 + int(m.group(1))), text
    )

    # Step 4: Standalone 十 before time/date unit → 10
    text = re.sub(r"(?<!\d)十(?=点|分|秒|时|半|刻|号)", "10", text)

    return text

# Period keywords ordered by length (longer first for finding in text)
_PERIOD_WORDS = sorted(PERIOD_HOUR_OFFSET.keys(), key=len, reverse=True)


def _find_period(text: str) -> str:
    """从文本中找出时段词（上午/下午/晚上等）"""
    for p in _PERIOD_WORDS:
        if p in text:
            return p
    return ""


def _adjust_hour(h: int, period: str = "", day_str: str = "") -> int:
    """根据时段词调整小时数"""
    if period in PERIOD_HOUR_OFFSET:
        if PERIOD_HOUR_OFFSET[period] == 12 and h < 12:
            return h + 12
        if PERIOD_HOUR_OFFSET[period] == 0 and h >= 12:
            return 0 if h == 12 or h == 24 else h - 12
    elif day_str in ("今晚", "明晚", "今夜"):
        if h < 6:
            return h + 12
        elif 6 <= h < 12:
            return h + 12
        elif h == 12:
            return 0
    return h


def _next_weekday(target_dow: int, include_today: bool = False) -> datetime:
    """Return the next occurrence of target_dow (1=Mon...7=Sun) from today."""
    today = datetime.now()
    current = today.weekday() + 1
    days_ahead = target_dow - current
    if days_ahead < 0 or (days_ahead == 0 and not include_today):
        days_ahead += 7
    return today + timedelta(days=days_ahead)


def _parse_time(text: str) -> dict:
    """解析时间表达式，返回 {cron?, interval_seconds?, confidence, matched, remaining}"""
    # 先转换中文数字 → 阿拉伯数字
    remaining = _normalize_cn_numbers(text.strip())

    # ── 0. 特殊处理: 零点 → 0点 (如果 _normalize_cn_numbers 没处理到) ──
    remaining = remaining.replace("零点", "0点")

    # ── 1. cron: 每星期X / 每周X ──
    m = re.search(
        r"每(?:星期|周)([一二三四五六日天])"
        r"\s*(?:凌晨|清晨|早上|上午|早晨|中午|下午|傍晚|晚上|夜晚)?\s*"
        r"(\d+)\s*点\s*(?:(\d+)\s*分?|半)?",
        remaining
    )
    if m:
        dow = WEEKDAY_NAMES.get(m.group(1))
        if dow and dow <= 7:
            h = int(m.group(2))
            has_half = "半" in (m.group(0) or "")
            explicit_min = int(m.group(3)) if m.group(3) else 0
            mi = explicit_min or (30 if has_half else 0)
            period = _find_period(m.group(0))
            h = _adjust_hour(h, period)
            if 0 <= h <= 23 and 0 <= mi <= 59:
                cron_dow = 0 if dow == 7 else dow
                return {
                    "cron": f"{mi} {h} * * {cron_dow}",
                    "confidence": 95,
                    "matched": m.group(0),
                    "remaining": remaining.replace(m.group(0), "", 1).strip(),
                }

    # ── 2. cron: 每工作日 / 每个工作日 ──
    m = re.search(
        r"(?:每工作日|每个工作日|每工作天|每个工作天)"
        r"\s*(?:凌晨|清晨|早上|上午|早晨|中午|下午|傍晚|晚上|夜晚)?\s*"
        r"(\d+)\s*点\s*(?:(\d+)\s*分?|半)?",
        remaining
    )
    if m:
        h = int(m.group(1))
        has_half = "半" in (m.group(0) or "")
        explicit_min = int(m.group(2)) if m.group(2) else 0
        mi = explicit_min or (30 if has_half else 0)
        period = _find_period(m.group(0))
        h = _adjust_hour(h, period)
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return {
                "cron": f"{mi} {h} * * 1-5",
                "confidence": 95,
                "matched": m.group(0),
                "remaining": remaining.replace(m.group(0), "", 1).strip(),
            }

    # ── 3. cron: 每天 / 每日 / 每晚 / 每早 ──
    m = re.search(
        r"(?:每天|每日|天天|每晚|每夜|每早|每朝)"
        r"\s*(?:凌晨|清晨|早上|上午|早晨|中午|下午|傍晚|晚上|夜晚)?\s*"
        r"(\d+)\s*点\s*(?:(\d+)\s*分?|半)?",
        remaining
    )
    if m:
        h = int(m.group(1))
        has_half = "半" in (m.group(0) or "")
        explicit_min = int(m.group(2)) if m.group(2) else 0
        mi = explicit_min or (30 if has_half else 0)
        period = _find_period(m.group(0))
        h = _adjust_hour(h, period)
        # Auto-adjust: 每晚/每夜 默认+12h 如果是小数字
        prefix = m.group(0)
        if any(p in prefix for p in ("每晚", "每夜")):
            if h < 12:
                h += 12
                if h == 24:
                    h = 0
            elif h == 12:
                h = 0  # "每晚12点" → 0:00 (午夜)
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return {
                "cron": f"{mi} {h} * * *",
                "confidence": 95,
                "matched": m.group(0),
                "remaining": remaining.replace(m.group(0), "", 1).strip(),
            }

    # ── 3b. cron: 每周末 ──
    m = re.search(
        r"每周末"
        r"\s*(?:凌晨|清晨|早上|上午|早晨|中午|下午|傍晚|晚上|夜晚)?\s*"
        r"(\d+)\s*点\s*(?:(\d+)\s*分?|半)?",
        remaining
    )
    if m:
        h = int(m.group(1))
        has_half = "半" in (m.group(0) or "")
        explicit_min = int(m.group(2)) if m.group(2) else 0
        mi = explicit_min or (30 if has_half else 0)
        period = _find_period(m.group(0))
        h = _adjust_hour(h, period)
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return {
                "cron": f"{mi} {h} * * 0,6",
                "confidence": 95,
                "matched": m.group(0),
                "remaining": remaining.replace(m.group(0), "", 1).strip(),
            }

    # ── 3c. cron: 每月X号 ──
    m = re.search(
        r"每月(\d+)[号日]"
        r"\s*(?:凌晨|清晨|早上|上午|早晨|中午|下午|傍晚|晚上|夜晚)?\s*"
        r"(\d+)\s*点\s*(?:(\d+)\s*分?|半)?",
        remaining
    )
    if m:
        dom = int(m.group(1))
        h = int(m.group(2))
        has_half = "半" in (m.group(0) or "")
        explicit_min = int(m.group(3)) if m.group(3) else 0
        mi = explicit_min or (30 if has_half else 0)
        period = _find_period(m.group(0))
        h = _adjust_hour(h, period)
        if 1 <= dom <= 31 and 0 <= h <= 23 and 0 <= mi <= 59:
            return {
                "cron": f"{mi} {h} {dom} * *",
                "confidence": 95,
                "matched": m.group(0),
                "remaining": remaining.replace(m.group(0), "", 1).strip(),
            }

    # ── 4. cron: 每N小时 ──
    m = re.search(r"每(\d+)\s*小时", remaining)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 23:
            return {
                "cron": f"0 */{n} * * *",
                "confidence": 80,
                "matched": m.group(0),
                "remaining": remaining.replace(m.group(0), "", 1).strip(),
            }

    # ── 4b. 每半小时 ──
    m = re.search(r"每半\s*(?:个)?\s*小时", remaining)
    if m:
        return {
            "interval_seconds": 1800,
            "confidence": 80,
            "matched": m.group(0),
            "remaining": remaining.replace(m.group(0), "", 1).strip(),
        }

    # ── 5. 相对时间 ──
    # 5a. 半分钟/半分钟后
    m = re.search(r"半\s*分钟\s*(?:后|以后)", remaining)
    if m:
        return {
            "interval_seconds": 30,
            "confidence": 75,
            "matched": m.group(0),
            "remaining": remaining.replace(m.group(0), "", 1).strip(),
        }

    # 5b. N小时后
    m = re.search(r"(\d+)\s*小时\s*(?:后|以后)", remaining)
    if m:
        n = int(m.group(1))
        return {
            "interval_seconds": n * 3600,
            "confidence": 80,
            "matched": m.group(0),
            "remaining": remaining.replace(m.group(0), "", 1).strip(),
        }

    # 5c. N分钟+[N]分? 混合 (如 2小时30分后)
    m = re.search(
        r"(\d+)\s*(?:小时|时)\s*(\d+)\s*分\s*(?:后|以后)",
        remaining
    )
    if m:
        total = int(m.group(1)) * 3600 + int(m.group(2)) * 60
        return {
            "interval_seconds": total,
            "confidence": 75,
            "matched": m.group(0),
            "remaining": remaining.replace(m.group(0), "", 1).strip(),
        }

    # 5d. 每隔N分钟/每隔N秒
    m = re.search(r"每隔\s*(\d+)\s*分钟", remaining)
    if m:
        n = int(m.group(1))
        return {
            "interval_seconds": n * 60,
            "confidence": 80,
            "matched": m.group(0),
            "remaining": remaining.replace(m.group(0), "", 1).strip(),
        }

    m = re.search(r"每隔\s*(\d+)\s*秒(?:钟)?", remaining)
    if m:
        n = int(m.group(1))
        return {
            "interval_seconds": n,
            "confidence": 80,
            "matched": m.group(0),
            "remaining": remaining.replace(m.group(0), "", 1).strip(),
        }

    # 5e. N分钟后 / N秒后 (保留原逻辑)
    m = re.search(r"(\d+)\s*分钟\s*(?:后|以后)", remaining)
    if m:
        n = int(m.group(1))
        return {
            "interval_seconds": n * 60,
            "confidence": 85,
            "matched": m.group(0),
            "remaining": remaining.replace(m.group(0), "", 1).strip(),
        }

    m = re.search(r"(\d+)\s*秒(?:钟)?\s*(?:后|以后)", remaining)
    if m:
        n = int(m.group(1))
        return {
            "interval_seconds": n,
            "confidence": 85,
            "matched": m.group(0),
            "remaining": remaining.replace(m.group(0), "", 1).strip(),
        }

    # ── 6. 固定时间: 今晚/明天/后天/大后天 + 时间 ──
    m = re.search(
        r"(今晚|今天|今夜|明早|明天|明晚|明日|后天|後天|大后天|大後天)"
        r"\s*(凌晨|清晨|早上|上午|早晨|中午|下午|傍晚|晚上|夜晚)?\s*"
        r"(\d+)\s*点\s*(?:(\d+)\s*分?|半)?",
        remaining
    )
    if m:
        day_str = m.group(1)
        period = m.group(2) or ""
        h = int(m.group(3))
        has_half = "半" in (m.group(0) or "")
        explicit_min = int(m.group(4)) if m.group(4) else 0
        mi = explicit_min or (30 if has_half else 0)

        h = _adjust_hour(h, period, day_str)

        if 0 <= h <= 23 and 0 <= mi <= 59:
            offset = DAY_OFFSET.get(day_str, 0)
            target = datetime.now() + timedelta(days=offset)
            target = target.replace(hour=h, minute=mi, second=0, microsecond=0)

            if offset > 0 or target > datetime.now():
                return {
                    "cron": f"{mi} {h} {target.day} {target.month} *",
                    "confidence": 92,
                    "matched": m.group(0),
                    "remaining": remaining.replace(m.group(0), "", 1).strip(),
                }
            return {
                "cron": f"{mi} {h} * * *",
                "confidence": 60,
                "matched": m.group(0),
                "remaining": remaining.replace(m.group(0), "", 1).strip(),
            }

    # ── 6b. 相对星期: 下周一/本周五 ──
    m = re.search(
        r"(下|本|这)(?:周|星期|週)\s*([一二三四五六日天])"
        r"\s*(?:凌晨|清晨|早上|上午|早晨|中午|下午|傍晚|晚上|夜晚)?\s*"
        r"(\d+)\s*点\s*(?:(\d+)\s*分?|半)?",
        remaining
    )
    if m:
        prefix = m.group(1)
        dow = WEEKDAY_NAMES.get(m.group(2))
        if dow and dow <= 7:
            h = int(m.group(3))
            has_half = "半" in (m.group(0) or "")
            explicit_min = int(m.group(4)) if m.group(4) else 0
            mi = explicit_min or (30 if has_half else 0)
            period = _find_period(m.group(0))
            h = _adjust_hour(h, period)
            if 0 <= h <= 23 and 0 <= mi <= 59:
                include_today = prefix in ("本", "这")
                target = _next_weekday(dow, include_today)
                target = target.replace(hour=h, minute=mi, second=0, microsecond=0)
                if include_today and target <= datetime.now():
                    target += timedelta(days=7)
                return {
                    "cron": f"{mi} {h} {target.day} {target.month} *",
                    "confidence": 90,
                    "matched": m.group(0),
                    "remaining": remaining.replace(m.group(0), "", 1).strip(),
                }

    # ── 8. 模糊时间: 9点左右/9点多/9点刚过 ──
    m = re.search(
        r"^[^\d]*?(\d+)\s*点\s*(左右|多|刚过|出头|来钟)",
        remaining
    )
    if m:
        h = int(m.group(1))
        period = _find_period(m.group(0))
        h = _adjust_hour(h, period)
        if 0 <= h <= 23:
            return {
                "cron": f"0 {h} * * *",
                "confidence": 55,
                "fuzzy": True,
                "matched": m.group(0),
                "remaining": remaining.replace(m.group(0), "", 1).strip(),
            }

    # ── 7. 裸时间: 只有 "X点" 无前缀 ──
    m = re.search(
        r"^[^\d]*?(\d+)\s*点\s*(?:(\d+)\s*分?|半)?",
        remaining
    )
    if m:
        h = int(m.group(1))
        has_half = "半" in (m.group(0) or "")
        explicit_min = int(m.group(2)) if m.group(2) else 0
        mi = explicit_min or (30 if has_half else 0)
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return {
                "cron": f"{mi} {h} * * *",
                "confidence": 40,
                "matched": m.group(0),
                "remaining": remaining.replace(m.group(0), "", 1).strip(),
            }

    return {"confidence": 0, "matched": "", "remaining": text}


def _parse_actions(text: str) -> dict:
    """解析动作，返回 {notify_message?, launch_target?, template_id?, confidence}"""
    result = {}
    confidence = 0

    # 提醒我X
    m = re.search(r"提醒我\s*(.+?)(?:$|并|而且|还有|然后|再)", text)
    if not m:
        m = re.search(r"提醒我\s*(.+)", text)
    if m:
        notify = m.group(1).strip()
        notify = re.sub(r"(?:并|而且|还有|然后|再)\s*$", "", notify).strip()
        if notify:
            result["notify_message"] = notify
            confidence += 40

    # 打开X / 启动X
    m = re.search(r"(?:打开|启动|开启|运行程序)\s*(.+?)(?:$|并|而且|还有|然后|再)", text)
    if not m:
        m = re.search(r"(?:打开|启动|开启|运行程序)\s*(.+)", text)
    if m:
        launch = m.group(1).strip()
        if launch:
            result["launch_target"] = launch
            confidence += 30

    # 运行X / 执行X（模板）
    m = re.search(r"(?:运行|执行|启动流程)\s*(.+?)(?:$|并|而且|还有|然后|再)", text)
    if not m:
        m = re.search(r"(?:运行|执行|启动流程)\s*(.+)", text)
    if m:
        run = m.group(1).strip()
        if run:
            result["template_id"] = run
            confidence += 30

    result["confidence"] = min(confidence, 90)
    return result


# ── 风险等级判定 ─────────────────────────────────────────

RISK_CRITICAL_KW = ["删除", "格式化", "清除文件", "卸载", "清空", "擦除"]
RISK_CONFIRM_KW = ["关机", "重启", "注销", "关闭电脑", "重新启动", "重启电脑", "睡眠", "休眠"]
RISK_NOTIFY_KW = ["打开", "启动", "运行", "执行", "运行程序", "开启"]


def _detect_risk_level(text: str, actions: dict) -> str:
    """根据文本和动作判断风险等级: critical > confirm > notify > info"""
    t = text.strip()
    # 原文含销毁性操作 → critical
    for kw in RISK_CRITICAL_KW:
        if kw in t:
            return "critical"
    # 原文含系统状态变更 → confirm
    for kw in RISK_CONFIRM_KW:
        if kw in t:
            return "confirm"
    # 有启动/运行行为 → notify
    if actions.get("launch_target") or actions.get("template_id"):
        for kw in RISK_NOTIFY_KW:
            if kw in t:
                return "notify"
    # 纯提醒 → info
    return "info"


def is_schedule_intent(text: str) -> bool:
    """检查文本是否可能是调度意图"""
    keywords = [
        "提醒", "定时", "预约", "计划", "安排",
        "今晚", "明天", "后天", "明早", "明晚",
        "每天", "每日", "每周", "每工作日",
        "分钟后", "秒后", "小时后",
        "星期",  # 每星期一~日
        "周",    # 每周一~日
        "每晚", "每夜", "每早", "每朝",  # 每日变体
        "每隔",       # 每隔N分钟/秒
        "零点", "零时",  # 午夜
        "号",         # 每月X号
        "点左右",     # 模糊时间
    ]
    t = text.strip()
    if len(t) < 4:
        return False
    return any(k in t for k in keywords)


def parse(text: str) -> dict:
    """解析自然语言调度文本，返回结构化调度数据

    返回:
        dict 包含:
          - name: str — 调度名
          - triggers: list[dict] — 触发器列表
          - risk_level: str — "info" | "notify" | "confirm" | "critical"
          - notify_message: str (可选)
          - launch_target: str (可选)
          - template_id: str (可选)
          - confidence: int (0-99)
          - original: str — 原始文本
    """
    original = text.strip()

    time_result = _parse_time(original)
    action_result = _parse_actions(original)

    confidence = 0
    if time_result["confidence"] > 0:
        confidence += time_result["confidence"] * 0.55
    if action_result["confidence"] > 0:
        confidence += action_result["confidence"] * 0.45

    name = "定时任务"
    if action_result.get("notify_message"):
        name = action_result["notify_message"]
    elif action_result.get("launch_target"):
        name = f"打开{action_result['launch_target']}"
    elif action_result.get("template_id"):
        name = f"运行{action_result['template_id']}"

    triggers = []
    if time_result.get("cron"):
        triggers.append({"type": "cron", "cron_expr": time_result["cron"]})
    elif time_result.get("interval_seconds") is not None:
        triggers.append({"type": "interval", "interval_seconds": time_result["interval_seconds"]})

    if not triggers and action_result["confidence"] > 0:
        confidence = min(confidence, 40)

    result = {
        "name": name,
        "triggers": triggers,
        "risk_level": _detect_risk_level(original, action_result),
        "confidence": min(round(confidence), 99),
        "original": original,
    }
    if time_result.get("fuzzy"):
        result["fuzzy"] = True
    if action_result.get("notify_message"):
        result["notify_message"] = action_result["notify_message"]
    if action_result.get("launch_target"):
        result["launch_target"] = action_result["launch_target"]
    if action_result.get("template_id"):
        result["template_id"] = action_result["template_id"]

    return result
