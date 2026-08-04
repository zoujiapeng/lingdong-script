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
  -> POST /api/v2/goals/runs { goal, optional thread_id }
  -> server-owned Thread / Turn + Goal Admission
  -> 同一个 LiveVisualActor：direct reply、通用 connector 调用或 observe_desktop
       -> reply 终态（窗口始终可见）
       -> 显式配置的 MCP：search/call 后仍由同一个 Actor 继续决定
       -> run-bound desktop lease（此时才让出前台）
          -> 精确 target + 当前权威 JPEG + 小工具面
          -> 模型一笔动作 / 新帧 / 同一视觉线
          -> claim 或 blocked
  -> matching terminal 由产品自行恢复窗口
```

这是当前 GUI 产品验收的唯一语义入口。内部仍会托管 ARP job 和兼容数据，但它们不能获得第二份任务计划或执行策略。显式 MCP 只作为同一 Actor 的普通工具；`/api/chat`、CLI、直接 API runner、FlowScript 或直接 MCP 调用仍不能替代客户端 E2E，也不能发展成另一套产品主循环。

## 核心组成

| 组件 | 职责 | 不应承担 |
| --- | --- | --- |
| `AgentThreadStore` | 保存有界成功 Turn 文本并绑定 run 终态 | 长期记忆、摘要模型、执行选择 |
| `LiveVisualActor` | 直接回复、选择 connector 或请求桌面；进入视觉线后看 JPEG 并选一笔工具 | 物理输入、应用脚本、隐藏执行策略 |
| `ConnectorGateway` | 向同一 Actor 暴露显式配置的 MCP 目录和只读调用回执 | 服务专属 adapter、关键词路由、外部写入授权 |
| `DesktopLeaseSlot` / Electron | 精确配对桌面让出、target 交付和窗口恢复 | 预判任务类型、替模型选择 GUI |
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
- 当前 live 模型面提供点击、双击、文本输入、快捷键、滚轮、拖拽和连续/跨帧 held-pointer；Legacy 兼容层仍有独立限时按键与等待 watcher，但它们不是当前模型工具；
- 按下后目标才出现时，跨权威帧保留一个有超时和强制清理的 held-pointer session；
- 动作前新鲜度检查、动作后因果区域验证、trace、artifact 和 completion obligation；
- 同一模型的 direct reply 与按需桌面租约；只有模型请求桌面后才隐藏，matching
  terminal 自行恢复；
- 有界 Thread/Turn 上下文：只保留最近成功且有用户结果的原生文本交换，失败 Turn
  不污染下一轮；
- 两个通用 connector 工具 `search_connector_tools` / `call_connector_tool`；它们只发现
  显式配置的 MCP，并与 reply/GUI 同时交给同一个 Actor，不经过关键词 router；
- connector 当前只执行明确标注 `readOnlyHint=true` 且未标注 destructive 的工具；
  调用前重新核对完整 descriptor，并服从同一 deadline/cancel；
- run 级冷启动、session 引用、Memory Graph 和已验证 Skill replay；
- 真实 Electron 客户端 E2E harness 与浏览器侧可信指针 oracle。

原生视觉配置默认关闭 UIA、OCR 和 predictive OCR；这些结构化 provider 仍可在来源、当前帧和预期收益明确时显式启用，但动作失败不会自动触发它们。

模型后端保留 OpenAI-compatible Chat Completions 默认路径，并新增显式选择的
OpenAI Responses、Anthropic Messages 和 Gemini `generateContent` 原生协议。wire
协议只由 `api.protocol` / `api.api_style` 显式选择；`api.provider`、模型名和
`base_url` 都不切换 wire。审计发现的 provider 误切 wire 与 Anthropic 丢失
canonical `strict:true` 已修复，相关 5 个测试文件 `214 passed`。当前范围是非流式文本、
Base64 图片和 function tools；真实三家 credential/接口尚未完成验收。

仓库整体存在冻结 Legacy 的稳定前缀机制：`agent/loop.py` 在 32 KiB 前保持
append-only history，并把 session continuity 放在 cache-friendly prefix。当前唯一活动
vNext 不走这个 owner，只可能利用请求自然稳定部分的 provider 自动缓存，也不发送
`prompt_cache_key`、Anthropic `cache_control` 或 Gemini `cachedContent`；vNext 记录
provider 返回的 cache hit/write token，观测本身不等于创建缓存。

### 当前验证快照（2026-07-31）

- 当前切片 Python 全量 `3458 passed, 13 skipped, 1 warning`，renderer `234 passed` 且
  typecheck 通过；测试通过不能替代下面的真实 GUI oracle。
- 通用 fixture 的可见 Electron 长 Thread 完成 20 个聊天 Turn `20/20`；同一报告
  （含其末尾额外 connector probe）累计 `21626` prompt tokens，其中 provider 报告
  `13248` cache hits、`8378` misses、`0` cache creation。报告：
  `output/playwright/client-thread-connector-e2e/20260731-211729-thread-connector/report.json`。
- 新鲜 Thread 的 GitHub-like read 与 connector→Notepad 组合任务外部结果为 `2/2`；
  后者用真实 GUI 把 connector 随机值精确落盘，但独立 oracle 通过时 run 仍在
  `running`，所以它不证明停止判断已经可靠。报告：
  `output/playwright/client-thread-connector-e2e/20260731-214024-thread-connector/report.json`。
- GitHub 官方 remote MCP 已完成真实 discover、`get_file_contents`，以及返回独立
  `gh api` 同一 master SHA 的 `get_commit` 物理调用。首个中文 Electron run 在
  before-submit 已把目标转成大量 `?`，只能记测试输入干扰；随后 ASCII run
  `arp-20260731-220327-1ed4e4` 完整提交目标，官方 remote 有真实会话/POST，但五次
  模型响应后以 `Actor initial turn returned an unapproved tool` fail closed，回复为空、
  零 GUI 动作；usage 为 `8126` prompt、`539` completion、`4160` hit、`3966` miss。
  这个干净任务失败属于模型工具选择/停止，不属于 transport。报告：
  `output/playwright/github-connector-e2e-ascii/20260731-220236/report.json`。
- 同一可见 Electron Thread 已连续完成 7 个 direct reply 和一个显式鼠标键盘
  Notepad Turn；聊天期间不请求 lease，桌面轮只在 Actor 请求后隐藏，终态自行恢复，
  随后的上下文回复能精确回忆桌面轮随机 token。
- Notepad 外部文件精确保存为 `DESKTOP-c419bb42`；两次一次性 provider
  `Connection error` 经普通用户重发恢复，失败 Turn 没有污染历史。桌面结果通过，
  但其最终回复比“只回复已完成”更长，属于模型格式偏差。
- 当前生产保留态通过可见 Electron 客户端跑完 10 项受控矩阵，独立浏览器 oracle 为 `5/10`：精确点击、有序三击、普通拖放、滑块和动态按住通过；试卷 4/6，四个走廊拖拽失败。
- 原始目标由操作系统键盘键入可见 Electron，再由真实鼠标点击“发送消息”；UIA 只用于定位和读回控件。
- Visual Actor 只保留最近一笔 provider 原生 `user → assistant tool call → tool result` 交换，不增加角色、动作或提示词。有序三击因此从重复第一目标直至耗尽回合，变为三次真实点击后通过。
- 试卷本次为 4/6；同一源码此前有 6/6、5/6 和错误提交 `81`，因此只能归为模型样本波动，不能写成算术能力修复。
- 四个走廊失败中，模型提交的路径与物理执行一致，但路径自身切角或出界；没有加入通道规则、坐标补偿或重瞄器。

这些样本说明基础动作、坐标、键盘和短多轮状态链可以闭环，但当前视觉模型的算术、几何规划、自我纠错和随机长任务可靠性仍明显不足。单次通过不能换算为成功率；仓库中的固定任务只是回归探针，任务空间不是 11 类或任何有限分类。详细正反证据见 [架构重置审计](docs/GUI_AGENT_ARCHITECTURE_RESET_2026-07-29.md)。

仍然存在的重要工程债务：

- live vNext 已收敛为一个 Visual Actor，但 Mission/Projection/Evidence 合同仍然偏重；只有真实 E2E 证明某项校验能阻止基础设施错误时才保留；
- 同一 Actor 已能在 GUI/control 与显式配置的只读 connector 间选择；shell 和外部
  mutation 尚未接入，后续也必须直接加入同一工具面，不能增加前端执行选项或预分类 Agent；
- Legacy 的 `AgentRunner`、Task Contract、StageRuntime、Controller、UIA/OCR 和应用索引仍很大；它们只可作为逐能力候选，不能整体合并回主循环；
- 一笔原生交换足以修复当前有序三击的跨帧遗忘，但不能替代长任务状态、模型算术和几何推理；不得把更多历史、Verifier 或任务规则默认塞回主循环；
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

GitHub 使用官方 remote MCP 示例；仓库不内置 token，也没有 OAuth 登录流程：

```powershell
$env:GITHUB_PAT_TOKEN = '<GitHub token>'
$env:WINPILOT_MCP_CONFIG = (Resolve-Path .mcp.example.json)
npm run dev
```

示例在 server 与 Agent 两侧都保持只读。其他 remote HTTP 或 stdio connector 复用同一
`.mcp.json` 结构和两个通用工具，不应新增服务专属 adapter。

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

- [AI / Codex 接手入口](CLAUDE.md)
- [全项目精确讲解与逐文件索引](docs/project-guide/README.md)
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
- [2026-07-29/30 架构重置与 E2E 证据](docs/GUI_AGENT_ARCHITECTURE_RESET_2026-07-29.md)
- [贡献指南](CONTRIBUTING.md)
- [安全政策](SECURITY.md)

## 维护准则

- 先删除、归并或复用，再新增模块和状态账本。
- 不为一个应用、一个对象或一个测试句子增加生产关键词、坐标、prompt 或完成条件。
- 新能力必须说明唯一 owner、通用输入、通用输出、失败语义和至少两个不同任务族的验证。
- GUI 改动必须同时提供单元/集成证据和真实客户端证据；不能用直接 API 成功冒充用户体验成功。
- 文档只保留当前机制、边界和可复现命令；具体 run ID、截图和临时测试数字留在报告、trace 或 changelog。
