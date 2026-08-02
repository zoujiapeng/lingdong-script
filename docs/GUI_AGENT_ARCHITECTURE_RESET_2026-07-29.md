# GUI Agent 全分支与真实端到端架构审计

审计日期：2026-07-29
审计基线：`master`、全部 8 个本地分支、61 个本地 tag、当前真实 Electron 客户端与 Windows 桌面
整改工作分支：`codex/lean-gui-agent-core`

## 1. 执行结论

当前五项验收任务的通过数是 **0/5**。

这不等于五项任务都获得了同样完整的正式失败证明：

- 任务 2、3、4 有当前版本、真实 GUI、外部 oracle 的正式失败记录；
- 任务 5 本轮从可见 Electron 客户端口头输入“必须使用 GUI”，真实操作 QQ 后失败；
- 任务 1 没有严格等价的 Krita→保存→QQ 发送正式运行，只有近似绘画任务的超时失败证据，因此只能记“未通过、正式覆盖缺失”，不能伪装成已经完整测试。

根因不是这些任务本身异常困难，也不是模型完全不会看图，而是当前产品路径同时存在以下四个结构性缺口：

1. **桌面工作区能力缺失**：运行锁死一个启动时前台 HWND，不具备人类式的应用发现、打开、切换、弹窗接管和跨窗口任务连续性。
2. **通用动作面残缺**：当前 vNext 主路径主要只有点击、双击、滚动、输入、热键和后来补入的拖拽；绘图路径、鼠标按下/抬起、等待异步变化、工件保存/选择、应用打开/聚焦等能力不可统一到达。
3. **长任务没有语义工作状态**：系统没有可靠保存“当前阶段、已覆盖对象、已抽取事实、工件、待办和退出条件”，同时历史还主动删除文本、坐标和意图。
4. **不确定性被误判成违规**：正常弹窗、焦点变化、过时观察、诊断日志失败和模型偶发错误会穿过多层 fail-closed gate，被提升成整项任务终止。

与此同时，项目已经为控制和证明上述不完整能力建立约 8.4 万行彼此重叠的 GUI 执行层。系统呈现的是：

> 全局架构过度、局部基本能力不足；证明动作的代码很多，完成任务的状态和动作很少。

本轮没有再增加一个新 planner 或上万行框架，只落地两个从真实 GUI 失败直接推导出的通用修复：

- 最大化窗口包含屏幕外不可见 resize border 时，绑定其可见区域，而不是在首帧前终止；
- 输入被证明“尚未执行”时，诊断证据写入失败不再杀死整项任务，而是重新观察后继续；输入已执行或结果未知时仍保持 fail-closed。

这两个修复让任务 5 从“0 次模型调用、0 次动作”推进到真实 QQ 窗口上的 27 次有效 GUI 输入，随后暴露出真正的下一层瓶颈：没有跨群覆盖状态、摘要记忆和停止判断。它们是架构重置的第一小步，不代表任务已经成功。

## 2. 证据规则

本报告只按以下优先级判断成功：

1. 用户可见结果或独立外部 oracle；
2. 绑定准确 run ID 的真实 GUI trace 和屏幕事实；
3. 产品代码实际调用链；
4. 内部 `done`、claim、单元测试和文档只作辅助，不可单独证明任务成功。

今后的每次正式运行必须产生不可变 run manifest，至少包含：

- commit / dirty diff 标识；
- 操作系统、DPI、显示器、语言、应用版本和登录态；
- 原始用户提示词和其中的执行约束；
- 初始窗口/进程状态；
- setup ID、run ID、trace ID；
- 外部 oracle 版本及结果；
- 是否发生外部发送、删除、购买等不可逆行为；
- 终态、动作数、耗时、恢复次数和失败分类。

仓库中同一个任务已经出现文档描述与机器报告互相矛盾的情况。正式统计必须以 manifest + oracle 为唯一真相，不能再由说明文档追认成功。

## 3. 五项真实任务结果

| 任务 | 严格完成所需能力 | 真实结果 | 直接阻塞点 |
| --- | --- | --- | --- |
| 1. Krita 画彩色猫、浅黄背景、保存并用 QQ 发手机 | 打开/聚焦 Krita、连续绘图、颜色/画布操作、保存工件、切换 QQ、选附件、确认发送 | **未通过；等价正式 run 缺失**。近似的详细绘画任务 182.18 秒超时，内部出现 3 次完成声称，但没有证明 Krita、浅黄背景、保存或 QQ 发送 | vNext 没有完整绘图路径和工件/跨应用闭环；最终发送属于一次外部代表性行为，应只在“附件已选好、即将发送”时确认 |
| 2. 当前网页答完所有题 | 视觉读题、滚动、填写、覆盖所有题、提交、结果 oracle | **0 次通过**。最近正式 run `arp-20260729-141348-7f42dd` 执行 9 次物理 GUI 动作后连续产生无效提案，恢复耗尽，终态 `invalid_proposal`；网页 oracle 显示未完成、未提交 | 没有题目覆盖状态；单动作协议和恢复层放大格式错误；最终帧无法重建长过程 |
| 3. 画板井字棋，黑 O 后用红色画 X 并等待下一手 | 棋局理解、正确格选择、连续路径绘制、颜色状态、异步等待和重新观察 | **0 次通过**。最近正式 run `arp-20260729-143230-bf9d0b` 用满 30 轮；oracle 期待中间格，检测到红色轨迹但没有有效 X，并发现错误区域红色 | 有鼠标动作不等于具有绘图原语；像素变化被误当进展；缺少等待用户下一手的事件语义 |
| 4. 网易云搜索梁静茹、播放第一首、音量 5% | 打开/聚焦应用、搜索、列表选择、播放状态、音量控制和多项 oracle | **0 次通过**。正式 run `arp-20260729-135144-143e4f` 有 3 次点击后模型请求 `BadRequestError`，终态 `actor_error`；外部 UIA/CoreAudio oracle 均为假 | 一次模型错误不可恢复；目标/窗口切换能力不可靠；音量必须明确为应用内或 Windows 会话音量 |
| 5. 查看所有 QQ 群最近消息并汇总，口头要求必须 GUI | 口头约束理解、群列表枚举、逐群覆盖、消息抽取、临时语义记忆、最终文本输出 | **0 次通过**。修复窗口绑定后，run `arp-20260729-160636-4a880c` 完成 27 次有效输入（5 click、22 scroll），30 次模型调用后预算耗尽，未 claim、未验证、无汇总 | 只有机械 GUI 循环，没有“已看群/未看群/每群事实/结束条件”；长尾连续滚动仍被像素变化记作进展 |

### 3.1 口头 GUI 要求成为唯一输入

历史实验曾把客户端选择器保持在 **Auto**，只在自然语言提示词中写：

> 一定要用 GUI 完成，不要使用命令行、API 或软件专用适配器。

run `arp-20260729-160636-4a880c` 证明原始目标能完整到达 Visual Actor，也证明额外选择器没有帮助 Agent 理解任务。2026-07-30 已把选择器及其请求、准入、Broker 和证据状态链完整删除。

正确设计不再产生第二份 `ExecutionIntent` 或 `interaction_mode`。原始提示词是唯一真相；同一个语义 Agent 在看见当前画面和全部允许工具后选择 GUI、命令行或 provider。当前 live vNext 尚只有 GUI/control 工具，所以“自主混合选择”仍是未实现能力，不能用隐藏默认值伪装完成。将来增加其他工具时也不增加关键词 router、预检 Agent 或新的用户开关。

## 4. 仓库、分支和增量规模

### 4.1 仓库基线

`master` 审计快照：

- 689 个 tracked files；
- 约 10.98 MiB；
- 约 301,240 行文本；
- Python 约 251,884 行 / 482 个文件；
- TypeScript 约 20,497 行 / 76 个文件；
- TSX 约 8,760 行 / 42 个文件；
- pytest 收集到 5,749 个测试，用时约 18 秒；
- 测试和验证基础设施约占仓库 37.5%。

五套主要 GUI 控制路径合计约 84,015 行：

| 路径 | 规模（约） | 当前职责重叠 |
| --- | ---: | --- |
| `winpilot/agent` | 25,473 | 模型循环、工具、恢复、完成、状态 |
| `winpilot/vnext` | 21,448 | mission contract、broker、evidence、runtime、model roles |
| `winpilot/tasking` | 19,404 | task contract、interaction stage、阶段执行 |
| `winpilot/fast` | 11,090 | controller、subgoal、快速执行 |
| `winpilot/orchestrator` | 6,600 | admission、graph、live canary、run 编排 |

最大的核心文件也跨越多个 owner：

- `agent/loop.py`：7,307 行；
- `tasking/interaction_stage_runtime.py`：3,941 行；
- `fast/controller.py`：3,754 行；
- `fast/subgoal.py`：3,584 行；
- `tasking/interaction.py`：3,530 行；
- `vnext/mission/contracts.py`：2,971 行；
- `orchestrator/vnext_live_canary.py`：2,964 行；
- `action/executor.py`：2,719 行；
- `recorder/trace.py`：2,655 行；
- `agent/done_verifier.py`：2,320 行。

vNext 产品代码约 21,448 行，而 vNext 测试约 27,225 行。仅 7 个 contract 文件约 8,026 行，占 vNext 产品代码 37.4%；`model_roles` 只有约 1,054 行。系统主要复杂度不在“智能角色”，而在契约、确定性 owner 和证明层。

### 4.2 最近增量

- 2026-07-14 到本次基线：368 个文件，约 `+144,359/-23,064`；
- 最近四天：140 个文件，约 `+74,578/-845`；
- `codex/vnext-gui-e2e-foundation` 相对 `master` 只有 1 个提交，却是 62 个文件、`+10,596/-1,069`；
- 该提交约 78.3% 是测试和 harness，产品代码约 19.5%；
- 其中 QQ 群、网易云和画板游戏三个专用 harness 就分别约新增 1,217、1,121、863 行。

这解释了“每轮加很多代码但真实能力没有同比增长”：大部分增量在为单例建立 setup、证据和适配层，不是在增加通用 Agent 可调用的能力。

### 4.3 本地分支

相对 `master` 的左右提交数：

| 分支 | master 独有 | 分支独有 | 判断 |
| --- | ---: | ---: | --- |
| `master` | 0 | 0 | 文档规定的唯一长期主线 |
| `codex/vnext-gui-e2e-foundation` | 0 | 1 | 当前万行 E2E 基础提交 |
| `codex/lean-gui-agent-core` | 0 | 1 | 本次整改分支；另有未提交的小改动 |
| `codex/vnext-agent-core` | 90 | 193 | 大幅分叉的另一套主线 |
| `fix/pr10-python-ci` | 90 | 194 | 在大分叉上继续演化 |
| `recovery/c4-canonical-from-d418-local` | 41 | 1 | 恢复分支 |
| `recovery/c4-fa2e9caf-local` | 43 | 2 | 恢复分支 |
| `test/pr15-legacy-electron-oracle` | 87 | 2 | 旧基线测试分支 |

另有 61 个 tag，其中 52 个是 backup tag。问题不只是“分支多”，而是两条相差近 200 个提交的能力线同时存在，造成：

- 同一通用动作在不同路径有不同 schema 和行为；
- 成功/失败证据落在不同版本；
- 修复无法确定应该进入哪个唯一 owner；
- recovery/tag 保留了历史，但没有提供可执行的能力迁移清单。

不应直接删除这些分支。先生成唯一提交和能力清单，把仍有价值的通用能力以小 PR 迁到唯一主线，再归档。

## 5. 当前产品的实际调用链

```text
用户在 Electron 输入自然语言目标
  → Electron 最小化，短暂等待
  → 捕获当时的前台窗口
  → POST /api/v2/goals/runs
  → Goal Admission 固定选择 live_gui / vNext
  → 三节点图：Start → 一个 full-goal → End
  → 建立初始窗口身份与因果 DesktopWorkspace
  → Visual Actor 每轮必须恰好输出一个 tool call
  → MissionRuntime / Broker / MutationPermit
  → AtomicDesktopAdapter / RawDevice
  → 当前权威帧、逐动作窗口权限、真实输入和回执
  → 已完成动作后接管因果后继窗口
  → Actor claim
  → 确定性校验 claim 与 run-scoped evidence 的结构一致性
  → ARP 终态
```

这条路径的关键事实：

- live vNext 当前只有 Visual Actor 一个模型角色；独立视觉 verifier 和第二次 provider 调用已经删除；
- graph 只有一个 `full-goal` obligation，最大访问一次，`fail_fast`，shell 关闭；
- DesktopWorkspace 可以接管由已完成动作造成的前台后继窗口，但尚不等于具备完整的应用发现、启动和任意抢焦点恢复能力；
- Actor 每轮只能做一个动作或 claim/blocked；
- Actor 持有有界任务内 scratchpad 和上一动作事实，runtime 不替模型推断任务级进展；
- 内部 `mission_verified` 只证明 Actor claim、当前帧和运行证据在结构上闭合，不证明真实任务成功；正式成功仍只认独立外部 oracle；
- mission 状态主要在内存，进程重启后只能标记 interrupted，不能从语义 checkpoint 恢复。

## 6. 为什么“简单任务”会系统性失败

### 6.1 旧基线锁死一个 HWND；当前 workspace 仍不完整

任务 1 至少需要 Krita→保存对话框→QQ；任务 4 可能需要启动/聚焦网易云和音量面板；普通菜单和拥有者弹窗也会改变命中窗口。

旧基线把启动时前台窗口冻结为整个任务的安全边界。于是：

- 打开菜单可能触发 `hit_test_changed`；
- 文件对话框、新应用、系统面板可能被视为 target mismatch；
- 焦点被通知或登录弹窗抢走时，系统既不能接管也不能安全恢复；
- 用户说了目标应用名，Goal Admission 也不会根据目标检查当前窗口是否正确。

当前 `DesktopWorkspace` 已能在真实动作完成后接管稳定的因果前台后继窗口，解决了其中一部分结构性不可达；但它仍不负责发现或启动任意目标应用，也没有把所有弹窗、文件对话框和非因果抢焦点恢复统一打通。

真实人类保持的是“任务工作区和因果连续性”，不是“整个任务永远在同一个 HWND”。

### 6.2 原语不应继续扩张，协议层必须一致

模型面对的桌面动作冻结为六个：

- `click`
- `double_click`
- `drag`
- `scroll`
- `type_text`
- `press_key`

`observe` 是每轮输入，不是动作；窗口发现和因果接班是 runtime 内部机制，不是教模型的新工具；打开应用、保存文件、选择工件和恢复焦点应优先由模型组合现有键鼠动作完成；shell 是用户允许时的另一执行通道，不是 GUI 原语；等待是循环调度状态，不再额外造一个桌面动作。

只有随机化基准证明六个动作无法表达某类物理交互时才扩展协议。画布连续轨迹应优先泛化现有 `drag` 的载荷，而不是同时增加 path、mouse-down、mouse-up 等互相重叠的工具。

本轮真实任务还发现 tool schema 允许单个 `Win` 键，而底层 `HotkeyChord` 拒绝 modifier-only 输入。修复方式是统一既有 `press_key` 契约，而不是再增加“打开开始菜单”宏工具。这类多层重复校验很容易产生“上层允许、下层拒绝”。

### 6.3 没有长任务所需的语义工作记忆

任务 5 是最清晰的证据：

- GUI 输入本身 27/27 都成功到达 OS；
- 系统仍然只在首个群附近持续滚动；
- 没有群列表快照、已访问集合、每群摘要、下一群选择和退出条件；
- 30 轮后 mission 仍是 `running`。

把所有历史动作留给模型重新从截图推断，既昂贵又不可靠；再删除历史中的文本、坐标和意图后，长任务几乎不可能稳定完成。

需要的是短小、临时、可修改的 `TaskState`，而不是另一个确定性 planner：

```text
goal
interaction_constraints
current_objective
covered_items / remaining_items
facts
artifacts
pending_external_effect
last_action_receipt
stop_condition
```

它只在本次任务生命周期和 checkpoint 中存在；默认不进入跨任务长期记忆。

### 6.4 “像素变了”不等于“任务有进展”

当前 action-effect 的像素阈值为 0，任何对齐后非零变化都可能标记 `changed`。光标、动画、时间、滚动回弹和通知都能掩盖无进展。

正确的进展判断必须由语义状态变化给出，例如：

- `covered_items` 增加；
- 答题完成数增加；
- 工件出现并保存成功；
- 播放状态从 stopped 变为 playing；
- 棋局出现合法新落子。

像素变化只证明“画面发生了变化”，不能刷新任务无进展预算。

### 6.5 证明层反向控制了执行层

真实井字棋运行中，点击颜色弹窗后下一次 proposal 因目标过时被拒绝，且输入明确没有执行；但记录“拒绝证据”本身失败，导致整项任务终止。

正确语义应是：

| 输入状态 | 处理 |
| --- | --- |
| 明确未开始 | 丢弃 proposal，重新观察，可记录诊断但诊断失败不终止 |
| 已执行 | 必须记录回执并从新画面恢复，不能盲目重放 |
| 结果未知 | fail-closed，要求人工或可靠 reconciliation |

证据系统应该忠实描述任务，而不应因为自身非关键写入失败制造一个新的任务失败。

### 6.6 一次模型/格式错误被放大

任务 2 的 invalid proposal 和任务 4 的模型 HTTP 400 都成为终态。通用 Agent 面对不确定 UI 时必然偶尔误点、输出格式错误或遭遇瞬时服务错误。能力不应建立在“每一轮都完美”之上。

恢复边界应小而统一：

- 格式或参数错误：不静默改写成合法动作；保持桌面未变并记录模型失败；
- retryable 模型错误：指数退避并保持 checkpoint；
- 非 retryable provider 错误：换同能力 provider 或向用户报告基础设施失败；
- 明确未执行的 stale action：刷新观察；
- 已执行后的意外画面：reconcile，不自动重放；
- 重复动作后仍只提供 fresh frame 与真实回执，由模型自行决定下一步，不由 runtime 教授反思或换策略。

## 7. 多角色、隐私和安全的准确判断

### 7.1 不是“智能角色太多”

当前 live vNext 只有 Actor 一个真正的模型角色。问题是：

- AgentRunner、TaskContractCompiler、StageRuntime、InteractionStageEngine、SubgoalExecutor、Controller、Broker、MutationPermit、Adapter、Evidence Reducer、Done Gate 等确定性 owner 都保留了部分解释权；
- 旧 agent/tasking/fast 和新 vNext 并存；
- 每层都在限制下一层，却没有一个 owner 对“完成整个桌面任务”负责。

因此应删除的是重复 owner 和状态，不是再设计更多“经理、规划师、审计员、执行员”模型角色。

默认只需要一个语义 Agent 循环。当前确定性完成校验只核对 claim 与运行证据的结构一致性，不解释画面；setup/oracle 属于测试系统，不属于生产 Agent。

### 7.2 隐私安全不是主要 LOC，却造成跨层语义伤害

按本次粗粒度统计，显式 safety/capability/mutation 相关代码约 3,001 行，约占仓库 2.7%。所以不能说隐私安全“占了绝大多数代码”。

更准确的问题是影响范围：

- 历史删除文本、坐标、意图，破坏长任务记忆；
- mutation authority 和 evidence gate 在多个 owner 中重复 fail-closed；
- 正常窗口变化被当作安全异常；
- 诊断/审计失败可以覆盖真实输入事实；
- 为最坏情况建立的限制默认施加到读消息、浏览页面和本地绘图。

安全边界应放在**外部不可逆动作发生前**，而不是平均铺在每一次鼠标移动上：

- 读消息、导航、切换窗口、本地绘图：无需确认；
- 选择附件、填写待发送内容：可继续；
- 最后点击 QQ 发送、对外发消息、购买、删除：动作时一次确认；
- 用户在原始提示词中已经明确授权的同类可逆动作，可按产品策略减少重复确认；
- 输入结果未知时仍必须保守处理，不能以“体验”为由自动重放。

隐私策略也应按边界分层：

- **任务内临时状态**：允许保存完成任务所需的语义事实；
- **本地诊断 trace**：参数可脱敏，但不能删除恢复所需的运行态；
- **跨任务长期记忆**：默认不保存敏感内容；
- **发送到外部模型/服务**：按用户与部署策略最小化。

## 8. 目标架构：Lean Desktop Agent

```text
原始用户目标
  ↓
一个 Semantic Agent Loop
  ├─ 解释口头交互约束
  ├─ 维护紧凑临时 TaskState
  └─ 每轮选择一个通用动作
  ↓
DesktopWorkspace
  ├─ 当前桌面和可见窗口
  ├─ list/open/focus/adopt
  └─ 窗口因果谱系与预期 transition
  ↓
Observer → 当前权威帧
  ↓
Universal Action Executor
  ↓
ActionReceipt: not_started | completed | unknown
  ↓
Reconcile 预期窗口/画面变化
  ↓
更新 TaskState + checkpoint
  ↓
继续、请求用户、确认外部动作或输出结果
```

### 8.1 唯一职责

#### Semantic Agent Loop

- 持有完整原始目标；
- 从原提示词解释 GUI-only / mixed 等约束；
- 看当前画面和 TaskState；
- 选择一个通用动作；
- 更新覆盖、事实、工件和停止条件；
- 不直接调用 Win32，不实现应用专属流程。

#### DesktopWorkspace

- 维护多个窗口，而不是冻结一个 HWND；
- 发现、打开、聚焦、恢复、接管窗口；
- 用进程、父/owner、启动时间、前后窗口快照建立因果谱系；
- 允许模型预期的菜单、弹窗、文件对话框和跨应用切换；
- 意外窗口出现时返回新观察，不直接宣判任务失败。

#### Observer

- 原子捕获桌面或目标窗口帧及坐标空间；
- 提供可选局部高分辨率 crop；
- 不解释任务、不判断完成。

#### Universal Action Executor

- 只执行一笔规范动作；
- 返回 `not_started / completed / unknown`；
- 不规划、不重试语义动作、不判断任务完成。

#### Checkpoint

- 保存原始目标、口头约束、TaskState、窗口谱系、最后回执和工件引用；
- 可在进程重启后恢复；
- 不默认写入长期个性化记忆。

### 8.2 不再保留的默认层

- 不再为普通目标先编译任务族脚本；
- 不再用一个 full-goal obligation 套所有长任务；
- 不再让 planner/compiler/stage/subgoal/controller 各自维护一份进度；
- 不再让证据 broker 决定未发生输入的任务命运；
- 不再默认保留两套或三套 GUI runtime；
- 不再把具体应用名、按钮位置和测试句子写入生产路由。

合法的领域能力可以存在，例如“QQ 发送属于外部代表性行为”或“音量是 Windows 会话量”，但领域合同不应变成固定坐标脚本。

## 9. 迁移、删除和小 PR 计划

### Phase 0：冻结扩张，建立可信基线

目标：1 周。

- 暂停新增 task family、stage、ledger、broker 和应用 adapter；
- 新增数据化 benchmark manifest，五项任务只描述 setup、prompt、随机化和 oracle；
- 所有 runner 复用一个驱动；任务专用代码只允许在 setup/oracle；
- 固化本报告的 0/5 基线；
- 产出分支唯一提交与通用能力迁移清单。

退出条件：

- 任意 run 能由 commit + manifest + run ID 唯一复现；
- 文档不能覆盖机器结果；
- 没有新增生产应用特判。

### Phase 1：动态 DesktopWorkspace

目标：2–3 周，分 3–5 个小 PR。

1. 引入最小 `WindowRef` 和 `WindowTransition`，不引入 planner；
2. 把现有单 HWND binding 改为 per-action authority；
3. 支持 owned popup、文件对话框和预期新前台窗口；
4. 在 runtime 内部发现并接管因果后继窗口，不向模型增加 `list/open/focus/adopt` 工具；
5. 让 `/goals/runs` 的小比例流量进入新 workspace。

每个 PR 建议 200–800 行产品代码，特殊情况下总改动不超过约 1,500 行。新增一个 owner 时，必须在同一 PR 停用或删除旧 owner 的对应职责。

退出条件：

- 在没有应用名特判的情况下完成“应用 A 打开文件对话框”和“应用 A→应用 B”两个不同任务族；
- 意外抢焦点可恢复，不会自动误操作；
- 任务 1、4 不再因单窗口结构而“天然不可达”。

### Phase 2：保持六个动作，收紧模型自主管理的 TaskState

目标：2–3 周。

- 冻结 click、double click、drag、scroll、type text、press key 六个模型动作；
- 若随机画布基准确有需要，只泛化 drag 的轨迹载荷；
- 把 TaskState 定义为模型拥有的有界 scratchpad，不让 runtime 推断 covered / remaining 等语义进展；
- 去掉每轮重复的大型行政 schema，TaskState 不作为完成或安全真相；
- 删除基于任意像素变化的无进展重置；
- 删除 runtime 的任务级重复动作判断，让模型从新画面和回执自行改变策略；
- 异步等待由观察循环和调度状态表达，不增加 `wait_for_change` GUI 原语。

退出条件：

- 画布类和表单类任务共享同一 action executor；
- 模型能在不新增宏工具的情况下表达画图、跨窗口、长覆盖和异步等待；
- TaskState schema 不再在每个动作工具中重复一份大型固定结构；
- runtime 中不存在五项任务的应用/题目/棋盘专用分支。

### Phase 3：切换产品入口并停止旧线演化

目标：2 周。

- `/api/v2/goals/runs` 逐步切到 Lean Desktop Agent；
- 旧 vNext 仅保留回放和对照，不再接受新功能；
- 先关闭 TaskContractCompiler 对未知 GUI 的脚本化；
- 再按调用方删除 StageRuntime、Subgoal、重复 controller 和 evidence owner；
- 每次删除都以真实 benchmark 回归保护，不做一次性大爆破。

退出条件：

- 产品只有一个默认 GUI runtime；
- 新功能只需要修改一个动作 owner 和一个状态 owner；
- GUI 控制相关生产代码出现持续净删除。

### Phase 4：分支收敛和遗留删除

- 盘点 `codex/vnext-agent-core` / `fix/pr10-python-ci` 的独有通用能力；
- 小批 cherry-pick 或重写到主线；
- 回放/兼容确有消费者的才保留 adapter；
- 归档 recovery/test 分支；
- 删除失去消费者的旧 AgentRunner/tasking/fast/vNext 路径。

长期目标不是再新增一个 2 万行 `lean/` 目录，而是让新循环接管消费者后，逐步净删约 4 万行以上重复 GUI owner。该数字是方向性预算，必须按实际依赖和回归证明执行，不能为追求数字误删能力。

## 10. 验收门槛

### 10.1 真实可靠性

任务空间是开放且不可枚举的。五项任务只是固定回归探针，不是产品的任务 taxonomy。发布基准同时包含固定探针和按能力轴随机组合的新任务：

- 视觉定位、文本输入、指针轨迹；
- 跨窗口、菜单/弹窗、异步变化；
- 长程覆盖、临时事实和工件；
- 可逆操作与不可逆外部边界；
- 随机窗口位置、DPI、分辨率、延迟、内容、遮挡和通知。

每个固定探针和每组随机能力组合至少运行 20 次；生产代码不得知道应用、题目或答案，具体软件只能出现在 setup 和独立 oracle 中。

- 模型负责应用理解、任务分解、动作序列、恢复策略和停止判断；
- runtime 只负责真实观察、坐标一致性、执行一笔物理动作、动作回执、因果窗口连续性、有界工作记忆及不可逆边界确认；
- 内部 claim 不计成功，只认外部 oracle；
- 每个任务迁移门槛建议先达到 ≥80%，再逐步提高；
- 五项通过只证明第一组长任务回归，不宣称“达到人类水平”。

同时记录：

- 任务成功率；
- 非预期点击/输入/外部发送率；
- stale/抢焦点恢复率；
- 平均动作数和耗时；
- 重复策略率；
- checkpoint 重启续跑率；
- 用户确认次数和确认是否只发生在正确边界。

### 10.2 代码准入

每个 GUI PR 必须回答：

1. 修的是哪个跨任务失败模式？
2. 在第二个不同任务族上如何证明？
3. 唯一 owner 是谁？
4. 新增了什么状态，删除或停用了哪份旧状态？
5. 产品代码净增多少？
6. 外部 oracle 是什么？
7. 是否引入应用名、按钮位置或提示词特判？
8. 是否改变外部不可逆动作的确认边界？

拒绝合并：

- 一个 PR 上万行；
- 为单个任务增加生产宏工具；
- 新 runtime 永久与旧 runtime 双轨；
- 只凭内部 done 宣称成功；
- 测试 harness 借助 DOM/UIA/API 完成 Agent 本应通过 GUI 完成的动作；
- 以更多 fail-closed gate 代替恢复设计。

## 11. 本轮已落地的小切片

工作分支：`codex/lean-gui-agent-core`

### 11.1 可见窗口区域规范化

涉及：

- `python/winpilot_home/winpilot/orchestrator/vnext_live_canary.py`
- `python/winpilot_home/tests/test_vnext_live_canary_composition.py`

真实失败：

- QQ 最大化窗口 Win32 rect 是 `[-10, 0, 1940, 1040]`；
- 其中 10 像素是屏幕外不可见 resize border；
- 原实现要求 exact target screenshot，导致首帧前终止。

修复：

- 将 target rect 与 virtual desktop 求可见交集；
- Electron 提交的 prebound rect 同样规范化；
- 完全不可见窗口仍拒绝。

### 11.2 未执行输入的诊断失败可恢复

涉及：

- `python/winpilot_home/winpilot/vnext/runtime/mission_runtime.py`
- `python/winpilot_home/tests/test_vnext_mission_runtime.py`

真实失败：

- Paint 颜色弹窗出现后旧观察失效；
- adapter 明确证明下一次输入没有执行；
- 记录 rejection evidence 失败却让整项任务 `state_rejected`。

修复：

- `input_completed is False` 时允许丢弃旧 proposal、重新观察；
- 诊断写入错误保留在 detail；
- 输入已执行或结果未知时仍进入原来的保守路径。

### 11.3 因果桌面工作区与真实输入完成

已落地：

- `DesktopWorkspace` 不再把整个任务锁死在启动 HWND，而是在已完成动作后稳定读取前台，并只接管具有因果关系的后继窗口；
- 单键 `Win`、`Alt` 等与组合键使用同一 `press_key` 能力；
- 完整按下并反向释放成功后，即使快捷键改变前台，也把物理输入记为 completed，再由 workspace 接管新窗口；
- 引入有界、仅在任务生命周期内存在的 TaskState；它是模型工作记忆，不是 runtime 进展真相。

没有增加应用发现宏、计算器工具、QQ 工具或任务分类器。

### 11.4 把策略解释权还给模型

已落地：

- 删除 task-level repeated-action suppression；重复点击或滚动是否合理由模型结合新画面和回执判断；
- 模型侧把既有 `hotkey` 准确显示为 `press_key`，内部动作和证据格式不变；
- 删除点击聚焦和滚动停止的微策略提示；runtime 只提供上一动作事实，不替模型规定下一步；
- Electron 控制面在 GUI run 期间退出任务栏和 Alt+Tab，并绑定具体 run；普通提醒不会提前恢复它，匹配的终态或启动失败才恢复；
- 启动 POST 一旦可能已创建 run，就不把客户端超时或后续 UI 绑定失败伪装成“任务未启动”。

当前模型动作仍只有六个：click、double click、drag、scroll、type text、press key。

### 11.5 自动化回归与真实 GUI 结果

当时该切片的相关 Python 回归：

```text
447 passed in 59.60s
```

当时该切片的 Electron 回归：

```text
typecheck passed
goalRunEntry: 1 test file / 8 tests passed
```

同一个随机跨应用任务连续做了三轮真实 GUI 运行：

```text
目标:
记事本已有 CROSS_APP_E2E_START
切到计算器计算 19+23
切回记事本输入 RESULT=42

arp-20260729-181027-cc08f7:
29 次物理输入均 applied；进入计算器后按钮坐标持续看错。

arp-20260729-182900-47e2a5:
30 次物理输入均 applied；模型已会使用 press_key，
但 Alt+Tab 在记事本与 Electron 控制面之间循环。

arp-20260729-183748-b61dc2:
控制面隔离后，成功经过 Start 搜索进入新计算器窗口；
30 次物理输入均 applied，但最终界面为 √(99999)=316.226184874055。
记事本仍只有 CROSS_APP_E2E_START，没有 RESULT=42。
终态: vNext turn budget exhausted。

arp-20260729-190610-bc9e08:
以当前精确源码、单个已打开且清零的计算器再次运行；
窗口只在记事本和计算器之间，动作序列为 1 次 press_key + 29 次 click，
30 次物理输入均 applied。计算器最终显示 73,753,744，
记事本仍没有 RESULT=42。终态仍为 vNext turn budget exhausted。
```

第三、四轮的独立可见 oracle 都明确判定失败。部分运行帧还受到基于屏幕裁剪的遮挡影响，但在无遮挡帧中模型仍持续提交错误坐标。因此当前主瓶颈已经不是动作原语、窗口权限或重复动作 gate，而是视觉模型的坐标 grounding、自我纠错能力以及观察帧质量。

### 11.6 已回退的实验：拆开决策与空间落点

对 `arp-20260729-190610-bc9e08` 的逐帧核验表明：

- 29 次提交图尺寸、窗口区域、DPI 映射和物理坐标换算全部一致；
- 29/29 原始输入回执均为 applied；
- 模型能说出“点击 9”“点击 C”等正确局部意图，却反复给出按钮外、边框或旧按钮的坐标；
- 把同一 JPEG 和一个已经决定的局部意图单独交给同一模型时，模型能把 1、9、C 落到正确按钮内部。

当时没有增加 OCR、控件树、计算器适配器或新动作原语，而是在同一个 `LiveVisualActor` 中做两段式调用：

1. 完整任务上下文只负责选择下一步动作和局部意图；
2. 只有空间动作才用同一 JPEG、动作和局部意图做一次小型 grounding，并只替换坐标。

真实长任务否定了这一拆分。`arp-20260730-030652-453a13` 中，首个 Actor 已给出动作和图像坐标，第二个 Grounder 却连续返回带尾括号的坐标字符串 `image_y='149]'`；结果是 2 个 turn、4 次模型调用、0 次物理动作。它重新查看同一张 JPEG、覆盖已经存在的坐标，并成为确定性单点故障。

因此当前实现已完整删除 Grounder contract、角色、第二次 provider 请求、预算接线和坐标覆盖逻辑，空间 turn 由单个 Visual Actor 直接决定。Broker schema、submitted-image 坐标绑定和 OS 输入前验证仍保留。删除后，同一随机试卷 `seed=1008554491` 在 `arp-20260730-031847-19f493` 中以 16 次真实鼠标键盘动作完成滚屏、6 道混合题和提交，外部服务器判定 6/6 全对；当时尚存的独立视觉 verifier 随后才接受完成声明。

### 11.7 口头 GUI 要求不再产生第二套策略状态

历史代码审计确认：原始目标会完整进入 Visual Actor；当前运行只走 GUI，是因为 live graph 关闭 shell，工具目录也只有 GUI/control，不是因为额外系统开关理解了口头要求。

曾按“让同一基础模型先做二分类”的最小方案做真实探针，但结果否定了该方案：

- 4 秒上限的三个中文样例中两个超时；
- 5 秒时，模型把明确的“必须使用 GUI”错误判成 `unspecified_or_mixed`；
- 额外调用还会消耗 Electron 已捕获目标仅 15 秒的新鲜期。

因此该预检实现已完整回退，没有留下关键词表、第三个 Agent 或死代码。2026-07-30 又继续删除了前端选择器和贯穿请求、准入、Broker、fingerprint、trace 的执行模式状态。当前决定是：

- 现阶段继续由收到完整原始目标的 Visual Actor 遵守口头约束，因为可执行目录本来就只有 GUI；
- 将来真正开放 shell/provider 时，把它们与 GUI 工具同时交给同一个语义 Agent 选择，不再先解析成另一份模式状态；
- 不为这个问题增加独立模型往返、关键词路由器、Task Profiler 或新的用户开关。

### 11.8 第一次可信定位闭环暴露的停止失败

真实 GUI 运行 `arp-20260729-204018-d48d47` 使用 Auto 下拉和原始口头约束“必须使用 GUI”，没有调用 DOM、脚本或命令行代替被测 Agent 操作页面。

外部 oracle 证明物理能力已经打通：

- Agent 先从错误的初始前台窗口用 `Alt+Tab` 切到 Chrome，`DesktopWorkspace` 随后因果接管新的 Chrome HWND；
- 第 2 轮对随机 `precise_click / seed=11` 的紫色六边形完成可信鼠标命中；
- 首次命中 client 坐标为 `(152,455)`，screen 坐标为 `(518,732)`，此前可信失败点击为 0；
- evidence 中 submitted image 坐标 `(192,748)` 加 frame origin `(456,168)` 得到物理坐标 `(648,916)`，再按 125% DPI 映射后与浏览器 oracle 完全一致；
- 在当时尚未回退的两段式实验中，Actor 和 Grounder 独立计数、独立标记，最终 30 个 turn 共 59 次模型调用；这只证明预算账本正确，不证明两角色架构合理。

但任务闭环仍失败：

- 成功文本位于当前 Chrome 视口下方，命中目标本身没有可见状态变化；
- Agent 没有滚动查找完成反馈，也没有提出 claim；
- 它对同一已成功目标又点击 27 次，最终以 `state_rejected / vNext turn budget exhausted` 结束；
- 因而这次运行只能记为“外部任务动作成功、Agent 自身闭环失败”，不能记为完整 E2E 成功。

最初的最小修复没有恢复坐标聚类拦截器，也没有增加等待、检查状态或应用专用原语，而是把 `previous_action.local_visual_change`、`no_progress_streak` 和 `completed_action_summaries` 的解释继续写进 Visual Actor 契约。后续真实动态长按表明这仍然不可靠：模型可以在清楚显示成功的 fresh frame 上忽略长契约并继续重放。84 词短契约是中间实验，当前又继续收缩为 45 个英文词，只保留接口事实，不再教授停止、重试或重新瞄准策略。

trace 还暴露了过度隐私裁剪造成的语义断裂：模型在成功 turn 中生成的 `local_intent` 是“点击唯一带白色中心的紫色六边形”，下一 turn 却只收到泛化后的 `click`。`local_intent` 已经发送给同一 provider 并保存在同一 run 的私有 decision record，因此现在只把上一条有界 intent 回传给下一 turn；不建立第二份历史账本，也不把坐标、输入文本或整段私有 evidence 放进长期状态。该变化恢复短期语义连续性，判断权仍属于模型。

短期 `local_intent` 回传仍保留；完成停止则不再依赖逐条活性提示。只有“可信外部成功 + Agent `mission_verified`”同时成立才算闭环修复完成。

### 11.9 从“每次都松手”到同一 drag 的跨帧生命周期

真实运行先给出了两种性质不同的结果：

- `arp-20260730-004648-007b80` 在随机 `corridor_drag` 中完成 3 次真实物理 drag，外部 oracle 成功，Agent 随后 claim 并得到当时尚存的独立视觉验证。这是当前架构第一次同时满足“可信外部成功”和 `mission_verified` 的完整 GUI 闭环；
- `arp-20260730-004951-a6890e` 在 `dynamic_hold` 中连续提交 29 次 drag。模型多次正确到达 checkpoint 和后来出现的 goal，但旧 drag 每一轮都会 `DOWN → MOVE → UP`，外部 oracle 最终记录 29 次按下，任务不可恢复。

第二个结果不是模型不会理解页面，而是动作生命周期表达不了“保持按下、观察新画面、继续移动、最后释放”。修复没有新增第七个模型动作，也没有增加 `mouse_down`、`mouse_move`、`mouse_up` 三个可随意组合的公开原语，而是在现有 `drag` 上增加两个可选参数：

- `image_points`：同一帧内的有序折线路径；
- `keep_holding`：当前段结束后是否保持同一次按下。

内部状态只有三段：released → BEGIN、held → CONTINUE、held → FINISH。lease 只由 Adapter/RawDevice/Broker 持有，模型只看到一个 `pointer_held` 布尔值；held 时 Broker 只暴露现有 `drag`，不会让模型夹入点击、键盘或滚轮。RawDevice 用进程级 owner 阻断其他输入，异常、非 completed 转换、会话关闭和 300 秒紧急超时都会尝试释放。`execute` 与 `close` 使用同一生命周期锁，cleanup 未确认时不会把 Adapter 伪装成已关闭，并有一次有界重试。

这是一条通用的鼠标生命周期能力，不包含 checkpoint、goal、网页或具体应用规则。

但一个概念上很小的生命周期仍传播到 schema、Broker、Envelope、Runtime、Evidence、Adapter 和 RawDevice，这本身说明当前 owner/合同层仍然偏多。后续应收敛这些 owner，而不是为每一种新手势再复制一套跨层合同。

### 11.10 随机 dynamic_hold 的真实闭环结果

当前精确源码通过可见 Electron 客户端提交了一个新随机任务：

```text
用户输入:
必须使用GUI和真实鼠标完成当前界面上的测试。

客户端执行约束选择器:
auto

case:
client-dynamic-hold-3f1cf7399e

seed:
1952988860

run:
arp-20260730-020157-a14ddb
```

因此 GUI 要求来自用户口头提示，不是测试脚本把隐藏执行约束设成 `gui_required`。被测 Agent 没有使用 DOM、页面脚本或 API 完成动作；setup 和 oracle 在 Agent 之外只负责生成随机页面和读取可信浏览器事件。

实际动作与外部事实：

- 第 1 个 drag：`started_held=false / keep_holding=true / segment_phase=begin`；
- 第 2 个 drag：`started_held=true / keep_holding=false / segment_phase=finish`；
- 浏览器只收到 1 次可信 `pointerdown`、76 次移动和 1 次可信 `pointerup`；
- checkpoint 在按下后到达，goal 随后才出现，最终释放点在 goal 内；
- 工具序列只有 `drag, drag, claim_obligation_done`；
- Runtime 用 3 个 turn、6 次模型调用结束为 `mission_verified`；
- runtime evidence journal 为 `complete`，14 个事件，0 个缺失证据，0 个验证失败；
- 独立浏览器 oracle 和客户端执行证据都判定通过。

随后又在不观察、不激活窗口干预被测过程的条件下运行第二个随机布局：`case=client-dynamic-hold-93a18a512f`、`seed=1862003966`、`run=arp-20260730-021252-b3c3b0`。结果仍为 2 次 drag 加 claim、1 次可信按下、79 次移动、1 次可信抬起，最终 `mission_verified`；evidence journal 同样是 14 个事件、0 缺失、0 验证失败。当前新实现的随机 `dynamic_hold` 实测为 2/2，但样本仍远不足以声称高可靠率。

这证明新架构已经跨过一个此前结构上不可能完成的动态 GUI 闭环，但不能外推为“通用桌面能力已达到人类水平”。目前只验证了 corridor 和 held-pointer 两种 gesture 能力；任意长任务、跨应用恢复、遮挡、弹窗、细粒度视觉定位和多小时稳定性仍需按能力轴随机测试。下一步不应增加任务宏，而应扩大随机布局/窗口扰动/组合任务的样本量，并以外部 oracle 统计成功率、恢复率和无泄漏率。

### 11.11 删除教学式长契约后的跨任务回归

后续运行又暴露出两个相互关联的闭环问题：

- 删除前的历史 run `arp-20260730-034540-3d75f9` 已以一次真实按下完成动态长按，Actor 也在成功帧上停止，但独立视觉 verifier 连续三次拒绝。根因是同一句口头目标同时充当视觉验收条件：当前 JPEG 能证明页面成功，却不能证明历史输入来自“真实鼠标”；运行时已经保存的物理回执没有进入该视觉判断。
- 给 verifier 明确“历史 GUI 输入看运行时动作摘要、可见终态看 JPEG”后，下一次随机窗口在第 2 个 drag 后已清楚显示 `DYNAMIC_HOLD_SUCCEEDED`，Actor 却无视原有长系统契约并继续执行 18 个 drag，最终破坏外部一次按下约束。

没有再增加完成检测器、OCR 关键词、应用 recipe 或第三个角色。当时先把 Visual Actor 契约收缩为 84 个英文词；随后进一步删到当前的 45 个词，只描述原始目标、权威 JPEG、TaskState、上一动作、提交图坐标、完成 claim 和 held pointer 接口。旧的窗口/标签教学、无进展解释、动作历史解释、重复策略说明以及当前模型输出数字字符串的静默修复全部删除。

在当时的同一精确源码、Auto 执行约束和用户口头 GUI 要求下，随后四个独立 GUI E2E 全部同时通过运行时与外部 oracle：

| case | seed | run | 实际工具序列 | 外部结果 |
|---|---:|---|---|---|
| dynamic_hold | 1548018253 | `arp-20260730-040524-847cb4` | `drag, drag, claim` | 1 down / 77 moves / 1 up，checkpoint 与 goal 均通过 |
| dynamic_hold | 1518325382 | `arp-20260730-040701-b3a1e3` | `drag, drag, claim` | 新随机布局，1 down / 77 moves / 1 up |
| answer_all_questions | 1639678656 | `arp-20260730-040908-7f5ffe` | 12 个 GUI 输入加 claim | 随机 6 题、混合文本与单选、滚屏、服务器 6/6 |
| precise_click | 1180824091 | `arp-20260730-041121-986e22` | `click, claim` | 62×62 随机目标，一次可信鼠标命中 |

这组结果支持“减少提示、保留模型判断”的方向。独立视觉 verifier 随后已经删除：当前每轮和终局都不再为同一 JPEG 增加第二次模型调用，也没有引入替代角色。

### 11.12 当前精确源码与模型精度边界

本轮对最新坐标链做了逐点核验。提交图 `1120×595` 到物理帧 `1920×1021` 的实际换算是：

```text
x = round(image_x * 1919 / 1119)
y = round(image_y * 1020 / 594)
```

在答题 run `arp-20260730-053228-c92a41` 中，六题均已选择，最后一题“北京”也正确；随后模型连续给出提交按钮坐标 `y=518/519`。同一批权威 JPEG 中按钮可见下边界约为 `y=514/515`，所以落点在进入坐标转换前已经位于按钮下方。物理执行坐标与公式逐点完全一致，不能用 DPI、标题栏或 SendInput 偏移解释。该 run 位于最后一次“删除提示条款与数字字符串静默修复”之前；坐标和物理执行链未随那次删除改变，所以它只用于失败归因，不冒充当前精确源码的通过样本。

因此没有加入全局 offset、按钮中心吸附、控件特判、重复点击重瞄规则或更长提示词。它们只会掩盖当前视觉模型的定位误差，并可能在替换为更准确模型后把正确坐标移错。

当前精确源码的两项独立 GUI 校准均通过：

| case | seed | run | 可信外部结果 |
|---|---:|---|---|
| precise_click | 424242 | `arp-20260730-055053-665c34` | 一次真实鼠标点击命中，浏览器记录 `client=(720,224)`、`screen=(719,246)` |
| dynamic_hold | 161803 | `arp-20260730-055310-92f4fd` | 1 次按下、80 次移动、1 次抬起；先到 checkpoint，目标出现后在目标内释放 |

答题失败仍按失败记录，不用上述两个基础能力通过来覆盖它。正确边界是：

- 基础设施负责提交图与物理屏幕坐标的一致、真实输入、按住生命周期和可信回执；
- 模型负责从图像选择语义目标和精确落点；
- 模型精度不足通过可替换模型基准暴露，不进入 runtime 补偿；
- 若未来发现同一正确提交图坐标被稳定执行到错误物理位置，才修坐标基础设施。

当前工作树在 2026-07-30 删除执行策略链后的非 GUI 回归：

```text
Python winpilot_home: 3367 passed / 13 skipped / 0 failed
Python 根目录独立套件: 2508 passed / 0 failed
TypeScript/Vitest: 22 files / 224 tests passed
TypeScript typecheck、renderer/main/CLI/AI build: passed
```

13 个 skipped 不被包装成通过；真实 GUI 结果以 13.7 的当前源码运行记录为准。

## 12. 立即决策

1. 冻结六个模型动作；不新增 wait、open-app、focus、save、calculator、QQ 等宏工具。
2. 把观察层改成无遮挡、坐标空间诚实一致的窗口/桌面帧；这是传感器正确性，不是教模型软件流程。
3. 建立随机坐标与控件定位基准，先比较可替换视觉模型；没有通过定位基准的模型不得进入长任务 E2E。
4. 把当前固定字段 TaskState 继续收缩成模型拥有的有界 scratchpad，停止在每个工具中复制大型进展 schema。
5. 五项任务保留为固定 probes，同时按能力轴生成随机组合任务；setup/oracle 可以软件相关，生产 runtime 不得出现 app/task recipe。
6. 逐步让单一 runtime 接管入口，并在同一批小 PR 中停用旧 owner；目标是持续净删，不再新增一个长期并存的大目录。
7. 口头“必须 GUI”最终应由同一个语义 Agent 从原始目标理解并遵守，不增加关键词路由器，也不要求用户再点系统开关。
8. 安全只放在不可逆外部边界；读取、导航、绘图、切换窗口和准备工件不应被普遍打断。
9. 模型视觉定位或 schema 精度不足优先通过模型替换解决；runtime 不增加坐标补偿、目标吸附或当前模型专用输出修复。

历史切片曾完成随机 `corridor_drag` 和跨帧 `dynamic_hold` 两个可信 GUI 闭环，但仍没有达到通用可靠或人类水平；13.7 的新随机样本再次证明单次通过不能继承为当前成功率。下一步应继续比较视觉模型，并把同一精简动作面放进更大规模的随机组合长任务；继续扩充提示规则、动作工具或应用适配器只会重复过去的失败。

## 13. 2026-07-30 最新落地状态

### 13.1 可恢复状态与本轮规模

本轮先把完整架构重置状态提交并推送为恢复点：

```text
branch: codex/lean-gui-agent-core
checkpoint: 6ab5a9d checkpoint lean GUI agent architecture
remote: origin/codex/lean-gui-agent-core
```

随后在该恢复点上继续净删除前端执行策略链。产品、测试和脚本 diff 为
`+115/-699`；新增文字主要是把原则、当前失败和恢复方法写入文档。没有增加
planner、模型角色、动作原语或应用适配器。

### 13.2 前端执行策略链已完整删除

删除范围不是隐藏一个下拉框，而是整条重复意图链：

```text
ExecutionConstraintSelector
  -> renderer state
  -> request.execution_constraint
  -> GoalExecutionConstraint
  -> GoalAdmissionAudit.execution_constraint
  -> InteractionMode
  -> BrokerRuntimeState fingerprint
  -> Broker policy branches
  -> GUI-mode trace/evidence fields
  -> E2E selector automation
```

当前 `POST /api/v2/goals/runs` 只接收 `goal + execution_context`。用户口头
强调 GUI 时，这句话不会被复制为第二份结构化模式。当前 live vNext 仍只有
GUI/control 工具，因此混合 GUI/CLI 自主选择尚未实现；后续必须由同一个
Agent 在同一工具面直接选择，不能再增加前端开关、关键词 router 或预检模型。

### 13.3 坐标责任边界

坐标验收只检查以下等式：

```text
模型提交的 image 坐标
  -> 唯一的 submitted-image/frame 映射
  -> evidence 中的 physical_arguments
  -> RawDevice 实际收到的物理坐标
```

现有原子 Adapter 回归已对 click、double-click、drag、带路径 drag、scroll
逐点比较提交值和物理执行值，共 7 个动作形态通过。测试不读取目标 bbox，
不判断按钮中心，也不做 offset、snap、重瞄或任务答案修正。目标是否命中只由
独立 E2E oracle 判断；若映射一致但未命中，归因到模型视觉定位或观察质量。

### 13.4 分支收敛门

恢复点 `6ab5a9d` 的分支关系快照（相对当时的
`codex/lean-gui-agent-core`；后续提交只会增加当前分支独有计数）：

| 分支 | 当前分支独有 | 对方独有 | 处理 |
| --- | ---: | ---: | --- |
| `master` | 2 | 0 | 已是祖先，无代码可合并 |
| `codex/vnext-gui-e2e-foundation` | 1 | 0 | 已是祖先，无代码可合并 |
| `codex/vnext-agent-core` | 92 | 193 | 禁止整体 merge；逐能力审查 |
| `fix/pr10-python-ci` | 92 | 194 | 只多一个 CI/路径修复候选；不得连同 193 个提交带入 |
| `recovery/c4-canonical-from-d418-local` | 43 | 1 | 恢复快照，不作为运行主线 |
| `recovery/c4-fa2e9caf-local` | 45 | 2 | 恢复快照，不作为运行主线 |
| `test/pr15-legacy-electron-oracle` | 89 | 2 | 只评估 oracle 是否仍有独立价值 |

每个候选能力采用同一流程：

1. 用当前主线先构造随机、独立 oracle 的失败样本；
2. 从旧分支只提取最小 owner，不连带旧 planner/状态/prompt；
3. 在至少两个不同任务族上复验；
4. 只有成功率或可恢复性有实质提升，且没有应用规则、提示增长和 owner 重复时保留；
5. 同一 PR 删除被替代实现；不能长期双写；
6. 无提升或只修当前模型偏差时丢弃候选。

第一候选能力轴是“目标发现/激活/跨窗口恢复”，因为它直接阻塞跨应用任务；
但在当前源码没有可重复失败与复验结果前，不 cherry-pick
`vnext-agent-core` 的 target-acquisition 提交簇。

### 13.5 Pi Agent 源码引入计划

Pi 的当前上游已从 `badlogic/pi-mono` 迁到
[`earendil-works/pi`](https://github.com/earendil-works/pi)，当前包名是
`@earendil-works/pi-agent-core`。它适合评估替换的是“模型消息 → tool call
→ tool result → 下一轮”这一小段 owner；它不提供 Windows 画面、坐标映射、
窗口工作区、真实输入或外部 oracle。

采用顺序：

1. 不在当前主线直接安装，也不把 Pi 包再包一层新 runtime；
2. 在短命 spike 分支用 Pi `Agent` 类和顺序工具执行复刻当前 Visual Actor
   最小循环；使用 `Agent` 而不是低层 observational stream，保证消息事件在
   tool preflight 前完成；
3. 工具面仍是当前六种 GUI 动作与 claim/blocked，系统 prompt 不增加软件
   规则；当前 JPEG、原始目标和有界 task state 直接进入同一 Agent；
4. 先用 scripted provider 做行为同构测试，再跑 precise-click、
   dynamic-hold、随机试卷和一个跨窗口随机组合任务；
5. 只有 E2E 不回退、模型调用/错误恢复更清楚，并能在同一 PR 删除
   `LiveVisualActor`/`ModelCallRuntime` 的等价 owner 时才落地；
6. 若需要长期 Python↔Node 双循环、额外 RPC 状态机或大量 glue code，停止
   引入；语言边界造成的新增复杂度会抵消 Pi 的简洁性。

Pi 的 `beforeToolCall` 只可承载 schema、当前帧和物理所有权等机械检查，不能
重建当前已删除的执行策略、应用白名单或任务路由。Pi 的 provider、steering、
并行工具等其他能力均不默认引入；桌面 mutation 保持顺序，一动作一新帧。

### 13.6 当前模型职责与压力

当前单一 Visual Actor 负责：

- 理解完整原始目标和口头要求；
- 从当前 JPEG 选择语义目标与精确坐标；
- 在小工具面中选择下一动作；
- 维护有界 task state；
- 根据新画面判断继续、blocked 或 claim。

Runtime 负责当前画面真实性、坐标机械映射、窗口/指针所有权、真实输入和
回执；它不替模型修目标、补坐标或规划应用流程。这个职责划分方向正确，但
模型压力仍偏高：一个模型调用同时承担视觉定位、长任务覆盖记忆、动作 schema
精度和停止判断。解决顺序应是更准确的可替换视觉模型、无遮挡观察和短小
task state；不应重新拆出 Grounder、Verifier、Profiler 或在 prompt 中教学。

现状只能称为“单一 GUI 主循环和基础动作链已打通”，不能称为通用可靠。
当前精确源码只有最新随机精确点击 formal pass；最新动态长按出现外部任务
成功但 Runtime 失败，最新随机三步点击则暴露模型重复错误动作直至耗尽回合。
五个长任务没有获得当前精确源码上的 5/5。新架构思想基本正确，能力成熟度
仍不足。

### 13.7 当前源码 E2E 正反证据

本轮先通过 GUI 刷新 Electron 客户端，使托管 Python 子进程重新加载当前工作
树。第一次 `precise-click` 与 Python 全量回归并发时，`/api/status` 探测超时，
没有进入 Agent 回合，也没有任何坐标动作；全量回归结束后同一 seed 重跑通过。
这条失败保留为测试调度证据，不伪装为模型失败。

| 用例 | seed / run | Runtime | 独立 oracle | 结论 |
| --- | --- | --- | --- | --- |
| `precise-click` | `7302026` / `arp-20260730-071320-ef6fb9` | done | passed | 当前 formal pass |
| `dynamic-hold` | `7302031` / `arp-20260730-071613-c3e9ae` | `state_rejected` | passed | 任务到达成功态，但释放发生在失败清理，系统没有闭环 |
| `ordered-clicks` | `7302032` / `arp-20260730-072342-178e5f` | turn budget exhausted | failed | 第一步正确，之后重复同一错误点击至 30 回合 |

精确点击坐标证据只比较模型提交与物理执行：

```text
submitted image: 1120 x 595
frame region:    (0, 0, 1920, 1021)
model submitted: (418, 147)
unique mapping:  (717, 252)
physical attempt:(717, 252)
exact match:     true
```

这里没有读取 target bbox 或计算按钮中心。外部 oracle 另行确认可信鼠标事件命中
随机目标。动态长按和有序点击的失败也不通过新增重复动作 gate、任务提示或坐标
补偿修复；它们进入后续模型对比基准和最小 loop owner 评估。

### 13.8 负优化审查与当前复测

对 `dynamic-hold` 的 `state_rejected` 加入临时诊断后，干净环境连续出现两个正式
通过样本：

| run | GUI 动作 | Runtime | 独立 oracle |
| --- | --- | --- | --- |
| `arp-20260730-142803-6c5ab7` | 3 drag + claim | `mission_verified` | passed |
| `arp-20260730-143437-d927e0` | 2 drag + claim | `mission_verified` | passed |

第二个样本是在删除全部临时诊断、通过 GUI 重载当前源码后运行的。它只有一次可信
`pointerdown`、80 次移动和一次目标内 `pointerup`。因此前一个失败不足以证明坐标
转换或 held-pointer 基础设施存在稳定缺陷；没有保留诊断事件，也没有增加静止拖拽
规则、提示教学或模型精度补偿。

随后 `precise-click` 在进入 Agent 前失败：Electron 显示
`Electron desktop handoff predates goal submission`，没有新 run、trace 或物理动作。
根因是一条跨 Renderer/Electron 主进程的毫秒时间戳先后比较。该时间来自客户端自报，
不能建立安全边界，而目标仍会独立验证 handoff 已让出、捕获晚于 handoff、15 秒
新鲜度、当前 HWND/process/region/foreground 身份和实时 hit-test。当前修复只删除
这条冗余比较，并把既有组合测试的两个客户端时钟设置为相差 1 ms；没有增加容差、
重试器、owner 或状态。

GUI 重载后，`precise-click` 正式通过：

```text
run:             arp-20260730-145535-209895
model submitted: (419, 143)
unique mapping:  (719, 246)
physical attempt:(719, 246)
exact match:     true
Runtime:         mission_verified
external oracle: passed
```

本轮生产代码总增量为 -2 行：删除 2 行判断，并去掉 1 个不再需要的赋值左值；测试
只增加 1 行说明并调整 1 个夹具值。WinPilot 全量回归为
`3367 passed, 13 skipped`，根目录回归为 `2508 passed`，前端为
`224 passed`，production build 通过。这轮的结论不是
“多加规则后更可靠”，而是删掉一个不能提供真实保证、却会随机阻断合法用户入口的
约束后，真实闭环恢复；模型偶发精度问题仍交给模型对比和随机样本统计，不写入系统。

### 13.9 模态窗口生命周期闭环

Krita 的“菜单 → 新建对话框 → 主窗口”真实 GUI 运行暴露出四个通用基础设施缺口：
Windows/Qt 有时让已禁用 owner 继续保持 Win32 foreground；菜单是同进程独立 popup；
对话框出现或消失可能恰好跨过一次截图提交；任务完成证据仍错误绑定启动 HWND。
最小修复只让 runtime 识别唯一可见的 owned modal、把同进程临时 popup 视为当前逻辑
前台、在捕获期间目标确实变化时立即重捕获一次，并让完成证据校验当前 workspace
目标。没有加入 Krita 名称、菜单坐标、固定等待、全局偏移、控件吸附或新提示词。

修复后的正式闭环为：

```text
run:             arp-20260730-182347-e23c5c
window path:     1709054 -> 88020202 -> 1709054
GUI actions:     3 clicks, all applied
stale proposal:  1 discarded before input
Runtime:         mission_verified
client:          done, execution_valid=true
```

同一源码上的独立随机精确点击也通过：

```text
seed / run:      732041 / arp-20260730-183324-84f0ca
GUI actions:     1 real click
Runtime:         mission_verified
external oracle: target index 3 hit, passed
```

原始长任务 `arp-20260730-182632-de1599` 已不再被窗口或命中校验阻断，30 次点击均
真实执行，但模型画出两个小点后连续 23 次选择前景色控件下边缘，最终耗尽回合，未
保存也未进入 QQ。提交图到物理坐标的换算一致，落点在转换前就贴近控件边界；因此
不增加坐标补偿或 Krita 特判。这次正反样本把责任边界分开：runtime 修窗口与坐标
机械一致性，模型负责语义目标和精确选点。

本次生产代码净增 82 行，测试承担主要增量；动作面、角色数、执行入口和系统提示词
均未增长。后续优先比较更准确的视觉模型并扩大随机组合 E2E，不以当前模型误差为
理由继续扩张 runtime。

### 13.10 一笔原生交换、Pi 边界与完整矩阵

`ordered-clicks` 的重复首目标不是坐标或鼠标问题，而是 Visual Actor 每回合只收到
新 JPEG 和压缩状态，provider 看不到上一笔自己发出的 tool call。短命 Pi
`0.83.0` spike 直接使用 `@earendil-works/pi-agent-core` 的原生消息序列，在两个
随机 seed 上都以三次点击通过有序目标；同一 spike 的随机试卷仍会选错算术答案。
这说明有效增量是标准 tool transcript，不是新增 Agent、策略或应用知识。

主线没有安装 Pi、没有加入 Node↔Python RPC，也没有复制它的 runtime。当前
`LiveVisualActor` 只借用这一条已被 E2E 证明的语义：下一回合最多携带最近一笔
`user image → assistant tool call → tool result`，再附新 JPEG；随后覆盖旧交换。
系统提示词仍为 45 个英文单词，角色数、动作面、前端选项和长期记忆均未增长。
同一固定 seed 的 `ordered-clicks` 从持续重复第一个目标，变为：

```text
run:             arp-20260730-213217-3cb01b
real GUI input:  click 1 -> click 2 -> click 3 -> claim
Runtime:         mission_verified
external oracle: strict sequence [1, 2, 3], passed
```

随后在当前精确工作树上运行完整 10 项可见 Electron 矩阵：

| 用例 | run | 独立 oracle |
| --- | --- | --- |
| `answer-all-questions` | `arp-20260730-215634-8994d6` | failed，6 题答完但只有 5 题正确 |
| `precise-click` | `arp-20260730-215908-c488c6` | passed |
| `ordered-clicks` | `arp-20260730-215942-32271e` | passed |
| `drag-fit` | `arp-20260730-220026-721b2b` | passed |
| `slider` | `arp-20260730-220105-cf5c4d` | passed |
| `dynamic-hold` | `arp-20260730-220143-68d8e8` | passed |
| `corridor-v0` | `arp-20260730-220226-07be9c` | failed，路径离开通道 |
| `corridor-v1` | `arp-20260730-220303-e5a9bc` | failed，直线穿过通道外区域 |
| `game-drag-v2` | `arp-20260730-220341-3f8260` | failed，折线切角 |
| `corridor-v3` | `arp-20260730-220415-2d3f1a` | failed，折线切角 |

总计 `5/10`。这不是“绝大部分可用”，也没有因总数未增加就否定改动：
`ordered-clicks` 的结构性多轮遗忘被一笔通用 transcript 修复，而原本偶发通过的
试卷暴露为模型算术波动。四个 corridor 的 `actor_decision` 与
`physical_attempt` 逐点一致，真实浏览器还记录了完整 pointermove；错误在模型提交
的几何路径中已经存在。

试卷的另一轮失败把键盘责任边界也闭合了。题目是 `8×9`，模型第一次提交：

```json
{"text":"81"}
```

在看到 `5/6` 后，它执行 `click → Ctrl+A → type_text`，但第二次提交仍明确为：

```json
{"text":"81"}
```

两次 `physical_attempt` 都逐字收到 `81`，回执均为 `2/2 codepoints`，纠错前后
帧也显示 `81`。另用同一 admitted physical `SendInput` 路径在可见 Chrome 输入框
做通用探针，`72`、`81→72`、ASCII、中文、emoji 和 `Ctrl+A` 覆盖均与 UIA
最终 ValuePattern 逐码点一致。因此不修改 `type_text`，也不添加乘法、答题或
“发现错误后换答案”的提示规则。

本轮停止继续扩张 runtime 的条件已经满足：剩余已复现失败属于当前模型智力和视觉
几何精度，不属于机械 owner。下一步是把 Visual Actor 保持为可替换模型边界，接入
更强的原生视觉模型做同矩阵 A/B；若新模型直接提高试卷与 corridor 成功率，现有
runtime 不应随模型升级再改。Pi 只有在未来能替换并删除 Python 模型循环 owner、
且跨任务 E2E 明显更好时才整体引入；当前结论是“借一条消息语义，不引入第二套
runtime”。提交前 WinPilot 全量回归为 `3373 passed, 13 skipped`。

### 13.11 Codex 窗口交接与唯一分支收敛

2026-07-30 在更换 Codex 窗口前，把本轮稳定思想、禁止项、精确测试状态和继续工作
顺序收敛到仓库入口，而不是新增一份脱离主文档的临时聊天纪要：

- 根目录 `AGENTS.md` 约束所有后续 Codex：先读 `CLAUDE.md`、以真实不确定任务 E2E
  为中心、只修证据首次分叉层、不得用应用规则或模型补偿扩张系统，并且不得触碰
  用户未跟踪的 `CON`；
- 根目录 `CLAUDE.md` 保存当前唯一产品链、owner 边界、45 词提示词、一笔原生
  exchange、坐标/键盘/窗口结论、负 A/B、Pi 边界、`5/10` 矩阵、五项长任务实况和
  下一窗口的操作顺序；
- `docs/architecture.md`、`docs/visual-runtime-principles.md`、
  `docs/model-providers.md` 与 `docs/vnext-migration.md` 同步改成当前 vNext 事实，
  旧 Task Contract、StageRuntime、AgentRunner、observe/wait 只标为 Legacy 或待删
  兼容面，不能再被误读为产品主链。

分支策略同时固定：`master` 是最终唯一长期分支；
`codex/lean-gui-agent-core` 在正式收敛前是唯一活动集成分支，不再创建平行开发
分支。Draft PR #45/#46 和其他冻结分支只作逐项恢复、审查或 oracle 候选；任何能力
都必须先在当前主线复现缺口、以最小 owner 提取，并在至少两个不同任务族取得外部
oracle 的实质收益。不能整体 merge 冻结架构来追求表面上的单分支。通过门槛的小
提交最终落到 `master`，再关闭恢复 PR 并删除其他本地、远端临时分支。

本节和交接文档不改变运行时代码，也不把 `5/10` 写成成功。最后一笔运行时代码仍是
`bf29783 fix: preserve one visual tool exchange`；后续首要实验仍是更强原生视觉
模型在完全相同 10 项矩阵上的干净 A/B，而不是继续添加 prompt、角色、工具或
验证层。

### 13.12 当前分支提升为主分支

用户随后明确要求让当前分支成为主分支。GitHub 默认分支本来已经是 `master`；核对
显示远端 `master` 是 `codex/lean-gui-agent-core` 当前 HEAD 的严格祖先，因此采用
纯 fast-forward 提升，而不是开新 PR、创建 merge commit、force push 或重写历史。
本次提升包含第 13.11 节的稳定交接文档，但没有把任何其他冻结分支整体合并进来。

提升完成后的规则是：

- `master` 同时是 GitHub 默认分支、当前活动分支和唯一长期产品分支；
- 后续 Codex 第一条命令改为 `git switch master`，所有正常改动直接进入
  `master`；
- 原 `codex/lean-gui-agent-core` 与 Draft PR #45/#46 由 `master` 上的相同提交
  取代，不再作为开发入口；
- 其他具名分支维持只读冻结。只有当前 `master` 的随机 E2E 先证明缺口时，才逐项
  审查最小候选；禁止整体 merge 冻结架构；
- 审计完成的冻结引用直接删除，不能为了分支数量而把未经验证的代码合入产品。

这仍是 Git/文档状态变更，不是新的运行时能力或 E2E 结果。最后运行时代码、
`3373 passed, 13 skipped` 回归和 `5/10` 可见 Electron 矩阵均保持不变。

### 13.13 浮动前端的真实输入验收

2026-07-31 在 `master` 上重新核对可见客户端入口时发现，历史 E2E 虽然通过真实
鼠标点击目标应用，但 Electron goal 仍由 UIA `ValuePattern.SetValue` 写入。它不
满足“用户像人一样从前端发送消息”的新验收口径，历史 `5/10` 因此只保留为模型
对照，不能冒充当前入口通过。

本轮只修改测试基础设施：UIA 继续定位和读回控件，goal 改为 Windows `SendInput`
逐 UTF-16 unit 键入，再用真实鼠标点击可见“发送消息”。同时适配浮动前端的资料
入口，并删除“运行时仍 running、但出现后续发送框就算 terminal”的旧 harness
误判。没有修改 Runtime、工具 schema、系统提示词或模型角色。

按此口径重跑完整矩阵为 `6/10`：试卷本次 6/6，原五个 GUI 通过项继续通过，四个
corridor 继续失败。四个失败的模型参数、canonical/physical 参数和真实浏览器事件
已经闭合：v0/v1 提交直线路径，v2/v3 提交的折线路径仍切角；RawDevice 原样执行，
浏览器分别记录 64–79 个可信 pointer move，最小通道余量为
`-74/-73/-42/-14`。失败首次出现在模型路径参数，不进入 Runtime 补偿。

冻结分支只读审计没有发现值得 merge 的能力；旧 vNext 和 recovery 树会恢复大量
profiler、verifier、runtime selector、target pipeline 和重复状态 owner，均不进入
主线。Pi 状态也不变：只保留最近一笔原生 tool transcript 的已验证语义；没有 Pi
依赖、RPC 或第二 Runtime。下一项仍是更强原生视觉模型在同一矩阵上的干净 A/B。
同一源码的 WinPilot 全量回归为 `3377 passed, 13 skipped`。

### 13.14 动作摘要删除 A/B、终态判定与模型切换阻塞

本轮唯一生产候选是删除 Visual Actor 的累计
`completed_action_summaries`，以及只服务该历史投影的
`previous_action.semantic_summary` 通路。系统提示词仍为 45 个英文词，八个
工具、一个原始 goal、一个 `LiveVisualActor` 和一条视觉执行线均未变化。

修复测试终态误判后的有效删除臂报告为
`output/playwright/completed-history-delete-20260731-r2/20260731-062444-suite/`，
结果 `5/10`：`precise-click`、`ordered-clicks`、`drag-fit`、`slider` 和
`dynamic-hold` 通过；试卷提交全部 6 题但只有 5 题正确，30 个 Agent GUI 动作后
耗尽回合；四个 corridor 仍失败。当前生产保留态的同口径基线是 `6/10`。
`ordered-clicks` 在删除臂仍通过，不能据此证明累计摘要必需；试卷单样本也不能
单独建立因果。但删除没有跨任务收益且总结果下降，v0 的重复拖拽增加、v1/v2
最小净空也变差，因此未达到架构准入门。生产删除已完整回退，当前仍暂留有界
动作摘要。这是未获准删除，不是为某类任务新增规则。v3 的物理输入没有被浏览器
事件捕获，存在测试观测歧义，没有用它作为修改 Runtime 的依据。

删除臂第一次运行无效并已排除：匹配 run 的 `current_run` 和 `runtime_jobs`
仍为 running，但 `/api/status` 顶层 `running` 短暂为 false，旧 harness 又把
重新出现的“发送消息”当成 terminal，于约 1.9 秒、零 GUI 动作时提前关闭目标，
随后 Runtime 才报告 `state_rejected`。保留的改动仅在测试 harness：终态判断
优先检查匹配 run/job 的 `queued/waiting_resources/running/canceling` 状态，再
参考聚合投影和前端按钮。r2 在该修复后完成完整矩阵；这是测试真实性修复，不是
Agent 能力提升。

更强模型 A/B 的前置 transport smoke 只发送一张最小 JPEG 和一个通用 tool
schema；`gpt-5.6-sol` 在返回任何模型响应前收到 HTTP 401
`AuthenticationError`。当前归因是 credential/infrastructure，不是模型失败，
也没有形成模型 A/B 结果；没有为此修改 provider gate 或 Runtime。下一轮唯一
最高价值实验仍是恢复有效 credential 后，在完全相同的 10 项矩阵上做更强原生
视觉模型 A/B。

### 13.15 原生 Explorer 长任务与重复 goal 去重负实验

用户暂时排除模型切换后，本轮先补了一条非浏览器、真实消费软件链路。测试先在
原生 Explorer 打开只含三个随机标识文本文件的隔离目录，再从可见 Electron
新对话中用操作系统键盘输入完整目标、用真实鼠标点击发送，最后把控制权交给
Explorer。目标要求依次重命名三个文件、新建目录并移动其中两个文件；命令行只
用于测试 setup 和独立文件系统 oracle，没有代替 Agent 的界面动作。

生产保留态对照 run `arp-20260731-072140-0f4b81` 有一次首轮
`APIConnectionError`，随后恢复并完成 29 次真实输入。每次
`physical_attempt` 都落在正确 Explorer HWND，模型参数、canonical 参数和物理
参数逐项一致；但外部终态只有
`cobalt-7Q4M.txt / jade-7Q4M.txt / north-7Q4M.txt.txt`，没有目录。模型在
Explorer 只选中基础文件名、扩展名保留的重命名状态下输入了完整 `.txt`，然后
多轮重复同一选择、F2、输入和确认序列。首个分歧在模型对可见应用状态的理解与
自我纠错，不在窗口绑定、坐标换算或键盘实现。

随后只做一个 `+0/-3` 的生产候选：从 Actor 的 provider 序列化视图删除与
`original_goal` 完全重复的 `desired_state`、`acceptance_criteria` 和固定
`required_capabilities`；canonical 对象仍保留全部字段，系统提示词、工具面、
角色和执行线不变。聚焦测试为 `29 passed`。实验 run
`arp-20260731-073504-a24efb` 的 29 次真实输入同样全部 applied，并最终正确完成
前两个重命名；但第 30 turn 后的外部终态仍是
`center-7Q4M.txt.txt / north-7Q4M.txt / south-7Q4M.txt`，没有创建目录或移动
文件。最后一次模型调用还把 `click.image_y` 生成为错误类型，Runtime 按 schema
拒绝并以 `invalid_proposal` 结束。

重启 Electron 后，前端把这个已失败 run 恢复成“已中断”。只读追链确认这是另一
条通用基础设施缺口：trace 已有 `vnext_canary_done` 和失败的 `arp_node_done`，
但外层 `arp_run_done` 在 trace recorder 关闭后才发布，只存在于当时的内存
BUS/WebSocket；重启恢复投影因此按设计把缺少外层终态的 run 标为 interrupted。
它影响历史状态真实性，不改变 Explorer 外部终态或本次模型归因。本轮不把 node
终态冒充 run 终态，也不为这条旁路问题修改生产代码。

删除臂有部分进展，但没有完成用户任务；单样本又受到对照首轮连接错误和模型采样
波动混杂，不能建立因果，更不能把“走得更远”当作 E2E pass。三行生产删除和对应
测试已完整回退。没有增加 Explorer 规则、扩展名修正、重复点击 gate、额外回合、
提示教学或新模型角色。

Pi `0.83.0` 的只读复核也没有改变结论：可复用的只是主线已搬入的最近一笔原生
tool exchange；Pi 不提供 Windows 目标发现、激活、截图或输入。冻结
`codex/vnext-agent-core` 的 target acquisition 又与大量 profiler/verifier 和
重复 owner 耦合，当前没有两任务族基础设施失败证据，不能 merge。

模型切换暂停期间，下一轮唯一最高价值实验是对上述三字段去重做严格的重复配对
A/B：至少覆盖原生文件组织和原生文本编辑两个任务族，保持同一前端物理输入、
外部 oracle、回合预算和模型配置；只有完整任务成功率跨族提高才保留，否则继续
维持当前零生产改动状态。

### 13.16 GitHub 未完成项与真实软件全量复验

2026-07-31 本轮先同步并核对 GitHub 全部开放 issue、历史 PR 和未解决 review
thread，再按当前单视觉主线逐项运行仍有意义且可安全执行的 E2E。所有有效 run
都从可见 Electron 空会话用操作系统键盘输入目标、用真实鼠标发送，再把前台交给
目标窗口；命令行只做 fixture、runner 和独立 oracle。Runtime、45 词系统提示、
八个工具、模型角色和视觉执行线均未改变，生产代码为 `+0/-0`。

受控矩阵的干净重跑报告在
`output/playwright/github-open-e2e-20260731/controlled-suite-r2/20260731-081832-suite/`，
结果为 `5/10`：

- `precise-click`、`ordered-clicks`、`drag-fit`、`slider`、`dynamic-hold`
  通过；
- 随机试卷只有 4/6；
- `corridor-v0/v1/game-drag-v2/corridor-v3` 的最小净空分别是
  `-24/-69/-12/-9`。

失败样本的模型参数、canonical 参数和物理轨迹一致；浏览器 trusted oracle
首先看到错误答案或出界路径。第一次矩阵运行因 runner stdout 管道被提前关闭而
抛出 `OSError`，只记测试干扰，不计产品结果。

本轮真实软件和原始任务结果如下。表中的“通过”要求同时有 Runtime 正常终止和
屏幕、文件或客户端回复等外部证据；通用 runner 因未配置 case verifier 仍会把
这些样本自动标成 `unverified`，不能反过来否定外部事实。

| 探针 | run | 外部结果 | 首次分歧 |
| --- | --- | --- | --- |
| Notepad #41 | `arp-20260731-083528-c1551a` | 文件精确为 `E2E-ALPHA-729`，磁盘 SHA256 为 `DBB27DB7B57FDC2D1532D9BA3198AC1454140080604CF7209FE2FC0151F55381`；通过 | 通用 runner 未接 verifier，属于测试假阴性 |
| Calculator #40 | `arp-20260731-083823-b89317` | 30 次真实点击后显示 `11`，不是 `703` | 模型定位和重复清除；canonical/physical 一致 |
| Paint #44 | `arp-20260731-084409-027ab9` | 期望格内外红像素均为 0，没有红 X | 模型停留在颜色控件，没有选择绘图动作 |
| 网易云“梁静茹 + 5%” | `arp-20260731-085045-937f4d` | 歌曲已播放，但应用可见音量约 2%，CoreAudio 主音量为 0% 且静音，均不是 5% | 模型精度/停止失败；oracle 另有应用音量与主音量混淆 |
| QQ 群摘要 | `arp-20260731-085623-10423f` | 真实画面进入至少一个群，但没有跨群覆盖或汇总，30 turn 耗尽 | 模型覆盖/停止失败；oracle 又把已进入群误报为 0 |
| QQ 空间“点赞看到的所有说说” | `arp-20260731-111613-e99fd9` | 从 QQ 主窗口因果接管 QQ（浏览）窗口；30 turn / 193.218 秒、30 次物理动作全部 applied。至少三条灰色赞变成蓝色，但终态当前视口仍至少有两条灰色未赞，外部 oracle 失败 | 模型从第 19 turn 起连续 11 次点击同一错误位置，覆盖、自纠错和停止失败；窗口接管、canonical 和物理输入没有分歧 |
| Krita 手机 | `arp-20260731-090305-4ab344` | 创建 1080×1920 文档并画出粉红竖矩形，仅是部分手机轮廓；未完成、未保存 | 16 次模型调用耗时 291.916 秒，物理执行 2.336 秒；300 秒是终止点，但只有一个任务族证据 |
| 网易云“周杰伦第一首” | `arp-20260731-092235-f09538` | 搜索结果第一行与底部播放器为同一首周杰伦歌曲，处于播放态；通过 | 无产品分歧 |
| 抖音 `kimi k3` | `arp-20260731-093313-aecc7c` | 搜索框和结果页均显示 `kimi k3`；通过 | 无产品分歧 |
| Wireshark 最近流量 | `arp-20260731-093836-612d39` | 打开最近捕获的 109 个包，并在 Electron 汇总 DHCP、ICMPv6、IGMPv2、LLMNR、NBNS；与包列表一致，通过 | 无产品分歧；仍缺固定 pcap 的自动事实 oracle |
| Paint 画花 | `arp-20260731-094242-05b4de` | 21 次点击全部在颜色面板循环，画布保持空白 | 模型动作选择循环；不是 300 秒预算导致 |
| “那你干嘛” | `arp-20260731-094953-8117f7` | 模型文字上识别为非可执行对话，却仍无意义点击 Paint 画布 | 模型意图判断与动作选择自相矛盾 |
| “演示能力” | `arp-20260731-095141-028681` | 模型声称画出字母 H，外部画面只有两条竖线 | 模型视觉自验和停止错误 |
| OneNote → Word | `arp-20260731-095532-c14984` | 当前笔记 fixture 存在；模型进入 File/Export 后 blocked，未出现 Word 窗口或工件 | 模型规划/恢复，没有采用复制、启动 Word、粘贴的通用路径 |

原始网易云第一次提交以及抖音的启动器 HWND 两次提交都在 Agent 启动前被
`current Electron target mismatch` 拒绝。前者没有先建立目标前台基线，后者的
启动器随后换成真实主窗口；改用稳定主窗口后同源码通过。这些只记为
fixture/handoff 测试干扰，不计模型失败，也没有据此增加 target discovery owner。

`client_e2e_run.py` 的 original/mixed 目录仍不能作为正式验收统计：

- 通用 case 没有 `independent_verifier`；
- `max_steps` 只是报告元数据，实际 Runtime 仍是
  `30 turns / 300 seconds / 60 model calls`；
- vNext 的 persistent 判定仍依赖 Legacy `open_conversation/read/wait` 事件；
- mixed 仍是拼接字符串，不是 turns、工件依赖、阶段 oracle 和 final oracle。

因此不能把批量 `passed=false` 写成产品 0/N，也不能把 Runtime `done` 写成任务
通过。QQ 空间点赞已经作为上表单项真实运行，并至少产生三次外部点赞；它仍因没有
覆盖当前可见说说而失败。再次复跑仍须在下一次真实点赞前满足动作时确认。发送
截图/图片、Krita→QQ 发文件和持续聊天仍包含外部代表性动作；`mix-drawing-handoff`
和 `mix-all-11` 因而没有静默运行。旧 immutable paired campaign 还绑定已冻结的
Legacy/vNext 双线，不为跑历史账本恢复 selector 或第二 Runtime。

本轮没有新的生产实验获准，也没有新增生产实验需要回退。既有最近一笔原生
tool exchange、单 `LiveVisualActor` 和机械执行链继续保留；上一轮三字段
`+0/-3` 删除臂和更早的累计动作摘要删除臂都因没有跨任务完成率收益而保持回退。
模型失败覆盖算术、路径几何、应用状态理解、重复动作、长任务规划、自纠错和停止；
基础设施缺口是 original/mixed oracle、QQ/网易云 oracle 债务和外层终态持久化；
测试干扰是 stdout 管道、未预激活目标和启动器 HWND 变化。上述基础设施问题均
没有解释真实外部任务失败，本轮不修改 Runtime。

当前架构仍符合一个原始 goal、一个 LiveVisualActor、一条视觉执行线和小型通用
工具面的思想。模型同时承担意图、视觉、schema、短期状态、恢复和停止，压力仍高；
Krita 中 300 秒有 291.916 秒消耗在模型调用，说明长任务吞吐受模型延迟影响，
但现有证据没有达到两个任务族“正确持续推进却被同一机械边界截断”的准入门。

Pi 计划不变：只保留已搬入并通过 E2E 的最近一笔原生 exchange 语义，不安装 Pi
依赖、不加 Node↔Python RPC、不建立第二 Runtime。只有 Pi 能整体替换并删除当前
模型循环 owner，且跨任务族 E2E 明显提高时才继续引入。

模型切换暂停期间，下一轮唯一最高价值实验仍是对上一轮三字段去重做严格重复配对
A/B：同一源码、模型、预算和真实前端入口，至少覆盖 Explorer 文件组织与 Notepad
文本编辑，并只按外部 oracle 的完整任务成功率决定是否保留；如果仍无跨族收益，
继续维持零生产改动。

### 13.17 三字段去重严格交叉配对

2026-07-31 按 13.16 预注册口径完成两组交叉顺序、两个原生任务族、A/B 各两次的
8-run 小样本。A 是当前保留态；B 只从 Actor provider 视图删除与
`original_goal` 重复的 `desired_state`、`acceptance_criteria` 和固定
`required_capabilities`，canonical 合同、系统提示、工具面、模型、预算与执行线
均不变。每次都从可见 Electron 空会话用操作系统键盘输入原始目标、用真实鼠标
发送，再由 Agent 操作 Explorer 或 Notepad；只按文件系统或磁盘内容 oracle 判定。

| 配对 | 臂 | Explorer | Notepad |
| --- | --- | --- | --- |
| P1 | A | `arp-20260731-120025-838593`；失败，20 次 applied 后 300 秒耗尽；双扩展且无目录/移动 | `arp-20260731-120818-078de7`；精确 `E2E-ALPHA-N1X7`，通过 |
| P1 | B | `arp-20260731-121614-10690b`；失败，29 次 applied 后 turn 耗尽；双扩展/重复拼接且无目录/移动 | `arp-20260731-122329-291212`；Runtime done，但外部为 `E2-ALPHA-N1X7`，失败 |
| P2 | B | `arp-20260731-122716-1f3bc5`；失败，30 次 applied 后 turn 耗尽；三个双扩展且无目录/移动 | `arp-20260731-123127-cdd90f`；精确 `E2E-ALPHA-N2K9`，通过 |
| P2 | A | `arp-20260731-124027-c371a4`；失败，26 次 applied、2 次安全拒绝后 invalid proposal；无目录/移动 | `arp-20260731-124427-4677c7`；精确 `E2E-ALPHA-N2K9`，通过 |

原始外部计分为 A：Explorer `0/2`、Notepad `2/2`；B：Explorer `0/2`、
Notepad `1/2`。B 没有带来任何 Explorer 完整成功，也没有跨任务族收益，故三行
生产删除和对应候选测试均完整回退，当前生产代码仍为 `+0/-0`。B 的 Notepad
原始分数较低不能直接证明字段删除造成负化：该 run 的模型 proposal、canonical、
submitted 和 physical 参数均精确为 14 字符 `E2E-ALPHA-N1X7`，RawDevice 也报告
`requested=14/completed=14`，但紧随其后的封存画面和磁盘都少第三个 `E`。首次
分歧在物理输入意图/回执与应用外部状态之间，属于输入交付/应用边界基础设施样本，
但现有单样本不能确定是 SendInput、事件调度还是应用处理；模型随后仍 claim done，
则另有视觉复核和停止错误。

配对做了 P1 A→B、P2 B→A 的顺序反转，模型、预算、fixture 和工具合同一致；但
真实 Explorer 的首帧选中态和导航栏布局仍有轻微漂移，run artifact 也没有封存
每臂源码 hash 或完整 provider request。因此本轮只能证明 B 未达到保留门，不能把
原始 `2/4` 对 `1/4` 写成字段删除导致回归的强因果结论。

四次 Explorer 的首次任务分歧均在模型层：进入重命名后，屏幕只选中文件主名，
模型却显式输入带 `.txt` 的完整名字；proposal、canonical、physical 与外部
双扩展逐项一致。
后续重复输入、地址栏误触、额外 Enter 打开关联应用、恢复失败和错误 schema 都发生
在建目录或移动之前。provider 延迟和 turn/时间预算是终止因素，不是这些任务错误的
起点；窗口绑定、跨窗口接管与 stale action 拒绝按合同工作。

此前 P1-A 两次尝试在发送前被 runner 拒绝，原因是长中文目标换行后 UIA
`ValuePattern` 只返回首个视觉行。runner 仍只用操作系统键盘写入，但现在可用只读
`TextPattern` 核对完整可见文档；聚焦 harness 回归为 `41 passed`。这两次没有
run id 的样本被排除，不属于上述 8 次有效运行，也不解释有效任务失败。

当前架构仍是一个原始 goal、一个 `LiveVisualActor`、一条视觉执行线和小型通用
工具面；没有增加提示词、角色、分类器、应用规则或修正器。模型同时承担意图理解、
视觉状态、schema、长程计划、恢复、自验和停止，Explorer 的重复耗散说明压力仍高；
本轮没有把这些模型失败下放给 Runtime。

Pi 引入计划不变：只保留已经搬入且有 E2E 证据的最近一笔原生 tool exchange
语义，不安装 Pi 依赖、不加 Node↔Python RPC、不建立第二 Runtime。只有 Pi 能整体
替换并删除当前模型循环 owner，且在不同任务族提高完整成功率时才继续搬运。

下一轮唯一最高价值实验是不改生产代码，先用同一可见 Electron 入口，在两个不同
原生任务族重复验证短字符串尤其相邻重复字符的 `type_text` 交付，并同时封存模型、
canonical、physical、紧随帧和外部 oracle。只有“前三层完整、外部仍丢字”跨族
复现，才允许对通用 SendInput 边界做单变量 A/B；否则维持当前输入实现。

### 13.18 `type_text` 跨原生任务族交付复验

2026-07-31 按 13.17 的停止规则冻结了 4 个 token，长度为
`12/12/48/48`，每个分别运行一次 Notepad 精确替换保存和 Explorer 唯一子目录
重命名，共 8 个槽位；顺序为 P1 Notepad→Explorer、P2 Explorer→Notepad、
P3 Notepad→Explorer、P4 Explorer→Notepad。所有运行保持同一
`mimo-v2.5`、vNext `30 turns / 300 seconds / 60 calls`、空 session、可见
Electron、操作系统键盘输入原始 goal 和真实鼠标发送。Explorer 改用无扩展名目录
重命名，排除了文件扩展名选区这一模型语义混杂；命令行只准备 fixture 和读取外部
oracle。

| 槽位 | 任务族与 run | `type_text` 交付 | 独立外部结果 |
| --- | --- | --- | --- |
| P1 | Notepad `arp-20260731-144901-8331bd` | `12/12` | 精确 12 bytes；SHA256 `0F3A19EBA066F15CDF69FAE9D722D6501F25B4C57A586E62F8DF772B0659BFDA` |
| P1 | Explorer `arp-20260731-145621-d06db3` | `12/12` | 唯一目标目录；sentinel `B46716CFFB544EEFE6C7634409E33BEABC200529D90122F441AD70E3A67F6526` |
| P2 | Explorer `arp-20260731-145917-baeed3` | `12/12` | 唯一目标目录；sentinel `F642C96E436E4C610627A69BF3565045F838E787B06AFDAA272E72128C958008` |
| P2 | Notepad `arp-20260731-150420-e73e76` | `12/12` | 精确 12 bytes；SHA256 `3A6DF86714CB2A74C1DEF5B0FF473D90D3C443A8CBE73EFA997D8FCD2FAE7E27` |
| P3 | Notepad `arp-20260731-151623-4610da` | 两次均 `48/48` | 精确 48 bytes；SHA256 `0D3AD0DA445C1ADA06FB060FAB3ECBD1B087599CB163FB3D9B6B0A14852F7F8B` |
| P3 | Explorer `arp-20260731-152205-793011` | `48/48` | 唯一目标目录；sentinel `B0476452BA49329FF8EA9B6DED0F9D8C72203D313E2A311343D9FB2B6DD6DC78` |
| P4 | Explorer `arp-20260731-152534-35173c` | `48/48` | 唯一目标目录；sentinel `1A3D2153F941013C7D01A35D89953E13B47F4DCCDCBB3193FCAFC16255A3DFCC` |
| P4 | Notepad `arp-20260731-152945-6bbca3` | 五次均 `48/48` | 失败；磁盘仍为初始 18 bytes，300 秒耗尽 |

8/8 槽位都至少有一次模型 proposal、canonical、submitted 和 physical 文本与
预注册 token 完全相同，RawDevice `requested=completed=token length`，目标 HWND
正确，且约 300–470ms 后的稳定帧没有字符丢失；同一槽位内的重复输入不能膨胀样本
数。因此 13.17 的单次缺 `E` 仍是输入交付/应用边界历史观察，但本轮没有跨族复现，
不准入 `type_text` 或 SendInput 生产改动，也不能反向宣称该边界永远无故障。

真实完整任务结果为 `7/8`。唯一失败的 P4 Notepad 在第一次
`click → Ctrl+A → type_text` 后，内存已经是精确 48 字符，标题为 dirty，状态栏为
第 1 行第 49 列；但约 592×411 的小目标帧水平滚到最右，只显示从 `DEE...` 开始的
后缀。模型把不可见前缀误判为未输入，随后重复 5 次完整输入，从未执行 `Ctrl+S`，
最终耗尽时间预算。首次任务分歧在正确输入后的模型进度、自验与停止决策；provider
长延迟放大耗散，窗口绑定、canonical 和物理输入没有先分歧。小窗口是合法桌面状态
和明确压力源，不是测试干扰，也不支持自动最大化、Notepad 规则或提示教学。

用户指出窗口尺寸后，另做了一次不计入预注册 8 槽位的探索性单变量对照。全新最大化
Notepad 在相同 token、goal、模型和预算下运行
`arp-20260731-154047-e6f06f`，模型只执行
`click → Ctrl+A → type_text → Ctrl+S`，磁盘严格为 48 bytes，SHA256
`AD2D6588D5437A0534683476D5D7A2FE9726BE22E381772C3F521C1B9C5FB14A`，外部任务
已经成功。保存后的模型调用先等待 83.479 秒并返回 `APITimeoutError`，重试又返回
`APIConnectionError`，所以 Runtime 最终为 `actor_error`，没有 claim done。该
样本应双口径记录为“用户任务 pass、Runtime 终态健康 fail”；它中等强度支持水平
视口是循环触发器，但单次探索不足以证明充分因果。

本轮另有两次非文本按键的跨族边界观察：P3 Notepad 首次 `Ctrl+A`、P4 Explorer
首次 `Enter` 都是 proposal、canonical、physical 和回执完整，但稳定外部画面未
生效，模型随后自行纠正。它们不允许加入重复按键规则，却使下一轮可以只读准入一个
通用 physical-key/focus/application-consumption 单变量复验：沿同一真实前端，
在至少两个任务族固定复现当前态与一个现有通用输入实现的测试臂，只有两个任务族的
外部成功率都提高才考虑替换并删除现 owner。生产代码本轮仍为 `+0/-0`，没有候选
获准，也没有新候选需要回退。

架构仍是一个原始 goal、一个 `LiveVisualActor`、一条视觉执行线和小型通用工具面。
模型仍同时承担视觉状态、计划、恢复、自验和停止，小视口循环说明压力很高，但这些
职责没有下放给 Runtime。Pi 计划不变：只保留已经证明的最近一笔原生 tool exchange
语义；除非 Pi 源码能整体替换并删除当前模型循环 owner 且跨任务族提高完整成功率，
否则不安装依赖、不加 RPC、不建立第二 Runtime。

### 13.19 聊天常驻与按需桌面租约：当前链路失败、候选未准入

2026-07-31 用当前 `master`
`fba5d7bb6dcb13c46e93dbc29bc3cef44e560622` 做了真实可见 Electron
复现。先从前端新建对话，再启动一个受控空白 Notepad 作为唯一外部目标；用操作系统
键盘输入“请只回答：`CHAT-VISIBLE-7Q31`，不要操作电脑。”，并用真实鼠标点击发送。
发送前 Electron 可见；发送点击结束后立即读取窗口就得到
`window is minimized`，而 ARP 尚未开始模型调用。run
`arp-20260731-171040-dafc25` 随后绑定 Notepad HWND `25301608`，读取一张
权威外部帧，用一次 `mimo-v2.5` Visual Actor 调用
`claim_obligation_done`，零 `physical_attempt`，最终记作
`mission_verified`。

产品终态没有自行 `restore/show`；必须由测试者手动激活 Electron 才能看到
“任务完成”和
`Answered CHAT-VISIBLE-7Q31 as requested without operating the computer.`。
这不是 direct reply，也没有让聊天窗口在等待期间保持可见。证据在
`python/winpilot_home/traces/arp-20260731-171040-dafc25.jsonl` 和被 Git 忽略的
`output/playwright/chat-desktop-lease-spike-20260731/live-electron-chat-baseline.json`。
因此当前失败首先属于入口/窗口生命周期基础设施：所有消息都在模型判断前执行
`prepareDesktopHandoff → setSkipTaskbar(true) → minimize`；当前 vNext 又只接收
预绑定 GUI target。模型本次没有产生物理误操作，但把聊天写成带视觉证据的任务完成，
属于该错误协议允许的后续语义错误。
当前 UI 还只是把独立 run 排成对话外观；新一轮没有把该 thread 的前一轮用户/模型
交换交给模型，因此也尚不具备 Codex 式上下文聊天。后续只能复用有界的最近原生
exchange，不得为此恢复无界历史、长期 memory owner 或第二 conversation role。

同时做了不改生产代码的同模型 transport 冻结实验。唯一通用工具是零参数
`observe_desktop`，`tool_choice=auto`、temperature 0、每例一次请求：

| 实验臂 | 严格结果 | 观察 |
| --- | --- | --- |
| 113 字系统提示 | `7/12` | 两个聊天样本错误申请桌面；三个 GUI 样本同时返回 tool 和 prose，违反当前严格协议 |
| 零系统提示 | `9/12` | 6/6 GUI 均干净申请桌面；两个聊天请求分别连接错误和超时；算术样本仍错误申请桌面 |
| 零提示受干扰样本原样复跑 | `1/2` | 天空解释恢复为直接回复；问候仍连接错误 |

零提示明显优于增加教学，也消除了 GUI 的 tool+prose 形状问题；但在有 provider
响应的普通聊天里仍有算术误申请桌面，问候又尚无稳定结果。不能用关键词 router、
额外 prompt 或答案修正关闭这些失败。一个 deterministic、零生产代码的协议原型
另外证明：同一 adapter、同一 `vnext_visual_actor`、零 classifier、零系统提示，
可以解析 direct reply、observe-only 和 observe-with-content，并拒绝未知工具与空
响应；它只证明数据契约，不证明当前模型选择质量。

正确候选不是“前端先判断聊天”，也不是复活 Legacy `/api/chat`。它应保持一个原始
goal 和同一个 `LiveVisualActor` 实例：首轮无截图直接回复，或调用
`observe_desktop`；只有后者才发布带 `run_id + lease_id` 的一次性桌面租约，Electron
同步让出前台并确认后，后端才冻结 target、取得首帧并把同一 Actor 继续接入现有
`MissionRuntime`。匹配租约的成功、失败或取消必须由产品自行
`restore/show`，stale run 不得恢复别的运行。直接回复必须是独立 `reply` 终态、
零物理动作，不能伪造 mission evidence。

这一正确切片预计需要约 `250–330` 行净新增生产代码并跨越 Actor 构造、延迟 target、
ARP reply 终态、租约 HTTP、renderer 和 Electron 生命周期。当前选择实验未达到足以
承担这项结构改动的准入线，因此本轮生产代码仍是 `+0/-0`；保留根因、外部 E2E
失败证据、零提示方向和测试型协议原型，回退“删掉 minimize”“按异步 action-start
事件隐藏”“第二模型/旧聊天链”以及立即落地整套租约的候选。

下一轮唯一最高价值实验仍不改生产代码：在 provider 稳定窗口对冻结的零提示
reply/observe 矩阵做预注册重复，并以 direct reply 精确文本、desktop lease 选择、
tool shape 和错误率为唯一 oracle。只有聊天与至少两个 GUI 任务族均达到准入线，才
实现 run-bound lease，再执行真实鼠标键盘的
`chat-visible → controlled GUI → contextual chat-visible`；最后一轮必须能回答前一
GUI 轮产生的随机值，且在任何 harness 强制恢复窗口之前判定产品是否自行恢复。

当前架构在 GUI 执行内部仍满足一个 goal、一个 Visual Actor 和一条视觉线，但产品
入口还不符合新架构思想：它在模型表达意图前把所有消息预分类为 GUI，事实上剥夺了
同一模型聊天或未来选择 shell/provider 的能力。模型在 GUI 后仍承担视觉、计划、
恢复、自验和停止，压力很高；正确按需租约不会再加角色，只把“是否需要外部状态”
还给同一个模型。Pi 引入计划不变：仅复用已证明的原生 tool exchange 语义；Pi
当前没有可直接搬入的 Windows handoff/restore owner，不安装依赖、不加 RPC，也不
为聊天建立第二 Runtime。

### 13.20 同一 Actor 的 Thread/Turn、按需桌面与连续对话：准入并通过

本轮按 Codex 公开的产品合同而不是软件样式做职责对齐：一个 Thread 包含连续
Turns，一次 Turn 包含用户输入、模型回复或工具动作；computer use 是同一 Turn 中
模型可选的能力，不是前端预分类出来的另一类任务。实现没有接回 `/api/chat`，也没有
增加 conversation responder、关键词 router 或第二模型。`LiveVisualActor` 在零
系统提示的首轮只看到有界原生文本历史和一个零参数通用工具
`observe_desktop`：它可以直接产生 `reply`，也可以申请桌面。只有后者才发布一次性
`run_id + lease_id`，Electron 收到请求后再让出前台、冻结当时的精确 target，并把
同一个 Actor 接入原有视觉线。

服务端新增小型内存 Thread/Turn owner：同一 Thread 同时只允许一个活动 Turn，只把
有真实用户结果的成功 `reply/done` 放入未来历史，最多 12 条原生消息、16 KiB；
失败 Turn 不污染上下文。前端只保存不透明 `thread_id` 和 run id，新建对话显式
重置。它不是长期 memory、没有摘要模型；当前服务进程重启后 thread 不持久化，是
明确剩余边界。

桌面租约使用 `idle → waiting → validating → delivered/closed` 的精确状态机。
目标验证和 bind 只接受同一 run/lease；响应丢失时前端先查询服务端真实状态，
`delivered` 不得误恢复，仍为 `waiting` 时也必须等精确 fail 返回 `closed` 后才能
恢复。matching terminal 由 Electron 自行 `restore/show`，非当前 run 的准确终态也
会释放它自己的 handoff；stale、重复或别的 run 不能改变当前窗口。未开始就失败的
vNext job 现在同样发布 `arp_run_done`，避免 Thread 和窗口永久停在 queued。

生产实现共修改 15 个文件，`+1372/-155`，净增 1217 行。规模高于 13.19 的早期
`250–330` 行估计，主要是此前估计遗漏了 server-owned Thread/Turn、响应丢失和
TOCTOU、非当前 run 恢复、未启动 terminal 清理及跨 Python/Electron 的合同测试面。
这是本轮最明显的架构成本；没有用应用规则或更多 prompt 换取代码量。测试与文档不计
入该生产行数。

合同验证为：

- Visual Actor `33 passed`；
- live canary composition `79 passed`；
- orchestrator API/runtime `84 passed`；
- Thread Store `2 passed`；
- 聚焦生产回归合计 `153 passed`；
- Electron harness `36 passed`，QQ harness `7 passed`；
- renderer `22 files / 234 passed`，TypeScript typecheck 通过；
- 最终 Python 全量 `3388 passed, 13 skipped`。此前两项失败只是 contract version
  与静态 export 快照，更新后完整重跑已清零。

真实验收在当前源码的冷启动 Electron 上完成，随机 nonce 为 `c419bb42`，同一
Thread 为 `thread-f0d94cfbf8c843a59f5f59ec94584fb3`。所有消息都从可见前端用操作
系统键盘输入并以真实鼠标发送：

| 顺序 | 目标与外部结果 | 桌面/窗口 |
| --- | --- | --- |
| 1–4 | 精确回复 `A-c419bb42`、基于上文改为 `B-c419bb42`、算出 `95`、倒序为 `gamma beta alpha` | 均为 `reply`；lease idle；Electron 全程可见 |
| 5 | 记住选择 `B` | 首次 provider `Connection error`；普通用户原样重发后回复 `B`，失败 Turn 未进入历史 |
| 6 | 同时回忆第 3 轮与最近选择 | 精确回复 `95-B`；lease idle |
| 7 | 明确要求只用鼠标键盘把当前 Notepad 全文替换并保存 | Actor 首轮后才申请 lease；精确绑定 HWND `4919522`；三笔 applied 原始动作 `Ctrl+A → type_text → Ctrl+S`；产品终态自行恢复 |
| 8 | 恢复后追问上一轮保存的完整文本 | 首次 provider `Connection error`；原样重发后精确回复 `DESKTOP-c419bb42`；同一 Thread、lease idle、零 target/physical evidence |

桌面任务的独立文件 oracle 为 16 bytes `DESKTOP-c419bb42`，SHA256
`AF4166A750386EABFA8D2141045366EEEBDB62AE397642D2ABEEDC5859D6ECB8`；
第 8 轮后 hash 和 mtime 均未再变化。第 7 轮外部任务完成，但模型最终文本是
“已完成：文本已全部替换……”而不是目标要求的严格“只回复已完成”，因此双口径记录
为“桌面结果 pass、回复格式轻微 model fail”，不能把外部成功抹掉，也不能用 Runtime
修正文案。两次 `Connection error` 都发生在一次模型 transport、租约 idle、零动作
之前，属于 provider/基础设施干扰；原样重发成功，不支持增加 Runtime 自动重试。

保留的实验是零首轮系统提示、唯一 `observe_desktop`、同一 Actor 跨租约继续、有界
成功 Turn 原生历史、run-bound lease 和产品自主恢复。13.19 的 113 字教学提示继续
按负优化回退；前端预隐藏/预绑定、旧聊天链、第二角色、关键词路由、自动最大化、
应用 prompt、答案修正和重复动作/重试规则均未准入。本轮没有新的生产候选因负优化
而回滚。

新架构现在符合“一个原始 goal、一个语义 Actor、一条视觉执行线、小型通用工具面”
的核心思想。新增 Thread Store 和 lease 只拥有机械生命周期，不理解任务；模型职责
没有下放给 router，但模型仍同时承担意图、直接回复、是否观察桌面，以及进入 GUI
后的视觉、计划、恢复、自验和停止，长桌面任务压力依旧很高。12 条/16 KiB 窗口避免
无界压力，却还没有跨进程延续。Pi 源码本轮没有新增搬运：继续保留已引入且经 E2E
证明的原生 tool-exchange 语义；只有 Pi 的 turn/history/cancellation owner 能整体
替换并删除当前 owner、且跨任务族提高外部成功率时才搬入，不安装整套依赖、不加
Node→Python RPC、不建立第二 Runtime。

下一轮唯一最高价值实验不改生产代码：在同一可见 Electron Thread 中预注册至少
20 个随机交错 Turn，混合普通聊天、跨轮引用和至少两个不同原生桌面任务族；每个
桌面轮生成只能由外部 oracle 读取的随机 token，后续聊天必须回忆它，同时注入一次
provider 失败和一次 stale lease 回复。唯一计分是用户外部结果、窗口生命周期、
历史隔离和精确动作证据，先测出长时压力分歧层，再决定是否需要删除或替换 owner。

### 13.21 通用 Connector、原生模型协议与真实缓存观测

本轮没有为 GitHub 或任何服务增加 adapter、关键词 router、任务分类器或第二模型。
同一个 `LiveVisualActor` 在文本首轮与视觉回合都可看到两个短通用工具：
`search_connector_tools` 与 `call_connector_tool`。只有显式登记的 remote HTTP / stdio
MCP server 会进入目录；connector 结果回到同一 Actor，后续仍由它选择 reply、继续
connector 或申请桌面。`ConnectorGateway` 只是机械边界：目录、参数和结果有上限，
远端数据标为不可信，工具 ref 绑定完整 descriptor fingerprint；Gateway 重发现后，
MCP client 又在同一物理 `call_tool` 前核对完整 descriptor 与 annotations。当前 live
Actor 始终不授予 external mutation，只有 `readOnlyHint=true` 且非 destructive 的工具
可执行；整次发现/调用共享 deadline/cancel，不重试含糊远端动作。

`.mcp.example.json` 使用 GitHub 官方 remote MCP、server-side readonly 与有限
toolsets，credential 只从环境注入。仓库没有写入任何 token，也没有内置 OAuth 流程；
同一 generic 配置结构是未来其他 connector 的基础，不存在 GitHub 生产分支。

生产规模必须如实记录。相对 13.20，本轮 connector、usage 与 native provider protocol
切片为 `+2207/-114`；连同 13.20 尚未提交的 Thread/Turn 与 lease，当前完整生产工作树
相对起始 `master` 为 `+3579/-269`。测试、E2E runner、fixture、文档和
`.mcp.example.json` 不计入生产行数。净增仍然很大，主要来自新的三协议 adapter
`provider_protocols.py` 和 connector/Actor composition；这不是“代码越多越完整”的
收益，native adapter 在真实 provider 通过前只能算 provisional，下一轮不得继续扩张。

通用可见 Electron 证据分为两个有效报告：

| 报告 | 结果 | 独立证据 |
| --- | --- | --- |
| `output/playwright/client-thread-connector-e2e/20260731-211729-thread-connector/report.json` | 20 个连续聊天 Turn `20/20` | 同一 Thread、20 个唯一 run；第 18 轮引入的随机 marker 在第 20 轮精确回忆。报告末尾另有一个不计入 `20/20` 的 connector probe，模型没有调用 fixture，故整个聚合报告仍为 failed |
| `output/playwright/client-thread-connector-e2e/20260731-214024-thread-connector/report.json` | GitHub-like exact read 与 connector→Notepad 外部结果 `2/2` | append-only fixture invocation log；第二项又用真实 GUI 动作把 30-byte 随机值精确保存，文件 hash/mtime 一致 |

第二份报告的 exact read 使用 3 次模型响应；组合任务使用 19 次，执行过
`click/type_text/hotkey` 等 9 笔 applied GUI 动作。组合任务在文件 oracle 已通过时 run
仍为 `running`，因此只证明用户外部结果已经成立，不证明模型停止判断可靠。两份最终
报告均从可见 Electron 用操作系统键盘输入并以真实鼠标发送；此前 runner-error 的
迭代样本不计成功率，最终 `214024` 报告没有测试干扰。

GitHub 官方 remote 的 transport 也有独立实证。Gateway 先发现 read-only
`get_commit` descriptor，再物理调用：

```text
owner=zoujiapeng
repo=lingdong-script
sha=master
detail=none
receipt.status=succeeded
```

返回值包含经独立 `gh api` 核对的当前 SHA
`fba5d7bb6dcb13c46e93dbc29bc3cef44e560622`；receipt 同时保存 tool fingerprint 与
result hash。此前 `get_file_contents` 也完成了真实物理调用。这里没有记录 credential
值。

首个中文可见 Electron run `arp-20260731-214538-485580` 不能作为干净模型样本：
before-submit 截图已经显示目标中的中文被转换为大量 `?`。它后续虽得到 970 字符、最近
40 个 commit 的回复，但首个分歧发生在测试输入编码，必须标记 invalid/interfered，不能
归因模型、connector 或 Runtime。

随后使用 ASCII 原始目标做可见复验。报告为
`output/playwright/github-connector-e2e-ascii/20260731-220236/report.json`，run 为
`arp-20260731-220327-1ed4e4`：before-submit 与服务端 Goal Admission 均保存完整目标；
GitHub 官方 remote 有真实会话/POST；零 GUI 动作。五次模型响应后，Actor 选择了一个
当前目录未批准的工具，Runtime 以 `state_rejected` fail closed，detail 为
`Actor initial turn returned an unapproved tool`，最终回复为空。usage 为 `8126` prompt、
`539` completion、`4160` cache hit、`3966` miss、`0` creation。这个才是有效的端到端
task fail：transport 已工作，首个分歧属于模型工具选择/停止；Runtime 的拒绝语义正确，
禁止新增 `latest-SHA` formatter、列表截断、参数改写或 GitHub prompt。

只读审计仍留下两个 connector 证据/兼容债务：当前官方 GitHub E2E 报告没有把每次
tool/args/result-hash receipt 持久化到公开 trace，直接 Gateway smoke 只能补强 transport
而不能替代 run-scoped receipt；旧 `agent/mcp_tools.py` 还是绕过 Gateway 的重复 Legacy
owner。显式 config 最高优先级和 120 秒目录 cache 按完整 server 配置 fingerprint 隔离
已经修复，聚焦测试 `21 passed`，不再列作未修 P1。这些基础设施债务没有证据解释
ASCII run 的未批准工具，不能抢在 history-depth 实验前扩张 Runtime；未来若修，优先
在测试/trace 侧保存无正文的 generic physical receipt，并逐项删除重复 owner。

缓存结论也需要修正为仓库整体与当前主线两层：冻结 Legacy `agent/loop.py` 确有
cache-friendly stable prefix——在 32 KiB 前保持 append-only history，并把 session
continuity 放在前缀中保护 DeepSeek prefix cache。当前唯一活动 vNext 不走该 owner；
已删除的 `cache_strategy` 只是从未生效的标签。vNext 不发送 OpenAI
`prompt_cache_key`、Anthropic `cache_control` 或 Gemini `cachedContent`，也不为缓存
恢复无界历史或重复旧截图，只记录 provider 对自然稳定请求部分返回的 usage。

20-turn 聚合报告（含其末尾额外 connector probe）真实记录 `21626` prompt、`734`
completion、`13248` cache hit、`8378` miss、`0` creation；新鲜 Thread 的两项
connector 报告记录 `92964` prompt、`1785` completion、`53376` hit、`39588` miss、
`0` creation。说明 `agent_usage` 与 run `model_usage` 已能看见上游自动缓存命中；它不
证明 vNext 主动创建了缓存，也不支持把 Legacy append-only owner 搬回产品主线。

模型后端保留既有 OpenAI-compatible Chat Completions，并以显式
`api.protocol` / `api.api_style` 支持 OpenAI Responses、Anthropic Messages 与 Gemini
`generateContent`。`api.provider` 只保留元数据语义；wire 不按 provider、模型名或
`base_url` 推断，避免把聚合器
上的 Claude/Gemini 模型误送原生接口；adapter 当前只覆盖非流式文本、Base64 图片、
function tools 和原生 reasoning/signature replay sidecar。三条原生协议只有确定性
transport/转换测试。审计发现的 provider 误切 wire 与 Anthropic 丢失 canonical
`strict:true` 已修复，相关 5 个测试文件 `214 passed`；但没有真实 provider credential
联机 E2E，仍必须标为 provisional，不能用 Python 全量回归声称“已适配大部分模型 API”。

本轮保留：

- 同一 Actor 的两个 generic connector 工具及 read-only Gateway；fixture 两个任务族
  外部结果和 GitHub 官方物理调用已证明它不是应用专属旁路；
- 显式 MCP 配置、环境变量替换、descriptor/fingerprint 重检、单次 deadline/cancel、
  bounded untrusted result 与 receipt；
- vNext `agent_usage` / `model_usage`，因为真实长 Thread 已返回非零 cache hit；
- 删除无效 `cache_strategy` 标签；不新增主动 cache owner；
- 三条 native protocol adapter 仅 provisional 保留，默认 OpenAI-compatible 路径不变；
  provider 元数据不切 wire，Anthropic `strict:true` 不再从 canonical schema 丢失。

本轮没有因负优化回滚新的生产候选；早期 harness 失败只是测试迭代，未计入有效矩阵。
明确拒绝、从未准入的是 external mutation、GitHub formatter/参数修正、关键词预路由、
connector 失败后的 GUI 自动 fallback、远端自动重试、协议按模型名/URL猜测、主动 cache
control、无界历史和重复截图。官方 GitHub 任务失败也没有触发任何 Runtime 补丁。

当前失败分层如下：

- **模型**：20-turn 报告末尾额外 connector probe 没有产生远端调用；干净 ASCII
  GitHub run 在五次响应后选择未批准工具且未形成回复；connector→Notepad 外部成功时
  仍未停止。
- **基础设施**：Responses/Anthropic/Gemini 没有可用于真实联机验收的 credential；
  因此只能 provisional，不能归因模型或宣称成功。GitHub transport 已被物理证据排除。
- **测试干扰**：中文 GitHub run 的 before-submit 编码破坏是明确干扰；早期
  connector→Notepad runner-error 也已排除。最终两份 fixture 报告与 ASCII GitHub run
  是有效样本，没有焦点/并发干扰可解释其结果。

最终回归为 Python `3458 passed, 13 skipped, 1 warning`、renderer `234 passed`，TypeScript
typecheck 通过。它们证明合同未回退，不能覆盖 ASCII GitHub 的端到端失败或 native
provider 的未验证状态。

新架构仍符合一个原始 goal、一个 `LiveVisualActor`、一条视觉执行线和小型通用工具面。
`ConnectorGateway` 与 `ProviderProtocolAdapter` 都是机械 transport owner，不理解任务，
没有新增 planner；但模型职责和压力确实增加：它现在同时承担聊天、是否申请桌面、
connector 目录搜索、schema/参数选择、远端结果理解，以及 GUI 中的视觉、动作、自纠错
和停止。20 个纯聊天 Turn 通过只证明有界近期历史可工作；末尾 connector 未调用、
ASCII GitHub 选择未批准工具和组合任务未及时停止共同说明，当前瓶颈已更多落在同一模型
面对工具目录与长上下文时的选择压力。解决方式不是再拆角色或增加 router，而是用单
变量矩阵测清压力随历史深度是否增长。

Pi 本轮没有新增源码。继续只保留已由跨帧 E2E 证明的一笔 provider 原生 tool exchange；
只有 Pi 的 turn/history/cancellation owner 能整体替换并删除当前 Python owner、且跨
任务族外部完成率提高时才引入，不安装整套依赖、不加 Node→Python RPC、不双 Runtime。

Operit 已审查到提交 `0921f749a087c4a52a2202abdf32491ae335dd41`。本轮没有逐字复制
其 Kotlin/Android runtime：Operit 以 LGPL-3.0 发布，且整体搬运会形成第二套 provider/Agent
owner。当前 Python native adapters 只按已审查的 Responses/Claude/Gemini 协议族与
replay 思路做 clean-room 实现。下一步不是继续照搬；先用真实三家 credential 验证。
只有某段 Operit 实现能替换并删除当前 adapter、遵守许可证且取得跨 provider 实证时，
才最小搬运并在同一变更删除旧 owner；否则应回退 provisional adapter，而不是继续加码。

下一轮唯一最高价值实验不改生产代码：做严格配对的“history depth” A/B。使用完全
相同的随机 read-only fixture schema、token、模型和外部 invocation/file oracle，A 在
新 Thread 第 1 轮执行 connector read→Notepad，B 先完成 20 个随机聊天 Turn，再在第
21 轮执行同一任务；记录模型原始工具/参数、canonical 参数、物理 MCP 调用、文件终态、
terminal 与 usage。它只改变历史深度，能直接判断长上下文是否导致 connector 选择与
停止退化；在结果出来前不增加 prompt、router、摘要模型、工具筛选器或 Runtime 修正。

### 13.22 History-depth pilot：不准入 Runtime 修改

本轮按 13.21 的结论只改测试设施，生产代码为 `+0/-0`；当前完整生产工作树仍维持
13.21 的 `+3579/-269`。系统提示词、模型、Runtime、connector 工具面、Thread Store 和
桌面租约均未改变。测试 runner 现在可以用相同 fixture/token/config/task/output path，
分别冷启动两个可见 Electron 客户端：新 Thread 第 1 轮直接执行目标，或先从前端完成
20 个普通聊天 Turn 再在第 21 轮执行同一目标。目标必须等 Agent 自主 terminal，文件
严格按原文相等，GUI oracle 必须整条 trace 通过，不能再用文件先出现或 `strip()` 提前
记成功。

测试侧临时 `sitecustomize` 只在 runner 托管的 `serve.py` 进程启用。它不改请求、返回、
重试或控制流，只记录私有、run-scoped 的：模型原始 connector 参数、Gateway 实际那次
canonical 解析、receipt，以及 Actor 首轮真正放入 provider request 的 history
message count/chars/roles/hash；正文不进入公开报告。20 个历史聊天 Turn 还必须逐轮证明
零 connector、零 desktop lease、零 GUI observation/action，否则整臂无效。fixture/source/
path control 也从硬编码 `true` 改为两臂实测 hash。固定 A→B 已改成 seed 决定先后顺序；
但单对仍只能叫 pilot，只有多个 seed 的 AB/BA 对消 warm-up 后才可做因果判断。

首个完整 pilot 报告为
`output/playwright/history-depth-ab/20260731-230951-history-depth-ab/report.json`，seed 为
`12392593358426046889`。两臂 task、fixture、path、Runtime/model identity 相同并各自
冷启动；当时报告把失败 target 的 Thread ID 错从 accepted sequence 读取，因而错误写成
`separate_threads=false`。实际 target Thread 分别为
`thread-23fad055a79b48cc86fe170bc714fa2b` 与
`thread-4057ef86f5ec4fb680b03a9356d124ea`；runner 已改为从 target 回退读取，但旧报告仍必须
保留为 control projection 有缺陷的 pilot，不能重写成 formal pass。

| 臂 | Connector/历史 | GUI 与外部终态 | 首个分歧 |
| --- | --- | --- | --- |
| 新 Thread，第 1 轮，`arp-20260731-231038-e42244` | raw→canonical→receipt→fixture `1/1`，参数一致 | token 已精确写进 Notepad；打开 Save As 并写入正确绝对路径后，后续 Save proposal 多次在物理输入前被 `target_identity_changed` 拒绝；文件不存在，30 turn 耗尽 | 该样本首先是 Runtime 身份 guard；但随后同源码复验未复现，单样本不能形成通用基础设施修复 |
| 前端 20 Turn 后第 21 轮，`arp-20260731-232202-615dff` | 20 个聊天 `20/20`；target connector 链正确执行两次 | target 零 GUI 动作；两次有界重新决策仍给出非法 visual proposal，7 次模型调用后 `invalid_proposal` | 模型 proposal/tool-contract 层；不得用提示教学或 Runtime repair |

两臂都失败且失败层不同，所以这个单对既不证明 history 负优化，也不证明没有负优化。
target usage 分别为 `128428/2986/98816/29612` 与
`29656/953/24896/4760`（prompt/completion/cache-hit/cache-miss）；前者又有更长 GUI
回合，不能用 token 总数直接比较 history 成本。B 的 20 个聊天 Turn 累计
`19986/789/13504/6482`，只说明 bounded Thread 与真实 cache telemetry 仍工作。

为判断首臂 guard 是否是通用基础设施缺陷，又在相同生产源码上做了一次新鲜 Thread
诊断：
`output/playwright/target-identity-diagnostic/20260731-232607-target-identity/fresh-thread-first-turn/report.json`，
run 为 `arp-20260731-232655-3f7c7a`。它没有复现 target identity mismatch；connector
参数链再次正确执行两次，13 笔 GUI 动作均有 applied 物理证据。Save As 的 Windows
原生文件名编辑框默认选区并不等同全选，模型又先单击再输入，使可见值成为
`*.txtC:\Users\zou\cne2e-identity-8d418e.txt`，两次收到“文件名无效”仍重复同类策略，
文件最终不存在。这里 Windows 编辑控件的选区与单击语义是外部应用真实行为；
`model args → canonical → physical_attempt` 没有变形，Agent 的策略没有根据可见选区和
错误框纠正。最后一次输入在已交付 `7/38` 字符后发生 `foreground_changed`，Runtime
以 `outcome_unknown/blocked` fail closed 是正确的，自动重试可能重复部分文本。
因此不加 Windows adapter、自动全选、重复保存或坐标修正。

最早一次 suite 在任何用户任务前失败：Vite 已 ready 但 runner 经代理误判、第二个
冷启动在 Runtime ready 前查询 status、sidecar 尚未把工作目录加入 import path。这三项
均是测试基础设施干扰，已在测试侧修复并排除，不计模型或产品成功率。一次性
target-identity monkeypatch 在未复现通用缺陷后已删除；保留的是可复用的实际 history
投影、raw/canonical/receipt 参数链和严格外部 oracle。新探针又通过单轮可见 Electron
预检 `arp-20260731-235249-105256`：首轮实际 provider messages 为 1、history 为 0，
并且 connector/lease/GUI 计数全部为 0。随后两轮预检的第二轮
`arp-20260731-235909-f4d259` 精确回忆随机 marker，实际 provider messages 为 3、history
为 2 条/48 字符、roles 为 `user,assistant`，证明新投影观察到的不是硬编码上限；同时
模型为这个纯文本问题申请了 desktop lease 并观察一次 GUI，新的 text-only oracle 因此
正确将该 history prefill 判为无效。它是 oracle 的负向样本，不计能力通过率，也不触发
Runtime 修正。对旧正式 B 的 20 个聊天 run 逐项回查原始 trace、private report 和 probe，
则 `20/20` 均为零 raw/Gateway connector、零 lease/observation、零 tool attempt/GUI action；
旧 prefill 实际未污染桌面，只是当时报告没有强制这道门。聚焦回归为 `34 passed`。
发布前完整回归为 Python `3462 passed, 13 skipped, 1 warning`、renderer
`234 passed`，TypeScript typecheck 通过；这些证明累计合同未回退，不改变 pilot 的失败
归因。

本轮保留与回退：

- **保留**：严格 paired runner、随机臂顺序、实际 history hash 投影、历史聊天零副作用
  oracle、实际 Gateway canonical 解析观测、严格文件/terminal/窗口生命周期判定；
- **回退**：只服务于一次 guard 排查的 target-identity monkeypatch；
- **未准入**：任何生产提示、router、摘要模型、自动重试、Save As/Windows 专属规则、
  connector formatter 或 Runtime 修正。

当前有效失败分层是：B、新鲜诊断和两轮负向预检分别属于模型工具协议、Windows GUI
策略与不必要桌面选择；首个 A 样本出现过未复现的基础设施 guard 信号；首次启动与旧
`separate_threads` 字段属于测试干扰。没有任何一项达到“至少两个不同任务族稳定复现”
的生产改动门。

新架构 owner 没有增加，仍是一个原始 goal、一个 `LiveVisualActor`、一条视觉执行线和
小型通用工具面。模型继续同时承担聊天、connector 选择、是否租用桌面、Windows 控件
语义、视觉动作、自纠错与停止，压力很高；本 pilot 只暴露压力样本，尚未证明压力由
history depth 造成。Pi 与 Operit 计划均不变：不引入第二 Runtime；只有源码片段能整体
替换并删除现有 owner、遵守许可证且跨任务族提高外部完成率时才搬运。native provider
adapter 仍因缺真实 credential E2E 而 provisional，本轮不继续扩张。

下一轮唯一最高价值实验仍不改生产代码：把 target 改成“已存在、已打开的随机文本文件
中，用 connector 值全量替换并保存”，移除 Save As 这个与 history 无关的高方差步骤；
用当前实际 history/零副作用探针跑至少四个随机 seed、AB/BA 对消顺序。只有 fresh 基线
稳定通过且 history 臂在相同 raw→canonical→physical→file/terminal 层重复退化，才讨论
history owner；否则继续把失败留在模型样本层。

### 13.23 预打开文件基线通过；长对话 A/B 尚未形成有效配对

本轮只改 E2E runner 与其测试，生产代码仍为 `+0/-0`，系统提示、Runtime、模型、
Thread Store、connector 和工具面均未改变。history-depth target 不再把绝对路径交给模型，
也不再经过 Windows Save As：runner 在自己的临时目录内为每一臂重新写入同一个、与
答案不同的随机初值，
用 `notepad.exe <path>` 打开已有文件；任务只表达“从 connector 读取 verification，用
鼠标键盘全文替换当前记事本文档并保存”。提交前必须同时证明 fresh HWND、窗口标题含
随机文件名、磁盘 SHA 仍等于初值；终态继续要求单次真实 connector 参数链、精确文件
全文、保存后 mtime、完整 GUI receipt、自主 terminal 和前端恢复。

测试清理也从任务结果中分离：owned Notepad 必须确认 HWND 消失；只有启动 PID 与窗口
PID 相同且该 PID 只剩这一扇 owned 窗口时才允许强制终止，否则 fail closed 并停止该
pair，不能杀共享的用户 Notepad。cleanup 失败只标记 test interference，不能覆盖已经
观察到的用户外部终态。A/B comparison 同样改为 controls 未齐时只写
`pair_incomplete_before_paired_target`；旧 `20260801-011434` 报告是在修正前生成，仍保留
其中错误的 `history_arm_regressed_in_this_pair` 字段，不能把旧 artifact 重写成因果结论。
两臂的 Runtime idle、托管 Electron 进程清理和无 runner failure 也属于 paired validity，
不是模型能力结果。聚焦测试为 `16 passed`。

最先两次 smoke 没有计入矩阵。`20260801-005351` 在用户目标前被 Windows 前台锁和 Vite
重启阻断，属于测试基础设施；用外部桌面控制只激活测试 Electron 后，
`20260801-010146` 的 fresh target 到达模型，但首次响应同时请求多个 desktop observation，
被现有单观察合同正确拒绝；history 首轮又是 provider `Connection error`。随后将同一个
workspace Vite 作为现有测试依赖在整个矩阵期间复用，`_url_ready` 与 listener identity
均实测通过；没有为此新增生产或测试 owner，矩阵结束后已停止该进程。

四个预注册 seed 按 fresh/history/history/fresh 对消顺序尝试；没有一个 pair 同时到达
两边 target，因此本轮没有有效 history-depth 因果样本：

| seed / 顺序 | fresh target | history 路径首个分歧 | 归因 |
| --- | --- | --- | --- |
| `91001` / F→H | 首臂 Vite 已打印 ready 但探活失败，target 未开始 | 11 个聊天 Turn 全部 reply 且零副作用；第 12 轮却申请 lease 并观察桌面，随后 `mission_verified` | fresh 为测试基础设施；history 为模型把普通聊天错误升级为 GUI |
| `91002` / H→F | `arp-20260801-012106-f70e19` 完整通过 | 第 11 轮 `arp-20260801-011942-105d7a` 在零工具/零动作前 `Connection error` | 模型 provider/API 基础设施，pair 无效 |
| `91003` / H→F | `arp-20260801-012620-d53674` 完整通过 | 第 1 轮 `arp-20260801-012451-4631e6` 在零工具/零动作前 `Connection error` | 模型 provider/API 基础设施，pair 无效 |
| `91004` / F→H | `arp-20260801-012829-12e83a` 完整通过 | 前 5 轮通过，第 6 轮在前端提交路径发生 UIA `COMError: 事件无法调用任何订户`，没有 run id | 测试干扰，pair 无效 |

三个有效 fresh target 为 `3/3`，耗时 `46.471 / 44.056 / 47.536` 秒。每次都是 raw model
arguments→Gateway canonical parse→真实 fixture 调用各 `1/1`，已有文件精确等于随机
connector 值，动作均为 `hotkey → type_text → hotkey`，模型自主 `done`，窗口恢复、
Notepad 清理和进程清理全部通过。这个重复外部成功证明预打开文件是比 Save As 更低方差、
同时仍保留用户鼠标键盘要求的测试基线；应保留在测试侧。它不证明 Notepad 类任务需要
适配，也不准许把 `Ctrl+A/Ctrl+S` 写入 Runtime 或提示。

冻结分支也完成了只读总审计，结论为 `0 cherry-pick / 0 merge / 0 Runtime 修改`：

- `codex/vnext-agent-core` 的 branch-only 生产代码约 13,342 行，主要是 target discovery/
  activation/launch、profiler、runtime selector、第二 verifier、shadow/bridge/action-history
  等重复 owner；它没有当前 Thread/connector 路径，也没有 maximize primitive，不能解释
  本轮失败；
- `fix/pr10-python-ci` 只在已删除的旧 `windows_facts.py` 增加 drive-qualified 校验；当前
  target identity owner 不存在同一缺口；
- `test/pr15-legacy-electron-oracle` 是绑定旧 Legacy SHA 的测试分支，其 external oracle、
  trace binding 和 unknown-no-replay 语义已由当前 harness/replay policy 覆盖；
- 两条 recovery/C4 的 canary、input hook、composition/evidence 已被 master 后续强化；
  真正残留的是已主动删除的 `LiveVisualObligationVerifier` 第二模型角色和前端 Runtime
  selector，恢复会直接违反单 Actor、单产品入口。

本轮保留：预打开文件 target、actual initial SHA/title precondition、独立 cleanup validity、
controls-first comparison 和既有 raw/canonical/physical/file oracle。未准入或回退：任何
冻结分支代码、Vite 新 owner、history 摘要/重试、聊天关键词路由、提示教学、Windows
快捷键规则及 Runtime 修正。当前失败分层为一个模型聊天误用桌面、两个模型
provider/API 连接错误、一个 UIA 提交错误和一次 Vite/前台测试干扰；fresh GUI task
本身没有失败。

最终源码回归为 Python `3464 passed, 13 skipped, 1 warning`、renderer
`234 passed`，TypeScript typecheck 通过。renderer 曾在与 typecheck 并行时只有
`store.test.ts` 超过 5 秒；串行原样重跑为 `234/234`，因此归为测试调度干扰，没有调高
超时或修改产品代码。

新架构 owner 未增加，仍是一个原始 goal、一个 `LiveVisualActor`、一条视觉执行线和小型
通用工具面。模型仍承担聊天、connector、是否观察桌面、GUI 策略、自纠错和停止，压力
很高；fresh `3/3` 说明这个组合任务在无历史时处于当前模型能力范围，但没有有效 paired
history target，不能说压力来自 history depth。Pi/Operit 计划不变：不引入第二 Runtime、
RPC 或 Android owner；只有源码能整体替换并删除当前 owner，且跨至少两个任务族提高
外部完成率时才准入。

下一轮唯一最高价值实验仍为 test-only：把 prefill 收到 **6 个全部零副作用的成功聊天
Turn** 后执行同一 preopened target。当前上限是 12 条原生消息，6 个完整 user/assistant
Turn 已填满模型可见 history 窗口；用四个 seed 做 AB/BA 对消，能把“最大可见 history
压力”与 20 次连续 provider/UIA 暴露分开。20-Turn durability 失败继续作为真实聊天可靠性
缺口单独保留，但不得通过 Runtime 自动重试、删题、提示或 router 清洗。

### 13.24 六轮原生历史通过；客户端只修通用交互真实性

本轮把 13.23 的唯一实验收敛到 6 个完整成功聊天 Turn。测试仍从可见 Electron 前端用
Windows 键盘输入、鼠标发送；聊天自动 oracle 只要求 Server Thread 中存在非空成功回复，
同时强制 connector raw/Gateway call、desktop lease、GUI observation、Runtime tool
attempt 和 applied GUI action 全部为零。六个问题覆盖日常聊天、礼貌改写、翻译、日常
解释、短规划和比较，不把答案或关键词交给生产模型。target 仍是相同的预打开随机文件、
只读 connector 和全文替换保存，外部 oracle 未放宽。

history control 不再接受“message count 大于零”。它现在要求实际 provider projection 为
精确 12 条、角色严格按六组 `user, assistant` 交替、字符数位于 `1..16384`；这证明六个
完整 Turn 已填满当前模型可见消息上限，而不是只证明 Thread UI 中看起来有六轮。一般
20-Turn durability runner 保持不变，仅 history-depth A/B 的默认值从 20 收到 6，减少
provider/UIA 累计干扰。

四个 seed 得到两个有效 pair：

| seed / 顺序 | 聊天路径 | fresh target | history target | 结论 |
| --- | --- | --- | --- | --- |
| `92001` / H→F | 首轮寒暄得到回复，但申请 lease 并观察桌面 | 首轮同时请求多个 desktop observation，合同拒绝 | 未到达 | 两个模型工具选择错误，pair 无效 |
| `92002` / F→H | `6/6` 非空回复且全部零副作用 | 完整通过 | 12 条/1313 字符历史后完整通过 | 有效 pair；没有 history 负信号 |
| `92003` / H→F | 新六类问题 `6/6` 非空回复且全部零副作用 | 完整通过 | 12 条/423 字符历史后完整通过 | 有效反序 pair；没有 history 负信号 |
| `92004` / F→H | 六轮均回复，前五轮零副作用；第六轮“陪我聊两句”却申请 lease/观察桌面 | 首轮同时请求多个 desktop observation，合同拒绝 | 未到达 | 两个模型工具选择错误，pair 无效 |

两个有效 pair 的四个 target 为 `4/4`：connector raw→canonical→fixture 各 `1/1`，磁盘
全文精确等于随机值，GUI receipt 含 `type_text` 与保存按键，模型自主 terminal，窗口恢复、
Notepad 清理和 Electron 进程清理全部通过。因此当前证据支持保留 12-message 原生 history
owner，不支持摘要、扩大上限、删除历史或增加模型角色。四组共得到 19 个成功聊天回复，
其中 17 个严格 text-only；两个错误升级为桌面的样本首分歧均是模型输出，不是客户端在
发送时主动隐藏。客户端仍只在真实 `desktop_lease_requested` 后最小化，这条窗口合同不改。

客户端审计同时发现三个确定性的通用体验缺口，均在现有 Electron owner 内最小修复：

- 消息框 Enter 现在检查 IME composition/229，中文候选上屏不再误发送；
- `arp-last-goal` 在成功取得 run id 和“新建对话”时清除；提交失败仍保留原输入，避免
  已发送任务在重启后伪装成未发送草稿；
- 聊天卡复用任务栏已经使用的 `classifyTaskOutcome`，所以 `reply/done` 携带显式失败
  结构时不会显示成功；失败结果直接可见，并提供“再次编辑”将原目标放回输入框，但绝不
  自动重发。可见 `92004` 失败终态截图显示精确合同错误、再次编辑和运行详情入口；相邻
  按钮间距由一条通用 CSS 规则修正。明确带 `runtime_version=vnext` 与
  `no_legacy_fallback=true` 的直接聊天字符串不再被解析为结果协议，用户要求展示
  `{"ok": false}` 一类 JSON 时不会被误判失败；对象型失败 envelope 与独立 error 仍然
  是失败。共享 classifier 一度改成忽略所有 `reply` 字符串，但审查证明 legacy 安全停止会
  以字符串 envelope 到达任务栏；该负优化连同测试已完整回退。`waiting_external`、
  `canceling` 和未知过渡状态在真正终态前继续显示为活动中。失败提交的草稿不再被旧 run
  水合清空；提交进行中，“再次编辑”和“新建对话”都会锁定，避免请求返回后覆盖草稿或
  把旧 Thread 接回新对话。

生产代码切片为 `+70/-26`：`PlatformWorkbench.tsx +44/-20`、`floating.css +4/-0`、
`taskOutcome.ts +20/-0`、`taskbarNotification.ts +2/-6`。测试/harness 为 `+254/-11`；
系统提示、Runtime、Thread Store、
connector、工具 schema、desktop lease 和模型调用次数均为 `+0/-0`。保留的实验是
6-Turn 默认、精确 12-message
control、IME guard、真实 outcome、显式再次编辑和成功提交清理。未准入或回退的是聊天
关键词 router、意图分类器、提交即隐藏、自动重试、自动新建 Thread、history 摘要、提示
教学、窗口 watchdog 以及任何模型纠错 Runtime。全局忽略 `reply` 字符串失败 envelope 的
候选因会掩盖 legacy 安全停止而按负优化完整回退；其余保留项没有观察到生产负优化。

失败分层：两次多 observation 和两次普通聊天申请桌面属于模型；本轮有效 pair 没有
provider 连接错误；测试启动仍表现出较长冷启动/输出缓冲，但没有污染有效 arms。新架构
仍是一个原始 goal、一个 `LiveVisualActor`、一条视觉执行线和小型工具面。模型继续承担
聊天与工具模态选择，两个误用桌面样本说明压力真实存在；但两个双向有效 pair 证明最大
12-message 原生历史没有使当前组合任务退化，不能把压力归咎于 history owner。

Pi/Operit 计划不变：不引入第二 Runtime、RPC、Android owner 或长期双写；只有外部 E2E
证明源码片段能整体替换并删除当前 owner、且跨至少两个任务族提高完成率时才准入。

最终回归为 Python `3464 passed, 13 skipped, 1 warning`、renderer `243 passed`、
TypeScript typecheck 通过；客户端 outcome/草稿聚焦回归为 `35 passed`，history harness
聚焦回归为 `16 passed`。

下一轮唯一最高价值实验转向 Codex 式会话连续性，仍先 test-only：通过真实前端建立带
随机 marker 的成功 Thread，正常关闭并重启 Electron 及其托管服务，然后检查旧消息是否
可见、下一条提交是否仍能绑定原 Thread、模型能否从恢复历史精确回答 marker。当前
Thread Store 是进程内存且客户端保存 stale thread id，代码审计预期这里会分叉；必须先
拿到可见重启证据，不能直接增加持久化 owner、自动创建新 Thread 或解除公共 trace 脱敏。

### 13.25 产品命名与仓库治理收口；同一客户端长对话加桌面任务复验通过

本轮没有继续扩 Runtime、工具面或提示词，而是先清除会误导后续维护者的治理与命名债务。
远端开发分支现只剩 `master`；`codex/vnext-agent-core`、`fix/pr10-python-ci` 与
`test/pr15-legacy-electron-oracle` 已按各自精确 tip 删除。PR10/PR15 的可恢复历史仍由
`archive/pr10-vnext-agent-core-20260726` 与
`evidence/pr15-legacy-electron-oracle-20260726` 只读 tag 保存，fix 分支提交则可由前者的
祖先链恢复。旧 Issue 已按当前事实重写或关闭：开放项只剩 #11、#39、#40、#43、#44，
不再把 Legacy、旧 Verifier 或已完成 C 阶段阻塞描述为当前产品方向。`4f93672` 的最新
Actions run `30663041917` 是账户 billing/spending limit 导致的零步骤终止，不是代码失败，
更不是绿色检查。

产品 composition owner 从 `orchestrator/vnext_live_canary.py` 改名为
`orchestrator/live_agent.py`；配置与状态 canonical key 从 `live_canary_enabled` 改为
`live_agent_enabled`，默认仍为 `true`。旧配置键只在 `config.py` 输入边界兼容：old-only
在内存中迁移并在下次保存时只写 canonical key；双键值和精确类型都相同时接受并去掉旧键，
冲突则在修改内存或磁盘前 fail closed，API 返回 400。Runtime、admission、status 与前端
合同都只读取新键。真正的 canary campaign、runner 与报告 schema 仍保留 canary 名称；一次
把这些实验资产也统改为 live-agent 的过宽候选已完整回退。没有新增关键词、应用 adapter、
任务分类、坐标规则、重试规则或提示教学。
13.10–13.24 中出现的旧 owner/event/key 名称是当时证据的追加式历史记录，也不机械改写。

生产代码切片为 `+88/-55`，净增 33 行；其中 owner 文件改名及其事件/错误文本为
`+35/-35`，新增量主要是 30 行通用配置边界。`config.example.json`、测试、runner 与文档
不计入该数字；系统提示词为 `+0/-0`。保留的是 canonical 产品命名、旧键输入兼容和已有
单 Actor 行为；回退的是过宽 canary 资产改名。没有为了 E2E 结果加入任何生产策略。

本地验证结果：focused orchestrator/Actor/broker/adapter/client 合同 `420 passed`，真实
canary campaign/harness `159 passed`，renderer `243 passed`，TypeScript typecheck 通过。
第一次 Python 全量为 `3466 passed, 2 failed, 13 skipped`：一项是本轮漏改的错误文本期望，
修正测试后清零；另一项是在重型全量负载下 stdio fixture 暂时宣告零工具，而同一聚焦测试
此前和随后都通过，属于测试调度干扰。两项原样复跑 `2 passed`，最终完整重跑为
`3468 passed, 13 skipped, 1 warning`。

首次推送 `87f04fd` 后，Actions run `30688219189` 已实际执行：frontend 完整通过，Python
出现 7 个测试基础设施失败。6 个来自 CI OpenCV 把 `HoughLinesP` 返回为 `(N,4)`，旧 Paint
oracle 只接受 `(N,1,4)`；1 个来自 composition 测试未冻结虚拟屏幕，GitHub runner 的
`1024×768` 把模拟窗口裁小。只在 test-only 层把 Hough 输出统一 reshape 为四列，并显式
冻结该测试的虚拟屏幕；相关两文件回归 `95 passed`。没有修改 Paint/井字棋产品路径或
Runtime，也没有为模型适配视觉几何。
修复提交 `65fdce0` 的后继 Actions run `30688550071` 随后完整通过；frontend 与 Python
job 都实际执行全部步骤并为 `success`。该绿色证据只绑定精确提交，不是永久 HEAD 声明。

可见 Electron 最终有效报告为
`output/playwright/live-agent-name-migration-e2e/20260801-142330-history-depth-ab/report.json`。
测试从真实前端用 Windows 键盘输入与原生鼠标提交，顺序为 fresh→history：fresh 的
connector→预打开 Notepad 任务通过；第二个冷启动客户端的 6 轮聊天 `6/6`，每轮均为非空
回复、零 connector、零 desktop lease、零 GUI observation、零 Runtime tool attempt；随后
第 7 轮在精确 12-message/699-character history 后再次完成同一组合任务。两臂均各有且只有
一次 connector raw→canonical→physical 调用，磁盘文件精确等于随机值，GUI receipt 都是
`hotkey → type_text → hotkey`，模型自主 terminal，窗口恢复、Notepad 与 Electron 清理均
通过。

同一组测试的前三次无效尝试没有混入成功率：`140029` 在任何 run 前被 Vite ready probe
阻断，`140631` 在任何 run 前被 Windows `SetForegroundWindow` 拒绝，均为测试基础设施；
`141407` 的首个普通聊天确实到达模型，但模型错误申请 desktop observation，属于模型模态
选择，随后另一臂又被 Vite 探活阻断。复用一个已验证 workspace Vite 并只在两个冷启动
Electron 出现时各聚焦一次后，`142330` 得到完整有效 pair；测试结束已按精确 PID 验证并
停止该 Vite。没有用 Runtime 修正聊天误选桌面。

新架构仍符合一个原始 goal、一个 `LiveVisualActor`、一条视觉执行线和小型通用工具面的
核心思想；本轮只把“实验旁路”名称还原为产品 owner，没有增加模型角色。模型仍承担意图、
聊天/connector/desktop 模态选择、视觉动作、自纠错与停止，职责和长任务压力都很高；有效
6 轮样本证明最大可见 history 本身没有造成当前组合任务退化，但 `141407` 的误申请桌面
继续证明模态选择压力真实存在。不能据此增加 router 或提示教学。

Pi/Operit 引入计划不变：本轮 `+0/-0`。不搬运第二 Runtime、RPC、Android owner 或整套
依赖；只有某段源码能整体替换并删除当前 owner，且在至少两个未知任务族的外部 oracle E2E
中提高完成率时才准入。

下一轮唯一最高价值实验仍是 test-only 的 Codex 式会话连续性：在真实前端建立带随机
marker 的成功 Thread，正常关闭并重启 Electron 与托管服务，再验证旧消息可见、下一条
提交仍绑定原 Thread、模型能从恢复历史精确回答 marker。先测出进程内 Thread Store 与
stale thread id 的真实分歧，再决定是否需要替换 owner；不得预先增加持久化、自动新建
Thread、router 或提示词。

### 13.26 有界会话重启与未发送草稿通过；全量探针继续按首分歧归因

本轮先执行 13.25 冻结的正常重启实验。失败基线
`output/playwright/client-thread-restart-e2e/20260801-153038-thread-restart/report.json`
在正常标题栏关闭、重新启动 Electron 与托管服务后得到旧 `thread_id` 404；首分歧明确在
服务端 Thread 连续性，不是模型、GUI 动作或应用状态。准入候选没有新增 memory owner，
只让既有 `AgentThreadStore` 把最多 100 个 Thread 各自最近 6 个成功 Turn（12 条原生消息、
总计最多 16 KiB）及已完成 run 投影原子写入被 Git 忽略的私有 checkpoint。失败、取消、
空结果、活动 Turn、截图、connector 正文和 trace 正文均不写入；损坏、重复、超限或类型错误
fail closed。写盘失败是 best-effort，不能阻断聊天。启动后仍由同一 owner 恢复原
`thread_id`、成功结果和 provider history，不增加 summarizer、第二模型或执行选择。

修复后的正式报告为
`output/playwright/client-thread-restart-e2e/20260801-194535-thread-restart/report.json`：
测试从可见前端用 Windows 键盘输入并以真实鼠标发送，正常关闭后观察到新的 Electron、
服务 PID 与 HWND；旧消息重新可见，继续提交仍绑定同一 Thread，模型收到的两条历史消息
内容/hash 精确，并正确回答随机 marker。随后同一恢复 Thread 自主选择只读 connector，
再用 GUI 把随机字节精确写入 Notepad；connector receipt、物理键盘回执与外部文件 hash
一致。聊天连续性与 connector→桌面文件是两个不同任务族，满足本轮最小 owner 准入门。
紧邻它的 `20260801-194425-thread-restart` 在 Electron PID/HWND 均为 0、任何 run 尚未创建前
被 Vite 探活阻断，严格记为测试干扰；同源码原样重跑后通过，未因此修改产品或放宽 oracle。

客户端还修复一个同 owner 的确定性体验缺口：输入框内容变化时持续 best-effort 保存已有
`arp-last-goal`，而不是只在执行时写入。未发送随机草稿在正常关闭/重启后逐字恢复且没有
创建 run；成功发送和“新建对话”仍清除。可见 Electron 报告为
`output/playwright/client-draft-restart-e2e/20260801-170711-draft-restart/report.json`。
这不是长期 memory，也没有改变消息发送、窗口租约或模型 prompt。

冷启动期间真实服务曾超过旧 30 秒 readiness，导致 Electron 把健康但仍在导入的托管服务
判死。默认等待只在既有 Electron process owner 中从 30 秒放宽到 120 秒；同源码正常重启的
两族 E2E 随后通过，因此保留。测试 runner 每次点击前重新聚焦被测前端，避免上一个桌面
动作把焦点留在目标应用；Paint/网易云外部 oracle 只记录一次截图异常，不再用异常覆盖此前
有效判定。后两项都只提高测试真实性，不改变生产 Agent。

当前源码重新跑完整可见受控矩阵：
`output/playwright/client-visual-e2e/20260801-183951-suite/suite-report.json`。结果为 `6/10`：
随机试卷 `6/6`，precise click、ordered clicks、drag、slider 和 dynamic hold 通过；四个
corridor 失败。四次失败的 raw model points、canonical points 与 physical attempt 逐点
一致，可信浏览器 pointer path 的最小余量分别为 `-84/-92/-27/-94`，首分歧是模型视觉
几何与停止，不修改坐标映射、path、提示或 Runtime。

真实应用探针也全部保留失败：Calculator 随机 `43×17` 的应用显示为 `44` 而非 `731`，
模型重复点击同一清除位置；Paint 只有 4 个红像素、没有任何合格对角线或中心红 X；QQ 群
摘要在零 GUI 动作前因未批准工具结束；网易云在零 GUI 动作前选择 connector 元工具并提交
错误 schema，歌曲播放和 5% 音量均未成立。对应正式报告位于
`output/playwright/client-e2e/20260801-185226-calculator-random-43x17/report.json`、
`output/playwright/client-paint-game-e2e/20260801-190517-paint-tic-tac-toe/paint-report.json`、
`output/playwright/client-qq-groups-e2e/20260801-190819-qq-groups/qq-groups-report.json` 与
`output/playwright/client-netease-music-e2e/20260801-191108-netease-music/netease-music-report.json`。
这些都属于模型参数、模态/工具选择或停止失败；没有应用 adapter、router、答案修正、重复
点击规则或软件 prompt 准入。

一次 6-Turn history-depth A/B 没有形成有效 pair：history 目标错误申请桌面，fresh
connector 目标也没有调用 connector。因此报告
`output/playwright/client-thread-connector-e2e/20260801-180756-history-depth-ab/report.json`
只证明观测链工作，不能证明 history 正负因果。usage 共 `8803` prompt tokens，其中
`6336` cache hit、`2467` miss；上游自动前缀缓存真实生效，但项目仍没有 Reasonix 式手工
cache key，也不为省钱恢复无界历史。第 6 轮 prompt 上升到 `4911`，说明模型上下文压力
值得继续测，不能先删历史或增加摘要模型。

仓库治理同时从五处复核并收口：本地 heads、`origin` heads、GitHub branches API 和默认
分支都只剩 `master`，只有一个 worktree，开放 PR 为零。两条本地 recovery 独有 tip 先保存为
`archive/c4-canonical-from-d418-local-20260801` 与
`archive/c4-fa2e9caf-local-20260801` 注释 tag，再删除所有剩余本地历史分支；tag 只作只读
恢复输入，不是开发分支。开放 Issue 保持 #11、#39、#40、#43、#44，不因单次样本关闭。

生产代码切片为 `+275/-21`：`AgentThreadStore/server +265/-19`、Electron readiness
`+1/-1`、未发送草稿 `+9/-1`。系统提示、模型角色、Runtime 工具面、视觉执行线、坐标/
按键实现与 connector schema 均为 `+0/-0`。E2E runner 为 `+773/-16`，自动化测试为
`+344/-0`。只读审查发现并在发布前修复三个 checkpoint 健壮性问题：删除空 Thread 的同步
写盘、跳过无成功历史的 Thread、把非字符串 status 统一拒绝为 `AgentThreadError`；同时让
恢复成功投影清除 paused/current node/current tool，避免矛盾终态。

完整本地发布门为 WinPilot `3478 passed, 13 skipped, 1 warning`、根测试 `2508 passed`、
renderer `244 passed`、TypeScript typecheck、renderer/Electron build 与 CLI/AI build
全部通过；checkpoint 审查修复后的聚焦测试为 `9 passed`。GitHub 绿色状态必须绑定发布后的
精确 SHA 单独查询，不能继承先前提交。

本轮保留：有界私有 continuation checkpoint、精确 terminal 恢复投影、未发送草稿、120 秒
冷启动 readiness、runner 每次导航前聚焦和不抹除有效 oracle 的瞬时错误记录。负优化并已
回退：把“查看运行时状态”改成泛化的“打开资料”（审查证明语义更弱）、driver 启动后固定
等待（造成 468 秒 renderer churn）、为多行草稿补 Shift+Enter 键盘规则，以及任何根据上述
失败修改 Runtime 的候选。

新架构仍是一个原始 goal、一个 `LiveVisualActor`、一条视觉执行线和小型通用工具面。
checkpoint 位于已有 Thread owner，没有新 planner；模型继续承担意图、聊天/connector/GUI
模态选择、视觉几何、自纠错与停止，职责很多、压力高，但这些正是不可用预分类器替代的
语义职责。当前模型在 corridor、算术、QQ/网易云工具选择上的失败不应下沉到机械层。

Pi/Operit 源码计划不变：不安装整套 Pi、不引入 Node↔Python RPC、Android runtime、第二
模型或长期双写。只审查能够整体替换并删除现有 provider/Actor lifecycle owner 的通用片段；
必须在至少两个不同任务族的外部 oracle E2E 中提高完整成功率后才准入。

下一轮唯一最高价值实验仍为 test-only：做多次、反序的 history/fresh 配对。每个历史臂先
从真实前端完成若干随机聊天，正常重启恢复同一 Thread，再分别执行一个随机只读 connector
任务和一个简单桌面文件任务；严格记录实际 provider history、usage/cache、工具选择与外部
oracle。只有形成有效重复 pair，才判断有界连续性是否在长对话下压低自主工具选择成功率；
本轮不再增加生产代码。

### 13.27 canonical 区域截图通过真实前端、物理坐标与独立像素 oracle

本轮只推进一个冻结候选：让当前目录内已有的 `screenshot` 节点支持用户从属性面板直接框选
区域，并证明这一个区域从前端到物理工件没有发生坐标漂移。没有改 Agent prompt、模型工具面、
任务路由、应用 adapter、坐标 offset、snap、答案修正、重试规则或 terminal；也没有开放历史
图片模板节点。实现仍只有一个 Electron 选区 owner、一个 canonical screenshot action 和一个
`ArtifactStore` 工件 owner。

首个可见基线在 125% DPI 下能画出选区，却在确认时 fail closed 为
`selection display geometry changed`。首分歧位于 Electron/Windows overlay 的内容边界：无框
窗口仍带 invisible thick frame，并被 1536x816 work area 裁短，不是模型、Renderer 参数或
截图 Runtime。尝试原生 fullscreen 后仍受工作区限制，因此完整回退；没有用比例、offset、
clamp 或变形预览掩盖错误。最终保留的通用修复是 `frame:false, thickFrame:false`，窗口显示后
用 `setContentBounds(display.bounds)` 覆盖整块显示器，并在确认时要求 content bounds 与选区
开始时的 display bounds 精确相同。每块屏幕按唯一 `display_id` 绑定缩略图，local DIP rect 只
通过 Electron 的 `dipToScreenRect` 转换一次为 `screen_physical_px`；拓扑、缩放、来源、宽高或
边界不明确都 fail closed。

Renderer 新增同一个 screenshot 属性面板的“全屏/框选区域”和整数 `x/y/w/h`，取消或错误不
改原参数；运行时不会复用编辑预览。编译器只做字段改名 `w/h -> width/height` 并带上坐标空间。
Python bridge 不再把 malformed region 擦掉后静默降级全屏；`ArtifactStore` 作为唯一裁剪、
落盘、hash owner，拒绝未知 scope、bool/非整数、非正数、跨屏、越界和捕获尺寸漂移。已有图片
参数仍显式请求预览以保持兼容，但没有因此把隐藏历史节点放回目录。

正式可见报告为
`output/playwright/offline-screenshot-region-e2e/20260802-122544/report.json`。用户从真实
Electron 前端先点“框选区域”再按 `Esc`，主窗口恢复，原参数不变且没有新 run；随后
再次框选并保存物理区域 `x=16,y=130,w=934,h=182`，前端校验通过后点击“运行工作流”。run
`arp-20260802-130804-ecc3bd` 的节点全部 `ok`，`trace_integrity=complete`，模型调用为 0。
action input/target/result、manifest 与 evidence 的 region 全部一致，artifact
`art-fd7b5d391bc94af7` 为 934x182。

正式 fixture 沿用了既有 click 流程并执行了一次 click helper；Runtime 外部 target JSON 证明
它没有触发靶场按钮，所以没有污染 screenshot 的独立像素 oracle，也不计为本 case 的 DUT。
后续可把 fixture 缩成 `start -> focus_window -> screenshot -> end`，但当前证据不能顺带把
`focus_window` 或第二次 `click` 计入矩阵。

Runtime 之外又用 `mss` 按同一物理区域重抓。两个 PNG 解码后的 RGB 像素逐点相同，像素
SHA-256 均为 `57d2bf67fcb8f14488b882a32864b09136a069ae96857ac98bbcaa7908ade743`；
工件文件 SHA-256
`4cce99e19d25549d1666d6d9cf03047128c4fb3bc54b9e87321c530f9144ab0d` 也与 manifest/evidence
精确一致。12 个由随机 marker 决定的颜色各有 3025 像素并按严格 x 顺序出现。另一次真实拖选
进入 Windows work-area 下方，得到 `x=50,y=1030,width=350,height=35`，证明 overlay 不是只
覆盖可用工作区。当前机器只有一块 1920x1080、125% 屏幕，第二显示器、混合缩放和负坐标
严格记为 `infrastructure_unavailable/not_run`；合成覆盖不计 E 级通过。

本轮保留：唯一 selector、真实 display source 绑定、一次 DIP→physical 转换、严格 fail-closed
region contract、唯一 artifact owner，以及重复全套 renderer 时稳定复现超时后给一个重型
test-only 宏 UX case 的 10 秒上限。负优化并已回退：原生 fullscreen overlay；也没有保留任何
坐标补偿。一次 marker 选择重试因诊断超过 runner 的 60 秒 selector 超时，未创建 run/artifact，
随后同源码立即通过，归类为测试干扰。最终有效闭环的首个产品失败属于 Windows/Electron
窗口几何基础设施；修复后 26/26 oracle 通过。模型未参与，所以本轮没有模型失败样本。

生产代码精确切片为 `+508/-248`（净增 260）：其中 `main.ts` 是 `+266/-221` 的重复 selector
收敛；其余为截图属性面板、canonical 字段映射和严格 artifact region 合同。测试/靶场为
`+289/-7`。完整本地发布门为 renderer `320 passed`、根测试 `2508 passed`、WinPilot
`3604 passed, 13 skipped, 1 warning`，TypeScript typecheck、renderer/Electron build 与
CLI/AI build 全部通过。WinPilot 唯一 warning 是既有 `mss.mss` deprecation。

严格离线节点独立矩阵因此只从 `1/19` 提升为 `2/19`：`click` 与 `screenshot`。新架构仍是
一个原始 goal、一个 `LiveVisualActor`、一条视觉执行线和小型通用工具面；这轮能力属于完全
离线低代码/重放基础设施，没有增加模型角色或默认提示词。模型仍承担意图、聊天/connector/
GUI 模态选择、视觉动作、自纠错和停止，职责与长任务压力仍高；本轮没有把任何压力转嫁给
分类器或机械层。trace 中仍有历史字段 `runtime_version:"legacy"`，虽然实际走 canonical ARP
兼容路径；这是命名债务，不影响本轮结果，也不在本轮顺手迁移。

Pi/Operit 引入计划不变：本轮 `+0/-0`。不搬运第二 Runtime、RPC、Android owner 或整套 provider
栈；只在某段源码可以整体替换并删除现 owner，且至少两个不同任务族的外部 oracle E2E 提高
完整成功率时才准入。

下一轮唯一最高价值实验是不改生产代码，从可见 Studio 独立验收 canonical `input_text`：在
属性面板填写当轮随机 ASCII、中文、emoji 与换行组合，运行
`start -> focus_window -> input_text -> end`，由 Runtime 外部 JSON/文件 oracle 精确核对文本、
长度和 SHA；目标/焦点缺失必须 fail closed，空串行为必须明确且不能静默变成另一种动作。
只有首分歧落在通用基础设施才修改对应 owner；不并行开放图片模板、不修改跨 decision held
gesture，也不增加字符答案修正或键盘重试规则。

### 13.28 新 Goal 主线与旧 Agent 物理解耦；第一批旧入口净删除

本轮目标不是继续给 vNext 加 owner，而是先保证它可以在旧 Agent 缺席时存在。依赖审计发现
新线仍从 `winpilot.agent.frame_authority` 取一个纯机械 owner，Electron 还保留无人消费的
`python -m agent` 子进程、五个 agent IPC、环境变量自动启动、local start API 和死事件链；
Renderer 也保留两个没有生产调用者且默认提交 legacy 的 client 方法。它们构成了先删消费者、
再删 provider 的第一批安全切片。

`FrameAuthority` 现归属 `visual_runtime`。旧 `AgentRunner` 可以反向导入共享机械 owner，
`live_agent` 与 atomic desktop adapter 不再经过旧命名空间。新 AST 合同从
`winpilot.orchestrator.live_agent` 广度遍历全部内部静态 imports，并对每个可达模块检查
`winpilot.agent` 的静态 binding 与可解析动态 import；当前审计闭包为 119 个模块、零旧边。
另一个 subprocess import hook 完全禁止 `winpilot.agent*`，仍必须加载 server、注册
`/api/v2/goals/runs` 并执行一次模拟 vNext admission。为满足这个物理合同，server 的旧
Runner/router/session/adapters 只在旧路由调用时加载；ChatGPT MCP 也只在外部 bridge 真正
创建旧 `ToolSession` 时局部加载。MCP 协议本身与 connector gateway 未被删除。

实际删除包括 `python/agent/__main__.py`、Electron autonomous-agent process/timeout/token/state、
`AGENT_GOAL`、agent start/stop/guidance/chat/status IPC、`/api/agent-start`、preload/type/event
listener，以及 Renderer `startGraph`、`compileTask` 和只为它们存在的测试合同。按 Git
rename detection 统计，生产代码为 `+55/-678`、净删除 623 行；`FrameAuthority` 文件迁移只
改了 1 行归属说明。测试代码为 `+117/-128`。系统提示、模型角色、视觉工具面、坐标/键盘实现与 Pi/Operit 源码均为
`+0/-0`。

物理 E2E 使用当前源码完整 build 和 unpacked Electron。第一臂从真实窗口输入并发送聊天
目标，产品在模型调用前明确失败：`deepseek-v4-flash` 没有配置可用 vision-mode endpoint。
随后关闭测试进程，把打包测试目录中的整个 `winpilot/agent` 可恢复改名，再重新启动完整
Electron。前端仍显示运行时在线、恢复未发送目标，并从真实输入框再次提交到同一个 vNext
配置错误；之后恢复测试包目录。由于旧包物理不存在，这证明 Electron→managed server→Goal
Admission 不再暗中依赖旧 Agent，但不能把两臂记为聊天或 GUI 任务通过。没有模型调用、没有
桌面 lease、没有外部应用终态；失败属于 provider 配置基础设施，不是模型行为，也不是本轮
删除回归。本轮遵守“暂不换模型”，没有增加 text/GUI 分类、自动 fallback 或 Runtime 修正。

保留实验：共享 owner 反向依赖、composition closure ban、blocked-import server/admission、
旧 Electron Agent 整链删除、死 Renderer consumer 删除和 ChatGPT MCP 的按需旧加载。生产
负优化回退为 0；裸 `electron.exe` 只打开欢迎页，因此不作为 E2E，改用仓库打包客户端。
没有顺手删除 `LEGACY_FLOW`、scheduler、宏、用户流程、旧 session 数据或外部 MCP desktop
tools，因为它们尚未完成消费者迁移与双族 E2E。

聚焦回归为 Python 370 项通过、ChatGPT MCP 23 项通过、renderer 87 项通过。完整本地发布门
随后为 WinPilot `3607 passed, 13 skipped, 1 warning`、根测试 `2508 passed`、renderer
`317 passed`，TypeScript typecheck 与完整 `npm run build` 通过。唯一 warning 是既有
`mss.mss` deprecation。精确新提交的 GitHub Actions 尚未运行，不能继承旧 SHA 的绿色状态。

新架构仍只有原始 goal、一个 `LiveVisualActor`、一条视觉执行线和小型通用工具面。模型继续
承担聊天/connector/GUI 模态选择、视觉动作、自纠错和停止，职责与压力没有因本轮删除增加；
当前 provider 缺少视觉端点使这些职责根本未进入模型。Pi/Operit 引入计划仍为零：只在源码能
整体替换并删除现 owner，且两个不同任务族外部 oracle 提升完整成功率时准入。

下一轮唯一最高价值实验是只删除 `ArpNodeKind.LEGACY_FLOW` 的执行兼容，保留历史文件的读取、
编辑与明确迁移错误。先用静态/编译矩阵证明当前 runnable 节点不生成它，再从可见 Studio 跑
canonical `input_text` 与 `screenshot` 两个任务族，以 Unicode 长度/SHA 和图片尺寸/像素 hash
独立验收；任一族退化就整批回退，不把旧 handler 改名保留，也不并行删除其他兼容面。

### 13.29 MiMo 真实能力分层与 `LEGACY_FLOW` 执行 owner 删除

本轮先冻结生产代码，重新跑当前 `master`、当前 MiMo `mimo-v2.5` 配置和真实可见 Electron。
受控 10 项矩阵为 `6/10`：`answer-all-questions`、`precise-click`、`ordered-clicks`、`drag-fit`、
`slider`、`dynamic-hold` 通过，四个 corridor 变体全部失败。有效失败 run 分别为
`arp-20260802-175858-25acb3`、`arp-20260802-180651-5c1d79`、
`arp-20260802-174829-0ec307`、`arp-20260802-174924-184d40`，外部浏览器 oracle 的最小通道
余量为 `-164/-94/-44/-9`。四次都是模型原始折线、canonical image points 与
`physical_attempt` 逐点一致；错误是模型一开始走错方向或切过内角，随后在目标未完成时声称
完成。一次被外部窗口和错误 seed 污染的样本已排除并按原 seed 重跑，不能混入产品统计。

原生软件提供了第二类失败证据。Notepad run `arp-20260802-181114-71609f` 通过：从外部基线
`BASELINE-NATIVE-20260802` 经 `click -> hotkey -> type_text` 精确变为
`NATIVE-E2E-8A31C5`，独立可见文本核对通过。Calculator 的同一 `43×17` 目标则两次失败：显式
GUI 口令 run `arp-20260802-181440-5ee079` 用满 turn 后停在表达式 `4 +`、显示 0；自然口令 run
`arp-20260802-181930-07a5fa` 停在 `43 ×`、显示 0。486x667 的窗口截图清晰，模型却把 `3`、
乘号和 `7` 多次定位到相邻按键或边界，并在新帧已经显示无效结果后重复同一错误。两次 raw
action、canonical 坐标和 SendInput 回执仍一致，首分歧都在模型参数层。

因此当前结论不是“MiMo 完全不会 GUI”，而是它能完成简单点击、文本替换、短状态和部分拖拽，
但在本产品需要的精细视觉定位、受约束几何、自纠错和停止判断上不够可靠。复杂架构主要保证
输入所有权、一次坐标映射、trace 和 fail-closed；这些机械合同不会提升视觉语义能力。继续为
Calculator 按键、corridor 转角或完成声明增加提示、规则、snap、offset、答案修正、第二模型或
应用 adapter，只会把模型失败藏进机械层，本轮全部拒绝。`thinking=true`、截图最大边
`1120 -> 1600`、移除 `temperature=0` 等既有 A/B 均更差；没有再次重复这些负实验。当前机器
也没有第二个可用视觉 provider credential，不能把 401、文本模型或 mock adapter 冒充模型 A/B。

随后执行上一节冻结候选。`ArpNodeKind.LEGACY_FLOW` 枚举仍保留，使历史流程 JSON 可以读取和
编辑；但 graph validation 现在对每个该 kind 给出明确的“retired legacy node type / migrate
the saved flow”错误。`legacy.flow` 不再出现在 capability catalog，`default_handlers()` 不再
拥有该 kind，旧 `_legacy_handler` 和六种节点的隐式 action 翻译已物理删除。生产 diff 为
`+4/-87`、净删除 83 行；测试为 `+35/-2`。没有改模型提示、角色、工具面、坐标、键盘、
scheduler、宏或用户保存数据。

删除后重启真实 Electron，Studio 仍显示 20 个可编辑且当前可执行节点，当前 20-node 编译矩阵
不生成 `legacy_flow`。可见 Studio run `arp-20260802-183654-20ec43` 执行
`start -> focus_window -> input_text -> screenshot -> end`，5 节点全部 `ok`、模型调用 0，trace
完整性为 `38 accepted / 0 dropped / 0 write failures`。外部 Tk oracle、流程参数与物理输入均为
`TXT-8A31C5-中文-🙂-END`：19 个码点，UTF-8 SHA-256 为
`ebcb3151c0be5d553a0606e9fab8f496ce8ba1d64f75a750ed2a750eb22a866c`。受管截图中
`x=70,y=290,w=768,h=59` 的 12 色静态区域与运行后独立 `mss` 重抓逐像素一致，RGB hash 均为
`e82463ec23dc36748988a2970377fa3448360e51ce65e2e0281a9b20b85e1321`。两族均通过，所以保留
删除；严格离线节点独立矩阵升至 `3/19`。

最终本地发布门为 WinPilot `3613 passed, 13 skipped, 1 warning`、renderer 30 个文件
`317 passed`、TypeScript typecheck 通过；聚焦 orchestrator/API 为 `101 passed`。唯一 warning
仍是既有 `mss.mss` deprecation。`CON` 未触碰；GitHub 精确 SHA 状态必须在提交后另查。

下一轮唯一高价值模型实验不是继续改 Runtime，而是在拿到一个真实支持图片与多轮 tool call 的
候选 provider credential 后，用相同 goal、原始截图、tool schema、turn budget 和外部 oracle
做盲 A/B：至少覆盖 Calculator、一个窄 corridor 和一个当前通过族。只有候选同时提高原生控件
定位与受约束几何的完整成功率、且不回退 Notepad/短任务，才保留最小 provider adapter；否则
整条回退。拿不到 credential 时只允许增加重复次数来估计 MiMo 方差，不以 prompt、router、
规则、OCR snapping 或 Runtime 自动修正代替模型比较。
