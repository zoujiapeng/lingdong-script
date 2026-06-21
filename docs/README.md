# 灵动脚本文档索引

本目录只描述当前代码实际存在的系统。若其它旧文本与这里冲突，以代码和本目录为准。

## 推荐阅读顺序

1. [../交接说明.md](../交接说明.md)
2. [../CLAUDE.md](../CLAUDE.md)
3. [architecture.md](architecture.md)
4. [winpilot-agent.md](winpilot-agent.md)
5. [apis.md](apis.md)
6. [replay-recording.md](replay-recording.md)
7. [flow-editor.md](flow-editor.md)

## 文档分工

| 文档 | 说明 |
|---|---|
| [architecture.md](architecture.md) | 两套系统边界、进程、端口、数据流 |
| [winpilot-agent.md](winpilot-agent.md) | 当前 WinPilot Agent 的执行循环、工具、感知、验证、记忆 |
| [apis.md](apis.md) | Electron HTTP API、WinPilot REST、IPC、MCP |
| [replay-recording.md](replay-recording.md) | trace、`.wps.json`、Replayer、人类演示录制、流程图导入 |
| [flow-editor.md](flow-editor.md) | React 流程图编辑器、NodeType、DSL、Python engine、@feature |
| [项目导览-面试版.md](项目导览-面试版.md) | 面向非深度开发者的项目讲解 |

## 可信边界

- `python/winpilot_home/winpilot/` 是当前 Agent 主体。
- `python/features/` 是流程图/@feature/MCP 体系，不是 WinPilot 主 agent 的工具来源。
- 根目录和子目录中的 `@AutomationLog.txt`、`lingdong_*.txt`、`canvas_snapshot.txt` 是运行产物或临时输出，不是架构文档。
- `tests/test_init_output.txt` 是历史测试输出快照，不代表当前完整测试状态。
