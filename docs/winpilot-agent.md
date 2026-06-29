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

## 工具结构

当前 GUI agent 工具被拆成几层：

| 文件 | 职责 |
| --- | --- |
| `agent/tool_schemas.py` | 构造短 OpenAI function-calling schema，直接发给 LLM |
| `agent/tool_result.py` | `ToolResult` 返回对象 |
| `agent/tools.py` | `ToolSession`、dispatch、屏幕/输入/视觉等核心运行态 handler |
| `agent/window_tools.py` | `list_windows`、`scan_windows`、`focus_window`、`find_app`、`find_program` 窗口/应用工具 mixin |
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

`agent/tools.py` 仍偏大，但 schema、窗口、文件、记忆、桌面杂项工具已拆出。后续继续拆输入、视觉等 handler。

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
