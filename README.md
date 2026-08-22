# 灵动脚本 / Lingdong Script

灵动脚本是面向 Windows 的视觉优先 GUI Agent。目标不是为少数应用编写自动化脚本，而是让同一个 Agent 在真实客户端里观察当前屏幕、理解未知界面、操作鼠标键盘、检查结果，并在失败后重新观察和调整。

项目仍处于研发阶段。受控视觉靶场和部分真实软件链路已经跑通，但这不等于任意电脑、任意软件或任意任务已经可靠完成。仓库中的 11 个真实任务和组合长任务是黑盒探针，不是产品规则库。

## 设计原则

1. **真实像素是可见状态的最终依据。** 原生视觉模型直接看当前权威帧；UIA、Win32、OCR 和传统 CV 只有在来源、窗口、帧和像素一致时才提供辅助定位，不能替代视觉理解。
2. **模型决定语义，本地运行时只执行通用动作。** 模型始终持有完整用户目标，按当前画面决定下一步；新画面或异常证据可以让模型修改乃至放弃旧策略，本地层不得要求它继续执行预定路径。本地层只负责坐标映射、物理输入与按键释放清理，不把目标改写成应用脚本，也不做任何拦截或验证。
3. **一个完整目标，一条视觉执行主线。** 未知 GUI 任务进入同一个视觉阶段和固定的通用工具面；每次 mutation 后都重新观察、判断和执行。工具返回 `ok` 或像素变化都不是任务完成。
4. **动作面保持小而稳定。** 点击、滚动、输入、快捷键和拖拽覆盖基本键鼠事务；按住和路径是拖拽的连续形态，不为打开应用、保存文件、答题、聊天或某个软件增加宏工具。新增动作必须先由跨任务 E2E 证明现有动作面结构上无法表达。
5. **原始 goal 是唯一语义真相。** Runtime 只保存这一个 goal 的最小状态，不能把未知任务编译成关键词分支、应用步骤或第二份执行意图。用户口头强调 GUI 时由同一个模型理解，不再通过前端开关、Task Profiler 或关键词 router 重复编码。
6. **记忆只加速，不补硬实力。** 冷启动会在整个 run 中禁用长期 Skill/Memory；有记忆和无记忆必须走同一条执行链。当前会话可以携带有界的原始事实与工件引用，但不靠关键词改写任务，也不可冒充当前屏幕证据。
7. **测试必须与用户入口一致。** GUI 端到端测试从可见 Electron 客户端填写任务并点击运行；直接调用 Python 类或 HTTP API 只能叫单元测试或集成测试。
8. **先修基础设施，不用提示词补模型。** Runtime 只保证当前帧、坐标映射、窗口/指针所有权、输入和回执的机械正确性；不做按钮吸附、目标重瞄、重复点击禁令或应用专属恢复。若提交坐标与执行坐标一致但任务失败，先比较模型和观察质量。
9. **每轮以净删和 E2E 证据为门。** 冻结分支不整体合并；一个候选能力只有在当前主线的随机失败样本上产生跨任务实质提升，且同一变更删除旧 owner、没有扩大提示词和应用规则时才保留。历史 run 不能证明当前源码。

## 产品调用链

```text
Electron「智能代理」
  -> POST /api/v2/goals/runs { goal, optional thread_id }
  -> server-owned Thread / Turn + Goal Admission
  -> 服务端建立桌面锚点并捕获完整真实桌面 JPEG
  -> 同一个 LiveVisualActor 首次调用即看到原始 goal、有界 Thread history 和 JPEG
       -> 直接回复，或调用 connector / shell / GUI
       -> 本地或 connector 结果回到同一个 Actor
       -> GUI 动作经唯一坐标映射和 RawDevice 执行，再提供新帧
       -> reply_to_user 或 blocked
```

这是当前 GUI 产品验收的唯一语义入口。内部仍会托管 ARP job 和兼容数据，但它们不能获得第二份任务计划或执行策略。显式 MCP 只作为同一 Actor 的普通工具；旧 `/api/chat` 已删除，CLI、直接 API runner、FlowScript 或直接 MCP 调用仍不能替代客户端 E2E，也不能发展成另一套产品主循环。

## 核心组成

| 组件 | 职责 | 不应承担 |
| --- | --- | --- |
| `AgentThreadStore` | 保存有界成功 Turn 文本并绑定 run 终态 | 长期记忆、摘要模型、执行选择 |
| `LiveVisualActor` | 首次调用即看完整桌面 JPEG，直接回复或选择 connector、shell、GUI 工具 | 物理输入、应用脚本、第二角色委派 |
| `ConnectorGateway` | 向同一 Actor 暴露显式配置的 MCP 目录和调用回执 | 服务专属 adapter、关键词路由、应用规则 |
| `MissionRuntime` | 驱动模型 → 工具 → 新帧循环，保存最小任务状态 | 关键词规划、修正目标、替模型重试策略 |
| `FrameAuthority` | 原子发布帧、窗口、区域、epoch 和提交图片尺寸 | 解释画面或选择动作 |
| `AtomicDesktopAdapter` | 将模型提交图片坐标映射一次并执行一笔输入 | 目标吸附、offset 补偿、应用特判 |
| `TraceRecorder` | 异步保存 BUS 事件、物理回执和 completion 诊断帧 | 阻塞物理输入、充当第二个语义 verifier |
| 外部 E2E oracle | 在测试环境独立判断随机任务结果 | 向生产 Agent 暴露答案或控件位置 |

更完整的职责和数据流见 [架构说明](docs/architecture.md)。

## 当前能力和边界

已经具备的通用机制包括：

- 多源应用发现、已运行实例优先、启动后进程/窗口身份核对、前台恢复和全虚拟桌面回退；
- 权威帧身份与窗口裁剪；模型空间工具使用所见当前图片的精确像素坐标，runtime 只映射一次到权威帧和物理桌面；
- 当前 live 模型面提供点击、双击、文本输入、快捷键、滚轮、拖拽和连续/跨帧 held-pointer；
- 按下后目标才出现时，跨权威帧保留一个有超时和强制清理的 held-pointer session；
- 动作前帧新鲜度检查、物理完成回执、新权威帧，以及不阻塞输入的异步 trace；任务是否完成由同一个模型在真实屏幕上判断；
- 同一模型首次调用就看到完整真实桌面；Electron 在 run 前后保持普通可见控制面；
- 有界 Thread/Turn 上下文：只保留最近成功且有用户结果的原生文本交换，失败 Turn
  不污染下一轮；
- 两个通用 connector 工具 `search_connector_tools` / `call_connector_tool`；它们只发现
  显式配置的 MCP，并与 reply/GUI 同时交给同一个 Actor，不经过关键词 router 或副作用门禁；
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

旧 `agent/loop.py` / AgentRunner / ToolSession 已删除。当前唯一活动 vNext 只可能利用请求
自然稳定部分的 provider 自动缓存，也不发送
`prompt_cache_key`、Anthropic `cache_control` 或 Gemini `cachedContent`；vNext 记录
provider 返回的 cache hit/write token，观测本身不等于创建缓存。

### 当前验证快照（2026-08-19）

- Python `2508 passed`，Renderer `327 passed`，TypeScript typecheck、Electron/CLI build 和
  项目路径扫描全部通过。
- 可见 Electron 受控桌面套件 `18/18`；99 次物理尝试与 99 次 RawDevice 回执一一对应，
  全部 `applied/input_completed`，32 次输入释放清理全部完成，trace 零丢失/零写入失败。
- 安全真实应用探针 `5/5`：Notepad 精确落盘、Calculator 显示精确结果、聊天后继续桌面任务、
  缺失文件诚实结束、Paint 外部图像判定检出真实笔画。含纯聊天样本的 6 个 trace 共
  34 次物理尝试/34 次回执，11 次释放清理全部完成。
- 可见 Studio 离线流程 `2/2`：click 外部计数 `0 → 1`，clipboard 条件走 YES 分支并生成
  1920×1080 managed screenshot；两个 run 均零模型调用、trace 完整。

这些是当前机器、当前模型和当前显示条件下的回归证据，不是通用成功率。仍存在的重要缺口：

- 没有独立的“用户点击可见 Stop 按钮后，held pointer/key 完整释放”命名 E2E；机械释放已有
  单元/集成覆盖。
- 第一次视觉调用就同时提供回复、显式 connector、shell 与 GUI/control；通用
  `run_shell`、`run_detached`、`write_file` 的进程或文件回执不替代物理输入回执，
  也不决定任务完成。
- Studio、Replay、Memory、旧 Flow/feature/F9 和本地 7880 API 仍是有真实消费者的兼容面；
  继续删除必须按消费者推进，不能重新引入 AgentRunner、Task Contract 或第二 verifier。
- 真实消费软件、多窗口、托盘、弹窗、高 DPI、多显示器和不可逆外部动作的安全覆盖仍不足；
  未执行会发送消息、点赞或发布内容的 destructive/representational E2E。

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
