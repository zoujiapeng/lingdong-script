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
   - WinPilot 成功 trace 可导出 `.wps.json`，再保守转换成 FlowScript 供编辑。

## 优先阅读

| 文件 | 作用 |
| --- | --- |
| `python/winpilot_home/winpilot/server.py` | FastAPI `/api/chat`、状态、trace、replay、agent schedule 入口 |
| `python/winpilot_home/serve.py` | 支持的 WinPilot 服务启动入口 |
| `python/winpilot_home/winpilot/config.py` | 默认配置和本机 `config.json` 读取 |
| `python/winpilot_home/winpilot/agent/router.py` | 只构建统一 `AgentRunner` |
| `python/winpilot_home/winpilot/agent/runtime_profile.py` | 每次 run 的 Runtime/Capability 开关 |
| `python/winpilot_home/winpilot/agent/loop.py` | LLM 主循环、上下文折叠、工具调用、done gate 编排 |
| `python/winpilot_home/winpilot/agent/tool_schemas.py` | OpenAI function schema 和 schema profile |
| `python/winpilot_home/winpilot/agent/tools.py` | `ToolSession` 状态、dispatch、目标窗口解析 |
| `python/winpilot_home/winpilot/agent/*_tools.py` | 窗口、感知、动作、系统、计划、文件、记忆等工具 mixin |
| `python/winpilot_home/winpilot/fast/` | 低延迟 controller、subgoal、watcher、input locator |
| `python/winpilot_home/winpilot/perception/` | UIA/Win32/OCR/CV/VLM 融合、reducer、world |
| `python/winpilot_home/winpilot/recorder/trace.py` | trace、`.wps.json` 导出、离线 replay |
| `python/winpilot_home/winpilot/model_adapter.py` | provider、thinking、usage/cache/cost 归一化 |
| `src/main/winpilotServer.ts` | Electron 托管 WinPilot FastAPI 子进程 |
| `python/agent/__main__.py` | Electron IPC 到 WinPilot BUS 的适配器 |

## 不要踩坑

- 不要直接运行 `uvicorn winpilot.server:app`；用 `python/winpilot_home/serve.py`。
- 不要把 `python/features/` 当成 WinPilot 的工具池。它属于流程图/DSL/HTTP/MCP legacy 体系。
- 不要写计算器、QQ、网易云等单软件专属规则；要提升通用感知、窗口限定、验证和恢复。
- 不要把 Coding 描述成用户可见的另一个 agent。它是同一 `AgentRunner` 下的 Coding Runtime。
- 不要相信 `done` 文本等于真实成功；必须核对截图、文件、进程、窗口、trace 或工具 evidence。
- 不要提交 `config.json`、`traces/`、`evals/`、`scripts/`、`experience_memory.json`、缓存或构建产物。
- 排查前先看 `/api/status.runtime` 或前端 runtime badge，确认当前服务来自哪个 repo root、branch、commit、PID 和端口。

## 运行时路径

```text
Frontend Agent page or scripts/gui_eval_run.py
  -> WinPilot FastAPI /api/chat
  -> agent/router.py builds one AgentRunner
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
- `drawing_gui` schema profile：绘图/画布任务只暴露必要 GUI/controller 工具，降低 prompt 成本。
- 物理坐标排查工具：`coordinate_diagnostics` / `coordinate_click_test`。
- `action/desktop_queue.py`：真实桌面写入动作进入全局 single-writer 队列，支持同步执行和后台 job。
- `action/executor.py`：已有 raw click/move/focus/key helper，但还不是完整独立 Raw Device Executor。

仍未完成：

- 没有成熟视觉伺服控制。
- 没有常驻轻量 UI 视觉模型/YOLO 主循环。
- 没有跨任务常驻的 OCR/UIA/CV/VLM 语义 perception daemon；当前常驻部分主要是 screen delta watcher。
- 没有按 hwnd 的细粒度 action queue；`target_hwnd` 目前主要用于记录和后续策略，不提供多窗口并行动作。
- 还没做到所有常规动作都不经过 LLM、LLM 只处理异常。
- Skill 复用率、VLM 兜底调度和低延迟 dashboard 仍需继续打磨。

## 当前实力边界

- 计算器、记事本、网页靶场、简单绘图/拖拽可以作为基础回归，但不能证明通用 computer use 能力。
- QQ、微信、Steam、抖音、网易云等真实复杂软件需要非破坏性 E2E：打开、聚焦正确窗口、定位输入框/搜索框、输入草稿或搜索词、观察结果、清理草稿；未经用户明确允许不要真实发送消息或发布内容。
- 通信任务的 `click/type_text/hotkey ok` 不是成功证据。必须看到目标会话、目标联系人、消息文本出现在输入框或聊天记录中；真实发送还要看到发送后的聊天记录证据。
- 前端目前是可用的工程工作台，不是最终产品形态。改前端时要保留真实 FlowCanvas、Agent iframe、Replay/trace 调试入口，不要用纯视觉 redesign 覆盖真实功能。

## Replay / FlowScript 边界

- `.wps.json` 是 WinPilot 语义重放格式，离线 0-token replay 由 `recorder/trace.py` 执行。
- FlowScript 是前端画布 JSON，执行器在 `src/renderer/store/executor.ts`。
- WPS 导入 FlowScript 是编辑桥，不代表完全等价执行。
- WPS-only 步骤会导入为 `_requires_wps_replay` 占位，前端 executor 必须 fail-fast。
- 从 WPS 导入的基础 GUI 节点带 `_source_wps_replay`，会优先走真实 Electron `callFeature` bridge；没有 bridge 或 bridge 返回失败时节点失败，不能浏览器模拟成功。

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
npx vitest run
npm run build
```

GUI 能力改动还需要跑 `/api/chat` 路径，并人工核对截图。成本敏感时优先用便宜模型、小步数、单个真实任务回归。
