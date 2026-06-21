# 接口与协议

项目有四类接口：Electron HTTP API、WinPilot REST、Electron IPC、MCP。

## Electron HTTP API

实现：`src/main/api.ts`。默认监听 `127.0.0.1:7880`，可由 `API_PORT` 覆盖。

主要端点按当前代码：

| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/ping` | GET/POST | 健康检查 |
| `/api/get-state` | GET/POST | 读取渲染器状态 |
| `/api/set-view-mode` | GET/POST | 切换视图 |
| `/api/fit-screen` | GET/POST | 画布自适应 |
| `/api/execute-js` | GET/POST | 在前端执行 JS |
| `/api/load-script` | POST | 加载 FlowScript |
| `/api/add-node` | POST | 添加节点 |
| `/api/update-node` | POST | 更新节点 |
| `/api/update-node-params` | POST | 更新节点参数 |
| `/api/delete-nodes` | POST | 删除节点 |
| `/api/add-edge` | POST | 添加连线 |
| `/api/delete-edges` | POST | 删除连线 |
| `/api/check-params` | POST | 检查缺失参数 |
| `/api/auto-layout` | POST | 自动布局 |
| `/api/run-dsl` | POST | DSL 转流程图 |
| `/api/generate-dsl` | POST | 自然语言生成 DSL |
| `/api/create-flow` | POST | DSL/自然语言创建流程 |
| `/api/engine/start` | POST | 启动流程图执行 |
| `/api/engine/pause` | POST | 暂停执行 |
| `/api/engine/resume` | POST | 恢复执行 |
| `/api/screenshot/capture` | POST | 截屏 |
| `/api/screenshot/select-region` | POST | 选择区域 |
| `/api/ocr/region` | POST | OCR 指定区域 |
| `/api/wheel-select` | POST | 滚轮选择器 |
| `/api/macro/start-recording` | POST | 普通宏录制开始 |
| `/api/macro/stop-recording` | POST | 普通宏录制停止 |
| `/api/macro/play` | POST | 播放宏 |
| `/api/macro/list` | POST | 列宏 |
| `/api/macro/delete` | POST | 删除宏 |
| `/api/macro/get-events` | POST | 读取宏事件 |
| `/api/macro/recording-status` | POST | 宏录制状态 |
| `/api/features/list` | POST | 列 @feature |
| `/api/features/reload` | POST | 重新加载 @feature |
| `/api/features/call` | POST | 调用 @feature |
| `/api/ai/generate` | POST | 前端/旧体系 LLM 调用 |
| `/api/set-ui` | POST | 更新 UI 状态 |
| `/api/command` | POST | 批量前端命令 |
| `/api/new-script` | POST | 新建脚本 |
| `/api/save-script` | POST | 保存脚本 |
| `/api/set-script-name` | POST | 设置脚本名 |
| `/api/save-template` | POST | 保存模板 |
| `/api/list-templates` | POST | 列模板 |
| `/api/search-templates` | POST | 搜模板 |
| `/api/marks/save` | POST | 保存标记 |
| `/api/marks/load` | POST | 读取标记 |
| `/api/undo`、`/api/redo` | POST | 撤销/重做 |
| `/api/select-all`、`/api/clear-selection`、`/api/copy-selected`、`/api/cut-selected`、`/api/paste`、`/api/duplicate-nodes` | POST | 画布编辑命令 |
| `/api/agent-start` | POST | 通过旧 IPC 路径启动 Agent |
| `/mcp` | POST | HTTP MCP JSON-RPC |

这组端点主要用于控制 React 流程图编辑器，不等同于 WinPilot REST。

## WinPilot REST

实现：`python/winpilot_home/winpilot/server.py`。Electron 会以本地动态端口启动它，前端 Agent 面板通过 iframe 和 REST 使用。

| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/chat` | POST | 启动 WinPilot 任务 |
| `/api/stop` | POST | 停止当前任务 |
| `/api/interject` | POST | 向运行中的任务插话/纠正 |
| `/api/status` | GET | 当前运行状态 |
| `/api/elevate` | POST | 以管理员权限重启/提升 |
| `/api/config` | GET/PATCH | 读取/更新配置 |
| `/api/traces` | GET | 列 trace |
| `/api/trace/{run_id}` | GET | 读取 trace JSONL |
| `/api/trace/{run_id}/shot/{name}` | GET | 读取 trace 截图 |
| `/api/export/{run_id}` | POST | trace 导出 `.wps.json` |
| `/api/scripts` | GET | 列 `.wps.json` 脚本 |
| `/api/script/{file}` | GET/PUT | 读写脚本 |
| `/api/human/start` | POST | 开始人类演示录制 |
| `/api/human/stop` | POST | 停止人类演示录制 |
| `/api/human/status` | GET | 人类演示录制状态 |
| `/api/replay` | POST | 离线 replay `.wps.json` |
| `/api/memory` | GET/POST/DELETE | 列/增/清记忆 |
| `/api/memory/{recipe_id}` | PUT/DELETE | 更新/删除单条记忆 |
| `/api/session` | GET/DELETE | 列/清会话上下文 |
| `/ws/events` | WebSocket | WinPilot BUS 事件流 |
| `/` | GET | webui 首页 |

## Agent IPC

实现：`python/agent/__main__.py`。

stdin 命令：

- `start`
- `stop`
- `guidance`
- `chat`
- `status`

stdout 事件来自 WinPilot BUS，常见包括：

- `agent_start`
- `agent_step`
- `agent_tool`
- `agent_usage`
- `agent_plan`
- `chat`
- `done_check`
- `agent_recovery`
- `playbook`
- `world`
- `agent_error`
- `agent_done`

## MCP

当前有三种 MCP 入口：

| 入口 | 代码 | 说明 |
|---|---|---|
| Python MCP | `python/mcp/server.py` | 从 @feature 注册表导出工具 |
| TypeScript MCP | `src/ai-interface/`，脚本 `npm run ai:mcp` | `run_flow/list_flows/create_flow/ask_ai/screen_capture` |
| HTTP MCP | `src/main/api.ts` 的 `/mcp` | 通过 Electron HTTP API 暴露 |

MCP 服务的是流程图/@feature/外部集成，不是 WinPilot 主 GUI agent 的工具表。
