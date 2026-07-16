# 文档索引

日常维护优先读短文档，不要从历史 trace、旧分支名或聊天记录推断架构。

| 文档 | 用途 |
| --- | --- |
| [../README.md](../README.md) | 项目总览、安装启动、测试、成熟度和目录边界 |
| [../CLAUDE.md](../CLAUDE.md) | AI / 开发者接手最短说明 |
| [architecture.md](architecture.md) | 当前架构、状态机、模块边界 |
| [frontend.md](frontend.md) | 原生前端五入口、运行详情/历史恢复、刷新策略、节点可达性与兼容/死代码边界 |
| [winpilot-agent.md](winpilot-agent.md) | WinPilot agent 运行机制、工具复盘、done gate |
| [model-providers.md](model-providers.md) | 模型 provider、原生视觉路由、thinking、cache、usage/cost |
| [apis.md](apis.md) | Electron HTTP API、WinPilot REST、runtime contract、IPC、MCP |
| [replay-recording.md](replay-recording.md) | trace、Automation IR、兼容 WPS、replay、导入流程图 |
| [flow-editor.md](flow-editor.md) | 流程图、DSL、`@feature` 体系 |
| [memory-graph.md](memory-graph.md) | Memory Graph、召回、反馈和迁移边界 |
| [chatgpt-desktop.md](chatgpt-desktop.md) | ChatGPT / Codex 桌面 MCP 桥接、安全和截图范围 |
| [项目导览-面试版.md](项目导览-面试版.md) | 以当前代码为准的面试导览、架构讲法、高频问答和过时说法清单 |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | 贡献流程、验证矩阵和文档同步要求 |
| [../SECURITY.md](../SECURITY.md) | 凭据、本地控制面、真实桌面动作和漏洞报告边界 |

## 可信边界

- 当前 agent 主体以 `python/winpilot_home/winpilot/` 为准。
- 流程图/DSL 体系以 `src/renderer/`、`python/engine/`、`python/features/` 为准。
- Runtime、低延迟 GUI、记忆、上下文折叠以 `CLAUDE.md`、`architecture.md`、`winpilot-agent.md` 为准。
- 原生工作台当前可见入口是“智能代理、流程工作台、运行记录、技能与学习、系统设置”；API、MCP、VLM、URL、模型名和坐标字段等技术标识保留原文。
- 运行列表和 RuntimeJob 列表只承载导航摘要；完整 timeline/evidence/result 与 typed ARP audit 按 run/job id 加载。历史 ARP 阶段、证据和 ArtifactStore manifest 可在重启后恢复，缺失的旧合同原文不得靠当前 parser 补猜。
- 运行中的 Python 服务必须满足 runtime contract v2，且启动时源码指纹、当前源码指纹和 Git revision 一致；前端发现漂移会禁止提交新任务并要求重启，旧进程的状态不能冒充当前代码事实。
- 原生视觉帧可能是经过验证的前台目标窗口裁剪，也可能是完整虚拟桌面。`controller.target_window_capture_enabled` 只是可关闭的性能优化；跨窗口、全局、目标含糊或几何不可信时必须回退全桌面。
- 模型可随时通过 `revise_plan` 替换、清空、增删、移动或重新打开任务项；计划是可变工作状态，不是完成证据。
- `controller.multi_input_cooperation` 是默认关闭的人机输入仲裁实验。Windows 仍只有一套系统光标和键盘焦点，不能把它描述成两套物理输入并发。
- 本机运行数据包括 `python/winpilot_home/config.json`、`python/winpilot_home/traces/`、`python/winpilot_home/evals/`、`python/winpilot_home/scripts/`、`python/winpilot_home/memory.db`、`python/winpilot_home/experience_memory.json` 和 `python/winpilot_home/agent_schedules.json`，不作为架构事实提交；根目录 `scripts/` 是受版本控制的开发/评测工具。
- 低延迟 GUI、RuntimeJob、Replay/IR、前端成熟度和真实软件能力以 README 的“成熟度快照”和 `architecture.md` 的边界描述为准；不要把路线图目标当成已完成事实。

## 维护规则

- 文档写当前事实，不堆历史 run 列表。
- 需要保留实验证据时，写到 eval 报告或 trace；主文档只保留结论和排障入口。
- 如果代码和文档冲突，先读代码，再更新文档。
- 单次成功运行只能证明该次链路，不能换算成成功率；性能或模型对比必须注明日期、入口、模型、显示/DPI、配置和外部 ground truth。
