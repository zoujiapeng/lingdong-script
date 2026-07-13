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

直接 `/api/chat` 与原生前端 ARP 目标最终共享同一个 AgentRunner：

```text
/api/chat -> RuntimeJob(interactive_runtime)
/api/v2/arp/runs -> RuntimeJob -> skill_replay miss/fail -> agent_goal
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
- Perception daemon lease：按 target hwnd 复用 watcher 与 ElementCache。

工具实现分布：

| 文件 | 工具范围 |
| --- | --- |
| `window_tools.py` | `list_windows`、`scan_windows`、`focus_window`、`find_app`、`find_program` |
| `perception_tools.py` | `observe`、`read_text`、`find_image`、`vlm_describe`、template/color 等感知 |
| `interaction_tools.py` | `click`、`type_text`、`hotkey`、`scroll`、`drag`、`draw_strokes`、`wait` |
| `system_tools.py` | `launch`、`run_shell`、`run_detached`、`tray_click`、`search_files`、`undo_last`、agent schedule tools |
| `fast_tools.py` | `execute_subgoal`、`run_action_batch`、`screen_watch` |
| `plan_tools.py` | `revise_plan`、`complete_subgoal`、`done`、`fail`、`blocked` |
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

## 原生视觉闭环

主模型显式支持图片且 `controller.visual_closed_loop=true` 时，`AgentRunner` 使用以下闭环：

```text
目标窗口预绑定
  -> 初始目标窗口真实帧
  -> 模型只决定一笔视觉事务
  -> ToolSession / Controller 执行动作
  -> 动作后目标窗口真实帧
  -> 下一轮决策
  -> done 时重新捕获真实帧并按用户目标核验
```

- 一笔视觉事务可以是一个低层 GUI mutation，或一次 Controller 管理的 `run_action_batch` / `execute_subgoal`。同一模型回复中的后续调用返回 `visual_turn_barrier`，等待模型检查动作后帧。
- `click`、`scroll`、`drag`、`draw_strokes` 等视觉位置使用当前整帧 0..1000 的 `frame_*` 坐标，由运行时映射到同一次截图的物理区域；原生视觉 schema 隐藏物理 `x/y`。低层动作必须有与当前 hwnd、screen epoch 和窗口区域一致的新鲜帧。动作前重截图能可靠定位目标时，检查外扩 `controller.visual_preflight_target_margin`（默认 60/1000）的 ROI，并使用 `controller.visual_preflight_target_change_ratio`（默认 `0.01`）；无法可靠定位目标时退回整窗 `controller.visual_preflight_change_ratio`（默认 `0.0025`）。超过对应阈值会取消动作并返回新帧；缺少新鲜画面则返回 `visual_grounding_required`。连续两次试图无画面依据操作会安全暂停。
- 原生视觉模型的普通无 query `observe` 使用 `fusion.native_vision_direct`：只建立窗口几何快照，把 JPEG 直接交给主模型，跳过 UIA/OCR/VLM 的同帧拆解；二次 VLM 工具也只对 text-only 主模型开放。query、`full`、`changed_only`、结构化锚定、非视觉模型和 fallback 仍使用融合感知。
- 真实帧是当前像素界面的权威状态；结构化元素、规则和 Controller 摘要只是提示。动作后的旧 element tree 会失效，不能压过相反的画面。
- `observe` 只读。通信场景即使识别出会话和输入框，也只返回 `execute_subgoal` 提案，实际输入/发送必须在下一笔显式事务中发生。

## 低延迟 GUI Controller

### execute_subgoal

`execute_subgoal` 是同一 AgentRunner 里的 controller-first routine，不是第二个 agent。它要求至少一个具体 routine 字段：

- `input`
- `click`
- `clicks`
- `draw`
- `draws`
- `verify_texts`

For printable key sequences such as `12*34=`, prefer `input.text` or `type_text`.
If a model still sends short printable labels in `clicks`, the controller may
rewrite that routine into keyboard typing and mark the action with
`keyboard_sequence_from_clicks`; this is a generic anti-coordinate-drift path,
not an app-specific calculator rule.

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

Controller batch 外层只取得一次 Desktop Action Queue 组合 lease；`action/raw_device.py` 直接调用常见动作 handler，避免每个子动作重新经过完整 `ToolSession.dispatch()`。物理鼠标/键盘全局单写；明确后台安全的 PostMessage 动作才按 hwnd 并发。复杂工具仍走完整 dispatch，确保权限、scope 和证据边界不丢失。

排查字段：

- `wrapped_by_fast_controller`
- `metrics`
- `screen_delta`
- `action_target`
- `auto_visual_verify`
- `visual_change_detected`

`screen_delta.changed=true`、颜色存在和 `auto_visual_verify.summary_kind=pixel_effect_only` 只说明发生了像素效果，不是业务成功或完成证据。必须结合 target window、任务目标、expected evidence 和新鲜截图。

### 坐标诊断

真实鼠标坐标排查用（原生视觉模型的普通动作不直接暴露物理坐标工具）：

- `coordinate_diagnostics`
- `coordinate_click_test`

`winpilot/__init__.py` 会先调用 `utils/dpi.py` 设置 Per-Monitor V2，避免只导入动作模块时被 Windows DPI 虚拟化。诊断工具报告物理屏幕、截图尺寸、monitor、窗口 region/DPI、目标相对坐标和真实鼠标点击路径；不要把浏览器 CSS/client 坐标当成真实 Windows 鼠标坐标。

### 真实软件评测边界

网页靶场只能验证输入、点击、拖拽、弹窗、画布和坐标映射等可控场景，不能代表真实桌面软件泛化能力。需要单独维护真实软件 E2E：

- 聊天软件：QQ/微信/企业微信。默认只做打开、识别已登录窗口、搜索联系人、进入会话、输入草稿、验证草稿或聊天记录；真实发送必须由用户明确授权。
- 媒体/商店软件：网易云音乐、Steam、抖音。验证搜索、播放/预览、进入发布前一步等非破坏性动作；不要默认购买、发布、删除或发送。
- 多窗口干扰：至少覆盖“一个已登录主窗口 + 一个登录页/错误窗口”的场景，确认 agent 不会死盯错误窗口。
- 证据要求：每个 run 都要保存 trace、LLM request/response、tool result 和截图；结论以截图/文件/窗口/可见文本为准，不以 agent 口头 done 为准。

直接 Agent 评测走 `scripts/gui_eval_run.py` 的公开 `/api/chat`；前端目标页走 `/api/v2/arp/runs`。两者不是同一个 HTTP 入口，但共享 RuntimeJob、AgentRunner、ToolSession、Controller、trace 和 done gate；内部 runner/CLI 仍不能替代前端路径。

汇总只把严格核验的 run 计入 `verified_success`：必须未超时、`status=done`、policy 和性能预算通过、外部 ground truth 存在且通过，并且没有 unverified done。没有检查项的报告标记为 `ungrounded`，不计成功；报告的 Git、模型、视觉配置 hash、display/DPI provenance 用来保证不同运行可比。

## Done Gate

`done` 必须有证据。常见拒绝原因：

- 用户要求的 literal 没出现在当前屏幕/工具证据中。
- 通信任务没有看到消息出现在目标会话。
- 绘图任务的新鲜整图没有满足目标语义；可见颜色或像素变化本身不足以完成。
- 文件/保存任务没有 `file_exists` 或等价确定性证据。
- 只看到模型总结，没有 observe/read_text/expect/VLM/deterministic evidence。

关键文件：

- `agent/done_verifier.py`
- `agent/task_rules.py`
- `agent/run_state.py`
- `agent/failure_classifier.py`
- `agent/failure_recovery.py`

原生视觉 GUI 任务在 `done` 时会重新捕获目标窗口，独立 judge 直接检查整张图与用户目标；缺少 UIA/OCR 文本但新鲜画面存在时，精确 literal 由该 judge 逐字检查，不会触发一次反向结构化 observe，更不会因“有截图”自动通过。对象、页面、文本或结果有矛盾、含糊、裁切或看不清都判 false。模型 judge 异常或达到 `done_max_rejects` 也 fail-closed；未验证结果不会因重试次数耗尽被强制升级为成功。文件/进程等不可见任务仍以确定性证据为准。

### 验证并发边界

- 同一个 `expect` 同时包含可见状态与文件/进程状态时，文件存在、文件内容、进程运行/退出检查会在后台与 UIA/OCR 重叠，最终仍按 AND 语义等待全部通过。
- `shell_true` 不进入并发白名单，因为任意 PowerShell 表达式无法证明无副作用。
- 鼠标和键盘写入继续经过全局 `physical` single-writer；同一 hwnd 和剪贴板资源也串行。只有不改变前台、光标和键盘焦点的后台窗口消息动作可在不同 hwnd 并发。
- OCR 与 Everything IPC 可在服务启动时后台预热。这些是只读准备工作，不改变桌面任务的顺序。

## Coding Runtime

Coding 不是独立 agent。触发后：

- `RuntimeProfile.coding=true`。
- schema 加入 repo 工具，例如 `apply_patch`、`github_search`。
- `list_dir/read_file/write_file/search_text/run_shell` 委托到 workspace-confined `CodingSession`。
- clone/build/test/run 类任务会走 anti-fake-success gate：没有实际 shell/构建/测试/环境检查尝试，不能 done。

GUI-only 任务不应暴露 coding/file/search 工具。

## Replay / Skill

- `recorder/trace.py` 通过有界异步 writer 记录 BUS、截图和 LLM I/O，before/after 由 `action_id` 配对。
- `automation-ir/1` 是新 Skill/Flow/trace artifact；`.wps.json` 是兼容步骤格式。
- `reuse_skill` 会强匹配已验证 `.automation.json`（旧 WPS 仍可读），0-token replay 后仍要求当前状态验证。
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
- 到期后提交统一 RuntimeJob，再走 `router.build_runner() -> AgentRunner`；忙时排队，不另建第二套 agent thread。
- 每次执行会写入独立 `run_id`、trace、`last_status` 和 `run_count`；一次性任务结束后自动禁用，周期任务计算下一次时间。
- `run_at` 必须是 ISO-8601 时间；自然语言时间要先由模型/上层解析成明确时间，底层不会把无效时间静默当成“立刻执行”。

这套不是 Windows Task Scheduler，也不同于 legacy Electron 模板调度器。

## 本地搜索与任务提醒

- `search_files` 默认调用 `winpilot/bin/es.exe` 查询已安装 Everything 的索引；支持 `kind=file|folder|any`、`extensions`、范围路径和排序。
- Everything IPC 不可用时会尝试启动用户会话客户端；仍不可用则切换到有时间上限的 `os.scandir`，并用 `partial` 明确标记可能不完整的结果。
- Electron 观察统一 run projection 的 active -> terminal 变化；极短任务即使跨过 active 轮询也会按新 terminal `run_id` 提醒。
- 成功、失败、停止分别使用 Windows 任务栏 normal/error/paused 状态；后台窗口闪烁并发送系统通知，同一 `run_id` 去重。

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
