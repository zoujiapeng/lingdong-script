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
