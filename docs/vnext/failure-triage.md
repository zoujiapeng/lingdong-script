# vNext 失败诊断：工具与骨架优先

本项目最容易走偏的方式，是看到一次任务失败就给模型加一句规则、增加一个应用 adapter，或再调用一个模型角色。vNext 要求先证明入口、任务结构、Profiler、状态、工具和执行链正确，再讨论 prompt 或模型能力。

## 1. 总原则

> **如果人类从同一张清晰截图能判断，Actor 离线也能判断，但运行时执行错了，这是工具、身份绑定或骨架问题。**

> **如果 Actor 在干净上下文中仍无法理解清晰静态画面，且坐标、工具和 envelope 合同已验证，才是模型视觉能力问题。**

> **如果只在长任务中遗忘，而单义务局部执行正确，这是 Mission/调度问题，不是给 Actor 增加任务提醒。**

> **如果简单任务启动过多角色，或复杂任务漏掉依赖/验收，这是 Task Profiler/Execution Policy 问题，不是 GUI 路线问题。**

> **如果 Verifier 拒绝后 Actor 重复相同 claim，这是 claim、evidence fingerprint、Reducer或Card反馈问题，不是放宽验收。**

## 2. 固定诊断顺序

前一层未证明正确，不进入下一层。

### 2.1 用户入口与测试真实性

核对：

- 任务是否从当前工作区可见 Electron 客户端提交；
- runtime version 是否显式选择；
- outer run、child run、trace 是否准确绑定；
- cold start/session 是否符合case；
- 外部窗口或用户操作是否遮挡/抢焦点；
- external oracle是否验证业务结果。

入口错误不能归因于Agent。

### 2.2 Goal Compiler 与 MissionDefinition

核对：

- `original_goal` 是否完整保留；
- ObligationSpec 是否描述结果而非GUI步骤；
- 是否错误拆分只有到界面才能知道的信息；
- artifact/recipient/target dependency 是否丢失；
-不可靠拆分是否退回单个lossless obligation；
- broad capability是否变成应用/工具步骤。

若单义务fallback能完成而多义务版本失败，先修Compiler/Definition。

### 2.3 Task Profiler 与 Execution Policy

核对：

- Profiler 是否只看允许的任务/初始状态事实；
-输出是否是多维结构，而不是`drawing/chat/game`标签；
- 是否包含按钮、工具、坐标、应用流程或runtime version；
- recommended/minimum/effective depth是否有清晰理由；
- Execution Policy 是否正确覆盖低置信、provider不可用、安全和验收要求；
- 简单任务是否over-profile；
- 复杂跨应用/工件/审批任务是否under-profile；
- profile revision是否只由用户修订或已验证事实冲突触发；
- profiler unavailable是否走safe standard，而非关键词规则。

Profiler判断错不能通过给Actor增加“记住这是复杂任务”的prompt补救。

### 2.4 Mission Reducer 与主线

核对：

- MissionProjection是否由Definition、accepted events和Evidence重放；
- ObligationSpec是否无runtime status；
- lifecycle是否只有一个`obligation_states`真相源；
- verified/pending集合是否只是派生；
- active obligation是否由依赖机械选出；
- claim是否仍只是claim；
- stale revision event是否拒绝；
-用户修改目标后goal revision是否正确；
-缓存删除后是否能重建相同Projection。

出现TaskContract、MissionState、Actor历史和Evidence各说不同状态时，先消除双账本。

### 2.5 Capability Broker

核对：

- Actor当回合实际看到哪些工具；
- necessary tool是否缺失；
-是否暴露大量无关工具；
- target/pointer/artifact/adapter/MCP/grant状态是否正确；
-工具变化是否来自accepted capability与真实状态；
- capability request是否被正确grant/deny/expire；
- mutation provider与GUI Actor是否同时写同一obligation；
- Broker是否偷读应用名、窗口标题或任务句决定流程。

模型没看到所需工具时不是模型不会用；被大工具面干扰也不应先加工具选择提示。

### 2.6 应用与窗口发现

核对：

- 是否枚举已运行、隐藏、托盘和可恢复窗口；
- Start Menu、StartApps、App Paths、快捷方式和安装信息是否完整；
- executable/process/window identity是否一致；
-启动后是否有因果快照；
-是否错误新开实例、退回网页或绑定登录页。

优先修 TargetDiscovery、WindowsTargetFacts、TargetBinding 与 activation causality，不能用 prompt 教固定安装路径。

### 2.7 Frame、目标与坐标

核对：

- 模型实际收到哪张图片、分辨率和capture region；
- frame id、epoch、HWND、DPI是否一致；
-是否有遮挡、动画、移动窗口或旧帧；
- image coordinate是否只映射一次；
- ROI crop后坐标是否相对新图片；
- Controller是否擅自调整目标，`target_adjustment_px`是否非零。

看错画面、用错坐标或旧帧执行不是prompt问题。

### 2.8 Runtime envelope

核对：

- 模型是否只输出无身份ActorProposal；
- mission/run/revision/obligation/frame/evidence/broker id是否由Runtime绑定；
-模型字段是否有机会覆盖envelope；
- proposal是否在binding后变stale；
- late model result是否写入新revision；
- evidence scope是否串用旧run、旧frame或旧artifact；
- Planner/Verifier/Inspector是否同样由Runtime绑定主体和输入证据。

若模型只是复制错ID，不应通过提示词要求它“更仔细”；应从模型schema删除机械身份。

### 2.9 模型请求与重试边界

核对：

- provider SDK 是否以 `max_retries=0` 创建；
- adapter 是否只准备一次，Runtime 是否冻结 request 并记录稳定 fingerprint；
- `request_retry` 是否保持同一 decision/role/purpose/fingerprint，且没有结果被调用者消费；
- transient transport allowlist、attempt 上限、总 deadline 和 backoff 是否有界；
- poll 是否只等待 in-flight request，而没有重新提交；
- 改变 messages、tools、thinking、预算或 purpose 的恢复是否使用新 decision id 并记为 redecision；
- empty content、JSON/schema 或业务验证失败是否被错误当成 transport retry；
- stop/timeout 后的 late response 是否可能进入 tool dispatch 或写入新 revision；
- mutation outcome unknown 是否进入 reconcile/block，而不是自动 action replay；
- legacy 名称中的 `retry` 是否其实是截断恢复或 tool-call correction，导致指标混淆。

请求重试发生在任何模型结果或桌面动作之前。它不能证明模型重新思考，也不能授权重复
GUI mutation。重复 provider 计费、相同请求却不同 fingerprint、parse error 后再次发送，或 timeout
后出现桌面动作，优先归因 `ModelCallRuntime`/transport ownership，而不是 Actor 智力。

### 2.10 基础输入工具

逐个核对：

- click down/up真实发生；
- type_text作用于已聚焦编辑区域；
- scroll方向、位置和窗口正确；
- drag按start -> points -> end连续插值；
- mouse_down/move/mouse_up持有同一lease；
- draw_strokes是通用路径；
- wait观察正确ROI；
- stop/pause/cancel释放所有输入。

“不会滚”“拖拽偏离”“点击没反应”先用确定性靶场验证工具。
Raw `APPLIED` 只说明机械输入函数明确完成，不证明目标画面变化；仍须取得新的权威
post-frame。`OUTCOME_UNKNOWN` 表示输入可能已经发生，只能观察、对账或阻塞，禁止
自动重放或换一种输入机制再试。

### 2.11 动作后观察与 Transition

核对：

- action后是否及时settle和新帧；
- pre/post是否可比较；
-动画/无关区域是否伪造进展；
-结果是否区分input completed、local effect和semantic completion；
- last transition是否简洁、真实并绑定evidence；
-无进展后是否废弃旧坐标/路线。

Actor不知道点击后发生什么，通常是Transition/Card质量问题。

### 2.12 Verifier 与完成语义

核对：

- Actor是否只是claim；
- Runtime是否绑定subject/revision/evidence；
- Verifier是否独立上下文、无mutation工具；
-是否优先deterministic/oracle/receipt；
- evidence是否与obligation、artifact、recipient和frame匹配；
-相同fingerprint是否重复昂贵验证；
- missing conditions是否正确回到Card；
- EvidencePolicy max attempts/uncertain policy是否生效；
-所有required obligations verified前是否终止mission；
-同模型Actor/Verifier是否在同一视觉误判上相关。

重复claim和假完成优先修协议；false negative则检查criteria和evidence，不靠无限放宽。

### 2.13 Actor 模型能力

只有前十二层有证据后才检查模型：

1. 用当时frame、MissionCard、BrokerProjection和相同schema离线重放；
2. 核对该回合是否投影 bounded previous action，以及 provider 是否按合同渲染原生 tool-call/result；
3. 去掉无关历史，保持角色合同；
4. 比较轻/深reasoning与更强computer-use模型；
5.检查能否描述可见拓扑、目标和不确定性；
6.静态理解正确但function call错时检查schema/输出预算；
7.多个模型都因细节失败时检查ROI/截图；
8.同模型Verifier重复同一误判时增加不同证据视角，不能把“角色不同”当独立性；
9.多个held-out场景仍稳定受益时才考虑Inspector或训练。

production trace 只保存 `previous_action_projected` 布尔值，不保存局部意图、参数、provider messages
或图像；这保护输入内容，也意味着仅凭生产 trace 不能 bit-for-bit 重建包含该 view 的模型请求。
需要精确回放时必须使用受控评测 fixture 在同一 Runtime/adapter 边界捕获冻结请求，不能从 Mission
replay 猜测，也不能把重启后已丢失的 sidecar 伪装成可恢复状态。

### 2.14 Prompt 变更

Prompt是最后一层。只允许修正：

-角色权限不清；
- typed字段语义不清；
-工具描述与执行不一致；
- Actor误以为claim等于verified；
- Profiler/Planner/Inspector/Verifier越权。

禁止：

-应用按钮位置；
-测试正确路线；
-“遇到输入框先怎样”的教学清单；
-OCR/UIA fallback顺序；
-固定重试动作序列；
-大量模仿示例。

修改prompt优先替换/删除旧文字，并在第二任务族/held-out验证。

## 3. 常见症状与优先 owner

这些是诊断映射，不是生产路由规则。

| 症状 | 优先检查 | 不应首先做 |
| --- | --- | --- |
| 简单任务启动Planner/Inspector | TaskProfiler、ExecutionPolicy | 给Actor解释为何任务简单 |
| 跨应用工件任务只执行第一段 | under-profile、MissionDefinition、dependency | 给Actor追加整条主线 |
| 只打开应用就claim完成 | obligation、claim/verifier、Card | 应用专属后续步骤prompt |
| 已有登录实例却新开 | TargetDiscovery、WindowsTargetFacts、TargetBinding、activation causality | 专属“先找窗口”规则 |
| 找到网页但找不到客户端 | 多源索引、candidate identity | 固定安装路径 |
| 点击输入框后停住 | 新帧、active obligation、Broker、延迟 | 输入框教程 |
| 点击无反应 | HWND/focus/frame/down-up/遮挡 | OCR附近自动改点 |
| 不会滚或方向错误 | scroll schema/坐标/窗口 | 应用滚动提示 |
| 拖拽离开通道 | ROI/关键点/插值/oracle/Actor几何 | 写死通道路线 |
| 绘图工具在别处不可用 | broad capability/Broker | DrawArtifact专属通道 |
| “刚刚的工件”丢失 | artifact binding/Mission | 全局最近文件 |
| 重复完成声明 | claim去重/Verifier/Card | 放宽Verifier |
| 持续会话立即结束 | lifecycle/watcher/obligation | 应用专属循环 |
| UIA/OCR带偏 | Broker/provenance/frame | 更多融合权重规则 |
| 模型调用慢 | Profile/tool/context/reasoning/role calls | 随意删视觉帧 |
| 同一模型请求重复计费/重复发送 | ModelCallRuntime、SDK retries、fingerprint/attempt trace | 增加调用点本地重试 |
| timeout或异常后重复桌面动作 | late-result gate、mutation outcome、action replay policy | 把node retry次数调高 |
| stale/跨义务动作 | Runtime envelope | 让模型复制更多ID |

## 4. 何时重新 profiling

只在：

-用户实质修改目标；
-已验证事实推翻初始结构假设；
-当前depth无法表达新发现的跨应用、工件、审批或持续等待；
-安全策略要求提升验证深度。

局部动作失败、一个按钮没反应或一次视觉不确定不触发整项任务re-profile。

## 5. 何时增加 Planner

Planner只解决主线结构问题。需要：

-基础工具与当前义务链正确；
-问题确实跨obligation；
-有可引用verified facts；
-Reducer无法仅靠依赖继续；
-触发事件属于复杂依赖、两种策略失败、用户修改、义务冲突或approval。

Planner输出GUI步骤时直接拒绝。

## 6. 何时增加 Inspector

需要：

- Actor看到正确Frame；
- ROI和分辨率足够；
- Controller/path工具已验证；
-失败跨多个seed或任务族；
- Actor明确视觉不确定或离线分析稳定错误；
- Inspector只返回事实；
- external oracle显示真实增益。

否则先修frame/ROI/path或换更适合computer-use的Actor模型。

## 7. UIA/OCR/MCP

它们不是失败升级阶梯。允许条件：

- active obligation capability明确需要；
-来源、HWND、frame/epoch和区域可核对；
-收益明确可验证；
-Broker显式加载；
-结果带provenance且不覆盖相反像素；
-provider mutation与GUI Actor互斥。

文件hash、provider receipt和标准办公API可提高确定性；游戏、自绘和视觉拓扑不能因UIA空树被判不可操作。

## 8. 失败报告模板

```text
任务/seed：
runtime/commit/model：
客户端入口与cold-start证明：
Goal Compiler/MissionDefinition：
TaskProfileProposal/ExecutionProfile/Policy override：
active obligation/Mission revision：
MissionCard大小：
BrokerProjection/CapabilityGrant：
权威frame id/region/size：
ActorProposal：
Runtime envelope：
Controller实际输入：
TransitionRecord：
Verifier/oracle：
首个错误owner：
为什么前一层已被排除：
建议修复的通用合同/工具：
第二任务族或held-out验证：
明确不添加的专属规则：
```

报告必须定位首个错误owner。后续层的连锁表现不能掩盖最早错误。
