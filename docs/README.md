# 文档索引

面向日常维护时，优先读短文档，不要从历史日志推断架构。

| 文档 | 用途 |
| --- | --- |
| [../README.md](../README.md) | 项目总览 |
| [../CLAUDE.md](../CLAUDE.md) | AI 接手最短说明 |
| [architecture.md](architecture.md) | 当前架构和边界 |
| [winpilot-agent.md](winpilot-agent.md) | WinPilot agent 运行机制 |
| [apis.md](apis.md) | API/IPC/MCP |
| [replay-recording.md](replay-recording.md) | trace、`.wps.json`、replay、导入流程图 |
| [flow-editor.md](flow-editor.md) | 流程图/DSL/`@feature` 体系 |
| [现状评估与改进路线-2026-06.md](现状评估与改进路线-2026-06.md) | 当前工程债和改进路线 |
| [项目导览-面试版.md](项目导览-面试版.md) | 面试/自学长文档，含通俗架构图和低延迟 GUI 复盘 |

## 可信边界

- 当前 agent 主体：`python/winpilot_home/winpilot/`。
- 旧流程图体系：`src/renderer/`、`python/engine/`、`python/features/`。
- 记忆/经验复用/上下文折叠当前事实以 `docs/winpilot-agent.md`、`docs/architecture.md`、`CLAUDE.md` 为准；不要从旧 trace 或旧长文档推断 replay 一定可复用。
- 低延迟 GUI 当前事实以 `CLAUDE.md`、`architecture.md`、`winpilot-agent.md` 为准：已有 batch/subgoal、自动 watcher、recent/action ROI、锚点缓存复验和 summary 指标；没有完成视觉伺服或常驻轻量 UI 模型。
- 本机运行数据：`python/winpilot_home/config.json`、`traces/`、`scripts/`、`experience_memory.json`。
- 临时截图、日志、trace、缓存不作为架构事实。
