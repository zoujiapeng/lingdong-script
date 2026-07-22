# vNext Agent 核心旁路

状态：**P0/P1/P1.5 已实现；P2 Profiler 与 P3 Broker 已有默认关闭的 shadow，但评测/enforce 门槛未达；P4a 注入式 Runtime、P4b 默认关闭的 atomic click/double-click 切片和 P4c 可注入 product coordinator protocol seam 已实现；P5a ObligationVerifier 和 P5b 本地 evidence-first MissionVerifier 已接入该注入式 Runtime；Server 默认未注入 coordinator，无 live Actor/coordinator 实现、无模型/视觉 Verifier，P6 禁用，P7–P9 未开始，legacy 删除为 0**

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

当前所有 vNext 能力仍是旁路或注入式能力；`legacy` 是默认且唯一可在 Electron 中修改桌面的 runtime：

- **Mission 与验证**：P1/P1.5 提供可重放的 Definition/Event/Projection/Reducer/Card。P5a 在 `claim_obligation_done` 被 Reducer 接受后，只在 ExecutionProfile 允许且注入只读 port 时调用 ObligationVerifier；Actor 不能直接完成义务。P5b 将本地 `DeterministicMissionCompletionVerifier` 与 Reducer 使用的同一 `EvidenceRegistry` 成对注入；全部 required obligation verified 后，Runtime 冻结 accepted verification/evidence，先写入 canonical aggregate evidence，再接受 `mission_verified`。这是进程内 evidence-first 机械闭合，不是模型/视觉 Verifier，也没有跨进程 WAL 或 exactly-once 保证。
- **Profiler 与 Broker**：P2a 纯合同/Policy、P2b 每 run 至多一次的 trace-only Profiler shadow、P3a 纯 ToolCatalog/Grant/Broker 和 P3b single-obligation partial shadow 已实现。两个 shadow 都默认关闭，不改 legacy prompt、工具或动作；当前没有完整 over/under-profile 基线、multi-obligation 对齐或 held-out relevance oracle，因此 Broker 不得 enforce，`necessary_tool_recall` 不得伪写为数值。
- **Actor 与输入链**：P4a 只实现无 Runtime 身份 proposal、Runtime-bound envelope 和注入式单回合 `MissionRuntime`。P4b 复用唯一 `FrameAuthority`/`DesktopActionQueue`/`RawDeviceExecutor`，完成默认关闭的 atomic `click`/`double_click` 窄切片：双段坐标映射、commit-time Windows target/frame 校验、每次 mouse-down 紧前 final guard，以及同一 physical lease 内的 post-action authoritative frame/evidence。它仅在注入测试中使用；`type_text`/`hotkey`/`drag`/`draw_strokes`/held pointer 尚无等价 vNext ControllerPort。
- **Runtime 与产品边界**：`DesktopRuntimeSelection` 每 run 只从产品配置冻结一次。P4c 已定义可注入的 product coordinator protocol/seam；`ArpExecution` 在 frozen `vnext` selection 且显式注入 coordinator 时可绕过 legacy handlers，并复用同一 run/BUS/stop/pause owner。Server 默认未注入该 port，仓库也没有具体 live coordinator/Visual Actor 实现；因此显式选择 `vnext` 仍在桌面 mutation 前 fail closed，不会在同一 run 偷回 legacy。目前没有可运行的 vNext Electron E2E 路径，单元/注入测试不得报告为 GUI 成功。
- **模型请求**：`winpilot/modeling/` 已统一 product package 的冻结 request 和有界 transport retry，unknown mutation 不自动 replay。全角色逐 attempt sidecar、legacy VLM stop_event seam 和外围 `python/providers/` 的生产可达性仍是迁移债务。
- **后续门槛**：P6 Planner/Inspector 在没有至少两个 held-out 任务族的 A/B 收益前保持禁用；P7 真实 canary、P8 默认切换和 P9 legacy 净删尚未开始。当前没有任何 legacy 产品职责达到删除门槛，删除数为 0。下一步是保持 Broker 非 enforce，组装默认关闭的同入口 live coordinator/Actor，再以 independent oracle 做 paired canary。

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
