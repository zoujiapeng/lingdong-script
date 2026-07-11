# API / IPC / MCP

项目有四类接口：Electron HTTP API、WinPilot REST、Electron IPC、MCP。不要混用它们的职责。

## Electron HTTP API

实现：`src/main/api.ts`。默认监听 `127.0.0.1:7880`，可由 `API_PORT` 覆盖。除 `/api/ping` 外要求 Electron 进程生成的 bearer token，并严格限制本地 origin。`/api/execute-js` 默认禁用，只能用显式开发环境变量开启。

当前端点：

- 基础/状态：`GET/POST /api/ping`、`GET /api/get-state`
- UI：`POST /api/set-view-mode`、`POST /api/set-ui`、`POST /api/fit-screen`
- 脚本/画布：`POST /api/load-script`、`/api/new-script`、`/api/save-script`、`/api/set-script-name`、`/api/check-params`、`/api/auto-layout`
- 节点/边：`/api/add-node`、`/api/update-node`、`/api/update-node-params`、`/api/delete-nodes`、`/api/add-edge`、`/api/delete-edges`
- 编辑命令：`/api/undo`、`/api/redo`、`/api/select-all`、`/api/clear-selection`、`/api/copy-selected`、`/api/cut-selected`、`/api/paste`、`/api/duplicate-nodes`
- 执行：`/api/run-dsl`、`/api/generate-dsl`、`/api/create-flow`、`/api/engine/start`、`/api/engine/pause`、`/api/engine/resume`
- 截图/OCR：`/api/screenshot/capture`、`/api/screenshot/select-region`、`/api/ocr/region`、`/api/wheel-select`
- 宏：`/api/macro/start-recording`、`/api/macro/stop-recording`、`/api/macro/play`、`/api/macro/list`、`/api/macro/delete`、`/api/macro/get-events`、`/api/macro/recording-status`
- `@feature`：`/api/features/list`、`/api/features/reload`、`/api/features/call`
- 模板/标记：`/api/save-template`、`/api/list-templates`、`/api/marks/save`、`/api/marks/load`
- AI/命令：`/api/ai/generate`、`/api/command`、`/api/execute-js`
- 旧 Agent IPC：`/api/agent-start`
- HTTP MCP：`POST /mcp`

## WinPilot REST

实现：`python/winpilot_home/winpilot/server.py`。由 `serve.py` 启动，Electron 通常选择动态本地端口。原生 React 工作台通过该控制面运行 Agent/ARP/Replay；`/` 下的旧 webui 只保留为兼容和诊断入口。

当前端点：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/api/chat` | 提交 Agent RuntimeJob，返回 `run_id/job_id/status` |
| POST | `/api/stop` | 请求停止当前任务 |
| POST | `/api/interject` | 向运行中的任务插话 |
| GET | `/api/status` | 当前任务、job queue、Desktop queue、Perception daemon 状态 |
| POST | `/api/elevate` | 权限提升/辅助启动 |
| GET/PATCH | `/api/config` | 读取/更新配置 |
| GET | `/api/traces` | 列 trace |
| GET | `/api/evals` | 列本机 GUI eval 报告摘要 |
| GET | `/api/trace/{run_id}/summary` | trace 低延迟/工具/证据摘要 |
| GET | `/api/trace/{run_id}` | 读取 trace |
| GET | `/api/trace/{run_id}/shot/{name}` | 读取 trace 截图 |
| POST | `/api/export/{run_id}` | 导出 `.wps.json` |
| POST | `/api/export/{run_id}/ir` | 导出 `automation-ir/1` |
| GET | `/api/scripts` | 列脚本 |
| GET/PUT | `/api/script/{file}` | 读取/保存 `.wps.json` |
| GET | `/api/script/{file}/ir` | 将已有 `.wps.json` 包装为 `automation-ir/1` |
| GET | `/api/automation-ir/{file}` | 读取并校验 `.automation.json` |
| POST/GET | `/api/human/start`、`/api/human/stop`、`/api/human/status` | 人类演示录制 |
| POST | `/api/replay` | 启动 `.wps.json` replay job，返回 `replay_id` |
| GET | `/api/replay/{replay_id}` | 查询 replay job 状态、结果、失败细节 |
| POST | `/api/replay/{replay_id}/cancel` | 取消 replay job |
| GET | `/api/v2/jobs` | 查询统一 Agent/ARP/Replay/Schedule jobs |
| GET/POST | `/api/v2/jobs/{job_id}`、`/cancel` | 查询或取消任意 runtime job |
| GET/POST/DELETE | `/api/memory` | 经验记忆 |
| PUT/DELETE | `/api/memory/{recipe_id}` | 单条经验记忆 |
| GET/DELETE | `/api/session` | 会话记忆 |
| GET/POST | `/api/agent-schedules` | 列出或创建 WinPilot Agent 计划任务 |
| PATCH | `/api/agent-schedules/{schedule_id}` | 更新、启用或重新定时计划任务 |
| DELETE | `/api/agent-schedules/{schedule_id}` | 禁用计划任务 |
| WS | `/ws/events` | BUS 事件流 |
| GET | `/` | WinPilot webui |

`/api/trace/{run_id}/summary` 里的 `low_latency` / `fast_gui` 字段是当前性能复盘入口：包含截图、感知、LLM 决策、动作执行、动作后验证、VLM/重规划/skill 复用频率、p50/p95 latency，以及 batch subgoal、controller phase、锚点点击/缓存/失败统计。

WinPilot API 除 `/api/ping` 外要求 Electron 托管进程传入的 bearer token；WebSocket 使用同一 token query。浏览器 `OPTIONS` 不携带最终 Authorization，因此先交给严格 origin/method/header 的 CORS 预检，实际 API 请求仍必须鉴权。Agent、ARP、Replay 与计划任务按 `interactive_runtime` 资源排队，不再仅因另一个交互 job 活跃就返回 409。`desktop_action_queue` 仍串行真实鼠标键盘写入。

`/api/memory` 的 recipe/note 结构包含 `task`、`app`、`steps`，并可带 `conditions` 和 `evidence` 数组；`PUT /api/memory/{recipe_id}` 也允许更新这两个字段。它们用于说明该记忆何时适用、复用前后需要什么证据，不能把 replay 执行成功当成任务完成证明。

## ChatGPT / Codex desktop MCP

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v2/integrations/chatgpt` | MCP readiness, local endpoint, tool catalog, and no-key mode |
| POST | `/mcp` | Official MCP Streamable HTTP endpoint for the logged-in desktop client |

The `/mcp` catalog is controller-first and intentionally excludes shell/file
tools. ChatGPT-hosted ARP calls reject `agent_goal`; the connected desktop model
is already the high-level planner.

## Electron IPC

实现：`python/agent/__main__.py`。名称仍叫 `agent`，但当前作用是 IPC adapter：接收 Electron 启动命令后调用 `winpilot.agent.router.build_runner()`。

## MCP

| MCP | 实现 | 说明 |
| --- | --- | --- |
| Python MCP | `python/lobster_mcp/server.py` | 从 `@feature` 注册表导出工具 |
| TypeScript MCP | `src/ai-interface/` | `run_flow/list_flows/create_flow/ask_ai/screen_capture` |
| HTTP MCP | `src/main/api.ts` 的 `/mcp` | 通过 Electron HTTP API 暴露 |

MCP 当前服务流程图/`@feature`/外部集成，不是 WinPilot GUI agent 的工具表。

## Memory Graph APIs

Legacy memory endpoints remain compatible:

- `GET /api/memory` returns recipe-like `id/task/app/steps/uses/successes/provisional/manual/script/conditions/evidence` items.
- `POST /api/memory`, `PUT /api/memory/{id}`, `DELETE /api/memory/{id}`, and `DELETE /api/memory` keep the old UI contract.

Graph-native endpoints:

- `POST /api/memory/search` returns compact graph search results with scores and reasons.
- `GET /api/memory/{id}/lineage` returns the node and related graph edges.
- `GET /api/memory/{id}/related` returns neighboring nodes/edges.
- `POST /api/memory/{id}/retire` marks a memory retired.
- `POST /api/memory/{id}/verify` records manual verification evidence and promotes confidence.

Secrets such as API keys, config values, and environment variables should never be written into memory nodes.
