# AI 接手速览

这份文件只写当前事实和排障入口。更完整说明看 `README.md` 和 `docs/`。

## 当前项目形态

1. `python/winpilot_home/winpilot/`
   - 当前 WinPilot desktop agent。
   - 用户始终面对一个 Agent。
   - 唯一主循环是 `agent/loop.py` 的 `AgentRunner`。
   - GUI / Computer Use 是默认能力；编程、仓库、Terminal、Browser、Replay 是同一 runner 下按任务启用的 Runtime/Capability。
   - `agent/coding.py` 只是旧导入兼容 shim，不再实现独立 CodingRunner。

2. `src/renderer/` + `python/engine/` + `python/features/`
   - legacy 流程图 / DSL / `@feature` 自动化系统。
   - 仍在使用，但不是 WinPilot 主 agent 的工具池。
   - WinPilot 成功 trace 会生成规范 `.automation.json`，同时保留 `.wps.json` 兼容导出；Studio 从 Automation IR 进入编辑和 ARP 编译。

## 优先阅读

| 文件 | 作用 |
| --- | --- |
| `python/winpilot_home/winpilot/server.py` | FastAPI control plane、RuntimeJob、trace、replay、schedule 入口 |
| `python/winpilot_home/serve.py` | 支持的 WinPilot 服务启动入口 |
| `python/winpilot_home/winpilot/config.py` | 默认配置和本机 `python/winpilot_home/config.json` 读取 |
| `python/winpilot_home/winpilot/agent/router.py` | 只构建统一 `AgentRunner` |
| `python/winpilot_home/winpilot/agent/runtime_profile.py` | 每次 run 的 Runtime/Capability 开关 |
| `python/winpilot_home/winpilot/agent/loop.py` | LLM 主循环、上下文折叠、工具调用、done gate 编排 |
| `python/winpilot_home/winpilot/agent/tool_schemas.py` | OpenAI function schema 和 schema profile |
| `python/winpilot_home/winpilot/agent/tools.py` | `ToolSession` 状态、dispatch、目标窗口解析 |
| `python/winpilot_home/winpilot/agent/*_tools.py` | 窗口、感知、动作、系统、计划、文件、记忆等工具 mixin |
| `python/winpilot_home/winpilot/fast/` | 低延迟 controller、subgoal、watcher、input locator |
| `python/winpilot_home/winpilot/action/raw_device.py` | Controller batch 内层低开销动作 adapter |
| `python/winpilot_home/winpilot/runtime_jobs.py` | Agent/ARP/Replay/Schedule 异步 job、资源租约和取消 |
| `python/winpilot_home/winpilot/automation_ir.py` | 新 automation artifact 的规范中间层与 WPS adapter |
| `python/winpilot_home/winpilot/perception/` | UIA/Win32/OCR/CV/VLM 融合、reducer、world |
| `python/winpilot_home/winpilot/recorder/trace.py` | 异步 trace/截图、Automation IR 主导出、WPS 兼容导出、离线 replay |
| `python/winpilot_home/winpilot/model_adapter.py` | provider、thinking、usage/cache/cost 归一化 |
| `src/main/winpilotServer.ts` | Electron 托管 WinPilot FastAPI 子进程 |
| `python/agent/__main__.py` | Electron IPC 到 WinPilot BUS 的适配器 |

## 不要踩坑

- 不要直接运行 `uvicorn winpilot.server:app`；用 `python/winpilot_home/serve.py`。
- 不要把 `python/features/` 当成 WinPilot 的工具池。它属于流程图/DSL/HTTP/MCP legacy 体系。
- 不要写计算器、QQ、网易云等单软件专属规则；要提升通用感知、窗口限定、验证和恢复。
- 不要把 Coding 描述成用户可见的另一个 agent。它是同一 `AgentRunner` 下的 Coding Runtime。
- 不要相信 `done` 文本等于真实成功；必须核对截图、文件、进程、窗口、trace 或工具 evidence。
- 不要提交 `python/winpilot_home/config.json`、`python/winpilot_home/traces/`、`python/winpilot_home/evals/`、`python/winpilot_home/scripts/`、`python/winpilot_home/experience_memory.json`、缓存或构建产物；根目录 `scripts/` 是受版本控制的开发/评测工具。
- 排查前先看 `/api/status.runtime` 或前端 runtime badge，确认 contract v2、repo root、loaded/current commit、source loaded/current stamp、drift、PID 和端口。源码漂移后服务会粘性 fail-closed，必须重启，不能继续拿旧解释器测试新前端。

## 运行时路径

```text
Frontend Agent page -> /api/v2/task-contracts/compile -> /api/v2/arp/runs
  -> TaskContract capability preflight -> typed task_stage graph
  -> StageRuntime / parameterized stage Skill / bounded semantic AgentRunner
scripts/gui_eval_run.py -> /api/chat -> RuntimeJob -> AgentRunner
  -> agent/router.py builds the same AgentRunner
  -> RuntimeProfile enables desktop/coding capabilities
  -> AgentRunner builds messages + tool schema
  -> LLM function calling
  -> ToolSession.dispatch()
  -> concrete *_tools.py mixin / fast controller
  -> BUS events + trace files + screenshots
  -> done_verifier.py before final success
```

`AgentRunner.status` 可能是 `pending`、`running`、`done`、`fail`、`reply`、`stopped`、`error`。`done` 仍要看 `done_check` 和最终证据。

## 低延迟 GUI 当前事实

已经落地：

- `execute_subgoal`：同一 `ToolSession` 下的 controller-first routine，不是第二个 agent。
- `run_action_batch`：本地连续执行一组 GUI 动作，中途做快速验证。
- 顶层 `click/type_text/hotkey/scroll/focus_window/draw_strokes/drag` 自动 fast-wrap 成单动作 batch。
- `screen_watch`、动作前后 `screen_delta/action_target`、`observe(changed_only=true)` 的 recent/action ROI 和 query fallback。
- `perception/reducer.py`：给 LLM 少量候选和不确定性，原始 snapshot 仍留在 trace。
- `fast/input_locator.py`：UIA-off 输入框定位，组合 UIA、OCR label、placeholder/search-like 文本、附近矩形、focused non-button 和 synthetic candidate。
- `agent/scope_guard.py`：GUI mutation 前检查目标窗口/前台，避免点到 Codex、日志窗口或错误窗口。
- `drawing_gui` 是内部兼容名称，本质是通用“连续指针输出”紧凑 schema：签名、手写、自由曲线、画布和路径都使用同一能力；不能添加猫、狗、画图应用等专属规则。
- 权威帧可为 `target_window` 或 `full_desktop`。`controller.target_window_capture_enabled` 默认开启，但只在目标窗口已验证、前台、可见、几何可信且任务不需要跨窗口上下文时裁剪；所有不确定情况回退完整虚拟桌面。
- `mimo-v2.5` 是明确支持 `image_url` 的原生视觉主模型；`mimo-v2.5-pro` 走 text-only。MiMo/Reasonix tool calling 默认关闭 provider thinking，`controller.native_visual_thinking` 只是默认关闭的连续空间实验。
- 紧凑 system prompt 以真实帧为权威；UIA/OCR/规则只作提示。普通 native observe 不重复走二次 VLM，同一视觉回合只允许一笔 mutation。
- `revise_plan` 可随时 replace/clear 或原子 add/update/remove/move/reopen；不要把初始 plan 当成不可变任务清单。
- `controller.multi_input_cooperation` 默认关闭。开启后是人类优先的焦点/光标仲裁，不是第二套鼠标键盘；无法证明目标窗口或用户中途接管时必须 blocked。
- 物理坐标排查工具：`coordinate_diagnostics` / `coordinate_click_test`。
- `action/desktop_queue.py`：真实桌面写入动作进入全局 single-writer 队列，Controller batch 可合并嵌套动作生命周期。
- `action/raw_device.py`：Controller 内层常见动作绕过递归 `ToolSession.dispatch()`；外层权限、scope、trace 和 evidence 仍只执行一次。
- `fast/perception_daemon.py`：按 hwnd 复用 watcher + ElementCache，短 run 之间保留 45 秒热状态。
- `runtime_jobs.py`：Agent、ARP、Replay 和 schedule 统一排队、查询和取消，不再各自维护临时 worker 状态。

仍未完成：

- 当前只有点击前局部视觉伺服和动作后变化验证，还没有鼠标移动全过程连续闭环伺服。
- 没有常驻轻量 UI 视觉模型/YOLO 主循环。
- Perception daemon 已复用 watcher/cache，但还不持续运行昂贵 OCR/CV/VLM，也不是学习型 UI detector。
- 物理鼠标/键盘仍全局单写；只允许已证明不改变真实光标/焦点的 PostMessage 窗口动作跨 hwnd 并发。实验协同输入也不允许两个物理鼠标任务并发。
- 还没做到所有常规动作都不经过 LLM、LLM 只处理异常。
- Skill 复用率、VLM 兜底调度和低延迟 dashboard 仍需继续打磨。

## 当前实力边界

- 计算器、记事本、网页靶场、连续指针/拖拽可以作为基础回归，但不能证明通用 computer use 能力。2026-07-14 的空白画板前端单次闭环约 55 秒且成图粗糙；它只证明完整链路可走通，不是稳定审美质量或成功率。
- QQ、微信、Steam、抖音、网易云等真实复杂软件需要非破坏性 E2E：打开、聚焦正确窗口、定位输入框/搜索框、输入草稿或搜索词、观察结果、清理草稿；未经用户明确允许不要真实发送消息或发布内容。
- 通信任务的 `click/type_text/hotkey ok` 不是成功证据。必须看到目标会话、目标联系人、消息文本出现在输入框或聊天记录中；真实发送还要看到发送后的聊天记录证据。
- 前端目前是中文原生 `PlatformWorkbench`：主导航为“智能代理、流程工作台、运行记录、技能与学习、系统设置”；没有 Agent iframe。API/MCP/VLM/URL/模型名等技术标识可保留原文。改前端时必须保留真实 FlowCanvas、Run/trace、Skill/Replay、计划任务和配置入口。

## Replay / FlowScript 边界

- `automation-ir/1` 是新保存流程和 Skill 的规范中间层；`.wps.json` 与 FlowScript 是兼容 adapter。
- 离线 0-token replay 由 `recorder/trace.py` 执行，IR 会先通过受检 adapter 转成 WPS runtime steps。
- Studio 运行链为 Flow editor state -> Automation IR -> `arp/1`；旧 `store/executor.ts` 仅保留兼容执行入口。
- Studio 节点库必须取 `RUNNABLE_FLOW_NODE_TYPES` 与后端 `CapabilitySpec.flow_node_types` 的交集。不要把历史 `NodeType` 或动态 `@feature` 仅因“有属性面板”就重新暴露为可执行节点。
- `GET /api/scripts` 中 `trust=draft` 的文件只是 artifact；只有 active memory、匹配 SHA-256 和验证来源齐全时才是 `trust=verified` Skill。脚本内容变化会使旧验证失效。
- WPS-only 步骤会导入为 `_requires_wps_replay` 占位，前端 executor 必须 fail-fast。
- 从 WPS 导入的基础 GUI 节点带 `_source_wps_replay`，会优先走真实 Electron `callFeature` bridge；没有 bridge 或 bridge 返回失败时节点失败，不能浏览器模拟成功。

## Memory / Context 边界

- `memory/compiler.py` 是 Agent run 的唯一长期记忆写入链；不要在 `AgentRunner` 完成路径再次调用 `experience.record()`。
- `memory/applicability.py` 的 typed contract 才决定 action memory 是否能复用；conditions/evidence 文本只用于解释。显式 `memory_id` 也不能绕过任务、app/window、stage、parameters、anchors 或脚本摘要检查。
- Apprentice 只产出 provisional candidate。任意人工字符串都不能晋升 Skill；必须由 `/api/memory/{id}/verification-runs` 的真实 replay 通过至少一个结构化 postflight check。
- 折叠旧上下文时必须保留 task goal、当前 mutable plan、关键 screen/window、run state/失败原因和最近工具证据。session memory 仅解指代，不能证明当前 GUI 状态。

## 计划任务

有两套调度系统，不要混淆：

- WinPilot Agent 内部调度：`schedule_task` / `list_scheduled_tasks` / `cancel_scheduled_task`，数据在 `python/winpilot_home/agent_schedules.json`，到期后仍走同一个 `AgentRunner`。
- legacy Electron 模板调度：`src/main/schedulerBridge.ts`、`python/engine/scheduler_daemon.py`、`schedules.json`，服务于流程图/模板体系。

WinPilot Agent 内部调度不是 Windows Task Scheduler。

## 如何复盘 GUI 任务

1. 走真实路径：前端 Agent 页面，或 `python scripts/gui_eval_run.py "任务"`。后者同样 POST `/api/chat`。
2. 拿 `run_id`。
3. 看 `python/winpilot_home/traces/<run_id>.jsonl`：
   - `agent_step`
   - `agent_tool_start`
   - `agent_tool`
   - `agent_batch_action*`
   - `done_check`
   - `agent_done`
4. 打开同目录截图：`before_NNN.jpg`、`after_NNN.jpg`、`batch_before_NNN.jpg`、`batch_after_NNN.jpg`。
5. 看 `NNN_llm_request.json` / `NNN_llm_response.json`，确认模型每一步实际收到什么。
6. 看 `NNN_tool_result.json` 获取完整原始工具结果；LLM history 里的 tool result 可能已被 compact。

## 验证命令

```bash
python -m pytest python/winpilot_home/tests -q
python -m pytest tests -q
npx vitest run
npm run build
```

GUI 能力改动还需要跑 `/api/chat` 路径，并人工核对截图。成本敏感时优先用便宜模型、小步数、单个真实任务回归。
