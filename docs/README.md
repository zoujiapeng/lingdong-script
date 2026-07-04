# 文档索引

日常维护优先读短文档，不要从历史 trace、旧分支名或聊天记录推断架构。

| 文档 | 用途 |
| --- | --- |
| [../README.md](../README.md) | 项目总览、启动、测试、目录边界 |
| [../CLAUDE.md](../CLAUDE.md) | AI 接手最短说明 |
| [architecture.md](architecture.md) | 当前架构、状态机、模块边界 |
| [winpilot-agent.md](winpilot-agent.md) | WinPilot agent 运行机制、工具复盘、done gate |
| [model-providers.md](model-providers.md) | 模型 provider、thinking、cache、usage/cost |
| [apis.md](apis.md) | Electron HTTP API、WinPilot REST、IPC、MCP |
| [replay-recording.md](replay-recording.md) | trace、`.wps.json`、replay、导入流程图 |
| [flow-editor.md](flow-editor.md) | 流程图、DSL、`@feature` 体系 |
| [replay-runtime-skill-convergence.md](replay-runtime-skill-convergence.md) | Replay/ARP/FlowScript 收敛方向 |
| [现状评估与改进路线-2026-06.md](现状评估与改进路线-2026-06.md) | 工程债和改进路线，偏战略 |
| [项目导览-面试版.md](项目导览-面试版.md) | 以当前代码为准的面试导览、架构讲法、高频问答和过时说法清单 |

## 可信边界

- 当前 agent 主体以 `python/winpilot_home/winpilot/` 为准。
- 流程图/DSL 体系以 `src/renderer/`、`python/engine/`、`python/features/` 为准。
- Runtime、低延迟 GUI、记忆、上下文折叠以 `CLAUDE.md`、`architecture.md`、`winpilot-agent.md` 为准。
- 本机运行数据以 `config.json`、`traces/`、`evals/`、`experience_memory.json` 为主，不作为架构事实提交。

## 维护规则

- 文档写当前事实，不堆历史 run 列表。
- 需要保留实验证据时，写到 eval 报告或 trace；主文档只保留结论和排障入口。
- 如果代码和文档冲突，先读代码，再更新文档。
