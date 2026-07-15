# API / IPC / MCP

项目有四类接口：Electron HTTP API、WinPilot REST/WebSocket、Electron IPC、MCP。它们服务不同的运行时，不能因为路径或工具名相似就混用。

## Electron HTTP API

实现：`src/main/api.ts`。默认监听 `127.0.0.1:7880`，可由 `API_PORT` 覆盖。除 `/api/ping` 外，接口要求 Electron 进程生成的 bearer token，并严格限制本地 origin。`/api/execute-js` 默认禁用，只能用显式开发环境变量开启。

当前端点（除明确列出的 GET 外，下面各功能组均为 POST；`/api/ping`、`/api/get-state`、`/api/set-view-mode` 和 `/api/fit-screen` 同时提供 GET/POST 形态）：

- GET：`/api/ping`、`/api/get-state`、`/api/set-view-mode`、`/api/fit-screen`、`/api/execute-js`。
- 脚本/画布：`/api/load-script`、`/api/new-script`、`/api/save-script`、`/api/set-script-name`、`/api/check-params`、`/api/auto-layout`。
- 节点/边：`/api/add-node`、`/api/update-node`、`/api/update-node-params`、`/api/delete-nodes`、`/api/add-edge`、`/api/delete-edges`。
- 编辑命令：`/api/undo`、`/api/redo`、`/api/select-all`、`/api/clear-selection`、`/api/copy-selected`、`/api/cut-selected`、`/api/paste`、`/api/duplicate-nodes`。
- 执行：`/api/run-dsl`、`/api/generate-dsl`、`/api/create-flow`、`/api/engine/start`、`/api/engine/pause`、`/api/engine/resume`。
- 截图/OCR：`/api/screenshot/capture`、`/api/screenshot/select-region`、`/api/ocr/region`、`/api/wheel-select`。
- 宏：`/api/macro/start-recording`、`/api/macro/stop-recording`、`/api/macro/play`、`/api/macro/list`、`/api/macro/delete`、`/api/macro/get-events`、`/api/macro/recording-status`。
- `@feature`：`/api/features/list`、`/api/features/reload`、`/api/features/call`。
- 模板/标记：`/api/save-template`、`/api/list-templates`、`/api/marks/save`、`/api/marks/load`。
- AI/命令/UI：`/api/ai/generate`、`/api/command`、`/api/set-view-mode`、`/api/set-ui`、`/api/fit-screen`、`/api/get-state`、`/api/execute-js`。
- 旧 Agent IPC：`/api/agent-start`。
- Electron 流程工具 MCP：`POST /mcp`。

这个 `:7880/mcp` 属于流程图、`@feature` 和外部编辑控制面，不是 WinPilot 的 GUI Controller MCP。

## WinPilot REST / WebSocket

实现：`python/winpilot_home/winpilot/server.py`。必须由 `python/winpilot_home/serve.py` 启动；Electron 通常为托管子进程选择动态本地端口。中文原生工作台“智能代理、流程工作台、运行记录、技能与学习、系统设置”都使用这套控制面；`/` 下的旧 webui 只保留为兼容和诊断入口。

### 运行时身份契约

`GET /api/status` 和 `GET /api/v2/platform` 返回 `runtime` 身份。当前契约是 v2：

- `runtime.contract_version == 2`。
- `runtime.source.loaded_stamp/current_stamp/drifted` 表示进程启动时与当前 Python 源码指纹。
- `runtime.git.loaded_commit/current_commit/branch/dirty` 区分进程实际加载的 revision 与工作区当前 revision。
- `runtime.pid/repo_root/winpilot_home/launcher/port` 用于排查旧进程、错误 worktree 和错误端口。

Electron 托管启动和 renderer 都会校验这些字段。契约缺失、源码漂移或 loaded/current commit 不一致时，前端不会继续提交任务；`POST /api/v2/arp/runs` 也会在源码漂移后返回 `409`。代码更新后必须重启运行时，不能靠刷新页面让旧 Python 进程冒充新代码。

### 当前端点

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/ping` | 公开存活检查 |
| POST | `/api/chat` | 提交直接 Agent RuntimeJob，返回 `run_id/job_id/status` |
| POST | `/api/stop` | 请求停止当前任务 |
| POST | `/api/interject` | 向运行中的 Agent 插话 |
| GET | `/api/status` | 当前任务、队列、Desktop queue、Perception daemon、模型与 runtime 身份 |
| GET | `/api/v2/platform` | 原生工作台聚合视图与版本化 contracts |
| GET | `/api/v2/capabilities` | ARP/Controller capability catalog |
| GET | `/api/v2/runs` | 统一运行投影列表 |
| GET | `/api/v2/runs/current` | 当前运行投影 |
| GET | `/api/v2/runs/{run_id}` | 单次运行投影 |
| GET | `/api/v2/jobs` | 统一 Agent/ARP/Replay/Schedule job 列表 |
| GET | `/api/v2/jobs/{job_id}` | 单个 runtime job |
| POST | `/api/v2/jobs/{job_id}/cancel` | 取消任意 runtime job |
| POST | `/api/v2/arp/validate` | 校验 `arp/1` 图 |
| POST | `/api/v2/task-contracts/compile` | 将自然语言编译为可满足性已检查的 `task-contract/1` 与 typed `arp/1` graph |
| POST | `/api/v2/arp/runs` | 启动原生 ARP 图；源码漂移时 fail-closed |
| GET | `/api/v2/arp/runs/{run_id}` | 查询 ARP 执行快照 |
| POST | `/api/v2/arp/runs/{run_id}/cancel` | 取消 ARP run |
| POST | `/api/v2/arp/runs/{run_id}/gate` | 决定等待中的人工门 |
| POST | `/api/v2/automation-ir/validate` | 校验 IR envelope、runtime、safety、graph 和 projection fingerprint |
| POST | `/api/v2/automation-ir/runs` | 运行 `wps_replay` 或 `arp_graph` IR |
| GET | `/api/v2/integrations/chatgpt` | 本地 WinPilot MCP readiness、端点和工具目录 |
| POST | `/api/elevate` | 请求 UAC 提权重启 |
| GET/PATCH | `/api/config` | 读取/更新配置；返回时掩码 API key |
| GET | `/api/traces`、`/api/evals` | 列出本机 trace / GUI eval 摘要 |
| GET | `/api/trace/{run_id}/summary` | trace 延迟、工具和证据摘要 |
| GET | `/api/trace/{run_id}` | 读取 trace |
| GET | `/api/trace/{run_id}/shot/{name}` | 读取 trace 截图 |
| POST | `/api/export/{run_id}` | 导出兼容 `.wps.json` |
| POST | `/api/export/{run_id}/ir` | 导出 `automation-ir/1` |
| GET | `/api/scripts` | 列脚本 |
| GET/PUT | `/api/script/{file}` | 读取/保存 `.wps.json` |
| GET | `/api/script/{file}/ir` | 将 `.wps.json` 包装为 `automation-ir/1` |
| GET | `/api/automation-ir/{file}` | 读取并校验 `.automation.json` |
| POST | `/api/human/start`、`/api/human/stop` | 开始或停止人类演示录制 |
| GET | `/api/human/status` | 人类演示录制状态 |
| GET | `/api/v2/replay/modes` | 查询 replay / apprentice 模式 |

`POST /api/v2/task-contracts/compile` 接受 `task`、`max_steps` 与可选的 `cold_start`。`cold_start=true` 会在合同和图上写入 `experience_policy=disabled`，不生成 `skill_replay` 节点，并禁止该 run 读取或写入阶段 Skill、经验提示和会话记忆；它用于验证首次探索路径，不会改变任务语义或为某个测试对象增加分支。

`GET /api/v2/arp/runs/{run_id}` 的 typed-task 快照包含 `task_contract`、`satisfiability`、`current_stage`、`stage_trace`、`artifacts`、`contract_evidence`、`experience_policy` 与 `skill_replay_node_count`。`stage_trace` 记录阶段状态、能力、模型可见工具、Controller 工具、artifact id、模型调用、Controller 耗时、尝试次数、失败分类和恢复策略；Controller batch 的 transaction trace 还记录 planned/attempted/committed/next-uncommitted、scope recovery 和重新绑定证据。观察帧不会出现在可发送 artifact 集合中。
| POST | `/api/v2/replay/apprentice/start`、`/api/v2/replay/apprentice/stop` | 开始或停止 Apprentice 录制 |
| GET | `/api/v2/replay/apprentice/status` | Apprentice 录制状态 |
| POST | `/api/replay` | 启动 replay job，返回 `replay_id` |
| GET | `/api/replay/{replay_id}` | 查询 replay job 状态、结果和失败细节 |
| POST | `/api/replay/{replay_id}/cancel` | 取消 replay job |
| GET/POST | `/api/agent-schedules` | 列出或创建 WinPilot Agent 计划任务 |
| PATCH/DELETE | `/api/agent-schedules/{schedule_id}` | 更新、启用、重新定时或禁用计划任务 |
| GET/POST/DELETE | `/api/memory` | 兼容 recipe 视图的记忆列表、创建和清空 |
| PUT/DELETE | `/api/memory/{recipe_id}` | 更新或删除单条记忆 |
| POST | `/api/memory/search` | Memory Graph 搜索 |
| GET | `/api/memory/{recipe_id}/lineage`、`/api/memory/{recipe_id}/related` | 谱系和邻接关系 |
| POST | `/api/memory/{recipe_id}/retire`、`/api/memory/{recipe_id}/verify` | 退役或人工验证记忆 |
| GET/DELETE | `/api/session` | 会话记忆 |
| WS | `/ws/events` | BUS 事件流；使用同一控制 token |
| POST | `/mcp` | WinPilot GUI Controller + ARP 的 Streamable HTTP MCP |
| GET | `/` | 兼容 WinPilot webui |

### 运行语义边界

- 原生视觉模型收到的不是固定全屏图。`controller.target_window_capture_enabled=true` 时，只在目标 hwnd 有效、可见、位于前台、几何可信、任务不要求桌面或多窗口上下文且裁剪确有收益时使用目标窗口；其余情况保守回退完整虚拟桌面。每帧都会给出 `scope`、`reason`、物理 `screen_region` 和 0..1000 坐标空间。关闭该配置可强制全桌面做 A/B 对照。
- `controller.multi_input_cooperation=false` 是默认值。开启后只是在人和 Agent 之间仲裁同一套系统光标/键盘焦点：优先后台安全动作，前台或光标所有权变化时中止物理动作。它不提供两个真实鼠标或两套键盘并发。
- `agent_plan` WebSocket 事件里的计划可被模型在运行中完全替换、清空，或原子 add/update/remove/move/reopen。客户端应把新快照当作当前状态，不能把旧索引缓存成固定任务清单。
- 计划完成、Controller 返回 `ok`、`screen_delta.changed=true`、颜色存在或 replay 成功都不是最终任务证据。完成仍由当前目标匹配的新鲜视觉核验或文件/进程等确定性 evidence 决定。
- Agent、ARP、Replay 与计划任务按 `interactive_runtime` 资源排队。`desktop_action_queue` 对真实鼠标键盘保持 `physical` 全局单写；只有明确证明不会移动真实输入的窗口消息动作才可跨 hwnd 并发。

`/api/trace/{run_id}/summary` 的 `low_latency` / `fast_gui` 是性能复盘入口，包含 capture、perception、LLM decision、action、post-verify、VLM、replan、skill reuse、p50/p95 以及 Controller/anchor 指标。没有外部 ground truth 的 `status=done` 不能据此换算成成功率。

### 鉴权

除 `/api/ping` 外，WinPilot `/api/*` 与 `/mcp` 都要求 Electron 托管进程传入的随机 bearer token；也支持 `X-WinPilot-Token`，WebSocket 使用 `?token=`。浏览器 `OPTIONS` 只通过严格 CORS 预检，后续真实请求仍必须鉴权。不要把 token 写入仓库、trace、截图、文档示例或 URL 查询串。

## ChatGPT / Codex 桌面 MCP

WinPilot 的 `POST /mcp` 是 controller-first MCP：读取 runtime/windows/runs/skills，按需观察或捕获视觉帧，执行有作用域的 GUI subgoal/短 batch，并运行或检查 ARP 图。它刻意不暴露任意 shell/file 工具；`run_arp_graph` 也拒绝嵌套 `agent_goal`，因为连接的桌面模型已经是高层规划器。

MCP 的 Controller 动作遵守同一目标窗口、新鲜状态、Desktop queue、多输入仲裁和 evidence 规则。截图入口略有不同：`observe_desktop` 返回结构化目标窗口观察；`capture_visual_fallback` 由高层规划器显式选择完整虚拟桌面或一个完整 ROI，并在 metadata 中报告实际区域，不自动套用 Agent 的目标窗口裁剪器。MCP 工具返回成功只说明该工具调用成立，不自动等于用户任务完成。

本地连接、bearer token、代理绕过和当前外部 HTTPS/OAuth 限制见 [chatgpt-desktop.md](chatgpt-desktop.md)。

## Electron IPC

实现：`python/agent/__main__.py`。名称仍叫 `agent`，但当前作用是 Electron stdin/stdout JSON-line 到 WinPilot BUS 的兼容适配器；它调用 `winpilot.agent.router.build_runner()`，不是另一套 Agent 主循环。

## MCP 实现边界

| MCP | 实现 | 当前职责 |
| --- | --- | --- |
| WinPilot Streamable HTTP MCP | `winpilot/integrations/chatgpt_mcp.py` + WinPilot `/mcp` | ChatGPT/Codex 高层规划器到 GUI Controller、ARP、Run、Skill |
| Electron HTTP MCP | `src/main/api.ts` 的 `:7880/mcp` | 流程图、`@feature`、外部编辑控制 |
| Python MCP | `python/lobster_mcp/server.py` | 从旧 `@feature` 注册表导出工具 |
| TypeScript MCP/HTTP | `src/ai-interface/` | `run_flow/list_flows/create_flow/ask_ai/screen_capture` 等旧流程集成 |

后面三项服务流程图/`@feature` 体系，不是 WinPilot 主 GUI Agent 的工具表；不要用它们的成功结果替代 WinPilot 的新鲜画面 done gate。
