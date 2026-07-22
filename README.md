# 灵动脚本 / Lingdong Script

灵动脚本是面向 Windows 的视觉优先 GUI Agent。目标不是为少数应用编写自动化脚本，而是让同一个 Agent 在真实客户端里观察当前屏幕、理解未知界面、操作鼠标键盘、检查结果，并在失败后重新观察和调整。

项目仍处于研发阶段。受控视觉靶场和部分真实软件链路已经跑通，但这不等于任意电脑、任意软件或任意任务已经可靠完成。仓库中的 11 个真实任务和组合长任务是黑盒探针，不是产品规则库。

## 设计原则

1. **真实像素是可见状态的最终依据。** 原生视觉模型直接看当前权威帧；UIA、Win32、OCR 和传统 CV 只有在来源、窗口、帧和像素一致时才提供辅助定位，不能替代视觉理解。
2. **模型决定语义，本地运行时只执行通用动作。** 模型始终持有完整用户目标，按当前画面决定下一步；新画面或异常证据可以让模型修改乃至放弃旧策略，本地层不得要求它继续执行预定路径。本地层只负责坐标、输入状态、窗口约束、动作事务和证据采样，不把目标改写成应用脚本。
3. **一个完整目标，一条视觉执行主线。** 未知 GUI 任务进入同一个视觉阶段和固定的通用工具面；每次 mutation 后都重新观察、判断、执行和验证。工具返回 `ok`、像素变化或模型声称完成都不是最终成功。
4. **通用原语优先。** 点击、滚动、输入、拖拽、按住、跨帧继续拖动、连续路径和限区移动属于所有视觉任务共享的能力，不归属于某个应用、任务类型或测试样例。
5. **编译器只表达合同，不替模型理解界面。** Task Contract 可以描述阶段、工件、依赖和完成义务，但不能把未知 GUI 任务编译成大量关键词分支和写死步骤。
6. **记忆只加速，不补硬实力。** 冷启动会在整个 run 中禁用长期 Skill/Memory；有记忆和无记忆必须走同一条执行链。当前会话可以携带有界的原始事实与工件引用，但不靠关键词改写任务，也不可冒充当前屏幕证据。
7. **测试必须与用户入口一致。** GUI 端到端测试从可见 Electron 客户端填写任务并点击运行；直接调用 Python 类或 HTTP API 只能叫单元测试或集成测试。
8. **先修基础工具，不用提示词补规则。** 借鉴 Codex 式责任划分，通用工具自行完成多源发现、执行和证据返回，模型结合画面与结果决定下一步。当前应用索引已经合并进程/窗口、Start Menu、StartApps、App Paths、快捷方式和可执行身份，并在启动后核对进程/窗口因果关系；不能靠应用专属 prompt 猜路径。托盘、登录态和所有发现来源的唯一 owner 仍需继续收敛。

## 产品调用链

```text
Electron「智能代理」
  -> POST /api/v2/task-contracts/compile
  -> POST /api/v2/arp/runs（per-run Legacy 默认 / vNext 实验）
  -> one queue-time DesktopRuntimeAdmission
  -> legacy: StageRuntime -> AgentRunner -> FastGuiController
     或 vNext click-only: immutable RuntimeCapabilitySnapshot
                          -> MissionRuntime -> Visual Actor -> Broker
                          -> shared Controller/RawDevice -> independent Verifier
  -> authoritative frame + physical input transaction + evidence
  -> Run projection / trace / artifact
```

这是 GUI 产品验收的唯一入口。`/api/chat`、MCP、CLI、直接 API runner 和 FlowScript 仍有开发或兼容用途，但不能替代客户端 E2E，也不能发展成另一套产品主循环。

## 核心组成

| 组件 | 职责 | 不应承担 |
| --- | --- | --- |
| `AgentRunner` | 目标理解、视觉决策、计划调整、异常恢复 | 物理坐标驱动、应用专属脚本库 |
| Task Contract / ARP | 阶段、能力、工件、依赖、完成义务 | 根据测试语句写死操作步骤 |
| `StageRuntime` | 保真传递完整目标并承载单一视觉阶段 | 再实现一套 GUI planner 或微步骤脚本 |
| `FrameAuthority` | 原子发布当前帧、窗口、区域、epoch 和坐标身份 | 截图、解释画面或选择动作 |
| `FastGuiController` | 规范化并执行一笔输入事务，采集局部效果证据 | 规划任务或判断开放世界目标已完成 |
| `RunStateMachine` | 记录阶段、因果债务和运行状态 | 生成计划、猜测语义或驱动恢复动作 |
| `FailureRecoveryLayer` | 分类失败并附加事实元数据 | 自动切换 UIA/OCR/MCP 或替模型选择恢复方案 |
| Perception | 权威截图与显式按需的结构化辅助 | 用 UIA/OCR 丰富度决定画面语义 |
| Done gate | 用新鲜证据核对用户目标 | 接受模型总结或工具 `ok` |
| Memory / Skill | 复用经过验证的经验 | 覆盖当前画面或掩盖冷启动失败 |
| MCP | 办公等能力的可选来源 | 取代 GUI Agent 的通用视觉闭环 |

更完整的职责和数据流见 [架构说明](docs/architecture.md)。

## 当前能力和边界

已经具备的通用机制包括：

- 多源应用发现、已运行实例优先、启动后进程/窗口身份核对、前台恢复和全虚拟桌面回退；
- 权威帧身份与窗口裁剪；模型空间工具使用所见当前图片的精确像素坐标，runtime 只映射一次到权威帧和物理桌面；
- 点击、双击、文本输入、快捷键、滚轮、拖拽、连续路径、限时按键，以及等待外部画面变化后取得新权威帧；
- 按下后目标才出现时，跨权威帧保留一个有超时和强制清理的 held-pointer session；
- 动作前新鲜度检查、动作后因果区域验证、trace、artifact 和 completion obligation；
- run 级冷启动、session 引用、可选 MCP、Memory Graph 和已验证 Skill replay；
- 真实 Electron 客户端 E2E harness 与浏览器侧可信指针 oracle。
- WinPilot product package 的模型请求由统一 Runtime 冻结并标识；provider SDK 隐式重试关闭，request retry、poll、redecision 与桌面 action replay 使用不同合同。
- vNext 已有默认关闭、按 run 显式选择的 click-only 实验链：一个 Visual Actor、Broker 小工具面、唯一 Controller/RawDevice、独立视觉 ObligationVerifier 和确定性 MissionVerifier。客户端默认仍为 legacy；2026-07-22 三个随机 seed 的冷启动、空 session、可见 Electron/UIA `precise-click` 已通过独立 trusted-browser oracle，但只证明这条受控点击闭环。
- vNext coordinator context `/1` 分离回合边界 pause wait 与非阻塞 pause reading，明确用 frame max-age（默认 30 秒）拒绝旧帧，并只从 canonical ExecuteDesktopTask `max_steps` 取得回合预算；物理 RawDevice 由产品 composition root 注入。queue-time capability admission 已收敛为唯一 `DesktopRuntimeAdmission`：legacy 保留原 preflight，vNext 从一个不可变 `RuntimeCapabilitySnapshot` 机械准入，同一 snapshot 供 `ExecutionPolicy`、live `RuntimeState` 与 Broker 使用。该收敛没有扩写 prompt、工具面或 Controller；下一门槛仍是多原语 parity 与同入口 E2E。

原生视觉配置默认关闭 UIA、OCR 和 predictive OCR；这些结构化 provider 仍可在来源、当前帧和预期收益明确时显式启用，但动作失败不会自动触发它们。

### 当前验证快照（2026-07-19）

- 本地抖音客户端成功显示 `kimi k3` 搜索结果；这是冷启动、空 session 的真实客户端任务，终态由人工视觉核对，通用 report 没有独立业务 oracle。
- 最近一次 `game-drag-v2` 已通过真实 Electron 客户端入口、冷启动和空 session 进入 Agent，并产生了可信的真实鼠标事件；但模型把弯曲通道误读成直角路径，独立 trusted-browser pointer oracle 判定失败。因此这次只能证明入口、记忆隔离和真实输入链路有效，不能记为任务成功。报告保存在 `output/playwright/client-visual-e2e/20260719-213401-suite/`。
- 网易云任务已在真实客户端达到“播放请求歌曲、Windows 音量 100%”，并由用户确认成功；随后按用户要求停止声音与测试，因此不伪称 harness 已产生 formal terminal pass。
- 2026-07-22 的 vNext click-only canary 从同一可见 Electron 客户端选择器进入，冷启动且 session 为空。seed `987654`（run `arp-20260722-154335-a9e64b`，3 turns）、seed `424242`（run `arp-20260722-154447-9329b4`，2 turns）和 capability-admission 收敛后的 seed `135791`（run `arp-20260722-165702-086da6`，2 turns）均通过独立 trusted-browser pointer oracle；这不是 legacy paired、11 题、真实应用或其他 GUI 原语的通过证据。

单次样本不能换算为成功率；11 个真实任务和混合长任务仍需继续逐项验收。

仍然存在的重要工程债务：

- `AgentRunner`、Task Contract、StageRuntime、Controller 和部分 adapter 之间仍有职责重叠，四个核心 owner 的边界还需继续落实；
- 历史序列化合同仍保留旧 interaction stage 与 subgoal/通信 adapter 兼容代码；新编译器不再生成这条多阶段交互路线，但兼容层仍需继续隔离、压缩或删除；
- 多源应用索引和启动后因果验证已经落地，但运行窗口、托盘、登录态和安装索引仍未完全收敛到唯一 owner；
- UIA/OCR 的旧融合路径仍然较重；失败后应先废弃旧依据、重新观察并让模型判断，而不是自动切到结构化感知或 MCP；
- 真实消费软件、多窗口、托盘、弹窗、动画目标、游戏、高 DPI 和多显示器的覆盖不足；
- 当前整改版本尚未完成 11 个真实任务及其混合长任务的真实客户端全量验收；不能把历史单次通过或受控拖拽描述为当前通用 computer-use 成功率。
- vNext 实验链尚未覆盖 `type_text`、`hotkey`、Actor 可见滚轮、拖拽、绘制、held pointer、ROI observe、应用发现、provider/MCP 或 live Profiler；Planner/Inspector 继续禁用，默认切换和 legacy 删除均未开始。
- `python/winpilot_home/winpilot/` 的模型 request-retry owner 已收敛，Profiler stop_event 链和精确 transport AST guard 已有测试；但外围 `python/providers/` legacy Flow/feature provider 仍有独立 SDK/HTTP 重试，全角色逐 attempt sidecar 尚未闭合，legacy VLM helper 也缺少 stop_event seam。迁移前先审计生产可达性，不能把外围路径保留成第二个 Runtime。

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

`master` 是唯一长期 canonical 产品分支。`codex/vnext-agent-core` 可在迁移期间作为临时集成分支持续存在并吸收 `master` 修复；默认切换完成或方案放弃后，应合并并归档/删除。活跃工作分支应推送到 GitHub 以便协作和恢复，耐久备份使用 tag。多分支不代表多套产品入口：同一次运行仍只能选择一个 GUI Runtime 和一个桌面 mutation owner。

## 文档

- [文档索引](docs/README.md)
- [架构与运行原理](docs/architecture.md)
- [视觉运行时原则](docs/visual-runtime-principles.md)
- [vNext Agent 核心旁路设计](docs/vnext/README.md)（P4/P5 click-only 实验链已接到同一 per-run 客户端入口并通过三个随机 seed 的受控 Electron canary；legacy 默认，P7 完整门槛、多原语、P6、默认切换和 legacy 删除尚未完成）
- [vNext Task Profiler 与执行深度](docs/vnext/execution-profile.md)（P2b 真实 Profiler 已默认关闭地接入 trace-only shadow，不参与 legacy 执行）
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
