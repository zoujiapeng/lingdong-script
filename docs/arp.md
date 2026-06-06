# ARP (Access Routing Protocol) 系统分析

> **文档**: `docs/arp.md`
> **核心文件**: `python/agent/tool_path_selector.py` (601行), `python/agent/planner.py` (859行)
> **前置阅读**: [system-map.md](system-map.md), [architecture.md](architecture.md)

---

## 1. ARP 架构总览

ARP = Access Routing Protocol — 工具访问路径选择协议。核心思想是**先选路径再选工具**，确保 Agent 总是使用最快、最可靠的方式完成任务。

```mermaid
graph TB
    subgraph ARP["ARP 系统"]
        Classifier["select_best_path()<br/>目标关键词 → 路径"]
        ClassifyTool["classify_tool()<br/>工具名 → 路径规则"]
        PathPrompt["build_path_prompt()<br/>LLM 提示词生成"]
        SystemPrompt["build_system_prompt()<br/>完整系统提示词"]
        Hidden["_AGENT_HIDDEN<br/>19 个隐藏工具"]
        Hints["_AGENT_HINTS<br/>36 个工具提示"]
    end

    subgraph Input["输入"]
        Goal["自然语言目标<br/>'打开百度搜索Python'"]
        ToolName["@feature 工具名<br/>'browser_navigate'"]
    end

    Goal --> Classifier
    ToolName --> ClassifyTool
    ClassifyTool --> PathPrompt
    PathPrompt --> SystemPrompt

    subgraph Output["输出"]
        Path["AccessPattern<br/>L0~L6 路径"]
        Prompt["按路径组织的<br/>LLM 提示词"]
    end
```

---

## 2. 7 级路径优先级

```mermaid
flowchart LR
    L0["L0: DIRECT_API<br/>~0.1s<br/>文件/系统/进程/剪贴板"]
    L1["L1: BROWSER_CDP<br/>~0.5s<br/>browser_* 工具"]
    L2["L2: WINDOWS_UIA<br/>~0.5s<br/>a11y_* 混合工具"]
    L3["L3: VISUAL_OCR<br/>~1-3s<br/>OCR 文字定位"]
    L4["L4: VISUAL_SOM<br/>~3-8s<br/>截图+元素检测"]
    L5["L5: MACRO<br/>~0.5-5s<br/>宏录制回放"]
    L6["L6: DSL_GEN<br/>~3-10s+LLM<br/>DSL 脚本生成"]

    L0 -.->|失败降级| L1 -.->|失败降级| L2 -.->|失败降级| L3 -.->|失败降级| L4 -.->|失败降级| L5 -.->|失败降级| L6
```

| 级别 | 枚举值 | 模式 | 成本 | 适用场景 | 工具数 |
|------|--------|------|------|----------|--------|
| **L0** | `DIRECT_API` | 直接 API | ~0.1s, 无界面依赖 | 文件操作、系统设置、进程管理、剪贴板、条件求值 | ~80 |
| **L1** | `BROWSER_CDP` | 浏览器 CDP | ~0.5s, 仅浏览器 | 任何 `browser_*` 工具、Web 相关目标 | ~42 |
| **L2** | `WINDOWS_UIA` | Windows UIA | ~0.5s, 标准控件 | `a11y_*` 工具、混合工具（click_target 等默认） | ~10 |
| **L3** | `VISUAL_OCR` | OCR 文字定位 | ~1-3s, 需截图 | `ocr_find_text`, `ocr_extract_all`, `find_element` | ~3 |
| **L4** | `VISUAL_SOM` | SoM 视觉标号 | ~3-8s, 需截图+检测 | 截图、图像匹配、颜色检测、摄像头、场景识别 | ~18 |
| **L5** | `MACRO` | 宏回放 | ~0.5-5s, 需预录制 | `macro_*` 工具 | ~10 |
| **L6** | `DSL_GEN` | DSL 脚本生成 | ~3-10s + LLM 调用 | AI 规划、多步骤工作流、"先...然后" | ~3 |

---

## 3. 路由分类规则

### 3.1 目标路由 (`select_best_path`)

```mermaid
flowchart TD
    Goal["目标文本"] --> DSL{"DSL 关键词?<br/>多步骤/工作流/流程/序列"}
    DSL -->|是| L6["L6: DSL_GEN"]
    DSL -->|否| Browser{"浏览器关键词?<br/>打开网页/搜索/浏览器/网站"}
    Browser -->|排除桌面搜索| Desktop{"桌面/文件搜索?"}
    Desktop -->|是| System
    Desktop -->|否| L1["L1: BROWSER_CDP"]
    
    Browser -->|否| System{"系统关键词?<br/>音量/亮度/关机/WiFi/电池"}
    System -->|是| L0["L0: DIRECT_API"]
    System -->|否| File{"文件操作?<br/>文件/目录/读取/写入/保存"}
    File -->|是| L0
    File -->|否| Process{"进程/窗口?<br/>打开/启动/运行/关闭/切换"}
    Process -->|是| L0
    Process -->|否| Clipboard{"剪贴板?<br/>复制/粘贴"}
    Clipboard -->|是| L0
    Clipboard -->|否| None["None (让 LLM 自行判断)"]
```

### 3.2 工具分类 (`classify_tool`)

| 规则 | 匹配条件 | 映射路径 |
|------|----------|----------|
| 浏览器 | `name.startswith("browser_")` | L1 BROWSER_CDP |
| UIA | `name.startswith("a11y_")` | L2 WINDOWS_UIA |
| 宏 | `name.startswith("macro_")` | L5 MACRO |
| AI 规划 | `category == "ai"` | L6 DSL_GEN |
| OCR | `ocr_find_text`, `ocr_extract_all`, `find_element`, `popup_close` | L3 VISUAL_OCR |
| 视觉 | `screenshot`, `find_image`, `color_check`, 等 | L4 VISUAL_SOM |
| 混合 | `click_target`, `type_text`, `hotkey`, 等 | L2 (默认, 运行时调整) |
| 系统 | `category == "system"`, 非视觉工具 | L0 DIRECT_API |
| 控制流 | `category == "control"` | L0 DIRECT_API |
| 感知 | `category == "perception"` (未匹配的) | L4 VISUAL_SOM |
| 兜底 | 以上都不匹配 | L0 DIRECT_API |

---

## 4. ARP 在 Agent 执行中的位置

```mermaid
sequenceDiagram
    participant LLM as LLM
    participant Agent as LingdongAgent
    participant ARP as ARP 路由器
    participant Planner as Planner
    participant Exec as PlanExecutor

    Note over Agent: 系统提示词阶段
    Agent->>ARP: build_path_prompt()
    ARP-->>Agent: 按7级路径组织的工具列表
    
    Note over Agent: 目标分类阶段
    Agent->>ARP: select_best_path(goal)
    ARP-->>Agent: path_hint (如 "直接 API")
    
    Note over Agent: 规划阶段 (Planned 模式)
    Agent->>Planner: plan(goal)
    Planner->>ARP: select_best_path(goal) → path_hint
    Planner->>LLM: 带 path_hint 的增强目标
    LLM-->>Planner: 结构化步骤列表 [{type, tool, params, ...}]
    Planner->>Planner: _steps_to_nodes() → Plan DAG
    Note over Planner: 每步使用 _STEP_TYPE_MAP<br/>设置 path_level + fallback_paths
    
    Note over Agent: 执行阶段
    Agent->>Exec: execute()
    loop 每步
        Exec->>Exec: 按 path_level 选择工具
        alt 工具失败
            Exec->>Exec: fallback_tools[更低路径]
            Exec->>Exec: fallback_paths[降级数字]
        end
        Exec->>Agent: step result
    end
    
    Note over Agent: 工具调用阶段 (text_protocol)
    Agent->>LLM: 系统提示含路径提示
    LLM-->>Agent: Tool: click_target
    Agent->>ARP: classify_tool("click_target")
    ARP-->>Agent: L2 (混合型)
    Agent->>Supervisor: execute("click_target", allow_uia=True)
```

---

## 5. 路径降级执行链

```mermaid
flowchart LR
    subgraph PlanNode["PlanNode 定义"]
        T["tool: browser_navigate<br/>path_level: 1"]
        F["fallback_tools: [browser_connect, click_target]<br/>fallback_paths: [3, 4]"]
    end

    subgraph ExecFlow["执行流程"]
        A["尝试 tool (L1)"] -->|成功| OK["✅ 完成"]
        A -->|失败| B["尝试 fallback[0] (L1)"]
        B -->|成功| OK
        B -->|失败| C["切换到<br/>fallback_path[0]=L3"]
        C -->|成功| OK
        C -->|失败| D["切换到<br/>fallback_path[1]=L4"]
        D -->|成功| OK
        D -->|失败| E{"节点是 critical?"}
        E -->|是| Fail["全计划失败 ❌"]
        E -->|否| Skip["跳过节点"]
    end
```

### _STEP_TYPE_MAP 示例

| 步骤类型 | 主工具 | path | fallback_tools | fallback_paths |
|----------|--------|------|----------------|----------------|
| `launch` | `launch_program` | 0 | `[run_command]` | `[1]` |
| `click` | `click_target` | 2 | `[a11y_click_element, image_click]` | `[3, 4]` |
| `text_click` | `click_target` | 3 | `[ocr_find_text]` | `[2, 4]` |
| `type` | `type_clipboard` | 0 | `[type_text, hotkey]` | `[2, 3]` |
| `browser_navigate` | `browser_navigate` | 1 | `[browser_connect, browser_new_tab]` | `[3, 4]` |
| `screenshot` | `screenshot` | 4 | `[]` | `[]` |

---

## 6. ARP 与 Planner 集成

```mermaid
graph TB
    subgraph Planner["Planner.plan()"]
        Phase1["Phase 1: 路径分类<br/>select_best_path(goal)"]
        Phase2["Phase 2: LLM 分解<br/>generate_flow_script()"]
        Phase3["Phase 3: 构建 Plan DAG<br/>_steps_to_nodes()"]
    end

    subgraph PlanExecutor["PlanExecutor.execute()"]
        Loop{"有就绪节点?"}
        Ready["get_ready_nodes()<br/>按 path_level 排序"]
        ExecNode["_execute_node()"]
        Fallback["fallback 链"]
        Cycle["依赖环检测"]
    end

    Goal["目标"] --> Phase1
    Phase1 -->|path_hint| Phase2
    Phase2 -->|结构化步骤| Phase3
    Phase3 -->|Plan DAG| Loop
    Loop -->|是| Ready
    Ready --> ExecNode
    ExecNode -->|失败| Fallback
    Fallback -->|重试| ExecNode
    Fallback -->|全部失败| Loop
    Loop -->|否| Done["计划完成"]
```
