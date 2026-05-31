"""
MemoryStore — 长期记忆系统 (偏好 + 行为规则 + 任务经验)

为 Agent 提供跨任务学习能力：
  - Preferences: 用户偏好 (写作风格、工具选择、领域习惯)
  - Rules: 行为规则 ("提交前先验证", "搜索时过滤经停>2h")
  - Episodes: 任务经验 (目标、结果、洞察、错误)

所有操作使用 threading.RLock() 保证线程安全。
JSON 文件存储于 ~/.lingdong/agent_memory/

参考模式:
  - claude-reflect 的两阶段捕获 (regex → AI validation)
  - claude-soul 的张量检测与证据追踪
"""

import json
import logging
import os
import re
import threading
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("agent.memory")

MEMORY_DIR = os.path.join(os.path.expanduser("~"), ".lingdong", "agent_memory")
PREFERENCES_FILE = os.path.join(MEMORY_DIR, "preferences.json")
RULES_FILE = os.path.join(MEMORY_DIR, "rules.json")
EPISODES_FILE = os.path.join(MEMORY_DIR, "episodes.json")

# 存储容量上限
MAX_PREFERENCES = 200
MAX_RULES = 200
MAX_EPISODES = 100


# ── Data classes ──────────────────────────────────────────────

@dataclass
class Preference:
    key: str
    value: str
    confidence: float = 0.5
    source_run_id: str = ""
    domain: str = "general"
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        if not self.updated_at:
            self.updated_at = self.created_at


@dataclass
class BehavioralRule:
    rule_text: str
    trigger_context: str = "general"
    confidence: float = 0.5
    category: str = "approach"  # verification | tool_choice | approach | timing | guardrail
    source_run_id: str = ""
    created_at: str = ""
    success_count: int = 0
    failure_count: int = 0
    # v2 fields — evidence tracking
    confirmed_count: int = 0
    contradicted_count: int = 0
    evidence: list = field(default_factory=list)  # evidence snippets
    is_guardrail: bool = False  # constraint-type ("don't X unless Y")
    last_applied: str = ""  # ISO timestamp of last use

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


@dataclass
class Episode:
    run_id: str = ""
    goal: str = ""
    success: bool = False
    total_steps: int = 0
    summary: str = ""
    insights: list = field(default_factory=list)
    mistakes: list = field(default_factory=list)
    tools_used: list = field(default_factory=list)
    elapsed_ms: int = 0
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


# ── Serialization helpers ─────────────────────────────────────

def _pref_to_dict(p: Preference) -> dict:
    return asdict(p)


def _rule_to_dict(r: BehavioralRule) -> dict:
    return asdict(r)


def _episode_to_dict(e: Episode) -> dict:
    return asdict(e)


def _dict_to_pref(d: dict) -> Preference:
    return Preference(**{k: v for k, v in d.items() if k in Preference.__dataclass_fields__})


def _dict_to_rule(d: dict) -> BehavioralRule:
    return BehavioralRule(**{k: v for k, v in d.items() if k in BehavioralRule.__dataclass_fields__})


def _dict_to_episode(d: dict) -> Episode:
    return Episode(**{k: v for k, v in d.items() if k in Episode.__dataclass_fields__})


# ══════════════════════════════════════════════════════════════
# PatternAnalyzer — fast regex pattern detection
# 灵感来自 claude-reflect 的两阶段捕获架构
# ══════════════════════════════════════════════════════════════

class PatternAnalyzer:
    """快速模式匹配分析器，无需 LLM 调用即可识别用户反馈类型。

    两阶段 pipeline:
      Stage 1 (同步, 零成本): regex 模式匹配
      Stage 2 (可选, LLM): 语义验证（由调用方决定是否执行）
    """

    # 显式记忆标记 — 最高优先级
    EXPLICIT_PATTERNS = [
        (r"remember:", "remember", 0.90),
    ]

    # 护栏模式 — "不要做 X 除非 Y"
    GUARDRAIL_PATTERNS = [
        (r"don't (?:add|include|create) .{1,40} unless", "dont-unless-asked", 0.90),
        (r"only (?:change|modify|edit|touch) what I (?:asked|requested|said)", "only-what-asked", 0.90),
        (r"stop (?:refactoring|changing|modifying|editing) (?:unrelated|other|surrounding)", "stop-unrelated", 0.90),
        (r"don't (?:over-engineer|add extra|be too|make unnecessary)", "dont-over-engineer", 0.85),
        (r"don't (?:refactor|reorganize|restructure) (?:unless|without)", "dont-refactor-unless", 0.85),
        (r"leave .{1,30} (?:alone|unchanged|as is)", "leave-alone", 0.85),
        (r"don't (?:add|include) (?:comments|docstrings|type hints|annotations) (?:unless|to code)", "dont-add-annotations", 0.85),
        (r"(?:minimal|minimum|only necessary) changes", "minimal-changes", 0.80),
    ]

    # 英文纠正模式
    CORRECTION_PATTERNS = [
        (r"^no[,. ]+", "no", True),
        (r"^don't\b|^do not\b", "dont", True),
        (r"^stop\b|^never\b", "stop-never", True),
        (r"that's (?:wrong|incorrect)|that is (?:wrong|incorrect)", "that-is-wrong", True),
        (r"^actually[,. ]", "actually", False),
        (r"^I meant\b|^I said\b", "i-meant-said", True),
        (r"^I told you\b|^I already told\b", "i-told-you", True),
        (r"use .{1,30} (?:not|instead of)\b", "use-x-not-y", True),
    ]

    # 中文纠正模式
    CJK_CORRECTION_PATTERNS = [
        (r"^不是[，,. ]", "bushi", True),
        (r"^错了|^錯了", "cuole", True),
        (r"不要.{0,20}要", "buyao-yao", True),
        (r"不是.{0,20}而是", "bushi-ershi", True),
    ]

    # 积极反馈模式
    POSITIVE_PATTERNS = [
        (r"perfect!|exactly right|that's exactly", "perfect", 0.70),
        (r"that's what I wanted|great approach", "great-approach", 0.70),
        (r"keep doing this|love it|excellent|nailed it", "keep-doing", 0.70),
        ("很好|不错|完美|对了|正是", "cjk-positive", 0.70),
    ]

    # 误报过滤 — 这些不是学习信号
    FALSE_POSITIVE_PATTERNS = [
        r"[?？]$",
        r"^(?:please|can you|could you|would you|help me)\b",
        r"(?:help|fix|check|review|figure out|set up)\s+(?:this|that|it|the)\b",
        r"(?:error|failed|could not|cannot|can't|unable to)\s+\w+",
        r"^(?:ok|okay|alright)[,.]?\s+(?:so|now|let)",
        r"^I (?:need|want|would like)\b",
    ]

    # 非纠正短语 — 看起来像纠正但实际不是
    NON_CORRECTION_PHRASES = [
        r"^no\s+problem",
        r"^no\s+worries",
        r"^no\s+need\b",
        r"^don't\s+worry",
        r"^don't\s+mind",
        r"^never\s+mind",
    ]

    @classmethod
    def analyze(cls, text: str) -> dict:
        """对文本进行模式分析，返回分类结果。

        Returns:
            dict with keys:
              - type: "explicit" | "guardrail" | "correction" | "positive" | None
              - patterns: list[str] — 命中的模式名
              - confidence: float 0-1
              - sentiment: "correction" | "positive"
        """
        result = {"type": None, "patterns": [], "confidence": 0.0, "sentiment": "correction"}
        if not text or not text.strip():
            return result
        stripped = text.strip()

        # 简短消息过滤
        has_cjk = bool(re.search(r'[　-鿿豈-﫿가-힯]', stripped))
        short_threshold = 2 if has_cjk else 4
        if len(stripped) <= short_threshold:
            return result

        # 1. 显式记忆标记 (最高优先级, 跳过误报过滤)
        for pat, name, conf in cls.EXPLICIT_PATTERNS:
            if re.search(pat, text, re.IGNORECASE):
                return {"type": "explicit", "patterns": [name], "confidence": conf, "sentiment": "correction"}

        # 2. 护栏模式 (高优先级, 跳过误报过滤)
        matched_guardrails = []
        for pat, name, conf in cls.GUARDRAIL_PATTERNS:
            if re.search(pat, text, re.IGNORECASE):
                matched_guardrails.append((name, conf))
        if matched_guardrails:
            names = [n for n, _ in matched_guardrails]
            conf = max(c for _, c in matched_guardrails)
            return {"type": "guardrail", "patterns": names, "confidence": conf, "sentiment": "correction"}

        # 误报过滤 — 仅对 correction/positive 级别生效
        for fp in cls.FALSE_POSITIVE_PATTERNS:
            if re.search(fp, text, re.IGNORECASE):
                return result

        # 非纠正短语
        for nc in cls.NON_CORRECTION_PHRASES:
            if re.search(nc, text, re.IGNORECASE):
                return result

        # 3. 积极反馈
        matched_positive = []
        for pat, name, conf in cls.POSITIVE_PATTERNS:
            if re.search(pat, text, re.IGNORECASE):
                matched_positive.append(name)
        if matched_positive:
            return {"type": "positive", "patterns": matched_positive, "confidence": 0.70, "sentiment": "positive"}

        # 4. 纠正模式 (英文)
        matched_corrections = []
        has_strong = False
        pattern_count = 0
        for pat, name, is_strong in cls.CORRECTION_PATTERNS:
            if re.search(pat, text, re.IGNORECASE):
                matched_corrections.append(name)
                pattern_count += 1
                if is_strong:
                    has_strong = True

        # 5. 纠正模式 (中文)
        matched_cjk = []
        cjk_strong = False
        for pat, name, is_strong in cls.CJK_CORRECTION_PATTERNS:
            if re.search(pat, stripped):
                matched_cjk.append(name)
                if is_strong:
                    cjk_strong = True

        if matched_corrections or matched_cjk:
            patterns = matched_corrections + matched_cjk
            # 置信度计算
            if len(patterns) >= 3:
                conf = 0.85
            elif len(patterns) >= 2:
                conf = 0.75
            elif has_strong or cjk_strong:
                conf = 0.70
            else:
                conf = 0.55
            # 长度调整
            if len(stripped) < 80:
                conf = min(0.90, conf + 0.10)
            elif len(stripped) > 300:
                conf = max(0.50, conf - 0.15)
            return {"type": "correction", "patterns": patterns, "confidence": conf, "sentiment": "correction"}

        return result


# ══════════════════════════════════════════════════════════════
# 主存储
# ══════════════════════════════════════════════════════════════

class MemoryStore:
    """线程安全的长期记忆存储。"""

    def __init__(self):
        self._lock = threading.RLock()
        os.makedirs(MEMORY_DIR, exist_ok=True)
        self._preferences: list[Preference] = []
        self._rules: list[BehavioralRule] = []
        self._episodes: list[Episode] = []
        self._load_all()
        # 启动时衰减过期记忆
        try:
            self._decay_stale()
        except Exception:
            pass

    # ── Load / Save ──

    def _load_all(self):
        with self._lock:
            self._preferences = self._load_file(PREFERENCES_FILE, _dict_to_pref)
            self._rules = self._load_file(RULES_FILE, _dict_to_rule)
            self._episodes = self._load_file(EPISODES_FILE, _dict_to_episode)

    def _save_all(self):
        with self._lock:
            self._save_file(PREFERENCES_FILE, self._preferences, _pref_to_dict, MAX_PREFERENCES)
            self._save_file(RULES_FILE, self._rules, _rule_to_dict, MAX_RULES)
            self._save_file(EPISODES_FILE, self._episodes, _episode_to_dict, MAX_EPISODES)

    @staticmethod
    def _load_file(path: str, converter) -> list:
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            items = data.get("items", []) if isinstance(data, dict) else data
            return [converter(item) for item in items]
        except (json.JSONDecodeError, OSError, TypeError) as e:
            logger.warning("Failed to load %s: %s", path, e)
            return []

    @staticmethod
    def _save_file(path: str, items: list, converter, max_items: int):
        trimmed = items[-max_items:] if len(items) > max_items else items
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "items": [converter(item) for item in trimmed]},
                          f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.warning("Failed to save %s: %s", path, e)

    # ── Preferences ──

    def add_preference(self, key: str, value: str, confidence: float = 0.5,
                       source_run_id: str = "", domain: str = "general"):
        """添加或更新偏好。同 key 合并，取较高 confidence。"""
        with self._lock:
            existing = [p for p in self._preferences if p.key == key]
            now = datetime.now(timezone.utc).isoformat()
            if existing:
                p = existing[-1]
                if confidence > p.confidence or value != p.value:
                    p.value = value
                    p.confidence = max(confidence, p.confidence)
                    p.updated_at = now
                    p.source_run_id = source_run_id or p.source_run_id
            else:
                self._preferences.append(Preference(
                    key=key, value=value, confidence=confidence,
                    source_run_id=source_run_id, domain=domain,
                    created_at=now, updated_at=now,
                ))
            self._save_all()

    def get_preferences(self, domain: Optional[str] = None,
                        min_confidence: float = 0.3) -> list[Preference]:
        """按 domain 和最低置信度筛选。"""
        with self._lock:
            result = list(self._preferences)
        if domain:
            result = [p for p in result if p.domain == domain]
        if min_confidence > 0:
            result = [p for p in result if p.confidence >= min_confidence]
        return sorted(result, key=lambda p: p.confidence, reverse=True)

    def get_preference(self, key: str) -> Optional[Preference]:
        with self._lock:
            for p in self._preferences:
                if p.key == key:
                    return p
        return None

    # ── Rules ──

    def add_rule(self, rule_text: str, trigger_context: str = "general",
                 confidence: float = 0.5, category: str = "approach",
                 source_run_id: str = "", is_guardrail: bool = False,
                 evidence: Optional[str] = None):
        """添加规则。同文本合并，累计 confidence。"""
        with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            existing = [r for r in self._rules if r.rule_text == rule_text]
            if existing:
                r = existing[-1]
                r.confidence = max(r.confidence, confidence)
                r.success_count += 1
                r.last_applied = now
                r.source_run_id = source_run_id or r.source_run_id
                # 累积证据
                if evidence and evidence not in r.evidence:
                    r.evidence.append(evidence)
                    if len(r.evidence) > 10:
                        r.evidence = r.evidence[-10:]
            else:
                self._rules.append(BehavioralRule(
                    rule_text=rule_text, trigger_context=trigger_context,
                    confidence=confidence, category=category,
                    source_run_id=source_run_id, created_at=now,
                    success_count=1, is_guardrail=is_guardrail,
                    evidence=[evidence] if evidence else [],
                    last_applied=now,
                ))
            self._save_all()

    def get_rules(self, trigger_context: Optional[str] = None,
                  min_confidence: float = 0.3) -> list[BehavioralRule]:
        """按上下文和最低置信度筛选。"""
        with self._lock:
            result = list(self._rules)
        if trigger_context:
            result = [r for r in result if r.trigger_context == trigger_context
                      or r.trigger_context == "general"]
        if min_confidence > 0:
            result = [r for r in result if r.confidence >= min_confidence]
        return sorted(result, key=lambda r: r.confidence, reverse=True)

    def get_relevant_rules(self, goal: str, min_confidence: float = 0.3) -> list[BehavioralRule]:
        """根据目标关键词推断 trigger_context。"""
        tokens = set(re.sub(r'[^\w\s]', '', goal.lower()).split())
        with self._lock:
            candidates = [r for r in self._rules if r.confidence >= min_confidence]
        matched = []
        for r in candidates:
            ctx_tokens = set(r.trigger_context.lower().split())
            if tokens & ctx_tokens or r.trigger_context == "general":
                matched.append(r)
        return sorted(matched, key=lambda r: r.confidence, reverse=True)[:5]

    def record_rule_failure(self, rule_text: str, evidence: Optional[str] = None):
        """标记一条规则失败过 (降低可信度, 累计反证)。"""
        with self._lock:
            for r in self._rules:
                if r.rule_text == rule_text:
                    r.failure_count += 1
                    r.contradicted_count += 1
                    r.confidence = max(0.1, r.confidence * 0.8)
                    if evidence and evidence not in r.evidence:
                        r.evidence.append(evidence)
                        if len(r.evidence) > 10:
                            r.evidence = r.evidence[-10:]
                    break
            self._save_all()

    def record_rule_success(self, rule_text: str, evidence: Optional[str] = None):
        """标记一条规则被成功应用 (提高可信度)。"""
        with self._lock:
            for r in self._rules:
                if r.rule_text == rule_text:
                    r.success_count += 1
                    r.confirmed_count += 1
                    r.last_applied = datetime.now(timezone.utc).isoformat()
                    r.confidence = min(1.0, r.confidence + 0.05)
                    if evidence and evidence not in r.evidence:
                        r.evidence.append(evidence)
                        if len(r.evidence) > 10:
                            r.evidence = r.evidence[-10:]
                    break
            self._save_all()

    def _decay_stale(self):
        """衰减过时规则的置信度。超过 7 天未使用的规则降低置信度。"""
        with self._lock:
            now = datetime.now(timezone.utc)
            for r in self._rules:
                if r.last_applied:
                    try:
                        last = datetime.fromisoformat(r.last_applied)
                        days_since = (now - last).days
                        if days_since > 7:
                            # 每周衰减 10%, 最低保留 0.2
                            decay = 0.9 ** (days_since // 7)
                            r.confidence = max(0.2, r.confidence * decay)
                    except (ValueError, TypeError):
                        pass
            self._save_all()

    # ── Tension Detection ──

    def detect_tensions(self, min_confidence: float = 0.5) -> list[dict]:
        """检测矛盾规则 (同一 trigger_context 下的对立规则)。

        灵感来自 claude-soul 的 tension-detector。

        Returns:
            list of {rule_a, rule_b, context, severity}
        """
        with self._lock:
            rules = [r for r in self._rules if r.confidence >= min_confidence]
        tensions = []
        # 按上下文分组
        groups: dict[str, list] = {}
        for r in rules:
            ctx = r.trigger_context
            groups.setdefault(ctx, []).append(r)
        for ctx, group in groups.items():
            if len(group) < 2:
                continue
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    a, b = group[i], group[j]
                    # 类别冲突: verification vs approach, 或 guardrail vs 普通规则
                    if a.is_guardrail != b.is_guardrail:
                        tensions.append({
                            "rule_a": a.rule_text,
                            "rule_b": b.rule_text,
                            "context": ctx,
                            "severity": "medium",
                            "type": "guardrail_vs_action",
                        })
                    # 语义否定: "do X" vs "don't do X"
                    a_lower = a.rule_text.lower()
                    b_lower = b.rule_text.lower()
                    if ("don't" in a_lower and "don't" not in b_lower
                            and a_lower.replace("don't", "").strip()
                            in b_lower):
                        tensions.append({
                            "rule_a": a.rule_text,
                            "rule_b": b.rule_text,
                            "context": ctx,
                            "severity": "high",
                            "type": "direct_contradiction",
                        })
        return tensions

    # ── Quick pattern analysis (static, no LLM) ──

    @staticmethod
    def quick_analyze(text: str) -> dict:
        """快速分析用户反馈文本，零 LLM 成本。

        基于 claude-reflect 的模式匹配系统。

        Returns:
            dict with type, patterns, confidence, sentiment
            或 {"type": None, ...} 表示无匹配
        """
        return PatternAnalyzer.analyze(text)

    # ── Episodes ──

    def add_episode(self, episode: Episode):
        with self._lock:
            self._episodes.append(episode)
            self._save_all()

    def get_recent_episodes(self, limit: int = 5) -> list[Episode]:
        with self._lock:
            return list(self._episodes[-limit:])

    def find_similar_episodes(self, goal: str, limit: int = 3) -> list[Episode]:
        """关键词匹配找相似历史任务。"""
        tokens = set(re.sub(r'[^\w\s]', '', goal.lower()).split())
        if not tokens:
            return []
        with self._lock:
            scored = []
            for ep in self._episodes:
                ep_tokens = set(re.sub(r'[^\w\s]', '', ep.goal.lower()).split())
                intersection = tokens & ep_tokens
                union = tokens | ep_tokens
                score = len(intersection) / len(union) if union else 0
                if score > 0.1:
                    scored.append((score, ep))
            scored.sort(key=lambda x: x[0], reverse=True)
            return [ep for _, ep in scored[:limit]]

    # ── Context Assembly ──

    def build_context_prompt(self, goal: str) -> str:
        """组装历史经验为 LLM 提示词块。"""
        parts = []

        # 偏好 (top 5)
        prefs = self.get_preferences(min_confidence=0.4)[:5]
        if prefs:
            lines = "\n".join(f"- {p.key}: {p.value} (confidence: {p.confidence})"
                              for p in prefs)
            parts.append(f"用户偏好:\n{lines}")

        # 规则 (top 5, 按目标相关性)
        rules = self.get_relevant_rules(goal)[:5]
        if rules:
            lines = "\n".join(
                f"- [{'护栏' if r.is_guardrail else '规则'}] {r.rule_text} "
                f"(confidence: {r.confidence})"
                for r in rules
            )
            parts.append(f"行为规则:\n{lines}")

        # 相似经验 (top 2)
        episodes = self.find_similar_episodes(goal, limit=2)
        if episodes:
            lines = "\n".join(
                f"- {ep.goal} ({'成功' if ep.success else '失败'}, "
                f"{ep.total_steps}步) — {ep.summary[:80]}"
                for ep in episodes
            )
            parts.append(f"相关任务经验:\n{lines}")

        if not parts:
            return ""

        return "## 历史经验\n" + "\n\n".join(parts)

    # ── Statistics ──

    def stats(self) -> dict:
        with self._lock:
            return {
                "preferences": len(self._preferences),
                "rules": len(self._rules),
                "episodes": len(self._episodes),
            }

    def clear(self):
        """清空所有记忆。"""
        with self._lock:
            self._preferences.clear()
            self._rules.clear()
            self._episodes.clear()
            self._save_all()
