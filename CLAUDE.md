# 灵动脚本工程速览

本文件给接手项目的 AI/开发者使用。所有描述按当前代码同步；如果历史日志或临时文件与这里冲突，以代码和 `docs/` 为准。

## 当前架构

灵动脚本是 Windows 桌面自动化项目，当前由两套系统组成：

1. **WinPilot Agent**：`python/winpilot_home/winpilot/`
   - 实时操控电脑。
   - 使用 OpenAI 兼容 function-calling 模型，默认 DeepSeek 配置。
   - 感知融合 UIA、Win32、OCR、CV、可选 VLM。
   - 动作带 expect 验证，done 有二次核验，重点防假成功。
   - 编程/仓库任务由独立 `CodingRunner` 处理。

2. **流程图编辑器/执行引擎**：`src/renderer/` + `python/engine/` + `python/features/`
   - React 画布编辑 FlowScript。
   - Python engine 执行 DSL。
   - @feature 注册表服务流程图、MCP、HTTP、CLI。
   - 不作为 WinPilot 主 agent 的工具来源。

WinPilot 跑出的 trace 可导出 `.wps.json`，由 `src/renderer/utils/wpsToFlowScript.ts` 纯代码转换成 FlowScript 进入画布。

## 硬边界

- 不要把 `python/features/` 的 @feature 塞进 WinPilot 主 agent。
- 不要让主 agent 感知或编辑流程图内部格式。
- 新专长优先参考 `CodingRunner` 做独立子 agent。
- GUI-only 测试以前端路径和真实屏幕为准，不能只看日志成功。
- `config.json`、`traces/`、`scripts/`、`experience_memory.json` 是运行数据，不提交。

## 主要目录

| 路径 | 说明 |
|---|---|
| `src/main/main.ts` | Electron 主进程、窗口和子进程管理 |
| `src/main/api.ts` | `127.0.0.1:7880` HTTP API 和 HTTP MCP |
| `src/main/winpilotServer.ts` | 启动 WinPilot FastAPI |
| `src/renderer/` | React 流程图编辑器、Agent iframe、导入录制 UI |
| `python/winpilot_home/winpilot/server.py` | WinPilot REST + WebSocket |
| `python/winpilot_home/winpilot/config.py` | WinPilot 配置默认值和持久化 |
| `python/winpilot_home/winpilot/agent/loop.py` | GUI `AgentRunner` |
| `python/winpilot_home/winpilot/agent/tools.py` | GUI agent 工具 schema 和 dispatcher |
| `python/winpilot_home/winpilot/agent/coding.py` | 编程子 agent |
| `python/winpilot_home/winpilot/agent/coding_tools.py` | 编程子 agent 工具 |
| `python/winpilot_home/winpilot/agent/router.py` | 任务路由 |
| `python/winpilot_home/winpilot/perception/` | UIA/Win32/OCR/CV/VLM 融合 |
| `python/winpilot_home/winpilot/action/` | 动作执行、shell、安全、expect 验证 |
| `python/winpilot_home/winpilot/recorder/` | trace、`.wps.json`、Replayer、人类演示录制 |
| `python/winpilot_home/winpilot/memory/` | 经验和事实笔记 |
| `python/agent/__main__.py` | WinPilot IPC 适配器 |
| `python/agent/dialog_handler.py` | 旧 engine/@feature 仍使用的 Windows 对话框 helper |
| `python/agent/menu_handler.py` | 旧 engine/@feature 仍使用的菜单 helper |
| `python/engine/` | FlowScript/DSL 执行引擎 |
| `python/features/` | @feature 注册表 |
| `python/mcp/server.py` | Python MCP server |
| `python/node-red-nodes/` | Node-RED 实验/PoC |

## WinPilot REST

当前端点来自 `python/winpilot_home/winpilot/server.py`：

- `POST /api/chat`
- `POST /api/stop`
- `POST /api/interject`
- `GET /api/status`
- `POST /api/elevate`
- `GET /api/config`
- `PATCH /api/config`
- `GET /api/traces`
- `GET /api/trace/{run_id}`
- `GET /api/trace/{run_id}/shot/{name}`
- `POST /api/export/{run_id}`
- `GET /api/scripts`
- `GET /api/script/{file}`
- `PUT /api/script/{file}`
- `POST /api/human/start`
- `POST /api/human/stop`
- `GET /api/human/status`
- `POST /api/replay`
- `GET/POST/DELETE /api/memory`
- `PUT/DELETE /api/memory/{recipe_id}`
- `GET/DELETE /api/session`
- `WS /ws/events`

## WinPilot 工具事实

GUI agent 工具主要包括：

`list_windows`、`scan_windows`、`observe`、`focus_window`、`find_app`、`find_program`、`tray_click`、`launch`、`click`、`type_text`、`hotkey`、`scroll`、`drag`、`wait`、`read_text`、`find_image`、`capture_template`、`color_probe`、`undo_last`、`search_files`、`done`、`fail`。

配置/任务可能额外启用：

`run_shell`、`run_detached`、`revise_plan`、`complete_subgoal`、`remember`、`forget`、`reuse_skill`、`vlm_describe`。

编程子 agent 工具：

`list_dir`、`read_file`、`write_file`、`apply_patch`、`search_text`、`github_search`、`run_shell`、`done`、`fail`。

## 配置事实

默认值在 `python/winpilot_home/winpilot/config.py`，不是 README 或旧样例。

重点默认项：

- `api.model`: `deepseek-v4-flash`
- `server.host`: `127.0.0.1`
- `server.port`: `8765`
- `agent.max_steps`: `40`
- `agent.llm_trace`: `true`
- `agent.verify_done`: `true`
- `agent.prompt_style`: `full`
- `coding.enabled`: `true`
- 感知默认开启 UIA、Win32、OCR、CV，VLM 默认关闭。

`python/winpilot_home/config.example.json` 是示例文件，字段可能少于 `config.py`；修改默认行为时同步它。

## 测试

优先验证：

```bash
python -m pytest python/winpilot_home/tests -q
npx vitest run
npm run build
```

根目录 `tests/` 是旧流程图/feature 和历史实验测试混合目录。里面仍可能有引用已删除旧 Agent 的实验测试，不能简单当成 WinPilot 当前状态。

## 文档维护

架构变化时同步：

- `README.md`
- `CLAUDE.md`
- `交接说明.md`
- `docs/*.md`
- `CHANGELOG.md`
- `HANDOFF_CODEX.md`，如果它仍作为当前交接文件使用

运行日志、临时 txt、历史输出不要当作架构事实。
