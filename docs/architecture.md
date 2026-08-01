# GUI Agent 架构与运行原理

本文描述当前代码的长期架构边界。它区分“已经实现的机制”“仍存在的兼容路径”和“目标约束”，不把路线图写成已完成事实。

## 目标

灵动脚本的核心目标是：同一个 GUI Agent 通过真实客户端，在未知 Windows 图形界面上持续执行“看画面 → 理解当前状态 → 做一个动作 → 看新结果 → 调整”的闭环。

仓库中的 11 个样例及其混合长任务只是应用发现、开放世界导航、工件传递、连续指针、视觉理解和持续会话等压力方向。生产 runtime 不应知道“第几个测试”，也不应靠原句关键词选择固定剧本。

## 不变量

### 1. 一个产品入口

用户从 Electron 的“智能代理”页提交目标。正式调用链是：

```text
PlatformWorkbench
  -> useControlPlane.startGoal
  -> POST /api/v2/goals/runs
  -> server-owned Thread / Turn + Goal Admission
  -> LiveVisualActor.begin_turn
       -> direct reply -> reply terminal
       -> generic connector search/call -> same Actor continues
       -> observe_desktop -> run-bound desktop lease
          -> Electron yield + exact target bind
          -> same LiveVisualActor -> MissionRuntime
          -> CapabilityBroker -> GUI transaction
```

`POST /api/v2/goals/runs` 是主 Agent 用户入口。请求只携带 `goal` 和可选
`thread_id`；不携带预绑定桌面、不接受客户端选择的执行媒介、`graph`、
`runtime_version` 或 `task_id`。服务端当前唯一选择 vNext，构造规范图并生成、绑定
图身份；vNext 不可用时请求失败，不静默回退 Legacy。只有同一 Actor 调用
`observe_desktop` 后，前端才通过匹配 `run_id + lease_id` 的独立请求交付当时的桌面
上下文。

这一主路径的 canonical composition owner 是 `orchestrator/live_agent.py`；
`vnext.live_agent_enabled=true` 是 fail-closed 的产品可用性配置，不是按任务选择的
canary selector，也不允许客户端逐 run 切换。

显式 MCP 只作为同一 `LiveVisualActor` 的普通工具。`/api/chat`、CLI、scheduler、
integration runner 或直接 MCP 调用可以复用内部能力，但不能形成行为不同的第二套 GUI
产品入口，也不能代替 Electron 客户端 E2E。`/api/v2/task-contracts/compile` 与可由
客户端提交图和 runtime 的 `/api/v2/arp/runs` 暂留给 Studio、自动化编辑和兼容调用，
不属于主 Agent 的意图或 runtime 选择链。

### 2. 一个完整目标和一条执行主线

Goal Admission 把用户原始目标直接绑定到一个 full-goal vNext turn。相同的
`LiveVisualActor` 先在没有截图、没有系统提示的原生文本回合中直接回答、调用显式
配置的两个通用 connector 工具，或调用 `observe_desktop`。只有后者才进入
full-goal GUI mission；connector 结果也只回到同一个 Actor 继续决定。模型可在运行中
调整、替换或放弃工作计划，最新画面与动作结果优先于旧计划的一致性。任务不能在
看见界面前被 compiler 拆成一串应用专属微步骤，也不能因任务名称切换到另一套
Agent、工具或提示词。Task Contract 只属于 Studio/兼容链。

原生客户端当前使用一个小而固定的本地 GUI 操作面：点击、双击、输入、快捷键、滚动、
拖拽/折线路径/跨帧按住，以及 claim/blocked；显式配置时，两个 generic connector
工具与它同轮可见。复合目标由模型在“新帧 → 一个决定
→ 一笔动作 → 新证据”的循环中完成，不靠公开宏工具承载写死流程。运行时可以拒绝
过期坐标、失去所有权的窗口或非法输入事务，但不能因为模型改变了语义判断而把它
拉回旧策略。

### 3. 一个可见状态权威

对屏幕上是否存在、位于何处、是否变化等问题，当前权威帧是最终依据。权威帧至少绑定：

- 目标 hwnd 或完整虚拟桌面；
- capture region；
- frame id 和时间；
- screen/window epoch；
- 坐标空间与 DPI 信息。

原生模型的空间工具使用它实际收到的完整图片像素（`image_x/image_y`、
`image_points`）。runtime 校验坐标仍位于这张图片内，再根据同一帧的尺寸和
capture region 唯一映射到物理桌面；不经过第二套归一化语义坐标。模型不需要猜
Windows 坐标，也不能把另一张截图、缩略图或旧帧的像素继续用于当前动作。

UIA、Win32、OCR、传统 CV 和 VLM fallback 都是有 provenance 的观测信号。它们只有在与当前窗口、当前帧和像素区域一致时才能帮助定位；不能仅因 UIA 树丰富或 OCR 有文字就否决模型从画面得到的语义。

### 4. 一个动作事务

GUI mutation 应归一化为同一种事务：

```text
pre-frame
  -> target/path/effect grounding
  -> focus and freshness preflight
  -> desktop input lease
  -> physical or proven background-safe input
  -> post-frame
  -> local effect evidence
```

点击、滚动、输入、快捷键、拖拽、笔画和按住不是不同应用的能力，而是不同形态的通用输入事务。

### 5. 一个完成语义

动作成功、局部像素变化、阶段成功和用户任务完成是四件事：

- **action success**：输入被执行；
- **local effect**：预期局部区域发生了与动作相关的变化；
- **stage success**：阶段工件或义务满足；
- **task completion**：用户的最终目标由当前证据满足。

只有最后一层可以让外层 run 进入可信完成。模型总结、工具 `ok` 或 runtime `done`
都只是候选状态。当前 vNext 由模型提交 claim，再由 obligation/evidence 链检查其
运行时一致性；测试中的真实业务成功仍由独立应用/browser oracle 判断。历史帧只能
证明先前发生过什么，不能替代用户要求持续保持的当前状态。

纯文本回答使用独立 `reply` 终态：它返回模型原始文本，不能构造 frame、target、
`physical_attempt` 或 mission evidence。`reply` 表示这一轮成功完成，但不会证明
任何外部应用状态；需要桌面操作的 `done` 仍必须由独立 oracle 验证用户结果。

## 控制平面

### 原始目标与执行媒介

主 Agent 不再有前端“执行约束”选择器，也没有对应的请求字段、Goal Admission 状态或 Broker `InteractionMode`。用户口头强调“必须使用 GUI”时，这句话只存在于原始 `goal`，由同一个语义 Agent 理解；系统不再要求用户把同一意图重复编码到系统开关，也不增加关键词路由器或第二个 Task Profiler。

当前 live vNext 在存在显式 MCP 配置时，把 `search_connector_tools` 和
`call_connector_tool` 与首轮 `observe_desktop` 同时交给同一个 Actor；进入视觉线后，
它们也与 GUI/control 工具同轮可见。因此当前已经能在直接聊天、只读 connector 和
GUI 间自主组合，但尚无 shell，也不准入外部 mutation。后续能力仍必须作为同一
Agent 的普通工具加入同一轮选择；`execution_medium` 只描述实际执行，不承载用户
策略。Runtime 只保留 schema、可信画面、窗口/指针所有权与不可逆动作边界等机械
约束，不解释任务类型。

桌面交接上下文只存在于一次性 lease bind。服务端在生成规范图之后绑定 `task_id`，
因此客户端上下文不能借机注入图身份或选择 runtime。真实验收检查“Agent 实际用了
什么”和外部结果，不检查前端策略状态。更完整的迁移边界、兼容面和退出门见
[vNext 迁移](vnext-migration.md)。

### Thread、Turn 与桌面租约

前端为一次对话保存不透明 `thread_id`，服务端为每条消息创建一个 Turn，并且同一
Thread 同时最多有一个活动 Turn。历史只保留最近成功且有用户可见结果的原生
`user/assistant` 文本交换：最多 12 条消息、16 KiB；失败、取消、空结果和内部证据
不进入下一轮。新建对话显式丢弃客户端 thread；这不是长期 memory，也没有 summarizer
或第二个 conversation model。`AgentThreadStore` 把最多 100 个 Thread 的同一有界成功
投影原子写入进程私有 checkpoint；启动时严格校验并恢复原 `thread_id`、最近成功结果与
原生消息。失败/取消/空结果、视觉帧、connector 正文、trace 正文和未完成 run 不写入，
损坏或超限 checkpoint fail closed。它只延续短会话，不承担长期记忆或语义总结。

`DesktopLeaseSlot` 是一次性的机械交接状态机。Actor 请求后状态进入
`waiting`，Electron 让出前台并提交精确 `run_id + lease_id + target`，服务端验证后
才进入 `delivered` 并允许首帧与物理动作。拒绝、超时或失败关闭 lease；matching
terminal 由 Electron 自行恢复窗口，stale run、stale lease 或重复终态不能恢复其他
运行。发送消息本身不再隐藏窗口。

### Task Contract（Studio/兼容）

Task Contract 把用户目标表达为：

- 原始目标与 typed intent；
- 阶段和依赖；
- 所需 capability；
- 输入/输出 artifact；
- evidence provider；
- completion obligation；
- `experience_policy`。

编译器适合表达“先得到工件，再发送工件”这类显式数据依赖，不适合替模型预写未知 GUI 的每一次点击。该链当前由 `/api/v2/task-contracts/compile` 暂留给 Studio 和兼容调用，不再替主 Agent 选择 intent 或 runtime。无法从用户语句确定的信息应由 Agent 到达对应界面后观察决定。

### ARP 兼容入口

`arp/1` 是可执行图契约，负责阶段顺序、失败边、等待、人类门和运行审计。它不应成为第二个 GUI planner。Task Contract 进入 ARP 后，未知视觉步骤仍由同一个 bounded Agent/Controller 闭环处理。

服务端仍在内部用 ArpExecution 承载 vNext run 的生命周期，但主 Agent 的规范图只能由 Goal Admission 在服务端构造。允许调用方提交图和 `runtime_version` 的 `/api/v2/arp/runs` 是 Studio/兼容入口，不能被重新接回智能代理提交页。

### StageRuntime（Legacy）

StageRuntime 是旧 Task Contract/TASK_STAGE 链的阶段生命周期 owner：能力预检、工件传递、可选能力、执行与验证。它仍服务兼容调用，但不是 vNext 主 Agent 的 owner；新能力不得为了兼容在 StageRuntime 与 MissionRuntime 各实现一份。

### vNext 当前 owner

| Owner | 唯一职责 | 明确禁止 |
| --- | --- | --- |
| `AgentThreadStore` | 保存并恢复有界成功 Turn 文本与已完成 run 投影 | 长期记忆、语义总结、执行选择 |
| `LiveVisualActor` | 理解完整目标和当前 JPEG，选择一笔工具调用 | 物理输入、应用脚本、第二角色委派 |
| `ConnectorGateway` | 有界发现显式 MCP，并把只读调用结果及 receipt 返回同一 Actor | 服务专属 adapter、任务路由、外部写入授权 |
| `DesktopLeaseSlot` + Electron handoff | 精确配对桌面让出、target 交付和窗口恢复 | 按任务词预租约、替模型决定是否用 GUI |
| `MissionRuntime` | 顺序驱动模型、动作、新帧与 obligation | 关键词规划、纠正模型答案 |
| `FrameAuthority` / workspace | 发布当前 frame、epoch、hwnd、region、图像身份 | 理解画面或选择目标 |
| `CapabilityBroker` | 投影 schema、当前可写性、窗口/指针所有权 | 根据任务类型预选路线 |
| `AtomicDesktopAdapter` / `RawDevice` | 唯一坐标映射、真实输入和机械回执 | snap、offset、语义成功 |
| `RuntimeEvidencePlane` | 绑定 actor、canonical、physical 和 frame 证据 | 充当第二个语义 verifier |

这些 owner 不应形成多个 planner。Legacy `AgentRunner`、FastGuiController、
RunStateMachine 和 FailureRecoveryLayer 中可复用的基础能力，只能逐项迁移或删除，
不能整体接回 vNext。失败后使旧 frame 失效，捕获新权威帧，再让同一个模型结合完整
目标与失败事实决定继续、换路径或 blocked；runtime 不解释业务含义。

## 视觉与感知

### 原生视觉主路径

当同一 Actor 申请桌面后，首个 GUI 回合保留初始有界文本消息及其原生
`assistant observe_desktop → tool result`，再附加当前权威 JPEG、完整原始目标和
有界 task state。后续 GUI 回合还保留最多最近一笔 provider 原生
`user image → assistant tool call → tool result`。模型从画面理解语义并提出一笔
typed transaction；动作后重新捕获权威帧，下一回合覆盖旧视觉 exchange。这里没有
无界聊天历史、第二 planner 或独立 Grounder/Verifier。

当前已复验基线固定 `api.thinking=false`、Visual Actor
`temperature=0.0`、最大提交图片边 1120，并且每个决策最多一次 provider attempt。
临时打开 thinking、把图片边提高到 1600、移除 temperature zero 均没有提高 E2E，
已经回退。Runtime 不根据任务词、失败类型或当前 benchmark 自动切换推理模式；未来
更换模型时用同一外部-oracle 矩阵 A/B，不随模型升级添加补偿规则。

当前 live vNext 最小工具目录不向模型暴露 Legacy
`observe(image_region=...)`。Legacy crop、UIA、OCR、CV 和 watcher 仍是兼容债务，
不是 vNext 失败后的自动 fallback。若未来证明当前观察结构上不足，新增观察原语也
必须先在不同任务族通过 E2E，不能为 corridor、棋盘或某个应用单独加入。

当前默认配置关闭 UIA、OCR 与 predictive OCR，`native_vision_direct=true`。这使默认路径先看图；结构化 provider 仍是可显式选择的兼容/提速能力，不是被删除的能力。

默认原则是：

- 不在每次模型看图前强制运行全屏 UIA/OCR；
- 不把模型未请求的 UIA/OCR/CV/MCP 结果塞进视觉回合；它们只是明确选择的辅助探针；
- 不把缩放后的 JPEG 像素当物理桌面坐标；
- 不在一个模型回合连续执行多笔未经新帧验证的空间动作；
- 看不清目标、规则或结果时使用新权威帧重新判断或 `blocked`，不猜。

### 模型调用协议

默认调用仍是 OpenAI-compatible Chat Completions。配置只通过 `api.protocol` 或
`api.api_style` 显式选择
`openai_responses` / `responses`、`anthropic` / `claude` 或 `gemini` / `google`
时，同一 `LiveVisualActor` 改走对应的 Responses、Messages 或 `generateContent` wire；
`openai_compatible` / Chat aliases 保持原路径。`api.provider` 只保留元数据语义；wire
选择不读取 provider、模型名或 `base_url`，
避免把聚合器上的 Claude/Gemini 模型误送到原生接口。

原生 adapter 只转换现有 canonical messages/tools，不增加 planner 或模型角色。当前
范围是非流式文本、Base64 data-URL 图片、function schema、tool call/result，以及
后续工具回合所需的 provider 原生 reasoning/signature sidecar；不支持音频、视频、
文件或 provider built-in tools。审计发现的 provider 误切 wire 与 Anthropic 丢失
canonical `strict:true` 已修复，相关 5 个测试文件 `214 passed`。三条原生线路目前仍
只有确定性 transport/协议测试，属于 provisional adapter；尚无真实 provider
credential 验收，不能据此声称 API 兼容已经端到端通过。

### 显式结构化辅助路径

结构化信号适合：

- 已确认的标准输入框、按钮和菜单；
- 文本查询与短标签定位；
- 后台安全且可验证的 UIA/Win32 操作；
- 明确选择的非视觉兼容运行；
- scoped OCR 和结果交叉核对。

结构化信号只有在模型或合同明确请求、来源与当前帧可核对、且预期收益清楚时才进入本回合。失败本身不是自动启用 UIA/OCR 的理由。结构化信号不适合单独理解游戏、自绘画布、动画、图片按钮、视觉布局规则或复杂跨区域关系。旧 `fusion.observe` 仍保留 UIA/OCR 优先和 rich-skip 分支，这是兼容债务，不是目标架构。

### 窗口与应用发现

这里采用与 Codex 类似的职责划分：基础工具负责尽可能完整地发现事实并返回证据，模型负责结合完整目标、当前画面和工具结果作语义决定。不要用 prompt 教模型某个应用藏在哪里；应修好所有任务共用的发现工具。

以抽象合同 `app.open` 为例（不是要求新增同名宏工具），基础层应查询并归一化：

- 运行进程、可见/隐藏/托盘窗口及其 pid/hwnd；
- Start Menu 和 StartApps 注册项；
- App Paths、安装元数据与可执行路径；
- `.lnk` 快捷方式的目标、参数和工作目录；
- 目标选择与启动前后的进程/窗口快照。

工具返回候选、provenance、置信依据和可执行启动方式；模型根据画面选择复用、恢复或启动。启动后必须用新进程/窗口与启动动作的时间、pid、路径和窗口变化验证因果身份，不能因为命令退出码为零就声称打开了正确应用。

应用解析应按以下顺序收集事实后决定：

1. 已运行且可见/可恢复的窗口；
2. 托盘或隐藏窗口；
3. 已安装应用和开始菜单入口；
4. 用户指定路径或可执行文件；
5. 最后才启动新实例。

“打开应用”不意味着无条件启动新实例。窗口标题、进程、可见状态、登录态线索和当前画面需要一起判断。应用别名只能帮助发现候选，不能决定操作流程。

当前实现已经在 `perception/apps.py` 与通用启动工具中接入 Start Menu、`Get-StartApps`、App Paths、快捷方式目标、可执行/进程身份、已运行实例复用和启动前后窗口证据。窗口、托盘、登录态、alias、focus 与 launch 仍分散在多个 owner；迁移切片需要重新通过真实客户端 E2E，不能继承旧 runtime 或历史 run 的结论。

## 动作执行

### 通用动作集合

仓库中的 Controller/Legacy 路径覆盖或编排：

- pointer move、click、double click、hover；
- scroll；
- type text、hotkey、timed key；
- drag；
- continuous strokes/path；
- wait for stable/change；
- mouse down / move / mouse up 的跨帧 held-pointer session。

当前 live vNext 的本地 GUI/control 投影只有 click、double click、
drag/path/held-pointer、scroll、type text、hotkey、claim 和 blocked；显式配置的两个
connector 工具由 Gateway 并列提供，不扩张本地动作面。上述 hover、timed key、独立
wait 和裸 mouse-down/up 仍是兼容/底层能力，不得写成当前模型已经可自主选择。

Legacy `wait(until_change=true, persistent=true)` 只负责在绑定窗口或 ROI 上观察稳定
变化；即使未来进入 vNext，变化含义和下一步仍必须由同一模型结合新帧决定。聊天、
棋局或某个测试的等待逻辑不得下沉到 watcher。

连续路径不是绘画专属通道。移动滑块、摆放对象、调整时间轴、手写和画图共用相同的指针、区域、路径、新鲜度与验证基础设施；任何 stage 都不能决定这些通用能力是否可用。

### 跨帧按住

有些任务在按下后才显示目标。运行时允许：

1. 在当前权威帧定位 source；
2. `mouse_down` 并建立 held session；
3. 捕获新权威帧；
4. 模型理解新出现的 checkpoint/target；
5. 只允许继续移动或匹配的 release；
6. 超时、失焦、取消或异常时强制 `mouse_up` 清理。

旧帧中未执行的空间坐标不能跨帧继续使用，必须基于新画面重规划。

### 桌面资源

物理鼠标和键盘通过 Desktop Action Queue 全局单写。只有被证明不会移动真实鼠标、不会抢键盘焦点的窗口消息/UIA 动作才可按 hwnd 隔离。`multi_input_cooperation` 只是实验性的人类优先仲裁，不代表 Windows 存在第二套物理输入设备。

## 计划、上下文与记忆

完整用户目标是不可丢失的根约束，计划只是模型内部的可变工作状态。初始计划不能限制后续视觉探索，也不能演化为本地编译器必须逐项执行的脚本。

上下文有三层：

- 当前 run 的目标、计划、窗口、画面和工具证据；
- session context，用于“刚刚画的画”“继续”等跨 turn 引用；
- long-term Memory/Skill，用于经过验证的经验复用。

冷启动只禁用第三层经验能力，并清空测试要求的 session；它不应破坏同一客户端会话中明确需要的多 turn 依赖。测试 harness 必须显式区分“独立 case”与“同一 scenario 的后续 turn”。

Memory Pack 只能作为提示。当前权威帧、确定性文件/进程证据和用户最新指令优先于记忆。

## MCP 边界

MCP 是显式可选的 capability source。只有 `.mcp.json`、配置路径或
`WINPILOT_MCP_CONFIG` 明确登记的 remote HTTP / stdio server 才会出现；没有配置时
两个 connector 工具不进入模型目录，原生视觉主线仍可独立运行。这里没有应用名
adapter、关键词 router 或 GUI 失败后的自动 fallback。

`search_connector_tools` 返回有界、标为不可信的 descriptor 和不透明 ref；
`call_connector_tool` 只接受该 ref 与 JSON object 参数。当前 live Actor 始终不授予
外部 mutation：只有 `readOnlyHint=true` 且 `destructiveHint` 不为 true 的工具可以
执行。Gateway 在调用前重新发现并核对 descriptor/fingerprint，MCP client 又在同一
物理连接的 `call_tool` 前核对一次 annotations 与完整 descriptor；整次发现/调用服从
单一 deadline/cancel，且不会重试含糊的远端动作。结果有大小上限、hash 与 receipt，
但仍是不可信数据，模型和外部 oracle 不能把远端 `ok` 当任务完成。

仓库的 `.mcp.example.json` 指向 GitHub 官方 remote MCP，用
`${env:GITHUB_PAT_TOKEN}` 注入 bearer token，并请求 server-side readonly 与有限
toolsets。仓库不分发 credential，也没有 GitHub OAuth 登录流程；其他 connector 复用
同一 generic MCP 合同，不新增服务专属实现。

当前外部证据已经分开证明“transport 可用”和“用户任务完成”不是一回事。GitHub-like
fixture 的精确 read 与 connector→Notepad 外部结果为 `2/2`；GitHub 官方 remote 也已
完成真实 discover、`get_file_contents` 以及
`get_commit(owner=zoujiapeng, repo=lingdong-script, sha=master, detail=none)` 物理调用，
后者 receipt 为 `succeeded`，并返回 2026-07-31 该次调用时独立 `gh api` 核对的
`fba5d7bb6dcb13c46e93dbc29bc3cef44e560622`。首个中文 Electron run 的
before-submit 已把目标损坏为大量 `?`，属于测试输入干扰，不能归因模型。随后 ASCII
run `arp-20260731-220327-1ed4e4` 完整提交目标，官方 remote 有真实会话/POST；五次模型
响应后，Actor 选择了未批准工具，Runtime 以 `state_rejected` fail closed，回复为空、
零 GUI 动作。这个干净任务失败的首个分歧是模型工具选择/停止，不是 connector
transport；Runtime 不应增加 GitHub 专属修正。

显式 MCP 配置现在拥有最高优先级，120 秒目录缓存也按完整 server 配置 fingerprint
隔离；相关聚焦测试 `21 passed`。这两项不再是未修 P1。

## 缓存与用量

仓库整体并非没有稳定前缀机制：冻结 Legacy `agent/loop.py` 在 32 KiB 前保持
append-only history 保护 DeepSeek prefix cache，并把 session continuity 放在
cache-friendly prefix。未生效的 `cache_strategy` 标签已删除；当前唯一活动 vNext 不走
这个 Legacy owner，只通过仍然相同的请求开头自然形成 provider 可自动命中的前缀，
不主动发送 OpenAI `prompt_cache_key`、Anthropic `cache_control` 或 Gemini
`cachedContent`，也不会为了命中缓存恢复无界历史或重复旧截图。

每次模型响应的 provider usage 会归一化为 prompt/completion、cache hit/miss/write
和 reasoning token，发布 `agent_usage` 并累计到 run 的 `model_usage`。这是成本与 A/B
观测面，不创建缓存；provider 不返回对应字段时保持为零，不能臆造命中或节省金额。
可见 Electron 的 20-turn fixture 报告累计 `21626` prompt tokens，其中 provider 返回
`13248` cache hits、`8378` misses、`0` creation；这证明 telemetry 与上游自动命中真实
生效，不证明 vNext 拥有主动缓存 owner。

## Trace、Artifact 与 Replay

- Trace 保存模型请求/响应、工具事务、帧、证据和状态变化。
- ArtifactStore 区分用户工件与模型观察帧，并用 run-scoped id/hash 传递。
- `automation-ir/1` 是新保存流程和 Skill 的规范中间层。
- `.wps.json`、FlowScript、DSL 和旧浏览器 executor 是兼容路径，不是 GUI Agent 的事实源。
- Replay 只有在适用条件和 postflight evidence 重新通过时才可算 Skill 成功。

## 当前实现债务

全面审查确认以下问题仍在代码中，文档不能掩盖：

1. `/api/v2/task-contracts/compile`、`/api/v2/arp/runs` 和 Legacy runtime 仍为 Studio/兼容调用存活；必须先清点并迁移消费者，再删除提供者。
2. `agent/loop.py` 体积过大；`FrameAuthority`、`FastGuiController`、`RunStateMachine` 与 `FailureRecoveryLayer` 虽已建立边界，旧逻辑尚未全部迁出和去重。
3. `TaskContractCompiler`、`StageRuntime`、`InteractionStageEngine`、`SubgoalExecutor` 和 Controller 存在重复解释。
4. 某些 communication/application adapter 仍含布局、别名或任务族特化。
5. 原生配置已默认关闭 UIA/OCR，但 `fusion.observe` 等兼容入口仍保留旧 provider 顺序；必须继续防止非默认调用面把结构化信号升级成视觉语义或失败后的自动 fallback cascade。
6. `/api/chat`、legacy IPC、FlowScript 和旧 feature 系统仍增加维护面。
7. 当前整改版本尚未用真实 Electron 客户端完成 11 个任务和混合长任务的全量验收，不能继承历史 run 的通过结论或据此宣称通用成功率。
8. connector transport 已有 fixture 与 GitHub 官方 remote 实证，但首个官方 GitHub
   干净用户任务仍因模型选择未批准工具并停止失败；不得加入专属 formatter 或参数
   修正。原生
   Responses/Anthropic/Gemini 仍未经过真实 provider 调用，只能保持 provisional。
9. connector 的 run trace 尚未持久化无正文 physical receipt；旧
   `agent/mcp_tools.py` 仍是绕过 Gateway 的 Legacy 重复 owner。这些是待证据驱动删除的
   基础设施债务，不解释当前 GitHub 模型工具选择失败。

当前切片的删除顺序与退出门见 [vNext 迁移](vnext-migration.md)，更广泛的整改优先级见 [GUI Agent 架构审计](GUI_AGENT_ARCHITECTURE_AUDIT.md)。

## 验收原则

GUI 架构改动至少需要：

1. 小范围单元测试证明数据和失败语义；
2. Python/TypeScript 常规回归；
3. 可控视觉目标验证真实物理输入和外部 ground truth；
4. 从可见 Electron 客户端提交的冷启动 E2E；
5. 至少一个与实现时样例不同的任务族；
6. trace 中准确绑定 outer run、child run、frame lineage 和 terminal evidence。

完整规则见 [端到端测试规范](testing.md)。

## 文档事实源

- 本文：架构、不变量和当前债务；
- [vnext-migration.md](vnext-migration.md)：主入口、模型工具选择、Legacy 兼容边界和删除退出门；
- [winpilot-agent.md](winpilot-agent.md)：工具、Controller、done gate 等实现细节；
- [testing.md](testing.md)：测试分类和通过定义；
- [frontend.md](frontend.md)：客户端入口和刷新/历史呈现；
- [apis.md](apis.md)：HTTP、IPC、MCP 契约；
- [replay-recording.md](replay-recording.md)：trace、IR、replay 与兼容层。

具体 run ID、截图路径和一次性性能数字属于测试报告或 trace，不应继续堆入架构文档。
