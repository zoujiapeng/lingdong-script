# API / IPC / MCP

项目有三类本地接口：Electron `:7880` HTTP、Electron 托管的 WinPilot REST/WebSocket、
Electron IPC。只有可见 Electron“智能代理”提交到 WinPilot
`POST /api/v2/goals/runs` 才是产品自然语言 Agent 入口；其他接口用于 Studio、回放、兼容或
进程控制，不能冒充客户端 E2E。

## Electron HTTP API

实现位于 `src/main/api.ts`，默认监听 `127.0.0.1:7880`，可由 `API_PORT` 覆盖。除
`/api/ping` 外都要求 Electron 生成的 bearer token，并限制本地 origin；
`/api/execute-js` 默认关闭。

这套接口服务流程编辑器和旧本地工具：

- 脚本/画布：load/new/save、节点/边、undo/redo、布局和选择；
- 执行：旧 DSL/engine start/pause/resume；
- 截图/OCR/区域选择；
- 宏、`@feature`、模板和标记；
- `POST /mcp`：流程图、`@feature` 和外部编辑控制面。

它不是 WinPilot GUI Agent 的第二个模型入口。旧 `/api/agent-start` 已删除。

## WinPilot REST / WebSocket

实现位于 `python/winpilot_home/winpilot/server.py`，只能由
`python/winpilot_home/serve.py` 启动。Electron 通常选择动态本地端口并托管该进程；不要直接
运行 `uvicorn winpilot.server:app`。

### 运行时身份

`GET /api/status` 与 `GET /api/v2/platform` 返回 contract v3 runtime identity：

- `contract_version == 3`；
- `source.loaded_stamp/current_stamp/drifted` 标记进程加载源码与当前工作区是否漂移；
- `git.loaded_commit/current_commit/branch/dirty` 区分已加载 revision 和工作区 revision；
- `pid/repo_root/winpilot_home/launcher/port` 用于排查旧进程、错误 worktree 或端口。

Electron 和 Renderer 会阻止源码漂移后的新任务。代码更新后必须重启托管 Runtime；刷新页面
不能让旧 Python 进程变成新代码。

### 当前路由

以下列表与 `server.py` 的当前 decorator 对齐：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/ping` | 无鉴权存活检查 |
| GET | `/api/status` | 当前 run、RuntimeJob、Desktop Action Queue 与 runtime identity |
| GET | `/api/v2/platform` | 原生工作台聚合视图 |
| POST | `/api/v2/goals/runs` | 唯一产品自然语言 goal 入口 |
| GET | `/api/v2/threads`、`/api/v2/threads/{thread_id}` | 有界 Thread/Turn 投影 |
| GET | `/api/v2/runs`、`/api/v2/runs/current`、`/api/v2/runs/{run_id}` | run 列表、当前 run 与详情 |
| POST | `/api/v2/runs/{run_id}/cancel`、`pause`、`resume` | 精确 run 控制 |
| GET | `/api/v2/runs/{run_id}/artifacts/{artifact_id}` | 读取该 run 的受管理工件 |
| GET | `/api/v2/jobs`、`/api/v2/jobs/{job_id}` | RuntimeJob 列表与详情 |
| POST | `/api/v2/jobs/{job_id}/cancel` | 精确取消 RuntimeJob |
| POST | `/api/v2/arp/validate`、`/api/v2/arp/runs` | Studio/兼容显式 ARP 图校验与执行 |
| GET/POST | `/api/v2/arp/runs/{run_id}`、`.../cancel` | ARP 快照与取消 |
| POST | `/api/v2/automation-ir/validate`、`/api/v2/automation-ir/runs` | Automation IR 校验与执行 |
| POST | `/api/v2/flow-fragments/proposals` | 用户主动的一次性 Studio 片段建议 |
| GET | `/api/v2/integrations/chatgpt` | 返回旧 ChatGPT 集成已移除状态 |
| GET/PATCH | `/api/config` | 读取/更新配置；API key 返回时掩码 |
| GET | `/api/traces`、`/api/trace/{run_id}` | trace 列表与有界事件详情 |
| GET | `/api/trace/{run_id}/shot/{name}` | 读取 trace 诊断截图 |
| POST | `/api/export/{run_id}`、`/api/export/{run_id}/ir` | 导出 WPS / Automation IR |
| POST | `/api/export/thread/{thread_id}/ir` | 聚合 Thread 的普通 ARP 动作 |
| GET/PUT | `/api/scripts`、`/api/script/{file}` | 脚本列表、读取与保存 |
| GET | `/api/script/{file}/ir`、`/api/automation-ir/{file}` | IR 转换或读取 |
| POST/GET | `/api/v2/replay/apprentice/start`、`stop`、`status` | 宏录制 |
| POST/GET | `/api/replay`、`/api/replay/{replay_id}`、`.../cancel` | 离线 replay RuntimeJob |
| GET/POST/PUT/DELETE | `/api/memory*` | Studio/Replay 的 recipe、搜索、谱系、验证与退役 |
| WS | `/ws/events` | BUS 事件流；使用同一控制 token |

路由不存在即表示功能已删除；不要按旧文档恢复 `/api/chat`、`/api/stop`、
`/api/interject`、`/api/elevate`、`/api/v2/capabilities`、
`/api/v2/task-contracts/compile`、ARP gate、trace summary、`/api/evals`、`/api/human/*`、
`/api/agent-schedules`、`/api/session`、WinPilot `/mcp`、`/` 或 `/static/*`。

### 运行语义

- `/api/v2/goals/runs` 只接收原始 goal 和可选 `thread_id`。服务端构造唯一 vNext
  产品图、捕获完整真实桌面并建立 live session；失败时不回退 AgentRunner、FlowScript
  或客户端图。
- `/api/v2/arp/runs` 与 Automation IR 是 Studio/兼容入口。`ACTION_BATCH` 当前在 run 内复用
  `FastGuiController` 并直连 canonical action；不再创建 `ToolSession`。
- Desktop Action Queue 对真实鼠标键盘保持全局 `physical` 单写；取消、异常和终态必须清理
  held pointer/key。
- vNext trace 用同一 `decision_id` 配对 `vnext_physical_attempt` 与
  `vnext_raw_receipt`。`trace_integrity` 只说明事件队列/写入是否完整；异步 `after_*` 截图是
  best-effort 诊断帧，不是业务结果证明。
- 任务完成由同一个 `LiveVisualActor` 查看真实新屏幕后决定。测试中的业务成功由外部文件、
  UIA、应用状态或浏览器 oracle 判断；工具 `ok`、Runtime `done` 或截图变化都不能替代它。
- 工件与 Memory/Replay 仍可使用文件摘要检查本地文件是否被改动；这不构成在线 Agent 的
  evidence/hash 链，也不能复活第二 verifier。

### 鉴权

除 `/api/ping` 外，WinPilot `/api/*` 要求 Electron 托管进程传入的随机 bearer token，也支持
`X-WinPilot-Token`；WebSocket 使用 `?token=`。不要把 token 写入仓库、trace、截图、文档示例
或持久化 URL。

## Electron IPC

当前 IPC 直接实现在 `src/main/main.ts` / `src/main/preload.ts`，不再经过已删除的
`python -m agent` 子进程。主要分组：

- 窗口最小化/最大化/关闭；
- 文件打开/保存/读写；
- 托管 WinPilot URL、连接状态、启动与重启；
- 旧 engine、截图、区域选择、OCR、wheel select、DSL、feature、宏；
- 本地模板 scheduler 的 list/create/update/delete 与提醒桥；它不是已删除的 WinPilot Agent
  schedule API，也不能自动提交自然语言 Agent goal；
- 设置、通知与菜单事件。

## MCP 边界

当前仓库只保留 Electron `:7880/mcp` 流程编辑控制面。WinPilot Streamable HTTP MCP、
`winpilot/integrations/chatgpt_mcp.py`、`python/lobster_mcp/` 和 `src/ai-interface/` 已删除。
产品 Actor 的 connector 工具由 `ConnectorGateway` 按显式配置访问外部 MCP；这不是一个本地
WinPilot MCP server，也不能绕过同一 Actor 的选择与 descriptor/参数边界。
