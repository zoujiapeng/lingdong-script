# 文档索引

文档按“长期事实优先、实现细节其次、兼容系统最后”组织。具体 run ID、截图和一次性性能数字保存在 trace/eval/report，不继续堆入总览。

## 核心阅读顺序

| 文档 | 内容 |
| --- | --- |
| [../README.md](../README.md) | 项目目标、原则、能力边界、启动和验证 |
| [architecture.md](architecture.md) | GUI Agent 架构、不变量、数据流和当前债务 |
| [testing.md](testing.md) | 真实 Electron 客户端 E2E、冷启动、上下文和外部 oracle 规范 |
| [GUI_AGENT_ARCHITECTURE_AUDIT.md](GUI_AGENT_ARCHITECTURE_AUDIT.md) | 全量架构审计、整改顺序和变更准入门 |
| [../CLAUDE.md](../CLAUDE.md) | AI/开发者接手最短说明 |

## vNext 旁路设计

| 文档 | 内容 |
| --- | --- |
| [vnext/README.md](vnext/README.md) | vNext 文档入口、状态、不可妥协边界和工作顺序 |
| [vnext/architecture.md](vnext/architecture.md) | 单一 Visual Actor、Mission Ledger、Capability Broker 与受限认知角色 |
| [vnext/contracts.md](vnext/contracts.md) | MissionState、Obligation、Actor/Verifier/Planner/Inspector typed contracts |
| [vnext/migration-plan.md](vnext/migration-plan.md) | 旁路阶段、同入口切换、legacy 职责删除门槛 |
| [vnext/evaluation.md](vnext/evaluation.md) | P0 指标、A/B、真实客户端 E2E 与 held-out 发布标准 |
| [vnext/failure-triage.md](vnext/failure-triage.md) | 工具和骨架优先的失败归因顺序 |
| [vnext/decisions.md](vnext/decisions.md) | 已接受与延后的架构决策 |

这些文档描述目标设计，不代表代码已经实现。当前产品事实仍以前一节核心文档为准。vNext 必须共用 Electron 用户入口和现有窄基础，不能形成第二个长期产品主线。

## 子系统文档

| 文档 | 内容 |
| --- | --- |
| [winpilot-agent.md](winpilot-agent.md) | AgentRunner、工具、Controller、视觉闭环和 done gate |
| [frontend.md](frontend.md) | 原生 Electron 工作台、运行记录、刷新和兼容边界 |
| [apis.md](apis.md) | Electron HTTP、WinPilot REST/WebSocket、IPC 与 MCP |
| [model-providers.md](model-providers.md) | 模型 provider、视觉路由、thinking、cache 和 cost |
| [memory-graph.md](memory-graph.md) | Memory Graph、Skill、session context 和反馈 |
| [replay-recording.md](replay-recording.md) | Trace、Artifact、Automation IR、Replay 与 WPS 兼容 |
| [flow-editor.md](flow-editor.md) | Legacy FlowScript/DSL 编辑兼容面，不是 GUI Agent 主线 |
| [chatgpt-desktop.md](chatgpt-desktop.md) | ChatGPT/Codex 本地 MCP 桥接和安全边界 |
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
- vNext 角色、状态合同、迁移阶段或实验门槛：同步 `vnext/` 全套文档，并明确哪些仍未实现。

不要把临时交接文档、绝对路径、旧工作树行数或单次 run 细节提交成长期文档。
