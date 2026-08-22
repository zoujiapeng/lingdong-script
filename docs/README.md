# 文档索引

文档按“长期事实优先、实现细节其次、兼容系统最后”组织。具体 run ID、截图和一次性性能数字保存在 trace/eval/report，不继续堆入总览。

## 核心阅读顺序

| 文档 | 内容 |
| --- | --- |
| [../CLAUDE.md](../CLAUDE.md) | 新 Codex/开发者接手入口、当前分支状态、已否定实验和下一步 |
| [../README.md](../README.md) | 项目目标、原则、能力边界、启动和验证 |
| [project-guide/README.md](project-guide/README.md) | 全项目精确讲解：系统架构、逐步运行流、合同、E2E 和逐文件零遗漏索引 |
| [architecture.md](architecture.md) | GUI Agent 架构、不变量、数据流和当前债务 |
| [vnext-migration.md](vnext-migration.md) | 主 Agent 服务端入口、模型自选工具、Legacy 兼容边界和删除退出门 |
| [testing.md](testing.md) | 真实 Electron 客户端 E2E、冷启动、上下文和外部 oracle 规范 |
| [GUI_AGENT_ARCHITECTURE_AUDIT.md](GUI_AGENT_ARCHITECTURE_AUDIT.md) | 全量架构审计、整改顺序和变更准入门 |
| [GUI_AGENT_ARCHITECTURE_RESET_2026-07-29.md](GUI_AGENT_ARCHITECTURE_RESET_2026-07-29.md) | 本轮全分支审计、负优化实验、Pi 边界和当前 E2E 正反证据 |

## 子系统文档

| 文档 | 内容 |
| --- | --- |
| [winpilot-agent.md](winpilot-agent.md) | Legacy AgentRunner、工具、Controller 和 done gate 实现参考 |
| [frontend.md](frontend.md) | 原生 Electron 工作台、vNext 目标入口、运行记录和兼容边界 |
| [apis.md](apis.md) | Electron HTTP、vNext/Legacy REST、WebSocket、IPC 与 MCP |
| [model-providers.md](model-providers.md) | 模型 provider、视觉路由、thinking、cache 和 cost |
| [memory-graph.md](memory-graph.md) | Memory Graph、Skill、session context 和反馈 |
| [replay-recording.md](replay-recording.md) | Trace、Artifact、Automation IR、Replay 与 WPS 兼容 |
| [flow-editor.md](flow-editor.md) | Legacy FlowScript/DSL 编辑兼容面，不是 GUI Agent 主线 |
| [chatgpt-desktop.md](chatgpt-desktop.md) | 已移除的 ChatGPT/Codex 本地 MCP 桥接边界 |
| [项目导览-面试版.md](项目导览-面试版.md) | 面向讲解的简化导览；事实冲突时以前三份核心文档为准 |

## 项目规范

| 文档 | 内容 |
| --- | --- |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | 通用能力准入、代码规模、测试和发布流程 |
| [../SECURITY.md](../SECURITY.md) | 本地控制面、凭据、真实桌面动作和漏洞报告 |
| [../CHANGELOG.md](../CHANGELOG.md) | 发布级稳定结论，不作为架构事实源 |

## 可信边界

- README 或文档写“已实现”，必须能指向当前代码和可复现验证。
- 受控网页靶场通过只证明相应输入/闭环机制，不证明真实软件或跨机器泛化。
- runtime `done`、工具 `ok`、像素变化和模型总结都不是独立完成证据。
- 11 个真实任务可以出现在测试和报告里，不能进入生产规则、prompt、坐标或完成分支。
- UIA/OCR 是按证据使用的辅助信号；对自绘和未知界面，当前权威像素与视觉理解优先。
- MCP 可以完成结构化办公步骤，但不是 GUI Agent 硬实力的替代品。
- FlowScript、WPS、DSL 和浏览器 executor 是兼容系统；新 GUI 能力只在主 runtime 实现一次。

## 维护规则

改动以下内容时同步更新：

- 产品入口/运行链：`README.md`、`architecture.md`、`frontend.md`；
- GUI 感知/动作/done：`architecture.md`、`winpilot-agent.md`、`testing.md`；
- API/IPC/MCP：`apis.md`；
- Memory/Skill：`memory-graph.md`；
- Trace/IR/Replay：`replay-recording.md`；
- 架构债务和整改顺序：`GUI_AGENT_ARCHITECTURE_AUDIT.md`。

不要新建一次性窗口笔记或提交本机绝对路径。跨窗口稳定规则更新
`../CLAUDE.md`；具体 run 留在日期化架构审计、trace、eval 或 report。
