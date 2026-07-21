# vNext Agent 核心旁路

状态：**P0/P1/P1.5 已实现；P2a/P2b Profiler shadow、P3a Broker 与 P3b single-obligation partial shadow 已完成；WinPilot product package 的模型请求/重试边界已统一；Broker 仍未 enforce，P4 Actor、P5 Verifier、P6 Planner/Inspector 尚未接入**

迁移分支：`codex/vnext-agent-core`

目标：在不复制产品入口、不编写应用剧本的前提下，旁路建设新的 Agent 核心，达到验收门槛后逐步切换并删除旧职责。

## 为什么建设 vNext

当前系统的问题不是“模型还少学了几个界面规则”，而是一个模型和多层本地代码同时承担太多职责：模型既要理解像素，又要记住长任务主线、判断任务复杂度、选择工具、维护鼠标状态、解释失败并给自己验收；Compiler、StageRuntime、AgentRunner、Controller 和 adapter 又会重复解释同一个目标与动作。

vNext 将这些职责压缩为一条主线：

> **一个唯一可修改 GUI 的 Visual Actor，一个由确定性 Reducer 产生的可重放 MissionProjection，一个低频只读 Task Profiler，以及多个无桌面写权限的按需认知角色。**

Visual Actor 只做最难本地化的事情：根据当前真实像素和局部义务，选择下一笔语义动作。任务主线、依赖、执行深度、工具面、输入状态、证据和最终完成都由外部 typed contract 与确定性组件维护。

## 不可妥协的边界

1. 只有 Visual Actor 可以提出桌面 mutation；Planner、Task Profiler、Inspector、Verifier 永远没有 GUI mutation 工具。
2. 只有 Mission Reducer 可以接受事件并产生新的 MissionProjection；完成真相必须能从 MissionDefinition、accepted event log 和 immutable run-scoped evidence 重放，不能再建第二份可独立修改的完成账本。
3. `original_goal` 完整、不可丢失；Obligation 只描述期望状态、验收条件、依赖与禁止效果，不描述点击步骤。
4. Task Profiler 只低频提出多维任务结构、执行深度和认知服务建议；Execution Policy 校验后采用。它不生成 GUI 路线、工具序列或应用专属架构。
5. Capability Broker 根据 accepted capability 与真实运行状态投影小工具面，不根据应用名、测试题号、prompt 关键词或模型自报阶段切换工具。
6. 模型只提交不带运行时身份的 typed proposal。`mission_id/revision/active obligation/frame/evidence/broker projection` 等由 Runtime envelope 绑定，不能要求模型复制或发明。
7. Controller 只执行模型选择的动作，不做 OCR 吸附、不换按钮、不启动 UIA/OCR/MCP fallback、不判断任务完成。
8. `claim_obligation_done` 只是候选声明；确定性证据、应用 oracle、provider receipt、独立视觉 Verifier 按顺序验收。
9. Planner 和 Visual Inspector 只按事件调用；没有跨应用依赖、重复失败或复杂视觉证据时不启动。
10. 基础工具与调度骨架优先于 prompt。没有证明工具和数据流正确前，不允许用更多提示词“教会”模型某个任务。
11. vNext 与 legacy 共用可见 Electron 用户入口、冷启动语义、Artifact/Evidence、FrameAuthority、Controller 基础和 E2E oracle；不得为 vNext 建第二套测试入口。
12. 11 个任务及其组合只能作为黑盒探针和报告数据，不能进入生产 prompt、Broker、Compiler、Profiler、坐标或完成条件。

## 文档导航

| 文档 | 作用 |
| --- | --- |
| [architecture.md](architecture.md) | 目标架构、唯一写入者、角色边界、完整运行流程 |
| [execution-profile.md](execution-profile.md) | Task Profiler、ExecutionProfile、执行深度和同模型角色隔离 |
| [contracts.md](contracts.md) | Mission、Actor、Broker、动作、证据和角色 I/O 合同 |
| [migration-plan.md](migration-plan.md) | 旁路目录、阶段切换、旧职责删除门槛和分支纪律 |
| [evaluation.md](evaluation.md) | P0 指标、A/B 方法、真实客户端 E2E、held-out 与发布门槛 |
| [failure-triage.md](failure-triage.md) | “工具与骨架优先”诊断顺序，以及何时才应调整模型或增加认知角色 |
| [decisions.md](decisions.md) | 已接受架构决策、延后问题与防漂移规则 |

## 当前阶段

当前分支已完成 P0/P1/P1.5、P2a 纯合同、P2b 只读 shadow 与 P3a/P3b Broker shadow，legacy 仍是默认且唯一桌面执行 runtime：

- P0 只在既有 trace/BUS 事件上增加观测字段；
- P1 提供可重放的 MissionDefinition/Event/Projection/Reducer/Card 及 legacy shadow；
- P2a 在独立 `vnext/profiling/` 中提供严格版本化合同、确定性 Execution Policy、序列化和可注入 shadow 评测；
- P2b 提供每个 run 至多一次、无工具、无 runtime identity 的真实 Profiler provider；只有显式启用 `vnext.profiler_shadow.enabled` 时，才从同一 Electron/ARP 入口写入私有 trace sidecar；
- Profiler shadow 不修改 legacy prompt、工具面、GUI 动作链、Controller、Mission 或完成语义，setup/provider 失败也不能阻断 legacy；
- P3a 提供唯一 canonical capability catalog、严格版本化的 ToolCatalog、run/mission/obligation/revision/turn 有界 CapabilityGrant，以及只读取 MissionProjection、ExecutionProfile 和真实运行状态的确定性 CapabilityBroker；
- P3b single-obligation partial shadow 通过默认关闭的 `vnext.broker_shadow.enabled` 在同一 Electron/ARP 入口逐回合写私有 sidecar；它读取现有 FrameAuthority、已验证 target、同一次 pointer 状态和 owner-scoped capability registry，但不改 legacy 请求、BUS、Controller 或完成语义；
- target 变化在 shadow 中也必须由 run-scoped EvidenceRef 与 accepted Mission events 完整 replay；多 obligation 尚无法和 legacy child runner 可靠对齐时明确 unavailable，必须等 P4 Mission Runtime 提供 active-obligation envelope；
- Broker shadow evaluator 只有获得完整、可靠的 necessary-tool 标注时才计算 recall；当前尚无 held-out 实测数据，因此 Broker 不能 enforce，也没有裁剪 legacy 工具面；
- 跨阶段的 `winpilot/modeling/` 合同把 WinPilot product package 中的逻辑模型请求绑定为 `model-request/0`：provider adapter 先准备请求，Runtime 冻结一次，SDK 隐式重试为零；只有同一 decision、role、purpose 和 request fingerprint 的瞬时 transport failure 才能做有界 request retry；
- poll 不重新提交模型请求；改变消息、thinking、工具、预算或目的属于新的 redecision；成功响应的解析/schema 失败不是 transport retry；桌面 action replay 由独立 mutation replay policy 管理，执行结果不确定时禁止自动重放；
- 该完成声明只覆盖 `python/winpilot_home/winpilot/` 产品包。仓库外围 `python/providers/` 的旧 Flow/feature provider 仍有独立 SDK/HTTP 重试，必须先审计生产可达性，再在 P9 删除或单独迁移；不能把它们包装成第二个 Runtime；
- Profiler 的 stop_event 已从 ArpExecution 经 shadow hook/runtime/provider 传到 ModelCallRuntime 并有测试；精确函数调用栈 AST guard 也已阻止 product package 绕过 transport 边界。仍未闭合的是全角色逐 attempt sidecar，以及缺少 stop_event seam 的 legacy VLM helper；
- 不改变当前 `master` 的产品行为；
- 不声称 Mission Ledger 已经接管产品运行；P1 projection 只能离线或 shadow 使用；
- P4 Visual Actor、P5 独立 Verifier、P6 Planner/Inspector 和 vNext 可写运行时均未实现；现有 legacy planner、done judge 或 VLM helper 经过统一 transport，不代表对应 vNext 角色已接入；
- 不把历史单次 E2E 结果改写成 vNext 成功证据；
- 下一步是在真实 legacy 回合收集完整 relevance oracle 并做 held-out 评测；在 required tool group recall 门槛达标前不能 enforce。

## 旁路不是第二条产品主线

`codex/vnext-agent-core` 是临时迁移集成分支，不是永久 fork。未来代码可以在独立命名空间中旁路实现，但每个 run 只能选择一个 runtime，legacy 与 vNext 不能同时操作桌面。只读 shadow 可以比较 TaskProfile、Mission、Broker 工具投影或 Verifier 判定，不能产生第二份鼠标键盘输入。

达到默认切换门槛后，vNext 合并回 `master`，旧 owner 按证据逐项删除，迁移分支由维护者合并后归档或删除。耐久备份使用 tag；长期产品事实仍以 `master` 为唯一 canonical 分支。

## 每轮工作开始前

1. 先说明本轮解决的通用失败模式和唯一 owner。
2. 先检查基础工具、帧、窗口、动作事务和 evidence 是否正确。
3. 指明本轮只实现一个迁移切片，不横跨多层重写。
4. 列出准备替换的旧职责及其删除条件。
5. 预先声明测试入口、对照组、外部 oracle 和 held-out 场景。
6. 若需要增加 Planner、Inspector、提示词或新模型调用，先给出没有它时跨任务失败的 trace 证据。
