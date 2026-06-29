# WinPilot Agent

当前 WinPilot 主体在 `python/winpilot_home/winpilot/`。

## 路由

`agent/router.py` 根据任务选择 runner：

- GUI/桌面任务：`AgentRunner`。
- 编程/仓库任务：`CodingRunner`。
- GUI-only 任务不会路由到 `CodingRunner`。

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
3. `agent/router.py` 选择 `AgentRunner` 或 `CodingRunner`；GUI-only 任务不会进入编程子 agent。
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
4. 打开 `python/winpilot_home/traces/<run_id>/<before|after>_NNN.jpg`。`TraceRecorder` 会给 `click`、`type_text`、`hotkey`、`launch`、`drag`、`scroll` 保存前后截图，用来确认是否真的点击/输入/切换/滚动。
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
| `agent/plan_tools.py` | `revise_plan`、`complete_subgoal`、`done`、`fail` 计划和终止工具 mixin |
| `agent/file_tools.py` | `list_dir`、`read_file`、`write_file`、`search_text` 文件工具 mixin |
| `agent/memory_tools.py` | `remember`、`forget`、`reuse_skill` 经验记忆和 0-token replay 工具 mixin |
| `agent/desktop_tools.py` | 剪贴板、鼠标细控、截图、显示器、已安装应用等桌面杂项工具 mixin |

当前 GUI agent 工具包括：

`list_windows`、`observe`、`scan_windows`、`focus_window`、`find_app`、`find_program`、`tray_click`、`launch`、`click`、`type_text`、`hotkey`、`scroll`、`drag`、`draw_strokes`、`wait`、`read_text`、`find_image`、`capture_template`、`color_probe`、`undo_last`、`search_files`、`done`、`fail`。

按配置动态加入：

- `agent.shell_enabled=true`：`run_shell`、`run_detached`
- `agent.planning_enabled=true`：`revise_plan`、`complete_subgoal`
- `agent.experience_memory=true`：`remember`、`forget`、`reuse_skill`
- VLM 配置有效：`vlm_describe`

额外桌面/文件工具：

`clipboard_read`、`clipboard_write`、`clipboard_paste`、`cursor_position`、`mouse_down`、`mouse_up`、`move_mouse`、`screenshot`、`hold_key`、`list_displays`、`zoom`、`list_installed_apps`、`list_dir`、`read_file`、`write_file`、`search_text`。

`agent/tools.py` 已拆出 schema、窗口、感知、输入动作、系统/启动、计划/终止、文件、记忆、桌面杂项工具；现在主要保留会话状态、dispatch 和目标窗口解析。

## 感知

主入口：`perception/fusion.py`。

维度：

- UIA：结构化控件。
- Win32：窗口/标题/Explorer 项。
- OCR：像素文字。
- CV：图标、画布、颜色、图像候选。
- VLM：可选视觉语义描述。

视觉模式下 UIA/OCR 可并行采集；VLM 可在配置有效且 UIA 不超过 assist 阈值时参与辅助。当前默认 `vlm_assist_threshold=60`。

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
- `agent.max_steps=40`
- `agent.shell_enabled=true`
- `agent.world_model=true`
- `agent.planning_enabled=true`
- `agent.playbooks_enabled=true`
- `agent.verify_done=true`
- `agent.llm_trace=true`
- `agent.reflect_interval=8`
- `coding.enabled=true`
- `server.host=127.0.0.1`
- `server.port=8765`

本机配置文件：`python/winpilot_home/config.json`，不提交。

## 编程子 Agent

主文件：`agent/coding.py`，工具在 `agent/coding_tools.py`。

工具包括 `list_dir`、`read_file`、`write_file`、`apply_patch`、`search_text`、`github_search`、`run_shell`、`done`、`fail`。clone/run 类任务有运行尝试 gate，避免“看了 README 就宣称成功”。
