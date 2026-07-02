# 当前架构

项目不是一个单体 agent，而是两个系统并存。

## 两条主线

| 系统 | 路径 | 职责 | 边界 |
| --- | --- | --- | --- |
| WinPilot desktop agent | `python/winpilot_home/winpilot/` | LLM 实时操控 Windows、感知、动作、验证、录制、记忆、编程子 agent | 不直接使用 `@feature`，不直接编辑流程图 |
| 流程图/DSL 系统 | `src/renderer/`、`python/engine/`、`python/features/` | 可视化编排、DSL 执行、`@feature`、MCP/HTTP/CLI | 不等同于 WinPilot 主 agent |

## 进程和端口

```mermaid
flowchart TB
    user["User"]
    renderer["React renderer<br/>src/renderer"]
    main["Electron main<br/>src/main"]
    api["Electron HTTP API<br/>127.0.0.1:7880<br/>src/main/api.ts"]
    wpboot["WinPilot launcher<br/>src/main/winpilotServer.ts"]
    serve["python/winpilot_home/serve.py<br/>dynamic local port"]
    wp["WinPilot FastAPI<br/>winpilot/server.py"]
    ipc["IPC adapter<br/>python/agent/__main__.py"]
    engine["Flow/DSL engine<br/>python/engine"]
    features["@feature registry<br/>python/features"]

    user --> renderer
    renderer --> main
    main --> api
    main --> wpboot --> serve --> wp
    main --> ipc --> wp
    renderer -->|"Agent iframe / REST"| wp
    renderer --> engine --> features
```

`serve.py` 会设置 `WINPILOT_LAUNCHED_VIA_SERVE=1`。`server.py` 只阻止真正的 `uvicorn winpilot.server:app` 直接启动，不阻止测试导入。

## WinPilot 内部

```mermaid
flowchart LR
    server["server.py"] --> router["agent/router.py"]
    router -->|"GUI task"| loop["AgentRunner<br/>agent/loop.py"]
    router -->|"coding/repo task"| coding["CodingRunner<br/>agent/coding.py"]
    loop --> rules["Task guards<br/>agent/task_rules.py"]
    loop --> donev["Done verifier<br/>agent/done_verifier.py"]
    loop --> context["Context guidance<br/>agent/context_guidance.py"]
    loop --> reflect["Reflection<br/>agent/reflection.py"]
    loop --> schemas["Tool schemas<br/>agent/tool_schemas.py"]
    loop --> tools["ToolSession<br/>agent/tools.py"]
    tools --> windowtools["WindowToolMixin<br/>agent/window_tools.py"]
    tools --> perceptiontools["PerceptionToolMixin<br/>agent/perception_tools.py"]
    tools --> interactiontools["InteractionToolMixin<br/>agent/interaction_tools.py"]
    tools --> systemtools["SystemToolMixin<br/>agent/system_tools.py"]
    tools --> fasttools["FastToolMixin<br/>agent/fast_tools.py"]
    tools --> plantools["PlanToolMixin<br/>agent/plan_tools.py"]
    tools --> filetools["FileToolMixin<br/>agent/file_tools.py"]
    tools --> memorytools["MemoryToolMixin<br/>agent/memory_tools.py"]
    tools --> desktoptools["DesktopToolMixin<br/>agent/desktop_tools.py"]
    tools --> fusion["fusion.observe<br/>UIA/Win32/OCR/CV/VLM"]
    tools --> action["action/executor.py<br/>SendInput/UIA/clipboard/screenshot"]
    tools --> verify["action/verify.py<br/>expect verification"]
    loop --> recorder["recorder/trace.py"]
    recorder --> wps[".wps.json"]
```

## 运行时状态机

```mermaid
stateDiagram-v2
    [*] --> idle
    idle --> pending: POST /api/chat
    pending --> running: background thread starts
    running --> done: done tool/plain reply passes done verifier
    running --> reply: plain assistant reply without tool evidence
    running --> fail: fail tool or max_steps
    running --> stopped: POST /api/stop
    running --> error: unhandled exception
    done --> idle
    reply --> idle
    fail --> idle
    stopped --> idle
    error --> idle
```

- `server.py` 的 `_RunState` 是服务级锁：同一时间只允许一个 agent、replay 或人工录制任务运行。
- `AgentRunner.run()` 是 GUI agent 主循环：注入任务/上下文/记忆 -> 调 LLM -> 处理 tool calls -> `ToolSession.dispatch()` -> 工具 mixin -> done gate/world/playbook/reflect。
- `ToolSession` 是每次运行的状态容器，保存最近 `Snapshot`、目标窗口 `hwnd`、world model、plan、undo stack；具体工具按窗口、感知、交互、系统、低延迟 batch、计划/终止、文件、记忆、桌面杂项拆到 mixin。
- `run_action_batch` 和 `screen_watch` 在 `agent/fast_tools.py`，内部进入 `fast/controller.py`。controller 会给 `draw_strokes/drag` 追加本地 `auto_visual_verify`；顶层 `draw_strokes/drag` 也会被 `AgentRunner` 自动包装成单动作 batch。验证充分时结果会带 `fast_path_advice`，减少额外 LLM round-trip。
- `ToolSession.dispatch()` 会给普通可见动作附加廉价 `screen_delta` 和 `action_target` 元信息。它用于定位“画面是否变化、变化时 foreground 是否仍是目标窗口”，不是语义完成证明；done 仍要看截图、目标窗口感知或外部证据。
- `launch` 的目标窗口会在发现后置前；`interaction_tools.py` 的输入动作优先使用 session target，而不是无条件使用当前 foreground；`action/verify.py` 在目标已知时会忽略无关 foreground，避免 Codex/日志/其他窗口 OCR 造成假成功。
- `agent/perception_tools.py` 在 UIA-off 绘图/画布任务上有 `canvas_fast_observe`：无 `query/full` 的 `observe` 跳过全窗口 OCR，直接提供 Win32/CV 和 `likely_canvas` 摘要；需要文字 grounding 时用 `observe(query=...)` 或 `observe(full=true)` 回到完整感知。
- BUS/WebSocket 给前端显示进度；`recorder/trace.py` 写可回放事件和截图；`agent/loop.py` 的 LLM trace 写每步真实 prompt/response，失败复盘必须优先看这些证据。

## 最近结构调整

- `src/renderer/store/index.ts` 不再复制 defaults/templates/localStorage helper；这些真实来源在 `store/defaults.ts` 和 `store/persistence.ts`。
- `agent/tool_schemas.py` 承担 GUI agent function schema，并使用短描述减少 LLM 上下文；`agent/tools.py` 保留运行态状态和 dispatch；`agent/window_tools.py`、`agent/perception_tools.py`、`agent/interaction_tools.py`、`agent/system_tools.py`、`agent/fast_tools.py`、`agent/plan_tools.py`、`agent/file_tools.py`、`agent/memory_tools.py`、`agent/desktop_tools.py` 分别承担窗口、感知、输入动作、系统/启动、低延迟 batch、计划/终止、文件、记忆复用、桌面杂项工具。
- `agent/task_rules.py` 承担 GUI task 分类、GUI-only 限制和 done gate guard 的纯规则；`agent/done_verifier.py` 承担 done 二次核验；`agent/context_guidance.py` 承担 planning/world/playbook 上下文提示；`agent/reflection.py` 承担反思检查点。
- `winpilot/utils/image_capture.py` 统一窗口截图转 JPEG base64。
- `python/engine/executor_types.py` 承担 legacy Flow/DSL executor 的共享状态、上下文和控制流异常，`executor.py` 继续作为 legacy 执行器维护。
- `python/core/screenshot.py` 保留 `capture_screen()`，兼容旧 Robot Framework keywords。
- 打包配置过滤 Python/runtime 的 pycache、测试、dist-info、运行数据；Electron 语言包限制为 `zh-CN` 和 `en-US`。

## 维护重点

- `agent/loop.py` 已拆出任务规则、done 核验、上下文提示和反思检查点，但仍承担 LLM 调用、工具循环和上下文折叠。
- `agent/tools.py` 已拆出 schema、窗口、感知、输入动作、系统/启动、低延迟 batch、计划/终止、文件、记忆、桌面杂项工具；后续新增工具优先放入对应 mixin。
- `python/engine/executor.py` 是旧 DSLExecutor，类型已拆到 `executor_types.py`；应冻结为 legacy 引擎，不继续扩张。

## 2026-07-03 低延迟 GUI 链路

```mermaid
flowchart LR
    llm["Main LLM<br/>任务理解/异常推理"] --> tools["ToolSession.dispatch"]
    tools --> launch["launch/focus_window<br/>目标窗口保护"]
    tools --> watcher["screen_watch<br/>cheap frame diff"]
    watcher --> roi["recent changed ROI<br/>30s cache"]
    roi --> observe["observe(changed_only=true)<br/>Win32/OCR/CV on ROI"]
    tools --> batch["run_action_batch<br/>fast controller skeleton"]
    observe --> world["WorldModel bulletin"]
    observe --> done["done_verifier<br/>fresh visible evidence"]
```

- `screen_watch` 是低成本变化检测层，不做语义理解；它保留 `recent_changed_regions`，让 LLM 慢一拍调用 `observe(changed_only=true)` 时仍能只看局部。
- `observe(changed_only=true)` 只在最近 ROI 上跑本地 Win32/OCR/CV；无 ROI 时明确 fallback 到普通 observe，方便 trace 排错。
- URL `launch` 会直接打开 URL，并用归一化窗口标题匹配后聚焦目标窗口，避免 Codex、日志窗口或其他前景窗口遮挡造成假感知。
- `done_verifier.py` 对“确认出现某个状态/标签”的任务有确定性收束：fresh screen OCR 看见目标文本才通过，可减少完成后的额外 LLM judge。
- 最新验证 run：`20260703-015021-f7167c`。它通过 `/api/chat` 跑 UIA-off 网页靶场，真实截图显示 `LOCAL_COUNTER_1`，changed-only observe 使用最近 ROI，done gate 以 fresh screen evidence 通过。
