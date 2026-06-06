# Memory 系统深度分析

> **文档**: `docs/memory.md`
> **核心文件**: `python/agent/memory.py` (267行), `reflection.py` (242行), `visual_locator.py` (379行), `experience_db.py` (137行)
> **前置阅读**: [system-map.md](system-map.md)

---

## 1. 记忆系统总览

```mermaid
graph TB
    subgraph Memory["记忆系统"]
        ShortTerm["短期记忆<br/>(agent.py 内联)<br/>_steps, _goal, _step_num"]
        LongTerm["长期记忆<br/>(memory.py MemoryStore)<br/>偏好/规则/任务经验"]
        Visual["视觉经验<br/>(experience_db + visual_locator)<br/>SHA256 索引→模板截图"]
        History["运行历史<br/>(state_persistence.py)<br/>RunSummary 50条"]
    end

    subgraph Storage["持久化"]
        JSON_Files["~/.lingdong/agent_memory/<br/>preferences.json<br/>rules.json<br/>episodes.json"]
        History_File["~/.lingdong/<br/>agent_history.json"]
        Exp_DB["python/experience/<br/>db.json + templates/*.png"]
    end

    ShortTerm -->|任务完成| LongTerm
    ShortTerm -->|任务完成| History
    LongTerm --> JSON_Files
    History --> History_File
    Visual --> Exp_DB
```

---

## 2. 三轨记忆体系

| 维度 | 短期记忆 | 长期记忆 | 视觉经验 |
|------|----------|----------|----------|
| **存储位置** | agent.py 实例变量 | `~/.lingdong/agent_memory/*.json` | `python/experience/db.json` + PNG |
| **生命周期** | 单次任务 | 跨会话持久 | 跨会话持久 |
| **容量** | 无限制(执行中) | 200偏好+200规则+100片段 | ~数千条目 |
| **访问方式** | 直接引用 | `build_context_prompt(goal)` | `locate() → OpenCV/VLM` |
| **更新时机** | 每步写入 | 任务完成后 | VLM 定位成功后 |
| **数据结构** | list[dict] | Preference/BehavioralRule/Episode | SHA256→{bbox,confidence,path} |

---

## 3. 长期记忆 (MemoryStore)

```mermaid
classDiagram
    class MemoryStore {
        -RLock _lock
        -list[Preference] _preferences
        -list[BehavioralRule] _rules
        -list[Episode] _episodes
        +add_preference(key, value, confidence)
        +add_rule(rule_text, trigger_context, confidence, category)
        +add_episode(run_id, goal, success, steps, summary, insights)
        +get_relevant_rules(goal)
        +get_relevant_episodes(goal, max_results)
        +build_context_prompt(goal)
        +detect_tensions()
        +record_rule_failure(rule_text)
    }

    class Preference {
        key: str
        value: str
        confidence: float
        source_run_id: str
        domain: str
    }

    class BehavioralRule {
        rule_text: str
        trigger_context: str
        confidence: float
        category: str
        success_count: int
        failure_count: int
        confirmed_count: int
        contradicted_count: int
        evidence: list
        is_guardrail: bool
        last_applied: str
    }

    class Episode {
        run_id: str
        goal: str
        success: bool
        total_steps: int
        summary: str
        insights: list[str]
        mistakes: list[str]
        tools_used: list[str]
        elapsed_ms: int
    }

    MemoryStore --> Preference
    MemoryStore --> BehavioralRule
    MemoryStore --> Episode
```

### 记忆注入流程

```mermaid
sequenceDiagram
    participant Agent as LingdongAgent
    participant Memory as MemoryStore
    participant LLM as LLM (推理/反思)
    
    Note over Agent: 任务开始前
    Agent->>Memory: build_context_prompt(goal)
    Memory->>Memory: 查询相关规则 (Jaccard token 匹配)
    Memory->>Memory: 查询相关偏好 (domain 匹配)
    Memory->>Memory: 查询相似片段 (目标文本相似度)
    Memory-->>Agent: 注入系统提示词
    
    Note over Agent: 任务执行中
    Agent->>Memory: record_rule_failure("...")
    Memory->>Memory: confidence *= 0.8
    
    Note over Agent: 每步完成后
    Agent->>Reflection: reflect_step(step, goal)
    Reflection->>Reflection: Stage 1: 模式匹配 (0成本)
    Reflection->>Reflection: Stage 2: LLM 分析 (有条件)
    Reflection-->>Agent: {insight, rule, confidence}
    
    alt 有规则提取
        Agent->>Memory: add_rule(rule_text, ...)
    end
    
    Note over Agent: 任务完成后
    Agent->>Reflection: reflect_task(goal, steps, success)
    Reflection->>LLM: 任务反思提示词
    LLM-->>Reflection: {rules, preferences, insights, mistakes}
    Reflection-->>Agent: 结构化结果
    
    Agent->>Memory: add_rule() × N
    Agent->>Memory: add_preference() × N
    Agent->>Memory: add_episode() × 1
```

---

## 4. 反思引擎 (ReflectionEngine)

```mermaid
flowchart TD
    subgraph StepReflection["每步反思 (reflect_step)"]
        InputStep["step: {tool, result, args, ...}"] --> Stage1["Stage 1: PatternAnalyzer<br/>模式匹配错误信息<br/>7类正则规则"]
        Stage1 -->|有信号| Stage2["Stage 2: LLM 深度分析<br/>仅当错误>20字<br/>或 Stage 1 有信号"]
        Stage1 -->|无信号| ReturnEmpty["返回空 dict"]
        Stage2 --> StepResult["{insight, rule, confidence}"]
    end

    subgraph TaskReflection["任务反思 (reflect_task)"]
        InputTask["goal + steps + success + elapsed"] --> BuildSummary["构建最后15步摘要"]
        BuildSummary --> LLMCall["带 TASK_REFLECTION_PROMPT<br/>的 LLM 调用"]
        LLMCall --> Parse["JSON 解析<br/>{} → ```json``` → 首 {}"]
        Parse --> TaskResult["{assessment, rules[],<br/>preferences[],<br/>insights[], mistakes[]}"]
    end

    subgraph PatternAnalyzer["PatternAnalyzer 7 类规则"]
        PR1["显式记忆: 'remember:' / '记住'"]
        PR2["护栏: 'don't X unless Y'"]
        PR3["英文纠正模式"]
        PR4["中文纠正模式: '应该' / '最好'"]
        PR5["积极反馈: 'good' / '正确'"]
        PR6["误报过滤"]
        PR7["非纠正短语"]
    end
```

---

## 5. 视觉经验系统

```mermaid
flowchart TB
    subgraph Store["存储经验"]
        Step["Agent 执行步骤"] --> Sig["_signature()<br/>SHA256(step_type|target_text|window_title)[:16]"]
        VLM["VLM 定位成功"] --> Crop["crop_and_save()<br/>裁剪按钮小图"]
        Crop --> Template["templates/*.png"]
        Sig --> DB["db.json<br/>{signature: {template_path,<br/>bbox, confidence, count}}"]
    end

    subgraph Retrieve["检索经验"]
        Query["new step"] --> Sig2["_signature()"]
        Sig2 --> Find["experience_db.find()<br/>查 db.json"]
        Find -->|命中| Match["visual_locator.template_match()<br/>OpenCV matchTemplate<br/>TM_CCOEFF_NORMED"]
        Match -->|≥0.75| Coord["返回 {center_x, center_y}"]
        Find -->|未命中| VLM_Backup["visual_locator.vlm_locate()<br/>多模态 LLM API"]
        VLM_Backup -->|成功| Store
        VLM_Backup -->|失败| Error["返回 error"]
    end

    subgraph Agent_Flow["Agent 调用链路"]
        Action["Supervisor.execute()"] --> Locate["apply_visual_locator()"]
        Locate --> Ret["连续3次失败→自动禁用"]
        Locate --> Retrieve
    end
```

---

## 6. 记忆生命周期

```mermaid
flowchart LR
    subgraph Create["创建"]
        Task["任务完成"] --> Rules["LLM 提取规则"]
        Task --> Prefs["LLM 提取偏好"]
        Task --> Ep["记录任务片段"]
        VLM_Success["VLM 定位成功"] --> Vis["存储视觉经验"]
    end

    subgraph Decay["衰减"]
        Age["7天未使用"] --> Times["confidence *= 0.9/周"]
        Times --> Min["最低保留 0.2"]
        Fail["规则违反"] --> Decay2["confidence *= 0.8"]
    end

    subgraph Tension["矛盾检测"]
        Detect["detect_tensions()"] --> Guard["护栏 vs 动作规则"]
        Detect --> Conflict["相同语境矛盾规则"]
    end

    subgraph Inject["注入"]
        Query_Goal["新目标"] --> Build["build_context_prompt(goal)"]
        Build --> RelRules["top 5 规则"]
        Build --> RelPrefs["top 5 偏好"]
        Build --> RelEps["top 2 片段"]
        RelRules --> SysPrompt["注入系统提示词"]
        RelPrefs --> SysPrompt
        RelEps --> SysPrompt
    end

    Create --> Decay
    Create --> Tension
    Decay --> Inject
    Tension --> Inject
```
