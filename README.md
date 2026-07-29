# 灵动脚本 / Lingdong Script

灵动脚本是面向 Windows 的视觉优先 GUI Agent。目标不是为少数应用编写自动化脚本，而是让同一个 Agent 在真实客户端里观察当前屏幕、理解未知界面、操作鼠标键盘、检查结果，并在失败后重新观察和调整。

项目仍处于研发阶段。受控视觉靶场和部分真实软件链路已经跑通，但这不等于任意电脑、任意软件或任意任务已经可靠完成。仓库中的 11 个真实任务和组合长任务是黑盒探针，不是产品规则库。

## 设计原则

1. **真实像素是可见状态的最终依据。** 原生视觉模型直接看当前权威帧；UIA、Win32、OCR 和传统 CV 只有在来源、窗口、帧和像素一致时才提供辅助定位，不能替代视觉理解。
2. **模型决定语义，本地运行时只执行通用动作。** 模型始终持有完整用户目标，按当前画面决定下一步；新画面或异常证据可以让模型修改乃至放弃旧策略，本地层不得要求它继续执行预定路径。本地层只负责坐标、输入状态、窗口约束、动作事务和证据采样，不把目标改写成应用脚本。
3. **一个完整目标，一条视觉执行主线。** 未知 GUI 任务进入同一个视觉阶段和固定的通用工具面；每次 mutation 后都重新观察、判断和执行。工具返回 `ok` 或像素变化都不是任务完成。
4. **动作面保持小而稳定。** 点击、滚动、输入、快捷键和拖拽覆盖基本键鼠事务；按住和路径是拖拽的连续形态，不为打开应用、保存文件、答题、聊天或某个软件增加宏工具。新增动作必须先由跨任务 E2E 证明现有动作面结构上无法表达。
5. **原始 goal 是唯一语义真相。** Runtime 可以建立一个全目标 obligation，但不能把未知任务编译成关键词分支、应用步骤或第二份执行意图。用户口头强调 GUI 时由同一个模型理解，不再通过前端开关、Task Profiler 或关键词 router 重复编码。
6. **记忆只加速，不补硬实力。** 冷启动会在整个 run 中禁用长期 Skill/Memory；有记忆和无记忆必须走同一条执行链。当前会话可以携带有界的原始事实与工件引用，但不靠关键词改写任务，也不可冒充当前屏幕证据。
7. **测试必须与用户入口一致。** GUI 端到端测试从可见 Electron 客户端填写任务并点击运行；直接调用 Python 类或 HTTP API 只能叫单元测试或集成测试。
8. **先修基础设施，不用提示词补模型。** Runtime 只保证当前帧、坐标映射、窗口/指针所有权、输入和回执的机械正确性；不做按钮吸附、目标重瞄、重复点击禁令或应用专属恢复。若提交坐标与执行坐标一致但任务失败，先比较模型和观察质量。
9. **每轮以净删和 E2E 证据为门。** 冻结分支不整体合并；一个候选能力只有在当前主线的随机失败样本上产生跨任务实质提升，且同一变更删除旧 owner、没有扩大提示词和应用规则时才保留。历史 run 不能证明当前源码。

## 产品调用链

```text
Electron「智能代理」
  -> POST /api/v2/goals/runs { goal, execution_context }
  -> Goal Admission（不含执行模式）
  -> 一个 full-goal mission / 一个 Visual Actor
  -> 当前权威 JPEG + 有界 task state + 小工具面
  -> 模型选择一笔动作
  -> CapabilityBroker 只投影机械可用工具
  -> AtomicDesktopAdapter 唯一坐标映射
  -> RawDevice 真实键鼠输入
  -> 新权威帧 / 回执 / 下一模型回合
  -> claim 或 blocked
```

这是当前 GUI 产品验收的唯一语义入口。内部仍会托管 ARP job 和兼容数据，但它们不能获得第二份任务计划或执行策略。`/api/chat`、MCP、CLI、直接 API runner 和 FlowScript 仍有开发或兼容用途，不能替代客户端 E2E，也不能发展成另一套产品主循环。

## 核心组成

| 组件 | 职责 | 不应承担 |
| --- | --- | --- |
| `LiveVisualActor` | 看原始 goal、当前 JPEG 和有界状态，选择一个工具调用 | 物理输入、应用脚本、隐藏执行策略 |
| `MissionRuntime` | 驱动模型 → 工具 → 新帧循环，保存最小任务状态 | 关键词规划、修正目标、替模型重试策略 |
| `FrameAuthority` | 原子发布帧、窗口、区域、epoch 和提交图片尺寸 | 解释画面或选择动作 |
| `CapabilityBroker` | 根据当前帧、窗口和指针所有权投影可执行工具 | 根据任务类型选择 GUI/CLI 或禁止模型策略 |
| `AtomicDesktopAdapter` | 将模型提交图片坐标映射一次并执行一笔输入 | 目标吸附、offset 补偿、应用特判 |
| `RuntimeEvidencePlane` | 保存模型提交、物理尝试和回执之间的绑定 | 充当第二个语义 verifier |
| 外部 E2E oracle | 在测试环境独立判断随机任务结果 | 向生产 Agent 暴露答案或控件位置 |
| Legacy owners | 兼容尚未迁移的能力 | 与 vNext 长期双写同一状态或决策 |

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

原生视觉配置默认关闭 UIA、OCR 和 predictive OCR；这些结构化 provider 仍可在来源、当前帧和预期收益明确时显式启用，但动作失败不会自动触发它们。

### 当前验证快照（2026-07-30）

- 当前源码全量回归通过：`python/winpilot_home` 3367 passed / 13 skipped，根目录 Python 2508 passed，Vitest 224 passed，typecheck 和 production build 通过。
- 随机精确点击 `seed=7302026` 通过真实 Electron 入口和独立浏览器 oracle。模型提交图片坐标 `(418,147)`，唯一映射得到 `(717,252)`，`physical_attempt` 实际记录也为 `(717,252)`；坐标验收没有读取目标 bbox。
- 随机 `dynamic-hold seed=7302031` 的真实鼠标轨迹到达目标，外部 oracle 判定任务成功，但 Runtime 在第三个模型提议上 `state_rejected`，最终释放发生在失败清理阶段，因此正式结果仍为失败。
- 随机 `ordered-clicks seed=7302032` 第一步正确，随后模型持续点击同一位置，30 回合耗尽，外部 oracle 判定失败。没有为此加入重复点击禁令、重瞄规则或更长提示词。

这些样本说明基础动作和坐标链可以闭环，但当前模型的跨帧完成判断、自我纠错和随机多步视觉可靠性仍明显不足。单次通过不能换算为成功率；仓库中的固定任务只是回归探针，任务空间不是 11 类或任何有限分类。

仍然存在的重要工程债务：

- live vNext 已收敛为一个 Visual Actor，但 Mission/Projection/Evidence 合同仍然偏重；只有真实 E2E 证明某项校验能阻止基础设施错误时才保留；
- 当前工具面仍主要是 GUI/control，自主混合选择 GUI、命令行和 provider 尚未实现；实现时必须让同一个模型直接看见工具，不能再增加前端执行选项或预分类 Agent；
- Legacy 的 `AgentRunner`、Task Contract、StageRuntime、Controller、UIA/OCR 和应用索引仍很大；它们只可作为逐能力候选，不能整体合并回主循环；
- 动态按住任务存在“外部任务已成功、Runtime 仍失败”的完成语义裂缝；随机多步点击显示当前视觉模型缺少稳定自我纠错；
- 真实消费软件、多窗口、托盘、弹窗、动画目标、游戏、高 DPI 和多显示器的随机覆盖不足；
- 当前源码没有完成用户五项长任务或任意组合任务的稳定验收；不能把历史通过、单次精确点击或受控拖拽描述为通用 computer-use 成功率。

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
- [视觉运行时原则](docs/visual-runtime-principles.md)
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
