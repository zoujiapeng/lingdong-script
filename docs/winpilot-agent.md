# WinPilot Agent

当前 WinPilot 主体在 `python/winpilot_home/winpilot/`。

## 路由和 Runtime

`agent/router.py` 不再根据任务切换 runner：

- 用户始终面对一个 WinPilot Agent。
- 所有任务都进入 `agent/loop.py` 的 `AgentRunner`。
- `agent/runtime_profile.py` 根据任务决定是否在同一主循环内启用 Coding Runtime。
- GUI-only 任务会关闭 Coding Runtime，并继续受 GUI-only guard 约束。
- `agent/coding.py` 只保留兼容旧导入的 `CodingRunner` 别名，不再有独立 planner/tool loop。

## AgentRunner

主文件：`agent/loop.py`。

职责：

- 构造 system prompt。
- 调 OpenAI 兼容 function-calling 接口。
- 管理工具调用、上下文折叠、interjection、usage。
- done 二次验证，防止假成功。
- 可选 planning、playbook、experience memory、world advisory。
- 视觉模型可用时，`observe` 后可把当前窗口截图注入为 `image_url` 消息。

`loop.py` 是唯一主循环，但部分纯逻辑已拆出：

| 文件 | 职责 |
| --- | --- |
| `agent/task_rules.py` | 任务分类、GUI-only guard、coding runtime 触发、done gate 需要的纯字符串规则 |
| `agent/runtime_profile.py` | 每次 run 的 RuntimeProfile；决定 Coding Runtime 是否启用、workspace 和 max_steps |
| `agent/done_verifier.py` | done 二次核验 mixin，集中处理假成功防护 |
| `agent/context_guidance.py` | upfront plan、world advisory、stuck recovery、playbook 注入 |
| `agent/reflection.py` | 反思检查点 mixin，不额外调用 LLM |
| `agent/loop.py` | LLM 编排、工具调用循环、上下文折叠和终止收尾 |

后续优先继续拆 `agent/tools.py` 和 runtime 模块，不要把新规则继续塞回 `loop.py`。

## 统一执行时序

一次任务的真实路径：

1. 前端 Agent 页或 `scripts/gui_eval_run.py` 调 `/api/chat`。
2. `server.py` 创建 `_RunState.stop_event`、`runner`、后台线程，并启动 `recorder/trace.py`。
3. `agent/router.py` 构建唯一 `AgentRunner`；`RuntimeProfile` 在同一主循环内选择 desktop/coding 等 runtime 能力。
4. `AgentRunner.run()` 组装 system/user messages、经验记忆、playbook、上下文提示和工具 schema。
5. 每一步调用 LLM function calling；返回 tool calls 后由 `_handle_tool_calls()` 交给 `ToolSession.dispatch()`。
6. `ToolSession` 维护最近 `Snapshot`、目标窗口 `hwnd`、plan、world model、undo stack；具体 handler 在各 mixin 中执行。
7. 工具结果回写 messages、BUS 和 trace；`observe/read_text/list_windows` 等大结果会在上下文过大时折叠。
8. `done` 或纯文本结束前会进 done verifier；证据不足会被拒绝并继续循环。
9. 结束状态写入 `AgentRunner.status`：`done`、`fail`、`reply`、`stopped` 或 `error`。

排错优先级：先看真实屏幕/截图，再看 `python/winpilot_home/traces/<run_id>.jsonl` 的工具序列和截图索引，最后看 `python/winpilot_home/traces/<run_id>/NNN_*.json` 里 LLM 每步实际收到的信息。

## 工具级复盘

目标不是看 agent 自称成功，而是复现“模型看到了什么、调用了什么、屏幕实际发生了什么”。

1. 走真实路径启动任务：前端 Agent 页，或 `python scripts/gui_eval_run.py "任务"`。后者也调用 `/api/chat`，不是离线 CLI。
2. 取得 `run_id`：前端日志、`/api/status`、或 eval 报告都会包含。
3. 打开 `python/winpilot_home/traces/<run_id>.jsonl`：
   - `agent_start`：任务文本和最大步数。
   - `agent_step`：第几轮 LLM、reasoning 摘要、tool call 数。
   - `agent_tool_start`：工具执行前，包含工具名和参数。
   - `agent_tool`：工具执行后，包含 `result_preview`、`terminal`、`elapsed_ms`、`replay`，动作工具还可能包含 `screenshot`。
   - `done_check`：done gate 的判断结果和拒绝原因。
   - `agent_done`：最终状态，不等于真实成功，必须结合证据判断。
4. 打开 `python/winpilot_home/traces/<run_id>/<before|after>_NNN.jpg`。`TraceRecorder` 会给 `click`、`type_text`、`hotkey`、`launch`、`drag`、`scroll`、`draw_strokes` 保存前后截图，用来确认是否真的点击/输入/切换/滚动/绘制。
   `run_action_batch` 内部子动作会写 `agent_batch_action_start/agent_batch_action`；会改变屏幕的子动作会保存 `batch_before_NNN.jpg` / `batch_after_NNN.jpg`，所以排查低延迟 batch 时要同时看这些截图，不能只看顶层 `run_action_batch` 的结果 JSON。
   对 `draw_strokes/drag` 这类强视觉动作，controller 还可能追加内部 `auto_visual_verify` 结果，提供动作区域的 `nonvisual_summary`，避免模型必须再走一轮独立 `zoom`。
   `click_element` 如果先前 snapshot 没找到文本锚点，会由 controller 自动做一次 `observe(query=label)` 并重试；排查时看 `controller_auto_observe`、`retry_anchor_locator` 和 `metrics.auto_observe_count`。
   `type_into_element` 是 batch 内部组合输入动作，会在一个 controller 子动作内完成锚点定位、点击聚焦、可选清空和输入；排查时看 `subresults`、`controller_internal_action_count` 和 `metrics.compound_round_trips_saved`。
   清晰的输入/按钮/绘图 routine 应优先看 `execute_subgoal`：它在同一 `ToolSession` 下调用 fast controller 完成定位、输入、点击、选色、绘制、等待和 `read_text`/changed ROI/局部视觉验证；工具结果里看 `subgoal_route=controller_first`、`actions_executed`、`controller_state`、`evidence_packet.matched_texts/missing_texts/candidate_click_labels/matched_visual_colors/missing_visual_colors/visible_colors`。如果锚点 label 失败但 `candidate_click_labels` 有值，下一步应让 LLM 用最相关的本地化/同义可见标签重试 `execute_subgoal`，不要转去 shell 或源码找 GUI 标签。如果绘图颜色失败，`missing_visual_colors` 会阻止 done gate 接受结果。若 LLM 顶层直接调用 `click/type_text/hotkey/scroll/focus_window/draw_strokes/drag` 而不是 `run_action_batch`，`AgentRunner` 会自动把它包装成单动作 batch；工具结果里应出现 `wrapped_by_fast_controller`、`metrics`、`needs_observe` 等证据。`draw_strokes/drag` 还可能出现 `auto_visual_verify` 或 `visual_change_detected`。验证充分时还会有 `fast_path_advice`，用于减少额外 zoom/observe 回合。
   如果 `execute_subgoal.draw` 返回 `needs_target_window=true`，说明模型在目标应用/画布打开前过早调用了绘图 routine；下一步应先 `launch/find_app/focus_window`，不要重复同一个 draw 请求。
   如果 `execute_subgoal` 只有 `verify_texts` 且没有目标 hwnd，它会返回 `needs_target_window=true`，避免把当前前台窗口里的同名文本误当成目标应用证据；这类任务要先打开/聚焦目标窗口，或先执行会产生状态变化的 GUI 动作。
   如果 `execute_subgoal.click.expected_region` 存在且当前没有 snapshot，它会先做 targeted initial OCR/CV。排查时看 `evidence_packet.prelude` 是否有 `targeted_observe`，以及 `dim_stats.*.scope=targeted_initial_roi`。如果 ROI 没识别到目标 label，会回退普通 observe。
   对带语义锚点的动作，controller 会把 `click(label=...)` 升级为 `click_element`，把 `type_text(label=..., text=...)` 升级为 `type_into_element`。看 `results[].upgraded_from`、`anchor_locator`、`resolved_element`、`subresults`，可以判断卡在锚点定位、聚焦、清空还是输入。
   普通可见动作也会在工具结果和 `agent_tool.result_meta` 里暴露 `screen_delta` 与 `action_target`。排查假成功时先看 `action_target.after.foreground_matches_target`：如果为 false，说明动作/验证可能落在 Codex、日志窗口或其他无关窗口上，必须重新 focus/observe 目标窗口。`screen_delta.changed` 只是像素变化证据，不等于任务成功。
   严格 GUI-only、`GUI only` 或 `只用GUI` 时，`run_shell/run_detached`、`search_files`、`list_dir/read_file/write_file/search_text` 不应出现在 `NNN_llm_request.json` 的 tools 列表里；如果仍出现，先查 `task_rules._gui_only_forbids_shell()` 和 `tool_schemas(..., allow_shell=False, allow_files=False)` 的路径。
   done gate 会要求显著字面量 token 出现在最终证据中，例如 `FAST_WRAP_OK_073`、`75765`。OCR/verify 会容忍 `_`、空格、连字符、冒号差异，所以 `FAST WRAP OK 073` 可以证明 `FAST_WRAP_OK_073`，但完全缺失该 token 不能用“功能等价”替代。
5. 打开 `python/winpilot_home/traces/<run_id>/NNN_llm_request.json` 和 `NNN_llm_response.json`。这里能看到模型每一步真实收到的 system/user/tool messages、折叠后的感知内容、用户插话、图片注入，以及模型实际返回的 tool calls。
   注意：`execute_subgoal`、`run_action_batch`、`zoom/screenshot`、`draw_strokes/drag` 写回 LLM history 时会经过 `_compact_tool_evidence()`，只保留 ok/verified、controller_state、视觉颜色、关键 metrics 和 fast_path_advice，避免后续每轮重复携带完整 strokes/截图摘要。完整原始工具结果仍在 `NNN_tool_result.json`、`.jsonl` 的 `agent_tool.result_meta` 和截图文件里；排查细节以 trace 原始文件为准。
   `/api/status.runtime` 和前端顶部 `runtime` badge 用来确认当前服务来自哪个 worktree/commit/端口/PID。先确认 runtime，再解释 trace；否则很容易把旧服务的行为误当成新分支结果。
6. 如果工具结果和屏幕不一致，按工具名定位 mixin：
   - 窗口/应用发现：`window_tools.py`
   - 感知/OCR/VLM：`perception_tools.py`
   - 点击/输入/拖拽/绘制：`interaction_tools.py`
   - shell/启动/tray/search/undo：`system_tools.py`
   - 计划/done/fail：`plan_tools.py`
   - 剪贴板/截图/鼠标细控：`desktop_tools.py`
7. 修复后重复同一路径测试，保留新的 trace/eval 报告；不要只用单元测试或 agent 的最终话术判断 GUI 能力。

## 工具结构

当前统一 Agent 工具被拆成几层：

| 文件 | 职责 |
| --- | --- |
| `agent/tool_schemas.py` | 构造短 OpenAI function-calling schema；Coding Runtime 激活时动态加入 repo 工具 |
| `agent/tool_result.py` | `ToolResult` 返回对象 |
| `agent/tools.py` | `ToolSession` 状态、dispatch、目标窗口解析和 runtime 挂载 |
| `agent/window_tools.py` | `list_windows`、`scan_windows`、`focus_window`、`find_app`、`find_program` 窗口/应用工具 mixin |
| `agent/perception_tools.py` | `observe`、`read_text`、`find_image`、`vlm_describe`、视觉摘要等感知工具 mixin |
| `agent/interaction_tools.py` | `click`、`type_text`、`hotkey`、`scroll`、`drag`、`draw_strokes`、`wait` 输入动作工具 mixin |
| `agent/system_tools.py` | `run_shell`、`run_detached`、`launch`、`tray_click`、`search_files`、`undo_last` 系统/启动工具 mixin；Coding Runtime 激活时 `run_shell` 委托到 workspace |
| `agent/fast_tools.py` | `execute_subgoal`、`run_action_batch`、`screen_watch` 低延迟 routine、动作批处理和屏幕变化 watcher mixin |
| `agent/plan_tools.py` | `revise_plan`、`complete_subgoal`、`done`、`fail` 计划和终止工具 mixin |
| `agent/file_tools.py` | `list_dir`、`read_file`、`write_file`、`search_text` 文件工具 mixin；Coding Runtime 激活时委托到 workspace-confined `CodingSession` |
| `agent/coding_tools.py` | Coding Runtime 的 repo 文件/patch/GitHub/shell 工具实现 |
| `agent/memory_tools.py` | `remember`、`forget`、`reuse_skill` 经验记忆和 0-token replay 工具 mixin |
| `agent/desktop_tools.py` | 剪贴板、鼠标细控、截图、显示器、已安装应用等桌面杂项工具 mixin |

默认 desktop/GUI 工具包括：

`list_windows`、`observe`、`scan_windows`、`focus_window`、`find_app`、`find_program`、`tray_click`、`launch`、`execute_subgoal`、`click`、`type_text`、`hotkey`、`scroll`、`drag`、`draw_strokes`、`wait`、`read_text`、`find_image`、`capture_template`、`color_probe`、`undo_last`、`run_action_batch`、`screen_watch`、`done`、`fail`。普通 desktop 任务仍可暴露 `search_files`；严格 GUI-only 且明确禁 shell 时会隐藏 `search_files`。

服务启动后会后台预热 OCR；排查第一次 OCR/`select_color` 特别慢时，先看 `/api/status.ocr_provider` 是否仍是 `uninitialized`。如果已经是 `DmlExecutionProvider` 或 `CPUExecutionProvider`，瓶颈通常在实际 OCR 区域、面板打开或色块扫描，而不是引擎冷启动。

按配置动态加入：

- `agent.shell_enabled=true`：`run_shell`、`run_detached`
- `agent.planning_enabled=true`：`revise_plan`、`complete_subgoal`
- `agent.experience_memory=true`：`remember`、`forget`、`reuse_skill`
- VLM 配置有效：`vlm_describe`

额外桌面/文件工具：

`clipboard_read`、`clipboard_write`、`clipboard_paste`、`cursor_position`、`mouse_down`、`mouse_up`、`move_mouse`、`screenshot`、`hold_key`、`list_displays`、`zoom`、`list_installed_apps`、`list_dir`、`read_file`、`write_file`、`search_text`。严格 GUI-only 且明确禁 shell 时会隐藏 `list_dir/read_file/write_file/search_text`；Coding Runtime 激活时还会加入 `apply_patch`、`github_search`，并把 `run_shell`、文件工具限定到 coding workspace。

`agent/tools.py` 已拆出 schema、窗口、感知、输入动作、系统/启动、计划/终止、文件、记忆、桌面杂项工具；现在主要保留会话状态、dispatch 和目标窗口解析。

## 记忆、经验复用和上下文折叠

当前实现不是一个单独的“大脑记忆库”，而是几层很小的运行时机制：

- 长期经验在 `memory/experience.py`，落盘到本机 `experience_memory.json`。每条 recipe 有 `id`、`task`、`app`、`steps`、`uses/successes`、`provisional`、`manual`、可选 replay `script`，现在还会带 `conditions` 和 `evidence`。老数据读取时会自动补默认适用条件和证据要求。
- `remember`/`forget`/`reuse_skill` 在 `agent/memory_tools.py`。`remember` 适合用户明确要求保存的长期事实或偏好；可以写入适用条件和证据要求。自动记录的动作 recipe 来自成功 GUI run，不用于 coding 任务。
- `reuse_skill` 只是 0-token 重放已导出的 `.wps.json`，不是语义成功证明。隐式复用必须是强任务匹配；弱关键词匹配只作为普通提示，不会自动 replay。只有 replay 本身成功且最终 run 完成的 recipe 才能获得正反馈；prompt 里出现过的 hint、或 replay 失败后靠实时操作兜底完成，都不算成功复用。replay 完成后仍必须用 `observe/read_text/VLM/nonvisual_summary` 或确定性工具结果确认最终状态，才能 `done`。
- 易变状态任务（当前窗口、是否登录、前台可见状态、状态检查等）不会记录或复用动作 recipe，避免旧 hwnd/旧屏幕结论污染新任务。
- session memory 在 `agent/session.py`，只保留本服务生命周期内最近完成任务，供“再来一次/继续/刚才那个/previous/same/it”等指代任务解析。普通新任务不会注入 session history，减少无效上下文。
- skill/playbook 是 `agent/playbooks.py` 加载的 JSON 情境攻略，有 task keyword 和 window title 两条注入路径。它是通用情境提示，不应变成单个软件的硬编码规则。
- world advisory 在 `perception/world.py`、`agent/context_guidance.py` 和 `agent/run_state.py`：只在 screen watcher 变化、重复动作、动作后无变化、need_observe/recovering 等有信号时追加短提示；它不主动感知，也不替代 done 证据。
- prompt/context 折叠在 `agent/loop.py`。旧的大型 observe/read_text/list_windows/截图消息只在上下文超过阈值后折叠，且保留最近两个全量观察。折叠后的 checkpoint 会保留任务目标、当前屏幕标题/hwnd/元素数、run_state、world 摘要、最近工具证据和被折叠项摘要。

上下文压缩子分支提示词：

```text
你负责 WinPilot GUI Agent 的上下文压缩子分支。先读当前代码，不要相信旧文档：agent/loop.py 的 messages/_fold_old/_fold_checkpoint，agent/session.py，agent/context_guidance.py，agent/run_state.py，perception/world.py，memory/experience.py，以及 done_verifier.py 的证据使用方式。目标是设计一个确定性、低风险的 context compactor：在不额外调用 LLM 的前提下，把历史消息压缩成结构化状态。必须保留 task goal、当前/最近屏幕标题和 target hwnd、关键 UI 状态、失败原因、run_state phase、最近工具结果、done gate 拒绝理由、用户 interjection、最近 plan 状态、已验证/未验证动作、replay/skill 结果及其证据要求。不得把 action ok 或 replay ok 改写成任务成功；不得为单个软件写专属规则；不得删除仍需模型引用的最近全量 observe。先补单元测试，再调整阈值和 trace 字段，最后跑 python -m pytest python/winpilot_home/tests -q。
```

## 感知

主入口：`perception/fusion.py`。

维度：

- UIA：结构化控件。
- Win32：窗口/标题/Explorer 项。
- OCR：像素文字。
- CV：图标、画布、颜色、图像候选。
- VLM：可选视觉语义描述。

视觉模式下 UIA/OCR 可并行采集；VLM 可在配置有效且 UIA 不超过 assist 阈值时参与辅助。当前默认 `vlm_assist_threshold=60`。
UIA 关闭且任务是绘图/画布时，`observe` 默认走 `canvas_fast_observe`：跳过全窗口 OCR，只返回 Win32/CV 元素和 `likely_canvas` 视觉摘要，适合先定位工作区再交给 `draw_strokes/drag`。如果确实需要读工具栏/菜单文字，调用 `observe(query="...")` 或 `observe(full=true)` 强制完整 OCR/UIA 感知。

## 动作

主文件：`action/executor.py`。

能力：

- SendInput 鼠标/键盘。
- 窗口聚焦/关闭/启动。
- UIA invoke/set_value。
- 剪贴板。
- 全屏/区域截图、zoom、display 列表。
- 绘制 stroke。

动作可经 `action/verify.py` 做 expect 验证。

## 配置

真实默认值在 `config.py`：

- `api.model=deepseek-v4-flash`
- `api.provider=auto`
- `api.pricing.provider=deepseek`
- `agent.max_steps=40`
- `agent.shell_enabled=true`
- `agent.world_model=true`
- `agent.planning_enabled=true`
- `agent.playbooks_enabled=true`
- `agent.verify_done=true`
- `agent.llm_trace=true`
- `agent.reflect_interval=8`
- `fusion.screen_watch_autostart=true`
- `fusion.observe_cache_ttl_s=2.0`
- `coding.enabled=true`
- `server.host=127.0.0.1`
- `server.port=8765`

本机配置文件：`python/winpilot_home/config.json`，不提交。

模型 provider、MiMo/Reasonix thinking 默认值、cache hit 字段兼容、usage/cost 统计见 `docs/model-providers.md`。当前真实实现是轻量 OpenAI-compatible Chat Completions adapter，不是完整 provider proxy。

## Coding Runtime

主开关：`agent/runtime_profile.py`。工具实现：`agent/coding_tools.py`。兼容旧导入：`agent/coding.py`。

Coding Runtime 不再是独立子 agent。它只在 `RuntimeProfile.coding=true` 时挂到 `AgentRunner` 的同一工具循环里，提供 workspace-confined 的 `list_dir`、`read_file`、`write_file`、`apply_patch`、`search_text`、`github_search`、`run_shell`。`done`、`fail` 仍走统一 `PlanToolMixin` 和统一 done gate。clone/run 类任务的运行尝试 gate 已迁入 `AgentRunner`，避免“看了 README 就宣称成功”。

## 2026-07-03 网页靶场和 changed ROI 复盘

网页靶场在 `python/winpilot_home/eval_targets/gui_agent_target.html`。启动方式：

```bash
cd python/winpilot_home/eval_targets
python -m http.server 8877 --bind 127.0.0.1
```

推荐测试任务：

```text
打开 http://127.0.0.1:8877/gui_agent_target.html。不要用shell、DOM或文件检查。主要走GUI：先观察页面，然后用 run_action_batch，subgoal 写成“触发局部状态变化”，点击页面左侧操作区从左到右第二个普通按钮（它在蓝色主按钮右边，会只改变局部状态卡），等待界面稳定，再调用 observe(changed_only=true) 只观察最近变化区域，确认状态卡出现 LOCAL_COUNTER，然后 done。
```

复盘时看这些证据：

- `agent_tool` 的 `launch` 应该 `verified=true`，并且目标窗口标题是 Chrome 靶场窗口，不应被 Codex 窗口遮挡。
- 如果没有显式调用 `screen_watch start`，也可能看到 watcher 状态：`fusion.screen_watch_autostart=true` 时，ToolSession 会在目标窗口就绪或可见动作前自动启动 watcher，并在 run 结束时停止。
- `run_action_batch` 的 `result_meta` 应该含 `subgoal`、`controller_state`、`metrics`；内部动作看 `agent_batch_action`。如果用了 `click_element`，还要看 `anchor_locator`、`resolved_element`、`element_cache`、`controller_auto_observe`，确认是缓存命中、snapshot 重新定位、目标 observe 后命中，还是锚点失败。`type_into_element` 还要看 `subresults`，确认失败阶段是 focus、clear 还是 type_text。
- `execute_subgoal` 的结果应该含 `actions_executed`、`controller_state` 和 `evidence_packet`。它支持输入+按钮、单个点击、有序 `clicks`、简单 `draw` 绘图，也支持“可见按钮 + 可验证状态”的 click-only routine；click-only 单状态验证会优先追加内部 `observe(changed_only=true, query=...)`，只看动作 ROI。绘图时 `select_color` 会先用通用颜色面板/色块检测选色，再用 `draw_strokes` 绘制；它只缓存颜色面板 opener 坐标，不缓存具体色块，色块仍每次重扫。颜色证据看 `matched_visual_colors/missing_visual_colors/visible_colors`。如果一个输入 routine 已输入成功但 `missing_texts` 包含后续才会出现的全局状态，说明证据作用域又被改坏了；当前规则只会继承当前 subgoal/args 中提到的全局 literal。锚点失败时看 `evidence_packet.candidate_click_labels`，这是从粗区域 snapshot 抽取的可见短文本候选，专门用于本地化/同义 UI label 恢复。
- 如果 `execute_subgoal` 只有 `subgoal` 和 `constraints`，没有 `input/click/clicks/draw/draws/verify_texts`，工具入口会快速 blocked，且不会初始化 controller/perception。排查这类 run 时不要去分析 OCR 为什么错了，真正问题是模型把 routine 工具当成了 launch/discovery；下一步应使用 `launch/find_app/focus_window/list_windows` 或补齐具体 routine。
- `execute_subgoal` 的 targeted initial ROI 只在有 `expected_region` 且 ROI 中识别到目标 label 时启用；snapshot 的 `region` 仍是完整窗口，避免 watcher 或后续坐标换算被裁剪区域污染。
- 如果 `execute_subgoal` 结果里的 `prelude` 有 `discard_stale_snapshot`，说明旧 snapshot 的 hwnd 和当前目标 hwnd 不一致，系统已避免把上一窗口的 reducer 证据混进当前任务。排查时应信当前 controller 动作/视觉证据，不要把旧 reducer 当失败原因。
- `planner.py` 会过滤“按钮标签回声 check”：例如点击 `Apply/Input` 按钮时，`text_appears=Apply/Input` 不是有效完成信号，因为点击前就存在；应使用点击后的新状态、文件、进程、窗口或显著 literal 作为 check。
- `complete_subgoal` 会接受当前 `execute_subgoal:evidence_packet` 里刚匹配到的可见文本，因此 controller 已验证的计划项不应再触发 full verify；普通 `observe/read_text` 历史证据仍只在任务或子目标明确“曾出现/出现过”时接受。
- 普通可见动作会有 `result_meta.screen_delta` 和 `action_target.after.foreground_matches_target=true`。现在动作 delta 使用独立前后帧；`screen_watch` 还会保留 `action_changed_regions`。如果后台 watcher 被弹层、动画、画布噪声带偏，`observe(changed_only=true)` 应优先新鲜 action ROI，再退回 recent ROI。
- `observe(changed_only=true)` 的 `result_text` 应该以 `Changed-only observe: OCR/CV/Win32 filtered to recent screen_watch ROI(s)` 开头；如果出现 `returned the normal observe snapshot`，说明没有可用 ROI 或 ROI 过期。如果是 `Changed-only observe query fallback`，说明 ROI 可用但没有命中 query，系统主动退回全窗口 observe。
- `done_check` 对显式可见状态目标可直接通过，例如 `Visible state accepted from fresh screen evidence: LOCAL_COUNTER`。这是 fresh screen OCR 证据，不是 agent 总结。

已验证 run：`20260703-025138-dcf2c6`。截图 `python/winpilot_home/traces/20260703-025138-dcf2c6/batch_after_002.jpg` 显示 `LOCAL_COUNTER_1`，整条路径走 `/api/chat`，UIA 关闭，只用 Win32/OCR/CV/VLM 维度。这个 run 的 `observe(changed_only=true)` 在 ROI 内直接识别到 `LOCAL COUNTER`，没有再补一次全量 `query=LOCAL_COUNTER`。

`/api/trace/<run_id>/summary` 会返回 `low_latency` 和 `fast_gui` 字段，用来定位瓶颈：截图耗时、感知耗时、LLM 决策耗时、动作执行耗时、动作后验证耗时、重规划频率、VLM 调用频率、skill 复用率、watcher 覆盖情况、batch/subgoal routine、controller phase、anchor click/cache/failure。`subgoal_elapsed_ms/subgoal_controller_elapsed_ms/subgoal_overhead_ms` 能区分慢在 controller 内部，还是慢在 `execute_subgoal` 前置 observe/包装开销。以后做速度优化先看这里，再决定该减少 VLM、减少 LLM round-trip，还是改 controller 内环。注意 `vlm_call_count` 只统计真实 VLM 工作：`count>0` 或 `ms>0`；未配置 VLM 时 fusion 里的 0ms 占位不会计数。

最新 controller-first 靶场验证：`20260704-005515-7f70a7`。任务通过 `/api/chat` 进入，最终工具序列为 `launch -> observe -> execute_subgoal(input+apply) -> execute_subgoal(counter) -> execute_subgoal(verify) -> done`，只有 11 个工具和 1 次 observe，done gate 一次通过；真实截图 `python/winpilot_home/traces/20260704-005515-7f70a7/batch_after_003.jpg` 显示输入框 `SUBGOAL_OK_078` 与状态卡 `LOCAL_COUNTER_2`。

最新 click-only 靶场验证：`20260704-014433-99e853`。任务通过 `/api/chat` 进入，最终工具序列为 `launch -> complete_subgoal -> execute_subgoal(click+verify LOCAL_COUNTER) -> complete_subgoal -> done`，5 个工具完成；真实截图 `python/winpilot_home/traces/20260704-014433-99e853/batch_after_002.jpg` 显示 `LOCAL_COUNTER_1`，`low_latency.vlm_call_count=0`。

最新 changed-only click-only 验证：`20260704-020133-18f863`。`execute_subgoal` 内部为 `click_element -> wait_stable -> observe(changed_only=true, query=LOCAL_COUNTER)`，工具耗时约 5.9s；真实截图 `python/winpilot_home/traces/20260704-020133-18f863/batch_after_002.jpg` 显示 `LOCAL_COUNTER_1`。`20260704-020623-8f8903` 进一步验证 `complete_subgoal` 直接使用 controller evidence，计划核验耗时约 1ms；但模型仍可能额外 `observe(query=LOCAL_COUNTER)`，这是后续要减少的 LLM 复验惯性。

最新 targeted initial ROI 验证：`20260704-022217-2eeead`。模型给 `execute_subgoal.click.expected_region=left`，`fast_gui.subgoal_overhead_ms=327`，说明没有内部全窗口 prelude；真实截图 `python/winpilot_home/traces/20260704-022217-2eeead/batch_after_002.jpg` 显示 `LOCAL_COUNTER_1`。该 run 仍有一次模型主动 `observe(full=true)`，后续要继续降低 LLM 执行前全量观察频率。

本地化标签恢复验证：`20260704-031451-4d2852`。任务通过 `/api/chat` 进入，要求点击英文 `Local Counter`，但页面真实按钮是中文“只改局部状态”。第一次 `execute_subgoal` 在没有 session hwnd 时使用 foreground hwnd 做 targeted ROI，并返回 `candidate_click_labels`；随后 controller recovery hint 让模型用中文可见标签重试并成功。工具序列为 `execute_subgoal(fail) -> observe -> execute_subgoal(success) -> complete_subgoal -> done`，tools schema 没有 `run_shell`，真实截图 `python/winpilot_home/traces/20260704-031451-4d2852/batch_after_002.jpg` 显示 `LOCAL_COUNTER_1`。

Paint 绘图/选色验证：`20260704-041425-bbbe11`。任务通过 `/api/chat` 进入，要求只用 GUI 打开 Windows Paint 并画蓝色三角形。真实截图 `python/winpilot_home/traces/20260704-041425-bbbe11/batch_after_002.jpg` 显示蓝色三角形；成功证据来自 1 次 `execute_subgoal.draw`，动作序列为 `select_color -> draw_strokes -> wait_stable`，动作区域 `auto_visual_verify` 报告 `matched_visual_colors=["blue"]`、`visible_colors.blue.pixels=2937`。失败对照 `20260704-035617-1d7d9c` 曾把顶部彩色图标误判为色块并画出黑色三角形，现已通过色块聚簇过滤、目标 hwnd 区域优先和 `missing_visual_colors` done gate 修复。

Paint 多图形验证：`20260704-045155-a9dc21`。在 `mimo-v2.5` 下要求只用 GUI 从空白 Paint 画布绘制左侧蓝色三角形和右侧红色矩形，最终 `done`。真实画面和 trace 证据一致：蓝色三角形 `visible_colors.blue.pixels=3835`，红色矩形 `visible_colors.red.pixels=2856`，两个 `execute_subgoal.draw` 都 `verified=true`。这轮验证了 canvas-ready wait、`draw.expected_region` 左右区域、视觉颜色 evidence ledger，以及绘图计划项不再把颜色词当屏幕文字核验。

`execute_subgoal.draws[]` 是当前多图形 routine 的优先路径：相邻的简单 shape/color/region 计划项会在 `AgentRunner` 中被保守合并为一次 controller 调用；自定义 `strokes`、确认/截图/保存类计划项不会被合并。无额外 check 的简单绘图 plan 项可由 verified `draws[]` 证据自动标记完成；“确认画布/图形/颜色可见”这类纯视觉 plan 项也可由同一 controller 颜色证据自动完成，plan 全部完成时当前 `execute_subgoal` 会返回 terminal done，但仍走 done gate。若后续 LLM 又请求最近 controller 已真实执行且颜色证据匹配的同一 shape/color/region，loop 会返回 `subgoal_route=controller_evidence_reuse`，让模型完成计划项而不是重复绘制。若后续只是纯颜色 verify，且最近 evidence 已匹配对应颜色，loop 会返回 `subgoal_route=controller_visual_evidence_reuse`。如果用户没有要求保存文件，`complete_subgoal` 会忽略绘图计划里凭空生成的 `file_exists`/`file_contains` 图片检查；这不是放宽 done，最终仍必须通过 controller 视觉颜色证据、截图或其它真实证据。

成本/步数回归验证：`20260704-053455-7b33d3`。同一 GUI-only Paint 多图形任务在便宜模型 `mimo-v2.5`、`max_steps=9` 下 8 个工具完成，序列为 `launch -> complete_subgoal -> execute_subgoal(draw blue) -> complete_subgoal -> execute_subgoal(draw red) -> complete_subgoal -> screenshot -> done`；真实截图 `python/winpilot_home/traces/manual_screen_20260704_053535.jpg` 显示两个图形。该 run 验证了两条成本/假证据修复：verify-only `execute_subgoal` 没有 target hwnd 时会快速返回 `needs_target_window=true`，不再从前台杂音里读 app 标题；planner 会丢弃绘图/视觉确认项里的弱文本 app-title check，避免把可见像素证据误核验成 `Paint/画图` 文字。`prompt_tokens=92863`，比 `20260704-052452-04bc28` 的 10 步成功 run `119532` 更低。

schema/计划成本回归：`20260704-055208-e16c54`。同一任务在 `mimo-v2.5`、`max_steps=8` 下完成；真实截图 `python/winpilot_home/traces/manual_screen_20260704_055249.jpg` 显示两个图形。`execute_subgoal` schema 文案已压缩但字段不变，GUI-only tools JSON 从约 23516 字符降到约 21471；planning 提示改为“如果当前 todo 已显示，就不要只为改写措辞调用 `revise_plan`”。该 run 无多余 `revise_plan`，`cache_miss_tokens=5516`。`complete_subgoal` 在全部子目标完成时会返回 `terminal="done"` 作为兜底节省最后一轮，但 `AgentRunner` 仍会对 terminal done 走原有 done gate。

绘图 guidance 压缩回归：`20260704-055941-d4c164`。同一任务在 `mimo-v2.5`、`max_steps=8` 下完成，真实截图 `python/winpilot_home/traces/manual_screen_20260704_060024.jpg` 显示两个图形。绘图任务额外 guidance 从旧 trace 约 2814 字符压到约 873 字符；该 run 通过 `complete_subgoal(4)` 的 `terminal="done"` 收尾，少一轮显式 `done`，`prompt_tokens=87619`、`cache_miss_tokens=5059`。

严格 GUI-only schema/终止核验回归：`20260704-062419-8f48dc`。同一任务在 `mimo-v2.5`、`max_steps=8` 下完成，真实截图 `python/winpilot_home/traces/manual_screen_20260704_062508.jpg` 显示两个图形。8 次 LLM request 的 tools schema 都是 39 个工具、约 19709 字符，且没有 `run_shell/run_detached/search_files/list_dir/read_file/write_file/search_text`；`done_checks` 记录 `Drawing task accepted from controller visual color evidence: red, blue.`，证明 `complete_subgoal` terminal done 没有跳过 done gate，`cache_miss_tokens=4401`。

plan tool history 压缩回归：`20260704-063415-fcd39d`。同一任务在 `mimo-v2.5`、`max_steps=8` 下完成，真实截图 `python/winpilot_home/traces/manual_screen_20260704_063504.jpg` 显示两个图形。`complete_subgoal` 原始结果仍完整写 trace，但 LLM history 里只保留 JSON 摘要：完成项、`remaining_steps`、`next_unfinished` 和简短 guidance；最后一个 LLM request 中相关 tool message 为 361-487 字符且不含 `Plan hint:`，`prompt_tokens=81545`、`cache_miss_tokens=4233`。

相邻绘图子目标合并/压缩证据回归：`20260704-073655-e7b108`。同一任务在 `mimo-v2.5`、`max_steps=8` 下完成，真实截图 `python/winpilot_home/traces/manual_screen_20260704_073736.jpg` 显示左侧蓝色三角形和右侧红色矩形。第一次 `execute_subgoal(draw blue)` 自动扩展为 `draws=[blue triangle left, red rectangle right]`，整轮只有 1 次 `execute_subgoal`；压缩后的 tool evidence 保留 `draw_strokes` 和视觉颜色证据，done gate 通过 `red, blue` 接受，`prompt_tokens=70113`、`cache_miss_tokens=3937`。

绘图计划假文件检查回归：`20260704-075519-703ae4`。同一任务在便宜模型 `mimo-v2.5`、`max_steps=8` 下完成，真实截图 `python/winpilot_home/traces/manual_screen_20260704_075555.jpg` 显示左侧蓝色三角形和右侧红色矩形。工具序列为 `launch -> complete_subgoal -> execute_subgoal(draws blue/red) -> complete_subgoal -> complete_subgoal -> screenshot -> complete_subgoal`，没有 `run_shell`、没有 File/Save As，也没有验证凭空路径；`done_checks` 仍由 controller 视觉颜色证据 `red, blue` 通过，`prompt_tokens=59172`、`cache_miss_tokens=3428`。

绘图视觉确认自动完成回归：`20260704-081006-61ccb2`。同一任务在便宜模型 `mimo-v2.5`、`max_steps=8` 下完成，真实截图 `python/winpilot_home/traces/manual_screen_20260704_081043.jpg` 显示左侧蓝色三角形和右侧红色矩形。工具序列缩短为 `launch -> complete_subgoal(1) -> execute_subgoal(draws blue/red, terminal done)`；`tool_count=3`、`decision_count=3`，没有额外截图/保存/文件检查，done gate 仍由 controller 视觉颜色证据 `red, blue` 通过，`prompt_tokens=27253`、`cache_miss_tokens=1269`。

颜色面板 opener 缓存回归：`20260704-082538-6ac6bd`。同一任务在便宜模型 `mimo-v2.5` 下完成，真实截图 `python/winpilot_home/traces/manual_screen_20260704_082615.jpg` 显示两个图形。第二次 `select_color` 复用 opener 后内部耗时约 1604ms；由于色块仍重新扫描，这个缓存不会把旧色块坐标当成新证据。

端到端清洁回归：`20260704-085111-80e560`。同一任务在便宜模型 `mimo-v2.5` 下完成，真实截图 `python/winpilot_home/traces/manual_screen_20260704_085209.jpg` 显示两个图形。工具序列为 `launch -> complete_subgoal(1) -> execute_subgoal(draws blue/red, terminal done)`；没有空 `execute_subgoal`、没有 shell、没有文件读写，`evidence_packet.perception_reducer={}`，done gate 仍由 controller 视觉颜色证据 `red, blue` 通过。
## 2026-07-04 low-cost schema profile

- WinPilot still exposes one `AgentRunner`; `agent.tool_profile=auto` only changes the model-facing function schema for a run.
- Strict GUI-only drawing/canvas tasks automatically use `drawing_gui`. The local controller and `ToolSession.dispatch()` are not reduced; only the tools shown to the LLM are filtered and compacted.
- `drawing_gui` keeps the controller-first path (`execute_subgoal`, `run_action_batch`, observe/focus/launch, basic click/type/hotkey/drag/draw/wait, screenshot/zoom, plan, done/fail) and hides unrelated tools such as template capture, color probe, low-level mouse hold/release/move, clipboard read/write, and installed-app registry listing.
- In `drawing_gui`, `execute_subgoal` only exposes draw/draws/verify fields; use `launch/find_app/focus_window` before drawing. The executor also rejects unresolved `target_window` process/title hints before initializing OCR/controller, so it will not silently operate on Codex or another foreground window.
- To debug token/cost regressions, inspect `python/winpilot_home/traces/<run_id>/NNN_llm_request.json`: strict GUI standard schema is roughly 19.9k JSON chars, while the drawing profile is roughly 7.4-8.2k JSON chars depending on VLM/schema options.
