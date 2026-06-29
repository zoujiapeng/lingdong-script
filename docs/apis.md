# API / IPC / MCP

项目有四类接口：Electron HTTP API、WinPilot REST、Electron IPC、MCP。不要混用它们的职责。

## Electron HTTP API

实现：`src/main/api.ts`。默认监听 `127.0.0.1:7880`，可由 `API_PORT` 覆盖。主要服务流程图编辑器、宏、`@feature` 和外部工具控制。

当前端点：

- 基础/状态：`GET/POST /api/ping`、`GET /api/get-state`
- UI：`POST /api/set-view-mode`、`POST /api/set-ui`、`POST /api/fit-screen`
- 脚本/画布：`POST /api/load-script`、`/api/new-script`、`/api/save-script`、`/api/set-script-name`
- 节点/边：`/api/add-node`、`/api/update-node`、`/api/update-node-params`、`/api/delete-nodes`、`/api/add-edge`、`/api/delete-edges`
- 编辑命令：`/api/undo`、`/api/redo`、`/api/select-all`、`/api/clear-selection`、`/api/copy-selected`、`/api/cut-selected`、`/api/paste`、`/api/duplicate-nodes`
- 执行：`/api/run-dsl`、`/api/generate-dsl`、`/api/create-flow`、`/api/engine/start`、`/api/engine/pause`、`/api/engine/resume`
- 截图/OCR：`/api/screenshot/capture`、`/api/screenshot/select-region`、`/api/ocr/region`
- 宏：`/api/macro/start-recording`、`/api/macro/stop-recording`、`/api/macro/play`、`/api/macro/list`、`/api/macro/delete`、`/api/macro/get-events`、`/api/macro/recording-status`
- `@feature`：`/api/features/list`、`/api/features/reload`、`/api/features/call`
- 模板/标记：`/api/save-template`、`/api/list-templates`、`/api/search-templates`、`/api/marks/save`、`/api/marks/load`
- AI/命令：`/api/ai/generate`、`/api/command`、`/api/execute-js`
- 旧 Agent IPC：`/api/agent-start`
- HTTP MCP：`POST /mcp`

## WinPilot REST

实现：`python/winpilot_home/winpilot/server.py`。由 `serve.py` 启动，Electron 通常选择动态本地端口。服务前端 Agent 面板和 WinPilot webui。

当前端点：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/api/chat` | 启动一个 WinPilot 任务 |
| POST | `/api/stop` | 请求停止当前任务 |
| POST | `/api/interject` | 向运行中的任务插话 |
| GET | `/api/status` | 当前任务状态 |
| POST | `/api/elevate` | 权限提升/辅助启动 |
| GET/PATCH | `/api/config` | 读取/更新配置 |
| GET | `/api/traces` | 列 trace |
| GET | `/api/trace/{run_id}` | 读取 trace |
| GET | `/api/trace/{run_id}/shot/{name}` | 读取 trace 截图 |
| POST | `/api/export/{run_id}` | 导出 `.wps.json` |
| GET | `/api/scripts` | 列脚本 |
| GET/PUT | `/api/script/{file}` | 读取/保存 `.wps.json` |
| POST/GET | `/api/human/start`、`/api/human/stop`、`/api/human/status` | 人类演示录制 |
| POST | `/api/replay` | 重放 `.wps.json` |
| GET/POST/DELETE | `/api/memory` | 经验记忆 |
| PUT/DELETE | `/api/memory/{recipe_id}` | 单条经验记忆 |
| GET/DELETE | `/api/session` | 会话记忆 |
| WS | `/ws/events` | BUS 事件流 |
| GET | `/` | WinPilot webui |

## Electron IPC

实现：`python/agent/__main__.py`。名称仍叫 `agent`，但当前作用是 IPC adapter：接收 Electron 启动命令后调用 `winpilot.agent.router.build_runner()`。

## MCP

| MCP | 实现 | 说明 |
| --- | --- | --- |
| Python MCP | `python/mcp/server.py` | 从 `@feature` 注册表导出工具 |
| TypeScript MCP | `src/ai-interface/` | `run_flow/list_flows/create_flow/ask_ai/screen_capture` |
| HTTP MCP | `src/main/api.ts` 的 `/mcp` | 通过 Electron HTTP API 暴露 |

MCP 当前服务流程图/`@feature`/外部集成，不是 WinPilot GUI agent 的工具表。
