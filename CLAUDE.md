# AI 接手速览

这份文件只写当前事实和排障入口，避免 AI 被旧 run 日志带偏。更长的说明见 `docs/`。

## 当前项目形态

1. `python/winpilot_home/winpilot/`
   - 当前 WinPilot desktop agent。
   - 用户始终面对一个 Agent。
   - 唯一主循环是 `agent/loop.py` 的 `AgentRunner`。
   - GUI / Computer Use 是默认 Runtime；编程/仓库任务由 `agent/runtime_profile.py` 在同一 runner 内启用 Coding Runtime。
   - `agent/coding.py` 只是兼容旧导入的 shim，不再实现独立 Coding 子 agent。

2. `src/renderer/` + `python/engine/` + `python/features/`
   - legacy 流程图 / DSL / `@feature` 自动化系统。
   - 仍在用，但不是 WinPilot 主 agent 的工具池。

## 先读哪些文件

| 文件 | 作用 |
| --- | --- |
| `python/winpilot_home/winpilot/server.py` | FastAPI `/api/chat`、状态、trace、replay 入口 |
| `python/winpilot_home/serve.py` | 唯一支持的 WinPilot 服务启动入口 |
| `python/winpilot_home/winpilot/config.py` | 真实默认配置和本机 `config.json` 读写 |
| `python/winpilot_home/winpilot/agent/router.py` | 只构建统一 `AgentRunner` |
| `python/winpilot_home/winpilot/agent/runtime_profile.py` | 每次 run 的 Runtime/Capability 开关 |
| `python/winpilot_home/winpilot/agent/gui_plan_helpers.py` | GUI schema profile、绘图计划解析、打开窗口计划自动完成等纯 helper |
| `python/winpilot_home/winpilot/agent/loop.py` | LLM 主循环、上下文折叠、工具调用、done gate 编排 |
| `python/winpilot_home/winpilot/agent/tool_schemas.py` | OpenAI function schema 和 schema profile |
| `python/winpilot_home/winpilot/agent/tools.py` | `ToolSession` 状态、dispatch、目标窗口解析 |
| `python/winpilot_home/winpilot/agent/*_tools.py` | 窗口、感知、动作、系统、计划、文件、记忆等工具 mixin |
| `python/winpilot_home/winpilot/fast/` | 低延迟 controller、subgoal、watcher、input locator |
| `python/winpilot_home/winpilot/perception/` | UIA/Win32/OCR/CV/VLM 融合、reducer、world |
| `python/winpilot_home/winpilot/recorder/trace.py` | trace、`.wps.json` 导出、离线 replay |
| `python/winpilot_home/winpilot/model_adapter.py` | provider 推断、thinking 默认、usage/cache/cost 归一化 |
| `src/main/winpilotServer.ts` | Electron 托管 WinPilot FastAPI 子进程 |
| `python/agent/__main__.py` | Electron IPC 到 WinPilot BUS 事件的适配器 |

## 不要踩坑

- 不要直接运行 `uvicorn winpilot.server:app`；用 `python/winpilot_home/serve.py`。
- 不要把 `python/features/` 当成 WinPilot 的工具池。
- 不要写计算器、QQ、网易云等单软件专属规则；要提升通用感知、窗口限定、验证和恢复。
- 不要把 Coding 描述成用户可见的另一个 agent。它是同一 AgentRunner 下的 Coding Runtime。
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

已落地：

- `execute_subgoal`：同一 `ToolSession` 下的 controller-first routine，不是第二个 agent。支持输入+按钮、单击、有序多击、click-only 验证、简单画布 `draw/draws`。
- `run_action_batch`：一组局部 GUI 动作在本地 controller 内执行，中途做快速验证。
- 顶层 `click/type_text/hotkey/scroll/focus_window/draw_strokes/drag` 会自动 fast-wrap 成单动作 batch。
- `screen_watch`、动作前后 `screen_delta/action_target`、`observe(changed_only=true)` 的 recent/action ROI 和 query fallback。
- `perception/reducer.py`：给 LLM 少量候选和不确定性，原始 snapshot 仍留在 trace。
- `fast/input_locator.py`：UIA-off 输入框定位，组合 UIA、OCR label、placeholder/search-like 文本、附近矩形、focused non-button 和 synthetic candidate。
- `agent/scope_guard.py`：GUI mutation 前检查目标窗口/前台，避免点到 Codex、日志窗口或错误窗口。
- `drawing_gui` schema profile：严格 GUI-only 绘图/画布任务只暴露必要 GUI/controller 工具，降低固定 prompt 成本。
- 绘图 routine 会合并相邻 `shape/color/region` 计划为一次 `draws[]`，用可见颜色 evidence 通过 done gate。
- verified `launch` / `focus_window` 可以自动完成纯打开窗口计划项；混合 open+draw/verify 不会由 launch 单独完成。

仍未完成：

- 没有成熟视觉伺服控制。
- 没有常驻轻量 UI 视觉模型/YOLO 主循环。
- 还没做到所有常规动作都不经过 LLM、LLM 只处理异常。
- Skill 复用率、VLM 兜底调度和低延迟 dashboard 仍需要继续打磨。

## 如何复盘一次 GUI 任务

1. 走真实路径：前端 Agent 页面，或 `python scripts/gui_eval_run.py "任务"`。后者同样 POST `/api/chat`，不是离线绕路。
2. 拿 `run_id`。
3. 看 `python/winpilot_home/traces/<run_id>.jsonl`：
   - `agent_step`：第几轮 LLM、tool call 数。
   - `agent_tool_start`：工具执行前参数。
   - `agent_tool`：工具结果、耗时、terminal、截图索引。
   - `agent_batch_action*`：batch 内部子动作。
   - `done_check`：done gate 接受或拒绝原因。
4. 打开截图：`python/winpilot_home/traces/<run_id>/<before|after|batch_before|batch_after>_NNN.jpg`。截图是判断假成功的第一证据。
5. 看 `python/winpilot_home/traces/<run_id>/NNN_llm_request.json` 和 `NNN_llm_response.json`，确认模型每一步实际收到了什么感知文本、工具结果、用户插话和图片。
6. 对 `execute_subgoal` 看：
   - `subgoal_route`
   - `actions_executed`
   - `controller_state`
   - `evidence_packet.matched_texts/missing_texts`
   - `evidence_packet.candidate_click_labels`
   - `matched_visual_colors/missing_visual_colors/visible_colors`
7. 对普通动作看：
   - `wrapped_by_fast_controller`
   - `screen_delta.changed`
   - `action_target.after.foreground_matches_target`
   - `auto_visual_verify`
8. 如果 strict GUI-only 任务里出现 `run_shell/run_detached/list_dir/read_file/write_file/search_text`，先查 `task_rules.py` 和 `tool_schemas.py` 的 allow flags。

## 文档和代码维护

- README 写项目总览，CLAUDE 写 AI 最短接手，`docs/architecture.md` 写结构边界，`docs/winpilot-agent.md` 写运行和复盘细节。
- 历史 run id 不要继续堆进主文档；需要保留时写入 eval 报告或 trace 目录。
- 新能力优先拆进独立模块，不继续膨胀 `agent/loop.py`、`agent/tools.py`、`python/engine/executor.py`。
- 修改工具 schema 时要记住：每个 description 都会进入 LLM prompt。
- 修改 replay / `.wps.json` / FlowScript 映射时，同时看 `recorder/trace.py`、`src/renderer/utils/wpsToFlowScript.ts` 和相关测试。

## 验证

```bash
python -m pytest python/winpilot_home/tests -q
npx vitest run
npm run build
```

真实 GUI 能力必须再跑 `/api/chat` 路径并人工核对截图；单元测试和 agent 口头成功都不够。
