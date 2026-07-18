# 灵动脚本 / Lingdong Script

灵动脚本是面向 Windows 的视觉优先 GUI Agent。目标不是为少数应用编写自动化脚本，而是让同一个 Agent 在真实客户端里观察当前屏幕、理解未知界面、操作鼠标键盘、检查结果，并在失败后重新观察和调整。

项目仍处于研发阶段。受控视觉靶场和部分真实软件链路已经跑通，但这不等于任意电脑、任意软件或任意任务已经可靠完成。仓库中的 11 个真实任务和组合长任务是黑盒探针，不是产品规则库。

## 设计原则

1. **真实像素是可见状态的最终依据。** 原生视觉模型直接看当前权威帧；UIA、Win32、OCR 和传统 CV 只有在来源、窗口、帧和像素一致时才提供辅助定位，不能替代视觉理解。
2. **模型决定语义，本地运行时执行动作。** 模型判断当前要做什么；Controller 负责坐标映射、输入状态、鼠标键盘事务、窗口约束和动作后采样。
3. **每一步都重新闭环。** 观察、决策、执行、验证循环推进；工具返回 `ok`、像素发生变化或模型声称完成都不是最终成功。
4. **通用原语优先。** 点击、滚动、输入、拖拽、按住、跨帧继续拖动、连续笔画和限区路径属于所有应用共享的能力，不属于 Paint、DrawArtifact 或某个测试样例的专用通道。
5. **编译器只表达合同，不替模型理解界面。** Task Contract 可以描述阶段、工件、依赖和完成义务，但不能把未知 GUI 任务编译成大量关键词分支和写死步骤。
6. **记忆只加速，不补硬实力。** 冷启动会在整个 run 中禁用 Skill/Memory；有记忆和无记忆必须走同一条执行链。会话上下文只用于解析“刚刚”“继续”等引用，不可冒充当前屏幕证据。
7. **测试必须与用户入口一致。** GUI 端到端测试从可见 Electron 客户端填写任务并点击运行；直接调用 Python 类或 HTTP API 只能叫单元测试或集成测试。

## 产品调用链

```text
Electron「智能代理」
  -> POST /api/v2/task-contracts/compile
  -> POST /api/v2/arp/runs
  -> Task Contract / ARP capability preflight
  -> StageRuntime
  -> bounded AgentRunner + FastGuiController
  -> authoritative frame + physical input transaction
  -> post-action evidence + completion gate
  -> Run projection / trace / artifact
```

这是 GUI 产品验收的唯一入口。`/api/chat`、MCP、CLI、直接 API runner 和 FlowScript 仍有开发或兼容用途，但不能替代客户端 E2E，也不能发展成另一套产品主循环。

## 核心组成

| 组件 | 职责 | 不应承担 |
| --- | --- | --- |
| `AgentRunner` | 目标理解、视觉决策、计划调整、异常恢复 | 物理坐标驱动、应用专属脚本库 |
| Task Contract / ARP | 阶段、能力、工件、依赖、完成义务 | 根据测试语句写死操作步骤 |
| `StageRuntime` | 执行阶段、传递工件、选择能力与证据 | 再实现一套 GUI planner |
| `FastGuiController` | 低延迟动作、窗口/帧约束、局部验证 | 判断开放世界任务已经完成 |
| Perception | 权威截图与按需结构化辅助 | 用 UIA/OCR 丰富度决定画面语义 |
| Done gate | 用新鲜证据核对用户目标 | 接受模型总结或工具 `ok` |
| Memory / Skill | 复用经过验证的经验 | 覆盖当前画面或掩盖冷启动失败 |
| MCP | 办公等能力的可选来源 | 取代 GUI Agent 的通用视觉闭环 |

更完整的职责和数据流见 [架构说明](docs/architecture.md)。

## 当前能力和边界

已经具备的通用机制包括：

- 目标窗口发现、已运行实例优先、前台恢复和全虚拟桌面回退；
- 权威帧身份、窗口裁剪、0..1000 归一化坐标和 DPI 映射；
- 点击、双击、文本输入、快捷键、滚轮、拖拽、连续路径和限时按键；
- 按下后目标才出现时，跨权威帧保留一个有超时和强制清理的 held-pointer session；
- 动作前新鲜度检查、动作后因果区域验证、trace、artifact 和 completion obligation；
- run 级冷启动、session 引用、可选 MCP、Memory Graph 和已验证 Skill replay；
- 真实 Electron 客户端 E2E harness 与浏览器侧可信指针 oracle。

仍然存在的重要工程债务：

- `AgentRunner`、Task Contract、StageRuntime、Controller 和部分 adapter 之间仍有职责重叠；
- compiler、subgoal 和通信 adapter 中仍存在应用/任务族特化，必须继续迁移到通用能力或删除；
- UIA/OCR 的旧融合路径仍然较重，自绘界面和弱可访问性软件容易受到错误结构化信号干扰；
- 真实消费软件、多窗口、托盘、弹窗、动画目标、游戏、高 DPI 和多显示器的覆盖不足；
- 11 个真实任务尚未全部通过，不能把受控拖拽成功描述为通用 computer-use 成功率。

最新代码审计和整改顺序见 [GUI Agent 架构审计](docs/GUI_AGENT_ARCHITECTURE_AUDIT.md)。

## 快速启动

CI 使用 Node.js 20 和 Python 3.11。Windows 首次准备：

```bash
npm ci
python -m pip install -r requirements.txt
npm run dev
```

Electron 主进程控制面默认监听 `127.0.0.1:7880`。WinPilot FastAPI 由 Electron 动态选择端口并托管启动；不要直接运行 `uvicorn winpilot.server:app`。模型配置可从客户端“系统设置”或本机忽略文件 `python/winpilot_home/config.json` 修改。

## 验证

常规回归：

```bash
python -m pytest python/winpilot_home/tests -q
python -m pytest tests -q
npm test
npm run typecheck
npm run build
npm run build:all
```

真实客户端 GUI 验收需要先运行 `npm run dev`，然后另开终端执行：

```bash
npm run e2e:client:visual
npm run e2e:client:original
npm run e2e:client:mixed
```

- `visual` 使用受控目标和外部可信指针事件验证通用点击、拖拽、路径和跨帧按住能力。
- `original` 使用 11 个真实任务作为黑盒探针。
- `mixed` 使用它们的依赖、引用和组合长任务验证持续规划。
- 所有客户端套件默认要求冷启动和准确 run/trace 绑定；没有独立结果证据的 `done` 不得计为通过。

完整测试定义见 [端到端测试规范](docs/testing.md)。

## 仓库边界

| 路径 | 用途 |
| --- | --- |
| `python/winpilot_home/winpilot/` | 当前 GUI Agent、Controller、Tasking、Perception、Memory、Trace 和 API |
| `src/main/` | Electron 生命周期、IPC 和托管 Python 服务 |
| `src/renderer/` | 原生客户端工作台 |
| `scripts/` | 受版本控制的评测、真实客户端 E2E 和报告工具 |
| `python/engine/`, `python/features/` | legacy FlowScript/DSL 兼容路径，不是 GUI Agent 工具池 |
| `python/winpilot_home/traces/`, `output/` | 本机运行产物，不提交 |

`master` 是唯一长期分支。临时分支只能用于短期审查，合并后必须删除；不保留第二条产品主线。

## 文档

- [文档索引](docs/README.md)
- [架构与运行原理](docs/architecture.md)
- [端到端测试规范](docs/testing.md)
- [WinPilot Agent 细节](docs/winpilot-agent.md)
- [原生前端](docs/frontend.md)
- [API / IPC / MCP](docs/apis.md)
- [模型与视觉路由](docs/model-providers.md)
- [Memory Graph](docs/memory-graph.md)
- [Trace / Replay / Automation IR](docs/replay-recording.md)
- [GUI Agent 架构审计](docs/GUI_AGENT_ARCHITECTURE_AUDIT.md)
- [贡献指南](CONTRIBUTING.md)
- [安全政策](SECURITY.md)

## 维护准则

- 先删除、归并或复用，再新增模块和状态账本。
- 不为一个应用、一个对象或一个测试句子增加生产关键词、坐标、prompt 或完成条件。
- 新能力必须说明唯一 owner、通用输入、通用输出、失败语义和至少两个不同任务族的验证。
- GUI 改动必须同时提供单元/集成证据和真实客户端证据；不能用直接 API 成功冒充用户体验成功。
- 文档只保留当前机制、边界和可复现命令；具体 run ID、截图和临时测试数字留在报告、trace 或 changelog。
