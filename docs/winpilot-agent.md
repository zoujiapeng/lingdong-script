# WinPilot Agent

本文只描述当前代码机制和排障方法。项目总览看 `README.md`，结构边界看 `docs/architecture.md`。

## 路由和 Runtime

`agent/router.py` 不再按任务切换 runner。

- 所有任务都进入 `agent/loop.py` 的 `AgentRunner`。
- `agent/runtime_profile.py` 根据任务文本和配置启用 Desktop/Coding capability。
- GUI-only 任务会关闭 Coding Runtime，并通过 schema 和执行层 guard 禁止 shell/file 绕路。
- `agent/coding.py` 只保留兼容旧导入的 `CodingRunner` shim，不再有独立 planner/tool loop。

产品表达：用户面对的是一个 Agent；Coding、Computer Use、Terminal、Browser、Replay 是它的能力。

## 主循环

一次 `/api/chat` 任务的真实路径：

```text
server.py _RunState
  -> router.build_runner()
  -> AgentRunner.run()
  -> build messages + tool_schemas()
  -> model_adapter.chat()
  -> _handle_tool_calls()
  -> ToolSession.dispatch()
  -> tool mixin / FastGuiController / CodingSession
  -> BUS + trace + screenshots
  -> done_verifier.py
```

`AgentRunner` 负责：

- system prompt、user/task、tool history、interjection、context folding。
- OpenAI-compatible function calling。
- usage/cache/cost 聚合。
- tool result 压缩写回 LLM history，同时保留完整 trace。
- done 二次核验和假成功防护。

`agent/gui_plan_helpers.py` 放置与主循环无关的纯 helper：schema profile 选择、绘图计划解析、绘图视觉确认判断、纯打开窗口计划项判断。新代码优先从 helper 模块引用。

## ToolSession

`agent/tools.py` 持有一次 run 的工具状态：

- 当前 target hwnd/title/process。
- 最近 `Snapshot`。
- plan/world/undo stack。
- RuntimeProfile；`runtime.to_dict().capabilities` 记录本轮 desktop/coding/replay/scheduler/automation_ir/desktop_action_queue 等能力。
- `dispatch()` 到各个 mixin。

工具实现分布：

| 文件 | 工具范围 |
| --- | --- |
| `window_tools.py` | `list_windows`、`scan_windows`、`focus_window`、`find_app`、`find_program` |
| `perception_tools.py` | `observe`、`read_text`、`find_image`、`vlm_describe`、template/color 等感知 |
| `interaction_tools.py` | `click`、`type_text`、`hotkey`、`scroll`、`drag`、`draw_strokes`、`wait` |
| `system_tools.py` | `launch`、`run_shell`、`run_detached`、`tray_click`、`search_files`、`undo_last`、agent schedule tools |
| `fast_tools.py` | `execute_subgoal`、`run_action_batch`、`screen_watch` |
| `plan_tools.py` | `revise_plan`、`complete_subgoal`、`done`、`fail` |
| `file_tools.py` | `list_dir`、`read_file`、`write_file`、`search_text` |
| `memory_tools.py` | `remember`、`forget`、`reuse_skill` |
| `desktop_tools.py` | clipboard、mouse low-level、screenshot、displays、installed apps、coordinate diagnostics/click test |
| `coding_tools.py` | workspace-confined repo file/shell/patch/GitHub tools |

## Tool Schema

`agent/tool_schemas.py` 构造模型可见工具。执行层能力不等于模型每轮可见工具。

主要开关：

- `allow_shell`
- `allow_files`
- `planning`
- `coding_runtime`
- `profile`

Profile：

- `standard`：普通 desktop/coding 默认。
- `lean_gui`：较窄的 GUI schema。
- `drawing_gui`：严格 GUI-only 绘图/画布任务；隐藏无关工具，并把 `execute_subgoal` 参数裁剪到绘图/验证相关字段。

排查 GUI-only 绕路时，直接打开 `traces/<run_id>/NNN_llm_request.json` 看 tools 列表，不要只看 agent 说了什么。

## 低延迟 GUI Controller

### execute_subgoal

`execute_subgoal` 是同一 AgentRunner 里的 controller-first routine，不是第二个 agent。它要求至少一个具体 routine 字段：

- `input`
- `click`
- `clicks`
- `draw`
- `draws`
- `verify_texts`

只有自然语言 `subgoal` 会快速 blocked，不初始化 controller/perception。

典型用途：

- 输入 literal 到可定位输入框，点击 Apply/Search/Send，并验证可见状态。
- 点击有可见 label 的按钮，并验证可见结果。
- 按顺序点击多个明显控件。
- 在简单画布中绘制 triangle/rectangle/circle/line/check 等 shape。

关键结果字段：

- `subgoal_route`
- `actions_executed`
- `controller_state`
- `evidence_packet.matched_texts`
- `evidence_packet.missing_texts`
- `evidence_packet.candidate_click_labels`
- `evidence_packet.matched_visual_colors`
- `evidence_packet.missing_visual_colors`
- `evidence_packet.visible_colors`

如果锚点 label 没命中但 `candidate_click_labels` 有值，下一步应让模型用最接近的本地化/同义可见标签重试，而不是转去 shell 或源码搜索 GUI 文案。

### run_action_batch

`run_action_batch` 在本地连续执行多个动作，并返回每个子动作结果。顶层 `click/type_text/hotkey/scroll/focus_window/draw_strokes/drag` 也会自动 fast-wrap 成单动作 batch。

排查字段：

- `wrapped_by_fast_controller`
- `metrics`
- `screen_delta`
- `action_target`
- `auto_visual_verify`
- `visual_change_detected`

`screen_delta.changed=true` 只说明像素变化，不是业务成功证据。要结合 target window、expected evidence 和截图。

### 坐标诊断

真实鼠标坐标排查用：

- `coordinate_diagnostics`
- `coordinate_click_test`

它们报告物理屏幕、截图尺寸、monitor、窗口 region/DPI、目标相对坐标、真实鼠标点击路径。不要把浏览器 CSS/client 坐标当成真实 Windows 鼠标坐标。

## Done Gate

`done` 必须有证据。常见拒绝原因：

- 用户要求的 literal 没出现在当前屏幕/工具证据中。
- 通信任务没有看到消息出现在目标会话。
- 绘图任务没有可见颜色或像素变化证据。
- 文件/保存任务没有 `file_exists` 或等价确定性证据。
- 只看到模型总结，没有 observe/read_text/expect/VLM/deterministic evidence。

关键文件：

- `agent/done_verifier.py`
- `agent/task_rules.py`
- `agent/run_state.py`
- `agent/failure_classifier.py`
- `agent/failure_recovery.py`

`done_max_rejects` 后可能 force-accept，但会标记 `unverified`。这种 run 不应写入 trusted memory/skill。

## Coding Runtime

Coding 不是独立 agent。触发后：

- `RuntimeProfile.coding=true`。
- schema 加入 repo 工具，例如 `apply_patch`、`github_search`。
- `list_dir/read_file/write_file/search_text/run_shell` 委托到 workspace-confined `CodingSession`。
- clone/build/test/run 类任务会走 anti-fake-success gate：没有实际 shell/构建/测试/环境检查尝试，不能 done。

GUI-only 任务不应暴露 coding/file/search 工具。

## Replay / Skill

- `recorder/trace.py` 记录 BUS 事件、截图、LLM request/response 和 `.wps.json`。
- `.wps.json` 是 WinPilot 语义重放格式。
- `reuse_skill` 会强匹配历史成功 `.wps.json`，0-token replay 后仍要求当前状态验证。
- Replay 成功不是 done 证据；必须 observe/read_text/expect 证明最终结果。
- FlowScript 导入只是编辑桥。WPS-only 节点 fail-fast，WPS 来源基础 GUI 节点要求真实 Electron feature bridge。

## Memory

当前长期记忆以 SQLite Memory Graph 为主：

- 主存储：`python/winpilot_home/memory.db`。
- 运行时：`memory/store.py`、`memory/runtime.py`、`memory/retriever.py`、`memory/packer.py`、`memory/feedback.py`、`memory/compiler.py`。
- 兼容门面：`memory/experience.py`。
- `experience_memory.json` 是迁移/兼容数据，不是主运行时事实。

清除记忆后 agent 变笨，通常说明 routine、感知或验证硬实力不足；不能靠用户历史记忆撑通用能力。

## Scheduler

WinPilot Agent 内部计划任务：

- 工具：`schedule_task`、`list_scheduled_tasks`、`cancel_scheduled_task`。
- 数据：`python/winpilot_home/agent_schedules.json`。
- 服务端线程：`server.py` 的 scheduler loop。
- 到期后仍走 `router.build_runner() -> AgentRunner`。
- `run_at` 必须是 ISO-8601 时间；自然语言时间要先由模型/上层解析成明确时间，底层不会把无效时间静默当成“立刻执行”。

这套不是 Windows Task Scheduler，也不同于 legacy Electron 模板调度器。

## 如何复盘工具细节

1. 确认 runtime：`/api/status.runtime` 或前端 runtime badge。
2. 找 `run_id`。
3. 看 `python/winpilot_home/traces/<run_id>.jsonl`：
   - `agent_start`
   - `agent_step`
   - `agent_tool_start`
   - `agent_tool`
   - `agent_batch_action*`
   - `done_check`
   - `agent_done`
4. 打开截图，优先核对 `batch_after_NNN.jpg` 和 `after_NNN.jpg`。
5. 看 `NNN_llm_request.json` 和 `NNN_llm_response.json`，确认模型实际看见了什么。
6. 看 `NNN_tool_result.json` 获取完整原始工具结果；LLM history 里的 tool result 可能已被 compact。
7. 如果截图和工具结果不一致，按工具名定位 mixin：
   - 窗口：`window_tools.py`
   - 感知：`perception_tools.py`
   - 输入动作：`interaction_tools.py`
   - 启动/shell/tray：`system_tools.py`
   - batch/subgoal：`fast_tools.py`、`fast/controller.py`、`fast/subgoal.py`
   - done/plan：`plan_tools.py`、`done_verifier.py`

## 配置

默认值在 `config.py`，本机覆盖在 `python/winpilot_home/config.json`。

常见项：

- `api.provider/base_url/api_key/model`
- `api.thinking`
- `api.pricing`
- `vlm.enabled/base_url/api_key/model`
- `perception.uia/win32/ocr/cv/vlm`
- `fusion.screen_watch_autostart`
- `agent.shell_enabled`
- `agent.planning_enabled`
- `agent.experience_memory`
- `agent.tool_profile`
- `coding.workspace`

不要提交本机 `config.json` 或 key。

## 验证

```bash
python -m pytest python/winpilot_home/tests -q
npx vitest run
npm run build
```

GUI 能力改动还需要跑 `/api/chat` 路径，并人工核对截图。成本敏感时优先用便宜模型、小步数、单个真实任务回归。
