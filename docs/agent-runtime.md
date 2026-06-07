# Agent Runtime 深度分析

> **文档**: `docs/agent-runtime.md`
> **核心文件**: `python/agent/agent.py`, `prompts.py`, `__main__.py`, `supervisor.py`, `config.py`

---

## 1. Agent 执行模式总览

```mermaid
graph TB
    User["用户自然语言目标"] --> Run["agent.run(goal)"]

    subgraph UR["_unified_run(goal, config) - 执行体"]
        S["config.resolve() / 展平配置 / sync 参数 / 注入记忆"]
        D{"execution_mode"}
        S --> D
        D -->|flow_chart| FC["_run_direct() / generate_flow_script()<br/>返回流程图 JSON，不下场执行"]
        D -->|multimodal| MM["run_text_protocol()<br/>截图直接送 LLM（多模态视觉理解）"]
        D -->|text| TX["run_text_protocol()<br/>SoM 标号 + UIA + OCR（本地视觉定位）"]
        D -->|offline| OFF["直接返回 success<br/>不调 LLM，不截屏"]
    end

    subgraph PT["run() 统一后处理（所有模式共享）"]
        P1["reflection.reflect_task()<br/>错误分析 / 改进建议"]
        P2["memory 更新<br/>add_rule / add_preference / add_episode"]
        P3["state_persistence.save_run()<br/>运行摘要持久化"]
        P1 --> P2 --> P3
    end

    Run --> UR
    UR --> PT
```

> **旧路由已移除**: 之前有 `resolve_auto()` / `agent_config_from_legacy()` / `LangGraph` / `Planned` / `ComputerUse` 等 5 种旧执行模式。现改为 `_unified_run()` 统一入口：内部 `config.resolve()` 展平配置 → `execution_mode` 4 种分发，所有模式共享 `run()` 的后处理管道（反思 + 记忆 + 持久化）。

---

## 2. 4 种执行模式对比

| 特性 | text_protocol (多模态) | text_protocol (纯文本) | flow_chart | offline |
|------|----------------------|----------------------|------------|---------|
| **适用模型** | 多模态 (GPT-4o, Claude Vision) | 纯文本 (DeepSeek, MIMO) | 任意 | 任意 |
| **通信协议** | Tool:/Args:/Done: 文本块 | Tool:/Args:/Done: 文本块 | generate_flow_script | 无 LLM |
| **循环方式** | LLM→解析→执行→截图→重复 | LLM→解析→执行→截图→重复 | 无循环，立即返回 | 无循环 |
| **视觉方案** | 多模态直传 (截图送LLM) | SoM/UIA/OCR | N/A | N/A |
| **LLM 调用** | 每步1次 | 每步1次 | 仅1次 | 0次 |
| **执行成本** | 多次LLM调用 | 多次LLM调用 | 1次LLM | 免费离线 |
| **截图** | 每步1张 (送模型) | 每步1张 (SoM) | 无 | 无 |
| **错误处理** | 重试/停止/跳过 + 去重 | 重试/停止/跳过 + 去重 | N/A | N/A |
| **对话模式** | ✅ guidance_queue | ✅ guidance_queue | ❌ | ❌ |
| **记忆/反思** | ✅ | ✅ | ❌ | ❌ |
| **最佳场景** | 多模态模型 | 纯文本模型 + SoM | 仅流程图生成 | 调试/无网络 |
| **入口方法** | `run_text_protocol()` | `run_text_protocol()` | `_run_direct()` | — |

---

## 3. text_protocol 执行循环 (最常用模式)

```mermaid
sequenceDiagram
    participant User as 用户
    participant Agent as LingdongAgent
    participant LLM as LLM API
    participant Sup as Supervisor
    participant Registry as @Feature Registry
    participant Screen as 屏幕

    User->>Agent: run_text_protocol(goal)
    
    Note over Agent: 初始化配置
    Agent->>Agent: _begin_prompt_trace(goal)
    Agent->>Agent: _ensure_registry()
    Agent->>Agent: 构建系统提示词
    Agent->>Agent: 注入记忆 _inject_memory_prompt()
    
    Note over Agent: 首次截图+感知
    Agent->>Screen: capture_b64()
    Agent->>Agent: _apply_som() → SoM 标号
    Agent->>Agent: _compact_som_label() → 紧凑文本
    Agent->>Agent: 获取窗口列表+屏幕上下文
    
    Note over Agent: 构建首轮消息
    Agent->>LLM: POST chat/completions (系统提示+截图+上下文)
    LLM-->>Agent: Tool: click\nArgs:\n  target: #3
    
    loop 每步执行
        Note over Agent: 解析 LLM 回复
        Agent->>text_protocol: parse_response(content)
        text_protocol-->>Agent: ("tool", "click", {target: "#3"})
        
        alt 多工具批处理
            Agent->>text_protocol: parse_tool_blocks(content)
            text_protocol-->>Agent: [("click",{...}), ("type",{...})]
        end
        
        Note over Agent: 执行前快照
        Agent->>Agent: _runtime_snapshot() → verify_before
        
        loop 批量工具块
            Note over Agent: SoM #N 解析
            Agent->>Sup: execute("click", {target: "#3"})
            
            Note over Sup: 8 步监督流水线
            Sup->>Sup: 0. SoM 解引用 (#3→坐标)
            Sup->>Sup: 1. UIA 语义定位/窗口限定/预聚焦
            Sup->>Sup: 2. 视觉定位兜底 (经验库+VLM)
            Sup->>Sup: 3. 窗口焦点安全
            Sup->>Sup: 4. 执行+重试
            Sup->>Sup: 5. 自动对话框处理
            Sup->>Sup: 6. 屏幕稳定等待
            Sup->>Sup: 7. 失败诊断截图
            Sup-->>Agent: {success: true, result: ...}
            
            alt 窗口上下文变化
                Agent->>Agent: verify_after 检测title变化
                Agent->>Agent: break (刷新感知)
            end
        end
        
        Note over Agent: 构建下一步消息
        Agent->>Agent: _append_step_feedback()
        Agent->>Agent: _strip_binary() 去除base64
        
        alt 对话模式
            Agent->>Agent: _process_chat_messages()
        end
        
        Agent->>Screen: capture_b64() 新截图
        Agent->>Agent: _apply_som() 新SoM
        Agent->>LLM: 执行结果+新截图+上下文
        
        LLM-->>Agent: Tool: ... / Done: Summary
        
        alt 连续失败>=max
            Agent->>Agent: 终止
        end
    end
    
    Note over Agent: 执行后处理
    Agent->>Reflection: reflect_task()
    Agent->>Memory: add_rule/add_preference/add_episode
    Agent->>Persistence: save_run()
    Agent-->>User: agent_done 事件
```

---

## 4. Supervisor 监督执行流水线

```mermaid
flowchart TD
    A["Supervisor.execute(name, args)"] --> B{"S. 脚本工具?"}
    B -->|是| B1["run_python_script()<br/>跳过视觉管道"]
    B -->|否| C0["步骤按顺序全部执行<br/>(以下每步可选,<br/>条件满足则执行)"]
    
    C0 --> C["0. SoM 解引用<br/>is_gui + _last_som_map"]
    C --> D["1a. UIA 语义定位<br/>uia_enabled + is_gui<br/>+ not prefer_local_vision"]
    D --> E["1b. 窗口感知OCR限定<br/>window_aware_ocr + is_gui"]
    E --> F["1c. UIA 预聚焦<br/>uia_enabled + uia_pre_focus<br/>+ type_text/clipboard"]
    F --> G["1d. 视觉定位<br/>auto_learn or multimodal"]
    G --> H2a["2a. 防重复启动<br/>name == click_target"]
    H2a --> H2b["2b. 启动程序去重<br/>(launch时检测已有窗口)"]
    H2b --> H2c["2c. 窗口焦点安全<br/>is_gui → ensure_safe_window()"]
    H2c --> I["3. 执行+重试<br/>execute_with_type_conv()<br/>GUI: 2次指数退避"]
    I --> J["4. 失败策略<br/>skip / replan / stop"]
    J --> K["5. 自动对话框处理<br/>GUI/hotkey/截图/启动后"]
    K --> L["6. 屏幕稳定等待<br/>wait_screen_stable()"]
    L --> M["7. 失败诊断截图<br/>GUI失败时"]
    M --> N["8. 记录最后启动程序<br/>_last_launched"]
```

---

## 5. IPC 协议 (stdin/stdout JSON)

### Electron → Agent (stdin 命令)

| 命令 | 字段 | 说明 |
|------|------|------|
| `start` | `goal`, `config` (含 `agent_config` dict) | 启动 Agent |
| `stop` | — | 优雅停止 |
| `guidance` | `text` | 注入用户指引 |
| `chat` | `text` | 对话模式消息 |
| `status` | — | 查询状态 |
| `memory_query` | — | 查询记忆快照 |

### Agent → Electron (stdout 事件)

| 事件 | 关键字段 | 触发时机 |
|------|----------|----------|
| `agent_start` | goal, engine, model | Agent 开始运行 |
| `plan_state` | plan (DAG) | 计划状态更新 |
| `step_complete` | step_num, tool, args, status, result, thought | 每步完成 |
| `agent_done` | status, total_steps, summary, fail_reason | Agent 结束 |
| `log` | level, message | 任何日志 |
| `agent_reflection` | step_num, insight, rule | 每步反思 |
| `agent_learned` | rules_count, prefs_count, insights, mistakes | 学习结果 |
| `memory_snapshot` | preferences, rules, episodes 计数 | 记忆快照 |
| `agent_chat_response` | text | AI 聊天回复 |
| `status_report` | running, stopped | 状态响应 |
| `error` | message, traceback | 未处理错误 |

---

## 6. AgentConfig 配置系统

```mermaid
graph TB
    subgraph Frontend["AgentView 配置面板"]
        ExecutionMode["执行模式<br/>flow_chart/multimodal/text/offline"]
        TextVision["文本视觉配置<br/>UIA/OCR/YOLO/SoM/窗口/本地视觉"]
        LLMConfig["LLM 配置<br/>API Key/Base/Model/温度"]
        Safety["安全限制<br/>max_steps=20<br/>max_failures=5<br/>on_error=retry"]
    end

    Frontend --> |localStorage| IPC
    
    subgraph IPC["IPC 传输"]
        Config["agent_config dict<br/>(execution_mode + 全局参数 + 子配置)"]
    end

    Config --> AgentCfg["agent/config.py<br/>AgentConfig.from_dict()"]
    AgentCfg --> Flat["config.resolve()<br/>→ 扁平 dict（参数合并覆盖）"]
    
    Flat --> Dispatch{"_unified_run()<br/>按 execution_mode"}
    Dispatch --> |flow_chart| FC["_run_direct()"]
    Dispatch --> |multimodal| MM["run_text_protocol()<br/>多模态"]
    Dispatch --> |text| TX["run_text_protocol()<br/>纯文本"]
    Dispatch --> |offline| OFF["直接返回"]
```

### config.py — AgentConfig 数据结构

```python
@dataclass
class AgentConfig:
    execution_mode: str = "flow_chart"  # flow_chart | multimodal | text | offline
    
    # 全局
    memory_enabled: bool = False
    max_steps: int = 20
    max_consecutive_failures: int = 5
    min_success_rate: float = 0.1
    temperature: float = 0.1
    max_tokens: int = 4096
    llm_retries: int = 2
    screenshot_quality: int = 60
    sliding_window_size: int = 0
    on_error: str = "retry"
    
    # 子配置（mode-specific）
    flow_chart_cfg: FlowChartConfig
    multimodal_cfg: MultimodalConfig
    text_vision_cfg: TextVisionConfig
    offline_cfg: OfflineConfig

    def resolve() -> dict:   # 展平成运行时扁平字典
```

**子配置一: FlowChartConfig**
- `supervise: bool` — 监督执行开关

**子配置二: MultimodalConfig**
- `screenshot_quality: int` — 唯一可配参数 (default 60)

**子配置三: TextVisionConfig** (纯文本模型成功路径)
- `uia_enabled: bool` — UIA 控件树 (default False)
- `ocr_enabled: bool` — OCR 文字检测 (default True)
- `yolo_enabled: bool` — YOLO 图标检测 (default False)
- `som_enabled: bool` — SoM 标号 (default True)
- `window_aware_ocr: bool` — 窗口限定 (default True)
- `local_vision: bool` — 本地视觉引擎 (default False)
- `visual_strategy: str` — som | ocr_llm | blind_guess | cli_path
- `llm_protocol: str` — text | function_calling
- `planning: str` — per_step | pre_plan

---

## 7. Agent 进程生命周期

```mermaid
sequenceDiagram
    participant Electron as Electron Main
    participant Agent as Agent 子进程
    participant LLM as LLM API
    
    Electron->>Electron: kill旧进程 (stop+timeout+kill)
    Electron->>Electron: nextAgentToken() 新token
    
    Electron->>Agent: spawn("python -u -m agent")
    Note over Agent: 初始化: DPI感知, 加载.env
    
    Agent-->>Electron: (等待300ms启动完成)
    
    Electron->>Agent: {"type": "start", "goal": "...", "config": {"agent_config": {...}}}
    
    Note over Agent: 预加载 feature 注册表 (~8s 主线程)
    Note over Agent: 创建 LingdongAgent
    Note over Agent: AgentConfig.from_dict() → resolve() → _unified_run()
    Note over Agent: 启动执行线程
    
    loop 执行循环
        Agent->>LLM: HTTP POST /chat/completions
        LLM-->>Agent: 工具调用回复
        Agent->>Agent: 解析+执行
        
        alt 安全超时 300s
            Electron->>Agent: SIGTERM
        end
        
        Agent-->>Electron: step_complete 事件
    end
    
    Agent-->>Electron: agent_done 事件
    
    alt 用户停止
        Electron->>Agent: {"type": "stop"}
        Agent->>Agent: set stop_event
        Agent-->>Electron: agent_done (stopped)
    end
```

### 统一路由流程

```
__main__.py: _run_agent()
  +-- cfg["agent_config"] → AgentConfig.from_dict()
  |   +-- execution_mode 指定 4 种之一
  |   +-- 子配置由 from_dict() 自动填入
  +-- agent.run(agent_config=cfg)
  |   +-- _unified_run(goal, config)
  |   |   +-- config.resolve() → 扁平 dict
  |   |   +-- 更新 supervisor / LLM 参数
  |   |   +-- _inject_memory_prompt()
  |   |   +-- 按 execution_mode 分发:
  |   |   |   flow_chart → _run_direct()
  |   |   |   multimodal → run_text_protocol()
  |   |   |   text       → run_text_protocol()
  |   |   |   offline    → 直接返回
  |   |   +-- return result
  |   +-- post-task (统一，所有模式)
  |   |   +-- reflection.reflect_task()
  |   |   +-- memory 更新 (rule / preference / episode)
  |   |   +-- state_persistence.save_run()
  |   +-- return result
```
