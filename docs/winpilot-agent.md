# WinPilot Agent

当前 WinPilot 主体在 `python/winpilot_home/winpilot/`。

## 路由

`agent/router.py` 根据任务选择 runner：

- 用户始终面对一个 WinPilot Agent；`AgentRunner`、`CodingRunner` 只是内部 runner/capability。
- GUI/桌面任务：主要走 `AgentRunner`。
- 编程/仓库任务：走 `CodingRunner` 这类 Coding capability。
- GUI-only 任务不会路由到 Coding capability。

## GUI AgentRunner

主文件：`agent/loop.py`。

职责：

- 构造 system prompt。
- 调 OpenAI 兼容 function-calling 接口。
- 管理工具调用、上下文折叠、interjection、usage。
- done 二次验证，防止假成功。
- 可选 planning、playbook、experience memory、world advisory。
- 视觉模型可用时，`observe` 后可把当前窗口截图注入为 `image_url` 消息。

`loop.py` 仍是 GUI runner 主循环，但部分纯逻辑已拆出：

| 文件 | 职责 |
| --- | --- |
| `agent/task_rules.py` | 任务分类、GUI-only guard、done gate 需要的纯字符串规则 |
| `agent/done_verifier.py` | done 二次核验 mixin，集中处理假成功防护 |
| `agent/context_guidance.py` | upfront plan、world advisory、stuck recovery、playbook 注入 |
| `agent/reflection.py` | 反思检查点 mixin，不额外调用 LLM |
| `agent/loop.py` | LLM 编排、工具调用循环、上下文折叠和终止收尾 |

后续优先继续拆 `agent/tools.py`，不要把新规则继续塞回 `loop.py`。

## GUI 执行时序

一次 GUI 任务的真实路径：

1. 前端 Agent 页或 `scripts/gui_eval_run.py` 调 `/api/chat`。
2. `server.py` 创建 `_RunState.stop_event`、`runner`、后台线程，并启动 `recorder/trace.py`。
3. `agent/router.py` 在同一个用户可见 Agent 内选择 `AgentRunner` 或 Coding capability；GUI-only 任务不会进入 Coding capability。
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
   如果 LLM 顶层直接调用 `draw_strokes/drag` 而不是 `run_action_batch`，`AgentRunner` 会自动把它包装成单动作 batch；工具结果里应出现 `wrapped_by_fast_controller`、`auto_visual_verify` 或 `visual_change_detected`。验证充分时还会有 `fast_path_advice`，用于减少额外 zoom/observe 回合。
   普通可见动作也会在工具结果和 `agent_tool.result_meta` 里暴露 `screen_delta` 与 `action_target`。排查假成功时先看 `action_target.after.foreground_matches_target`：如果为 false，说明动作/验证可能落在 Codex、日志窗口或其他无关窗口上，必须重新 focus/observe 目标窗口。`screen_delta.changed` 只是像素变化证据，不等于任务成功。
5. 打开 `python/winpilot_home/traces/<run_id>/NNN_llm_request.json` 和 `NNN_llm_response.json`。这里能看到模型每一步真实收到的 system/user/tool messages、折叠后的感知内容、用户插话、图片注入，以及模型实际返回的 tool calls。
6. 如果工具结果和屏幕不一致，按工具名定位 mixin：
   - 窗口/应用发现：`window_tools.py`
   - 感知/OCR/VLM：`perception_tools.py`
   - 点击/输入/拖拽/绘制：`interaction_tools.py`
   - shell/启动/tray/search/undo：`system_tools.py`
   - 计划/done/fail：`plan_tools.py`
   - 剪贴板/截图/鼠标细控：`desktop_tools.py`
7. 修复后重复同一路径测试，保留新的 trace/eval 报告；不要只用单元测试或 agent 的最终话术判断 GUI 能力。

## 工具结构

当前 GUI agent 工具被拆成几层：

| 文件 | 职责 |
| --- | --- |
| `agent/tool_schemas.py` | 构造短 OpenAI function-calling schema，直接发给 LLM |
| `agent/tool_result.py` | `ToolResult` 返回对象 |
| `agent/tools.py` | `ToolSession` 状态、dispatch、目标窗口解析 |
| `agent/window_tools.py` | `list_windows`、`scan_windows`、`focus_window`、`find_app`、`find_program` 窗口/应用工具 mixin |
| `agent/perception_tools.py` | `observe`、`read_text`、`find_image`、`vlm_describe`、视觉摘要等感知工具 mixin |
| `agent/interaction_tools.py` | `click`、`type_text`、`hotkey`、`scroll`、`drag`、`draw_strokes`、`wait` 输入动作工具 mixin |
| `agent/system_tools.py` | `run_shell`、`run_detached`、`launch`、`tray_click`、`search_files`、`undo_last` 系统/启动工具 mixin |
| `agent/fast_tools.py` | `run_action_batch`、`screen_watch` 低延迟动作批处理和屏幕变化 watcher mixin |
| `agent/plan_tools.py` | `revise_plan`、`complete_subgoal`、`done`、`fail` 计划和终止工具 mixin |
| `agent/file_tools.py` | `list_dir`、`read_file`、`write_file`、`search_text` 文件工具 mixin |
| `agent/memory_tools.py` | `remember`、`forget`、`reuse_skill` 经验记忆和 0-token replay 工具 mixin |
| `agent/desktop_tools.py` | 剪贴板、鼠标细控、截图、显示器、已安装应用等桌面杂项工具 mixin |

当前 GUI agent 工具包括：

`list_windows`、`observe`、`scan_windows`、`focus_window`、`find_app`、`find_program`、`tray_click`、`launch`、`click`、`type_text`、`hotkey`、`scroll`、`drag`、`draw_strokes`、`wait`、`read_text`、`find_image`、`capture_template`、`color_probe`、`undo_last`、`search_files`、`run_action_batch`、`screen_watch`、`done`、`fail`。

按配置动态加入：

- `agent.shell_enabled=true`：`run_shell`、`run_detached`
- `agent.planning_enabled=true`：`revise_plan`、`complete_subgoal`
- `agent.experience_memory=true`：`remember`、`forget`、`reuse_skill`
- VLM 配置有效：`vlm_describe`

额外桌面/文件工具：

`clipboard_read`、`clipboard_write`、`clipboard_paste`、`cursor_position`、`mouse_down`、`mouse_up`、`move_mouse`、`screenshot`、`hold_key`、`list_displays`、`zoom`、`list_installed_apps`、`list_dir`、`read_file`、`write_file`、`search_text`。

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

## 编程子 Agent

主文件：`agent/coding.py`，工具在 `agent/coding_tools.py`。

工具包括 `list_dir`、`read_file`、`write_file`、`apply_patch`、`search_text`、`github_search`、`run_shell`、`done`、`fail`。clone/run 类任务有运行尝试 gate，避免“看了 README 就宣称成功”。

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
- 普通可见动作会有 `result_meta.screen_delta` 和 `action_target.after.foreground_matches_target=true`。现在动作 delta 使用独立前后帧；`screen_watch` 还会保留 `action_changed_regions`。如果后台 watcher 被弹层、动画、画布噪声带偏，`observe(changed_only=true)` 应优先新鲜 action ROI，再退回 recent ROI。
- `observe(changed_only=true)` 的 `result_text` 应该以 `Changed-only observe: OCR/CV/Win32 filtered to recent screen_watch ROI(s)` 开头；如果出现 `returned the normal observe snapshot`，说明没有可用 ROI 或 ROI 过期。如果是 `Changed-only observe query fallback`，说明 ROI 可用但没有命中 query，系统主动退回全窗口 observe。
- `done_check` 对显式可见状态目标可直接通过，例如 `Visible state accepted from fresh screen evidence: LOCAL_COUNTER`。这是 fresh screen OCR 证据，不是 agent 总结。

已验证 run：`20260703-025138-dcf2c6`。截图 `python/winpilot_home/traces/20260703-025138-dcf2c6/batch_after_002.jpg` 显示 `LOCAL_COUNTER_1`，整条路径走 `/api/chat`，UIA 关闭，只用 Win32/OCR/CV/VLM 维度。这个 run 的 `observe(changed_only=true)` 在 ROI 内直接识别到 `LOCAL COUNTER`，没有再补一次全量 `query=LOCAL_COUNTER`。

`/api/trace/<run_id>/summary` 会返回 `low_latency` 和 `fast_gui` 字段，用来定位瓶颈：截图耗时、感知耗时、LLM 决策耗时、动作执行耗时、动作后验证耗时、重规划频率、VLM 调用频率、skill 复用率、watcher 覆盖情况、batch subgoal、controller phase、anchor click/cache/failure。以后做速度优化先看这里，再决定该减少 VLM、减少 LLM round-trip，还是改 controller 内环。
