# WinPilot Agent

WinPilot 是当前桌面 Agent 主体，位于 `python/winpilot_home/winpilot/`。

## 入口

| 入口 | 代码 | 说明 |
|---|---|---|
| 前端 Agent 页面 | `src/renderer/components/AgentView.tsx` + `winpilot_home/webui/` | iframe 打开 WinPilot webui |
| FastAPI | `winpilot/server.py` | `/api/chat` 启动任务，`/ws/events` 推送日志 |
| Electron IPC | `python/agent/__main__.py` | stdin/stdout JSON，路由到 WinPilot runner |
| 路由 | `winpilot/agent/router.py` | 根据任务选择 `AgentRunner` 或 `CodingRunner` |

## 配置

默认配置在 `winpilot/config.py`，运行时写入 `python/winpilot_home/config.json`。

重要配置：

| 配置 | 含义 |
|---|---|
| `api.base_url/api_key/model` | OpenAI 兼容文本模型，默认 DeepSeek API 和 `deepseek-v4-flash` |
| `vlm.*` | 可选视觉模型配置，默认关闭 |
| `perception.uia/win32/ocr/cv/vlm` | 感知维度开关和优先级 |
| `fusion.escalate_on_empty` | 空感知时逐级兜底 |
| `fusion.ocr_skip_when_uia_rich` | UIA 信息充足时跳过 OCR 节省时间 |
| `agent.max_steps` | 主循环最大步数 |
| `agent.shell_enabled` | 是否暴露 shell 工具 |
| `agent.world_model` | 是否注入世界状态/卡住提示 |
| `agent.planning_enabled` | 是否启用计划工具 |
| `agent.playbooks_enabled` | 是否按任务/窗口注入 playbook |
| `agent.verify_done` | 是否让 done 经过怀疑型二次验证 |
| `agent.llm_trace` | 是否保存每轮 LLM forensic trace |
| `coding.enabled/workspace/max_steps` | 编程子 agent 开关、工作区、最大步数 |

`config.example.json` 只是示例；若字段少于 `config.py`，以 `config.py` 默认值为准。

## GUI Agent 循环

`AgentRunner` 在 `agent/loop.py`：

1. 构造系统提示词、任务、记忆、playbook、世界状态。
2. 调用 OpenAI 兼容 chat completions function-calling。
3. 接收工具调用并交给 `ToolSession`。
4. 动作工具通过 `action/verify.py` 执行 expect 校验和重试。
5. `done` 不是直接相信模型，而是重新观察屏幕并按任务类型做二次判断。
6. 成功后可导出 trace/`.wps.json`，并写入经验/事实笔记。

当前代码对多类假成功有额外防护：

- 绘图任务要求真实拖拽、保存文件存在、必要颜色在保存图里出现。
- 通信任务要求用户指定的引号内容在目标界面可见。
- GUI 搜索/商店搜索要求最终屏幕证据。
- 上传/发布任务会阻止用媒体播放/分析冒充发布。
- GUI-only 任务会阻止 agent 用 shell/file 快捷方式绕过前端路径。

## 工具集

主 GUI 工具由 `agent/tools.py` 定义。核心工具包括：

`list_windows`、`scan_windows`、`observe`、`focus_window`、`find_app`、`find_program`、`tray_click`、`launch`、`click`、`type_text`、`hotkey`、`scroll`、`drag`、`wait`、`read_text`、`find_image`、`capture_template`、`color_probe`、`undo_last`、`search_files`、`done`、`fail`。

按配置/任务可额外启用：

- `run_shell`、`run_detached`
- `revise_plan`、`complete_subgoal`
- `remember`、`forget`、`reuse_skill`
- `vlm_describe`

注意：这些是 WinPilot 的 LLM 工具，不是 `python/features/` 的 @feature。

## 感知

`perception/fusion.py` 融合多种维度：

| 维度 | 代码 | 作用 |
|---|---|---|
| UIA | `perception/uia.py` | 控件树、可点击控件、名称/角色 |
| Win32 | `perception/win32.py` | 窗口、标题、进程等桌面信息 |
| OCR | `perception/ocr.py` | UIA 覆盖不到的软件文字 |
| CV | `perception/vision_cv.py` | 图像差异、颜色、画布、模板、粗视觉摘要 |
| VLM | `perception/vlm.py` | 可选视觉模型描述，配置后才启用 |

当前实现包含这些稳定性策略：

- UIA 超时和预算被压低，避免单轮 observe 卡死。
- 当前台模态窗口覆盖目标窗口时跳过目标窗口 UIA，改走 Win32/OCR/CV。
- UIA 足够丰富时可跳过 OCR。
- 空结果会升级到 OCR/VLM 兜底。
- 绘画/画布/颜色任务会追加 `Visual state summary`，包括可能画布区域、粗颜色、工具面板/弹窗风险。
- `drag` 会记录动作前后区域差异，给模型反馈是否真的画出了东西。

## 世界模型和记忆

- `perception/world.py` 和 loop 内状态用于发现重复动作、目标窗口变化、视觉告警。
- `memory/experience.py` 存成功经验和事实笔记。
- 编程任务不复用旧 GUI 动作轨迹，避免跨项目假复用；可保留事实笔记。
- `agent/session.py` 保存会话上下文，前端可查看和清空。

世界模型不是“用户记忆越多越聪明”的替代品。它的作用是把当前任务里的屏幕状态、重复失败、窗口锚点、视觉异常等短期事实反馈给模型，减少盲试和假成功。新用户没有历史记忆时，硬实力仍取决于感知质量、工具反馈、验证 gate 和 prompt 结构。

## CodingRunner

`agent/coding.py` + `agent/coding_tools.py` 是独立编程子 agent。

工具：

- `list_dir`
- `read_file`
- `write_file`
- `apply_patch`
- `search_text`
- `github_search`
- `run_shell`
- `done`
- `fail`

安全/质量约束：

- 路径限制在 `coding.workspace`。
- 保护 `.env`、secret、credential 等敏感配置。
- 对“克隆开源项目并跑起来”这类任务，不能只读 README 后宣布成功，必须实际尝试 clone/install/build/run/test 或给出精确 blocker。

## Playbook

`python/winpilot_home/playbooks/*.json` 是数据化情境攻略，不增加工具。当前有：

- `coding-project.json`
- `file-dialog.json`
- `media-playback.json`
- `tray-wakeup.json`

`agent.playbooks_enabled=true` 时按任务/窗口选择性注入；关闭时按代码逻辑处理为非选择性注入。
