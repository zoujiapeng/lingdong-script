# vNext Agent 核心旁路

状态：**P0/P1/P1.5 已实现；P2 Profiler 与 P3 Broker 已有默认关闭的 shadow，但评测/enforce 门槛未达；queue-time capability admission 已收敛为一个 runtime-aware owner；P4/P5 已接通默认关闭、按 run 显式选择的增量 live 实验链：具体 coordinator、唯一 Visual Actor、`click/double_click/scroll/type_text/hotkey/claim/blocked` 小工具面、共享 Controller/RawDevice、确定性 ActionEffectVerifier、独立视觉 ObligationVerifier 与确定性 MissionVerifier。P4b.1 keyboard 与一笔受限上一动作连续性已通过同入口受控 Electron oracle；existing-window activation 已闭合内部 RawDevice/Controller/post-frame/Runtime binding/trace，但尚未接入 live Catalog、Server backend 或 targetless Electron，也没有两类 target-acquisition oracle。这不是通用能力 uplift 或 P7 达标。legacy 仍默认，P6 禁用，P8/P9 未开始，legacy 删除为 0**

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

当前 vNext 仍是默认关闭的实验旁路；客户端可为单个新 run 显式选择 `vnext`，但 `legacy` 仍是默认。一个 run 只冻结一个 runtime，不允许执行中回退：

- **Mission 与验证**：P1/P1.5 提供可重放的 Definition/Event/Projection/Reducer/Card；`mission-event/1` 只接受 `kind`，不再兼容 `type` 或双写。动作闭合后，独立、只读的 deterministic ActionEffectVerifier 只在前后权威帧身份与 Registry 证据可核对时比较完整权威帧；只有解码后像素完全一致才报告 `unchanged`，任何像素差异只报告中性的 `changed`，无法比较则为 `unknown`。Reducer 从同一个 accepted transition event 按当前 obligation 重放派生 `no_progress_streak`，Actor 只看无 Runtime 身份的中性结果；它不证明动作因果、任务进展或语义完成，也不触发自动重试、换方向或 OCR/UIA/MCP fallback。Actor 只能提交 `claim_obligation_done`；Runtime 在 EvidencePolicy 和真实 evidence trust 同时许可时，才把当前义务、当前权威帧与冻结证据交给独立、无 mutation 工具的视觉 ObligationVerifier。Verifier 模型只按编号条件分类；Runtime 不接受模型复制的 canonical 条件或自报 runtime identity，而是机械重建 canonical assessment，当前 provider 未提供可靠数值置信度时记录 `confidence=None`。Runtime 校验并为 assessment 生成 attested run-scoped evidence；Reducer 才能接受 verification。全部 required obligation verified 后，成对注入的 `DeterministicMissionCompletionVerifier` 与同一 `EvidenceRegistry` 机械生成 aggregate evidence 和 `mission_verified`。可空的 evidence/transition/invalidation/rebind frame identity 只能是正整数或 `None`，不能用零或类型转换伪装。仍无跨进程 WAL 或 exactly-once 保证；当前虽已有 ActionEffect 与 P7a 首批 paired canary，仍无动画/真实任务和 Actor/Verifier 的规模化 A/B 校准。
- **Profiler 与 Broker**：P2a 纯合同/Policy、P2b 每 run 至多一次的 trace-only Profiler shadow、P3a 纯 ToolCatalog/Grant/Broker 和 P3b single-obligation partial shadow 已实现。两个 shadow 都默认关闭，不改 legacy prompt、工具或动作；当前没有完整 over/under-profile 基线、multi-obligation 对齐或 held-out relevance oracle，因此 Broker 不得 enforce，`necessary_tool_recall` 不得伪写为数值。
- **Actor 与输入链**：P4 已有具体 live coordinator 和模型 Visual Actor。当前模型边界是 `actor-proposal/1`、包含它的 `actor-envelope/2` 与 `actor-turn-input/5`，旧版或混合版均 fail closed。Actor 只看 MissionCard 的无身份投影、当前权威帧与 BrokerProjection，只能从已绑定状态的 `click`、`double_click`、`scroll`、`type_text`、`hotkey`、`claim_obligation_done`、`blocked` 小工具面提出一项 proposal；Runtime 每回合自动捕获最新权威帧，因此不再暴露等价的 full-frame `observe`。Runtime 绑定全部身份。Actor-visible scroll 使用明确的 `direction: up|down` 与正整数 `notches`；键盘侧只接受 bounded text 或 `{modifiers, key}`，Controller 才机械规范化为 canonical `HotkeyChord`。五种 GUI mutation 复用唯一 `FrameAuthority`/`DesktopActionQueue`/`RawDeviceExecutor` 和同一 `AtomicDesktopController` transaction；keyboard admission 不伪造坐标或 hit-test，但仍核对 frame、target、pointer released、foreground 与 final keyboard guard。动作连续性只在 canonical ActionEffect、target 和当前 obligation/revision 均可信时，把一笔允许公开的上一 GUI 动作放入 run-local、one-shot、非权威 sidecar；下一次真实 Actor 请求后即消费，重启即丢失，不进入 Mission、Evidence、claim、Verifier 或 replay。trace 只记录 `previous_action_projected` 布尔值；`type_text`/`hotkey` 本身及其文本/chord 不被保存。该切片没有扩写 system prompt，也没有应用、题目、布局或坐标规则；精确格式和失效条件见 [contracts.md](contracts.md) 与 [decisions.md](decisions.md)。部分输入、未知 mutation 结果和 post evidence 缺失仍不自动重放；`drag`、`draw_strokes`、held pointer、ROI observe 与通用 target acquisition 仍未接通。
- **Target acquisition 基础**：P4b.2 已落地只读 `TargetDiscovery`、一次性 Windows 进程/窗口事实快照、严格 `TargetBinding` attestation、targetless `full_desktop` 权威帧，以及 live Runtime 的 mutation-free discovery dispatch。候选必须具有 PID、创建时间与绝对 exe 路径强身份；空结果下一回合只向 Actor 投影 query 与 `no_candidates`，私有身份始终由 Runtime 保管。
  commits `2d1fb1b..845c034` 已在纯 activation 合同和共享 single-use permit 上闭合 existing-window 的 admitted RawDevice primitive、同一 AtomicDesktopController/ActionQueue、强身份 foreground guard、权威 post frame/evidence、MissionRuntime 精确 reconciliation、独立 binding attestation、唯一 `target_bound` event 与 accepted-event trace。候选 ordinal 与完整候选的关系只能来自刚消费的一次性 lease；Controller evidence 不能冒充 binding evidence，rejected/unknown/stale/tamper 都不绑定、不 fallback、不 replay。
  该链尚未加入 live Catalog，Server composition root 尚未注入 admitted activation backend；产品 coordinator 仍要求 foreground handoff，Electron targetless 入口不可达。`open_app` cold launch、strict initial-target 的 renderer/coordinator 接线，以及 existing-instance/cold-launch 两类同入口独立 oracle 均未完成。因此 P4b.2、P7、P8、P9 和 recovery gate 均未达，legacy 删除数为 0。
- **Runtime 与产品边界**：`DesktopRuntimeSelection` 每 run 冻结一次。可见 Electron 客户端提供明确的 `Legacy（默认）` / `vNext（实验）` 选择器，renderer 只在顶层 run request 发送选择；Server 仅对冻结为 `vnext` 的 run 注入增量 live coordinator，完全绕过 legacy handlers，并复用同一 run/BUS/stop/pause owner。队列准备、模型调用和桌面 mutation 之前只调用一次 `DesktopRuntimeAdmission`：legacy 分支保留既有两项 preflight 及顺序；vNext 分支从 coordinator 的不可变 `RuntimeCapabilitySnapshot` 机械核对 canonical obligation capability、服务、Catalog 与 Controller parity。该同一 snapshot 也提供给 `ExecutionPolicy`、live `RuntimeState` 和 `CapabilityBroker`，并以 fingerprint 写入 private trace sidecar，不能由任务文本、模型或应用名改写。`desktop-runtime-coordinator-context/1` 同时提供回合边界的 blocking pause gate 和非阻塞 `is_paused` reading；模型返回后若 run 已暂停，proposal 会在 admission 前丢弃，恢复后从新权威帧重新决策。任何构造、admission、Actor、Controller 或验证失败都 fail closed，不在同一 run 隐式回退 legacy。canonical broad capability 的唯一 owner 是 `vnext/capability_names.py`；`vnext/capabilities/names.py` 仅兼容 re-export。真实 Compiler 的 ExecuteDesktopTask 需要 `visual.interact` 与 `visual.read_text`，snapshot 与 live Runtime state 同时声明两者，因为 Actor 直接读取像素；Catalog 仅以 `visual.interact` 授权通用 GUI 工具，`visual.read_text` 不暗中加载 OCR/UIA，也不能另造应用或测试 capability。
- **Freshness、预算与物理 owner**：live Runtime 使用显式、可注入的 frame freshness 上限（当前默认 30 秒），Broker/admission 不接受超过上限的帧；这不能被一个泛化 `fresh=true` 绕过。回合预算只读取 canonical `task-contract/1` 中唯一 full-goal ExecuteDesktopTask 的整数 `max_steps`，若 session 同时携带 `requested_max_steps` 则必须完全一致；当前有效预算再受 coordinator 本地上限 24 约束，不从模型、prompt 或测试 case 读取。`RawDeviceExecutor` 由产品 composition root 构造并注入，coordinator 缺少该依赖就拒绝构造，不能在 vNext 内另建物理输入 owner。
- **模型请求**：`winpilot/modeling/` 已统一 product package 的冻结 request 和有界 transport retry，unknown mutation 不自动 replay。全角色逐 attempt sidecar、legacy VLM stop_event seam 和外围 `python/providers/` 的生产可达性仍是迁移债务。
- **评测与后续门槛**：旧 click/scroll canary 仍保留为历史证据；commit `f8fa2a7` 的首批 P7a paired 测量中，precise-click 的 1 个 eligible pair 两侧通过，但 scroll-reveal 为 vNext 1/2、legacy 2/2，vNext up 在 24 actions 后失败。外部 trusted-browser oracle 只属于测试 harness，不注入产品 Runtime、Actor 或 Verifier。相同模型存在 run-to-run 随机性，单 pair 或前后小样本不能称 uplift；recovery gate 与 P7 仍未达，P6 保持禁用，P8/P9 未开始，legacy 删除数为 0。
- **Keyboard 动作连续性证据**：commit `f26b7de` 的 `keyboard-entry` suite `20260723-053146-suite`、run `arp-20260723-053225-58685a` 在目标输入框已正确获得焦点后仍执行 24 次 click、0 次 `type_text`，独立 oracle 明确失败。commit `515b286` 在 suite `20260723-070921-suite` 首次通过，suite `20260723-071108-suite` 又以 3/3 seed 通过，每次均为 3 turns / 2 actions / `false_done=0`。`keyboard-select-all` suite `20260723-071428-suite` 因 oracle 的布局专属 DOM code 约束出现 false negative，可信键盘事件和最终文本已证明语义成功；只修改 oracle 的 commit `a5c0c95` 后，suite `20260723-072628-suite`、run `arp-20260723-072705-9dbc00` 通过。这只闭合受控键盘与一笔上一动作投影，不证明一般任务、动态界面、跨应用恢复或 P7。
- **工程基线**：`fc04f49` 已修复测试对 ignored `config.json` 中 UIA/OCR/predictive OCR 与模型配置的隐式依赖。启动时没有本地配置的 detached worktree 全量为 2822 passed、13 skipped；根目录 Python 为 2508 passed，前端为 218 passed，typecheck/build/diff-check 通过，GitHub Actions run `29932890053` 的 frontend/python 均通过。这只恢复可重复的工程基线，不计作 GUI E2E、P7 或产品能力证据。
- **下一门槛**：把已闭合的 existing-window 内部链接入 live Catalog 与 Server raw backend，但在 cold launch 的启动前后因果证明和 unknown-no-replay 同样闭合前，不接通 targetless Electron 产品入口。可见候选与已安装候选都有真实 owner 后，再把 strict initial-target seam 接到同一个 Electron request/coordinator，并分别运行 existing-instance reuse 与 cold-launch 的同入口独立 oracle。完成这些之前不得称通用 target acquisition 已接通。之后再逐项把 `drag`、`draw_strokes`、held pointer 与 ROI observe 接入同一个 snapshot、Catalog、Broker 和 Controller parity 合同；同时扩大 keyboard/scroll 的随机 seed、多布局、滚动边界、静止 no-op、ambient animation、不可比较帧、paired legacy、mixed 与 held-out 评测。每个原语都必须先有负向/property、unknown-no-replay 和真实 Electron oracle，不能靠 legacy fallback、prompt 扩写或任务专属规则补齐。

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
