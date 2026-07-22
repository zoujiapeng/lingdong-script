# vNext 旁路迁移计划

本文规定如何在原仓库旁路建设 vNext、如何逐步切换，以及什么时候才能删除旧职责。原则是：**小切片、同入口、单写入、先 shadow、可回退、以净删为终点**。

## 1. 分支与产品入口

迁移集成分支：`codex/vnext-agent-core`。

该分支可在迁移期间跨多个小提交持续建设，但不是第二个长期产品主线：

- `master` 保持 canonical 产品事实和当前可用行为；
- vNext 定期吸收 `master` 的必要修复，避免漂移；
- 产品仍只有 Electron“智能代理”入口；
- 每个 run 的 `legacy/vnext` runtime version 由显式产品或实验配置选择，不由任务文本、Task Profiler 或模型暗中切换；
- vNext 内部的 `direct/standard/orchestrated` ExecutionDepth 可以由 Profiler 建议并由 Execution Policy接受；这不等于切换产品 runtime；
- 一个 run 只能有一个桌面 mutation owner；
- shadow 只能比较纯状态、profile、工具投影或 verifier verdict，不能让 legacy/vNext 同时移动鼠标键盘；
- 默认切换完成后 vNext 合并回 `master`，旧职责净删，迁移分支归档或删除；耐久备份使用 tag。

## 2. 未来旁路目录

为了避免继续向过重模块塞职责，建议迁移期代码放入独立命名空间：

```text
python/winpilot_home/winpilot/vnext/
  profiling/
    contracts.py
    profiler.py
    policy.py
  mission/
    contracts.py
    events.py
    reducer.py
    compiler.py
    runtime.py
    card.py
  capabilities/
    catalog.py
    broker.py
  actor/
    proposals.py
    envelope.py
    loop.py
  verification/
    action_effect.py
    obligation.py
    mission.py
  cognition/
    planner.py
    inspector.py
  bridge/
    legacy_runtime.py
    shared_services.py
  telemetry.py
```

物理隔离只是迁移手段。切换完成后应提升为 canonical runtime，删除 `vnext` 命名和 legacy bridge；不能永久保留两套 Agent。

旁路复用现有服务，不复制：

```text
FrameAuthority
FastGuiController / RawDevice / Desktop Action Queue
ScreenWatcher / PointerLease cleanup
ArtifactStore / EvidenceRegistry
AppResolver
provider adapters / MCP registry
Electron run API / trace / E2E harness
model adapter / timeout / usage accounting
```

## 3. 实施纪律

每个实现提交只允许一个主要 owner 变化，并写明：

- 本轮通用失败假设；
- 新合同和唯一 owner；
- 与 legacy 的桥接点；
- 本轮明确不做什么；
- 受影响旧职责及删除门槛；
- unit/property/replay/E2E 证据；
- 生产代码净增、删除和下一轮预期净删。

建议单个生产提交保持可独立审查。若明显超过约 500 行净新增，应优先拆成合同、纯 Reducer、Profiler shadow、Broker shadow、Actor 接线和 Verifier等切片，而不是一次引入上万行骨架。

禁止在同一提交中同时：

- 改 Compiler 拆分、Profiler、Actor prompt、Controller 输入和 Verifier；
- 新增多个常驻模型角色；
- 复制 legacy ToolSession/AgentRunner 后再清理；
- 为一个失败样例加入 prompt、adapter 和坐标 fallback；
- 修改真实客户端入口给 vNext 特殊测试条件；
- 广泛重命名并同时迁移 owner；
- 在没有 shadow 数据时直接 enforce 工具裁剪或多义务状态。

## 4. P0：先建立可测基线

状态：**已完成（观测旁路）**。提交 `56b90f1` 建立 `vnext/telemetry.py`，由现有 trace BUS
旁路标注 baseline；提交 `eaf8ae4` 在 legacy `AgentRunner` 已有决策事件上记录精确的上下文和
工具 schema 字符数；提交 `c2f23a4` 补齐角色、Profiler/depth、obligation、oracle 和 artifact
错误等可选 baseline 字段。三处都不参与 prompt、模型选择、工具 dispatch、Controller 或完成判断。

目的：没有数据就不开始大迁移。

逐角色/回合记录：

```text
runtime_version
mission_or_stage_id
model_role
model_tool_count
model_tool_schema_chars
prompt_context_chars
mission_card_chars
invalid_tool_argument_count
duplicate_action_count
no_progress_streak
frame_refresh_count
verifier_reject_count
target_adjustment_px
model_elapsed_ms
controller_elapsed_ms
time_to_first_action_ms
profile_call_count
planner_call_count
inspector_call_count
```

逐 mission 记录：

```text
recommended_execution_depth
effective_execution_depth
execution_policy_override_reason
required_obligation_count
verified_obligation_count
false_done_count
artifact_identity_errors
external_oracle_pass
```

P0只添加观测，不改变决策。用同一 Electron入口收集 legacy基线，并冻结：

- 受控随机视觉场景；
- 原始 11 个真实任务；
- 组合长任务；
- 未共同调试的 held-out任务；
- UIA/OCR 错误或空树负向场景；
- 动画、遮挡、抢焦点、已有实例、高 DPI/多显示器场景。

完成条件：一次失败可以准确归因到入口、任务结构、窗口、帧、工具、Controller、模型、Verifier或外部环境，并知道模型当时看到什么。

当前证据：`python/winpilot_home/tests/test_vnext_baseline.py`，以及已有 trace/evidence/eval
测试回归通过。实际客户端 run 仍需在后续评测批次中收集 paired baseline；本提交不把单元测试当作
真实 GUI 成功证据。

## 5. P1：纯 MissionDefinition、Event 与 Projection

审计结果：canonical wire schema 只序列化 `ObligationSpec` 与 `kind`；历史 `ObligationDefinition`/`type` 只作为读取旧记录的兼容输入，禁止继续产生双写事实。尚未完整实现的 planner、approval、external、user-goal 事件必须 fail-closed。验证必须覆盖全部 acceptance criteria、绑定 `verification_id` 并使用当前 run-scoped EvidenceRegistry；`MissionProjection` 与 `MissionCard` 只能由 Definition、accepted events 和当前证据派生。

状态：**已完成（离线/shadow only）**。提交 `3de1227` 实现
`python/winpilot_home/winpilot/vnext/mission/` 中的 versioned contracts、纯 Reducer、事件
replay、stale revision/evidence scope 校验、单 obligation fallback、MissionCard 与
`project_legacy_contract`/`project_legacy_trace`；提交 `e3f938d` 对齐 `ObligationSpec` 别名、稳定
拓扑序、`kind`/`type` 双写线格式和事件级 evidence refs。它不被 legacy runner 读取，也不改变
`task-contract/1`。

新增内容仅限：

- versioned MissionDefinition/ObligationSpec；
- MissionEvent；
- 纯 Mission Reducer；
- MissionProjection；
- 机械 active obligation selection；
- MissionCard projection；
- event log/replay；
- property tests。

关键约束：

- ObligationSpec不含 lifecycle status；
- MissionProjection只保存一份 `obligation_states[id]`；
- verified/pending/blocked id集合只能派生；
- verified必须引用 accepted VerificationResult 与 immutable evidence；
- Projection必须可从 Definition、accepted events 与 EvidenceRegistry重放；
- 不建立脱离现有 TaskContract/Evidence的第二份持久化完成账本。

P1不启动模型、不操控桌面、不替换 legacy runner。完成后可用历史 trace 离线投影，但不改变产品行为。

测试重点：

- dependency DAG无环；
- deterministic replay；
- stale event reject；
- claim不能直接变verified；
-无 evidence不能写 verified fact；
- 单 obligation fallback保留完整 original goal；
- Planner patch不能加入GUI步骤；
- goal revision不丢 required coverage；
- MissionProjection缓存删除后可完全重建。

## 6. P2：Task Profiler 与 Execution Policy shadow

状态：**P2a 已完成；P2b 真实 Profiler 调用已作为默认关闭的 trace-only shadow 接入同一产品入口**。

P2a 已在独立 `vnext/profiling/` 旁路中实现严格版本化合同、确定性
Execution Policy、canonical broad capability/provider availability 校验、
safe-standard fallback、完整 policy reasons、序列化与可注入 shadow 评测。
P2a 本身不调用模型；P2b 在独立提交中增加只读 provider 边界和同入口 shadow。后续 P3a ToolCatalog/Capability Broker 与 P3b single-obligation partial shadow 已实现，但尚未 enforce。

P2a 已实现：

- TaskProfileProposal schema；
- ExecutionProfile schema；
- deterministic Execution Policy；
- `direct/standard/orchestrated` depth；
- CognitiveServices；
- 可重放 shadow case/result 与 over/under-profile、结构稳定性判定。

P2b 已按单独评审实现：每个 run 至多一次 semantic profile，无工具、无帧、无 runtime identity；SDK 隐式重试关闭，只有 Runtime 可对同一冻结请求做 transport retry。flag 默认关闭，结果只写 TraceRecorder 私有副本，不修改 BUS 或 legacy 执行。

Shadow阶段：

- Profiler读取 original goal、初始 MissionDefinition、verified session facts和可用 capability；
- 不生成 obligation、工具或GUI步骤；
- 不选择 legacy/vNext；
- Execution Policy只记录 accepted candidate profile，不改变 legacy执行；
- malformed/unavailable/low-confidence走安全 `standard`；
- 普通GUI回合不重复调用 Profiler。

记录：

```text
profile_dimensions
recommended_depth
minimum_depth
effective_depth
requested/effective cognitive services
policy overrides
profile latency/cost
over-profile / under-profile
```

P2进入 enforce 的门槛：

- profile不含应用流程、题号和工具序列；
- 改写任务、应用名变化与 held-out上保持语义稳定；
- under-profile没有导致显式依赖/验收丢失；
- over-profile额外成本可接受；
- fixed-standard对照组存在。

## 6.1 P2.5：统一模型请求与重试合同

状态：**WinPilot product package 已接入；外围 legacy provider、全角色逐 attempt sidecar 与 legacy VLM stop_event seam 尚未闭合。**

提交 `3198d5e` 建立 `model-request/0`、`ModelRequestEnvelope`、
`ModelCallRuntime` 和 SDK `max_retries=0` 的唯一 request-retry owner；提交
`435bb0f` 将桌面 mutation replay 从模型请求 retry 中分离，并对不确定 mutation 结果
fail closed。随后辅助 planner、done judge、VLM、conversation 和 message-content 逻辑调用
也通过同一 transport 合同，不再各自直接拥有重试循环。

合同边界：

- provider adapter 只准备一次请求，Runtime 冻结 JSON 并产生 fingerprint；
- request retry 只能重发同一 decision/role/purpose/fingerprint，且调用者尚未消费结果；
- retryable transport failure 使用显式 allowlist 和有界总 deadline；程序错误、解析错误和 schema 错误不重发；
- poll 只等待 in-flight request；修改消息、工具、thinking、预算或目的必须是新的 redecision；
- action replay 由 mutation replay policy 独立裁决；结果 unknown 时只能 reconcile/block，不能自动重复桌面输入；
- 精确函数调用栈 AST guard 限制 WinPilot product package 内允许的 Chat Completions transport，并拒绝 Responses 或未授权调用路径；物理 adapter call site 不等于第二个 retry owner。

该阶段不改变 prompt、模型工具面、Controller、GUI action、legacy done 或 vNext runtime
选择，也不实现 P4/P5/P6。仍未完成：

- `python/providers/openai.py`、`deepseek.py`、`vlm.py` 属于外围 legacy
  Flow/feature stack，仍有 SDK/direct-HTTP retry；必须先证明产品不可达，再于 P9 删除，或单独迁移；
- 某些 legacy 变量仍把截断恢复或 tool-call correction 称为 retry；它们改变请求，语义上必须记为 redecision；
- 全角色尚未把每个 `ModelAttempt` 写入统一 sidecar；legacy VLM helper 还缺少 stop_event seam。Profiler 的 stop_event 已从 ArpExecution 经 shadow hook/runtime/provider 传到 ModelCallRuntime 并有测试；缺失 telemetry 不得记为零或“已验证”；
- transport 收敛不改变 legacy planner/done/VLM helper 的语义 owner，不能据此宣称 Visual Actor、独立 Verifier、Planner 或 Inspector 已接入。

未来删除旧 transport/retry 职责的门槛是：产品可达模型调用无绕过 owner、SDK 隐式重试为零、
request retry/redecision/poll/action replay 可由 trace 区分、late response 不能产生动作，且外围
legacy provider 的生产调用连续观察为零或已通过独立迁移。

## 7. P3：Capability Broker shadow

状态：**P3a 纯合同与 P3b 默认关闭的 single-obligation partial legacy sidecar 已完成；多 obligation 对齐和 enforce 尚未接入。**

建立 ToolCatalog、CapabilityGrant 和纯 Broker：

```python
project_execution(
    mission_projection,
    execution_profile,
    target_state,
    pointer_lease_state,
    artifact_state,
    adapter_registry,
    approval_state,
    active_grants,
) -> BrokerProjection
```

先在 shadow 中映射每个 legacy 回合：

- legacy实际调用工具是否在 projection；
- route在 `visual_actor/deterministic/provider/blocked` 间单写入互斥；
- projection是否暴露无关工具；
- 缺失工具来自 profile/contract错误还是基础能力缺口；
- 平均/最大工具数与schema字符数；
- pointer、target、artifact、adapter、MCP变化是否正确；
- CapabilityRequest若出现，是否能被 catalog/policy安全解释。

P3不改变 legacy工具，不调用第二个桌面模型。

P3a 当前实现边界：

- `vnext/capability_names.py` 是 canonical broad capability 的唯一 owner，Profiler Policy 复用它；`vnext/capabilities/names.py` 只提供兼容 re-export；
- ToolCatalog 只把现有 schema owner 与通用 policy 关联，不复制工具实现；
- Broker 只消费 MissionProjection、ExecutionProfile、权威 target/frame、pointer、artifact、executor offer、授权和 grant 状态；输入合同没有应用名、任务原句、benchmark、布局或坐标字段；
- provider/deterministic offer 必须 live、已授权且能产生 receipt，并与 Visual Actor 路线互斥；artifact route 同时核对 run、artifact_id、content hash 与 sendable 状态；
- pointer held/unknown、stale frame、terminal mission 与 runtime 不可写状态均 fail closed；
- shadow evaluator 不把 legacy 实际选择当作“必要工具”真相。没有完整可靠 oracle 标注时，`necessary_tool_recall` 必须保持 `None`。

P3b 的同入口接线只在 `vnext.broker_shadow.enabled=true` 时启用：模型请求前读取同一个 FrameAuthority、已验证 target、同一次 pointer 状态和 owner-scoped local capability registry，记录 BrokerProjection、outer monotonic turn、child run/step 关联以及可重放的 Definition/event/evidence delta。实际工具继续使用现有 `agent_tool` trace 离线配对；shadow 不改 legacy schema、顺序或对象，也不增加 BUS/WebSocket 事件。observer 在 run 结束时从 runner 显式解绑。

尚未满足的门槛：当前没有真实 held-out necessary-tool 完整标注和 A/B 报告，因此不得启用 enforce，也不得删除 legacy 固定工具面。多 obligation 若无法与 legacy child runner 机械对齐必须记录 unavailable；artifact、adapter、MCP 与授权若没有可信 registry/receipt 必须保持空，不能从配置或任务文本推断。

进入 enforce 前：

- held-out trace无应用名/题号分支；
- 所有用于门槛计算的 turn 都有独立、完整的 relevance oracle；缺标注的 turn 单独报告，不进入分母；
- controlled、mixed 与 held-out 的 required tool group recall 均为 100%，任何必需 recovery、pointer release、stop/cancel 或证据工具漏失都阻止 enforce；
- 普通工具面通常小于十个；
- pointer lease工具面严格受限；
- grant有revision/obligation/expiry并可审计。

## 8. P4：Visual Actor 受控旁路

状态：**P4 的 click-only 实验闭环已接到同一产品入口：Server 按冻结的 per-run selection 注入具体 coordinator，唯一模型 Visual Actor 通过 Broker 小工具面提出 click/double-click/claim/blocked，动作复用唯一 Controller/RawDevice 链；三个随机 seed 的真实 Electron/UIA canary 已通过独立 oracle，legacy 仍默认，完整 P7 未达。**

P4a 先在独立 `vnext/actor/` 和 `vnext/runtime/` 中实现严格版本化的 Actor 可见 view、无 Runtime 身份 proposal、Runtime-bound envelope、注入端口与单回合协调器。当前 click-only live coordinator 在这些端口之外组装隔离模型 Actor、RuntimeState bridge、Broker 与 atomic Controller；MissionRuntime 本身仍不导入 legacy AgentRunner/ToolSession，也不选择 runtime version。legacy 仍是默认，P3 全量 Broker enforce 门槛仍未达到。

P4a admission 在 Actor 返回后重新读取 Mission revision/active obligation、完整权威 frame/target/pointer 状态并重新投影 Broker。TOCTOU 不一致或动作不在最新小工具面时丢弃 proposal；每回合至多向注入 ControllerPort 提交一笔 Runtime-bound transaction。Controller 结果严格区分 `completed`、`rejected`、`unknown`；`unknown`、执行后异常或无法对账的结果只会阻塞当前义务，不会自动 action replay。

`claim_obligation_done` 本身只接受 `obligation_claimed` 并进入 `CLAIMED`。P5a 只有在 ExecutionProfile 允许且注入只读 ObligationVerifier 时才继续验证；P5b 只有在本地 MissionCompletionVerifier 与 Reducer 共用同一 EvidenceRegistry 时才机械关闭 mission。Capability/Inspector/Planner 等未接服务仍返回 unavailable；P6 在没有跨任务 A/B 证据前继续 fail closed。

P4b 按原语把这些合同接到现有唯一 FrameAuthority、admission/坐标校验和 Controller/RawDevice 链。Bridge 必须先让 legacy 委托共享 owner，不能复制 capture、action parser、pointer owner 或执行器。当前只有 click/double-click 进入 live 实验链；三个受控 seed 证明该窄链可达，不证明其他原语或真实任务。

P4b 当前完成的可独立回退切片包括：legacy 委托两阶段 `FrameObserver`；submitted-image -> normalized-frame -> physical-screen 的单次 typed 坐标映射；每 run 冻结一次的 `DesktopRuntimeSelection`；typed scroll RawDevice seam；以及默认关闭的 `AtomicClickController`。该 Controller 仅接受 `click`/`double_click`，在唯一 physical lease 中消费 Runtime mutation permit、pin 权威 frame、核对 commit-time HWND/PID/epoch/region/hit-test，并在每次 mouse-down 紧前做 final target guard。动作后释放 frame pin 但保持同一 physical lease，再由 `AtomicPostActionBridge` CAS 提交新权威帧并向 run-scoped EvidenceRegistry 写入对应 evidence。

具体 live coordinator 已组装 `AtomicClickController`、共享 Frame/target/evidence owner、隔离 Visual Actor model port 和只读验证端口。它的 Catalog 只暴露 `click`、`double_click`、`claim_obligation_done`、`blocked`；Runtime 每回合自动捕获最新权威帧，不暴露会得到同一 full-frame 的冗余 `observe`。真实 ExecuteDesktopTask contract 和 live Runtime state 同时声明 canonical `visual.interact` 与 `visual.read_text`，因为 Actor 直接读取当前像素；Catalog 仍只以 `visual.interact` 授权点击，`visual.read_text` 不加载 OCR/UIA。此前 capability owner/授权不一致已统一，未增加应用或 canary 专属 capability。`type_text`、`hotkey`、Actor 可见 `scroll`、`drag`、`draw_strokes`、held pointer、ROI observe 仍未覆盖；FastGuiController 继续处理 legacy 产品原语。PostMessage 结果不确定时禁止 SendInput fallback，是 no-uncertain-replay 合同的刻意安全修正。

P4c 已把 product coordinator 严格注入 seam 接到 Server。可见客户端的 `Legacy（默认）` / `vNext（实验）` 选择只存在于顶层 run request；`ArpExecution` 仅在 selection 已冻结为 `vnext` 且注入 coordinator 时绕过 legacy handlers，并复用同一 run id、BUS、stop 与 pause gate。coordinator 构造或运行失败不得回退 legacy；回滚只能在下一个独立 run 明确选择 `legacy`。两个 `precise-click` grounded pass 是该窄入口的证据，不是完整产品切换证据。

本轮将 coordinator context 升为严格的 `desktop-runtime-coordinator-context/1`。新增
`is_paused` 是非阻塞 read：`wait_if_paused` 仍只在 turn 边界协作式等待，Actor/model 返回后则通过
`is_paused` 让 admission 立即失效；resume 不继续旧 proposal，而是从新权威帧开启新 decision。
旧 context `/0` 必须拒绝；result 合同仍是 `desktop-runtime-coordinator-result/0`，其他 `/0`
合同没有不兼容字段变化，不做无意义批量升版。

live bridge 还闭合三项机械来源：frame freshness 使用显式 injectable max-age（默认 30 秒）并在
post-model reprojection 重算；turn budget 只接受唯一 full-goal ExecuteDesktopTask 的 canonical
`max_steps`，与 session `requested_max_steps` 冲突时 fail closed，effective turns 再由本地默认 24
上限收紧；`RawDeviceExecutor` 只由 Server product composition root 构造并注入，coordinator 缺失
该依赖就拒绝构造，不复制 backend owner。

queue-time capability admission 债务已闭合：Server 冻结 runtime selection，vNext composition root
构造一个不可变 `RuntimeCapabilitySnapshot`，随后在 prepare/model/mutation 前只调用一次
`DesktopRuntimeAdmission`。vNext admission 机械核对单个 lossless full-goal obligation 的 canonical
capabilities、required services 和 snapshot identity；同一 snapshot 继续供 `ExecutionPolicy`、live
`RuntimeState` 与 Broker 使用。legacy 分支仍按原顺序保留既有 static/runtime preflight，无 contract
兼容行为不变。该切片没有修改 prompt、模型工具面、Controller、GUI action、done/Verifier、前端或
task-contract/1，也没有扩大 click-only 工具面。

下一迁移门槛是多原语 parity：按小切片将 `type_text`、`hotkey`、Actor 可见 `scroll`、`drag`、
`draw_strokes`、held pointer 与 ROI observe 接入同一个 snapshot/Catalog/Broker/Controller owner。每个
原语都必须先补 capability/service/parity 负向与 property 测试，再做同入口 Electron oracle；mutation
结果不确定时仍禁止 replay，缺能力时禁止 legacy fallback。门槛未达前 P7 仍未完成，P6 保持禁用，
P8/P9 不开始。

P4a 不授权删除任何 legacy 产品职责。`AgentRunner` 的全局 todo、模型直接 `done` 和固定大工具面只能在 paired canary 与真实 Electron E2E 达到 [evaluation.md](evaluation.md) 门槛，且 trace 证明所有 vNext Actor 回合分别由 MissionCard、P5 verification 和 BrokerProjection 接管后删除；单元测试、注入 fake 或一次受控成功都不构成删除证据。

建立最小 Actor loop，只消费 MissionCard、FrameState和BrokerProjection。模型只输出无Runtime身份的 ActorProposal，Runtime构造 ActorDecisionEnvelope。

第一批只运行受控视觉目标：

- 点击/输入/滚轮；
- 静态拖拽；
- 多关键点路径；
- held pointer；
- 外部变化等待；
- unknown single obligation。

同一 Electron客户端提交任务，runtime version由显式实验配置选择。

硬限制：

- 每回合一笔 canonical transaction；
- Actor不持有全局 todo；
- Actor无terminal done，只有claim/blocked/replan/request capability/inspection；
- 模型不提交 mission/revision/obligation/frame/evidence identity；
- Runtime envelope绑定当前状态；
- Controller不改变目标；
- legacy与vNext不同时运行；
-失败保留真实oracle结论。

## 9. P5：claim 与独立 Verifier

状态：**P5 的 click-only live 路径已接入独立视觉 ObligationVerifier 和本地 deterministic MissionCompletionVerifier；ActionEffectVerifier、跨进程 exactly-once 和真实 verifier 校准仍未实现。**

P5a 冻结 accepted claim fingerprint、attempt 和当前 run-scoped evidence，只把无 Runtime 身份的 assessment 交回 Runtime；不可用、异常、malformed、stale 或证据变化都 fail closed，最终只能由 Reducer 接受 verification event。当前 live port 使用与 Actor 隔离的只读视觉模型上下文，且没有 mutation 工具。视觉许可必须同时满足 EvidencePolicy 和 Registry 中真实 evidence trust；模型只分类 Runtime 编号后的验收条件，Runtime 机械恢复 canonical assessment，并在 provider 无可靠数值来源时记录 `confidence=None`。Runtime 校验当前 frame/revision/claim 后才把 assessment 写成 attested run-scoped evidence，模型 payload 不能自报 trust、frame、canonical 条件或 evidence identity。

P5b 是进程内 evidence-first 闭合：`DeterministicMissionCompletionVerifier` 和 canonical `EvidenceRegistry` 必须成对注入，且 Reducer 必须读取同一 registry。Runtime 冻结全部 required obligation 的 accepted verification identity 与完整证据，生成内容哈希绑定的 mission aggregate，先写 evidence，重读确认后再接受 `mission_verified`。视觉 ObligationVerifier 不拥有 mission completion 权限。这不是可持久 WAL；进程在 evidence 与 event 之间崩溃时没有已证明的跨进程恢复/exactly-once 语义。

拆分当前混合 DoneVerifier：

```text
ActionEffectVerifier
ObligationVerifier
MissionVerifier
```

顺序：

1. deterministic evidence chain；
2. application oracle/provider receipt；
3. 无mutation工具的独立视觉 ObligationVerifier；
4. Runtime绑定subject/revision/evidence，模型只输出 VerifierAssessment；
5. Reducer接受VerificationResult后才改变lifecycle；
6.全部 required obligation verified 后再运行 MissionVerifier/CompletionContractVerifier。

必须验证：

- 相同evidence fingerprint重复claim不重复调用模型；
- Actor与Verifier上下文隔离；
- verifier unavailable/malformed fail closed；
- deterministic contradiction优先；
- EvidencePolicy最大次数和 uncertain policy生效；
- false positive低于legacy，false negative可解释；
- 同模型Actor/Verifier错误相关性有报告。

## 10. P6：Planner 与 Inspector 按证据引入

状态：**禁用。尚无至少两个 held-out 任务族的 external-oracle A/B 收益证据，因此不实现常驻或可写 Planner/Inspector。**

Planner/Inspector不是P1-P5前置条件。

Planner引入判据：

- profile/Compiler无法安全表达跨义务依赖；
- 两种实质不同局部策略失败；
- 用户修改目标；
- obligation与新证据冲突；
- approval policy要求高层权衡。

Inspector引入判据：

- Actor已用高清ROI；
- frame/coordinate/path工具已通过确定性核对；
- 多个held-out视觉拓扑仍失败；
- Inspector相对Actor自行观察有external-oracle稳定增益；
- 收益覆盖至少两个任务族。

两者只读、按事件、无常驻上下文、无GUI mutation工具。模型proposal由Runtime绑定revision/frame/evidence。

## 11. P7：真实任务 canary

状态：**P7 已开始：同入口 click-only canary 的 seed `987654`、`424242` 与 `135791` 已从冷启动、空 session、可见 Electron/UIA 客户端通过独立 trusted-browser oracle；paired legacy、真实 11 题、mixed/held-out、多布局和多原语矩阵仍未完成，因此 P7 未达标。**

第一步只用随机化 `precise-click` 验证“客户端选择 -> runtime 冻结 -> Actor proposal -> Broker
admission -> 唯一 Controller/RawDevice -> 新权威帧 -> 独立 obligation/mission 验证 -> external
oracle”这一条 click 链。case 名、几何和成功条件只存在于 harness/oracle；生产 Runtime、Catalog、
Actor 和 Verifier 不读取它们。当前通过记录：

- seed `987654`：run `arp-20260722-154335-a9e64b`，3 turns，`output/playwright/client-visual-e2e/20260722-154302-suite/suite-report.json`；
- seed `424242`：run `arp-20260722-154447-9329b4`，2 turns，`output/playwright/client-visual-e2e/20260722-154415-suite/suite-report.json`；
- seed `135791`：run `arp-20260722-165702-086da6`，2 turns，`output/playwright/client-visual-e2e/20260722-165627-suite/suite-report.json`。

它们只证明对应 seed 的 click-only 闭环，不证明其他原语或真实任务。此前失败尝试暴露的
冗余 observe 循环、Verifier schema、`SW_RESTORE` 几何变化和 page-ready stale geometry 均以
通用 Runtime/Verifier/harness 修复闭合，生产代码未读取 case、seed、布局或固定坐标。

在受控场景稳定后运行：

- 11个黑盒任务；
- 组合长任务；
- held-out未知任务；
- 同一任务不同应用版本/布局；
- 已运行实例、托盘、登录态、弹窗和外部干扰；
- simple任务的over-profile；
- complex任务的under-profile。

每个case从同一客户端入口提交并隔离memory。测试不得预置内部obligation、profile、route、工具或Inspector结论；Compiler、Profiler、Policy、Reducer、Broker和Verifier都是被测对象。

Canary失败不在同一run偷切legacy。可以另开独立run对照。

## 12. P8：默认切换

状态：**未开始；P2/P3 评测门槛、P4/P7 真实 canary 结果、P5 false-positive 校准和多原语/真实任务报告均未满足。**

默认切换必须同时满足：

- contract/reducer/property tests稳定；
- Profiler/Policy没有应用/测试专属路由；
- Broker没有应用/测试专属分支；
-只读角色mutation权限为零；
- 受控E2E不低于legacy；
- held-out grounded pass不低于legacy，false done更低；
- mixed任务的obligation coverage、artifact identity和session reference正确；
- p50/p95模型等待、time-to-first-action和总耗时可解释；
-至少两种模型配置证明合同不依赖单一provider；
-同模型不同角色的隔离与相关错误已校准；
- stop/pause/cancel/pointer cleanup/external wait无回归；
-文档明确剩余失败。

先做小比例canary，再扩大。回退是下一个独立run显式选择legacy，不在运行中切回旧Agent。

## 13. P9：删除 legacy 职责

状态：**未开始；当前可删 legacy 产品职责为 0。**

切换不是完成，净删旧代码才是完成。每项删除需满足：

1. vNext owner已承载生产流量；
2. legacy调用trace连续观察窗口内为零；
3.等价或更强unit/E2E/oracle证据存在；
4.旧序列化合同已有单向migration adapter或不再读取；
5.删除后不保留第二份状态、guard、planner或tool route。

## 14. 旧职责迁移矩阵

| 当前职责 | vNext owner | 迁移方式 | 删除门槛 |
| --- | --- | --- | --- |
| `TaskContractCompiler` 对GUI任务族解释 | Goal Compiler | 只保留可靠obligation/artifact dependency，未知目标单obligation | coverage/cold-start E2E |
| Server 的 legacy `CapabilityPlanner` 通用 admission | `DesktopRuntimeAdmission` + immutable `RuntimeCapabilitySnapshot` | **已迁移**：一个 queue-time owner 按冻结 runtime 分派；legacy 保留原 preflight，vNext 使用 canonical catalog/service/controller inventory，不解释 GUI 路线 | 持续回归 paired missing-capability、identity/parity、admission 前零副作用；旧 Server 直调 owner 已删除 |
| Actor自行判断任务复杂度 | TaskProfiler + ExecutionPolicy | 先shadow，再accept profile | over/under-profile达标 |
| `_EXECUTE_DESKTOP_MODEL_TOOLS` 固定大工具面 | ToolCatalog + Broker | shadow后按run投影 | paired canary/真实E2E达标，所有Actor回合取BrokerProjection且必要工具recall门槛通过 |
| `AgentRunner`全局todo | MissionProjection + Runtime | Actor只消费MissionCard | paired canary/真实E2E达标且trace无legacy plan读取 |
| 模型复制frame/revision/obligation ids | Runtime envelope | proposal与身份分离 | 所有决策经envelope校验 |
| `AgentRunner` frame/coordinate镜像 | FrameAuthority + typed envelope | 动作只引用当前绑定 | 统一preflight |
| `done`直接结束run | claim + Verifier + Reducer | claim仅进入CLAIMED，P5验收后才前进 | paired canary/真实E2E达标且无模型terminal done |
| `DoneVerifierMixin`混合验证 | 三层Verifier | deterministic -> oracle -> visual | typed verdict全覆盖 |
| `RunStateMachine`与进度账本重叠 | transaction state + MissionProjection | 保留动作phase，删除全局语义副本 | 一个事实一个owner |
| `FailureRecoveryLayer`选择恢复倾向 | FailureFact + Actor/Planner | 分类不执行 | fallback cascade为零 |
| `InteractionStageEngine`重复轮次/等待 | MissionRuntime + Lease/Watcher | 保留通用wait/lease | 持续任务E2E |
| Subgoal/adapter GUI planner | Controller原语 + Actor | 保留receipt，不保留GUI路线 | 第二任务族证据 |
| UIA/OCR自动语义升级 | 显式probe capability | provenance/frame gate | negative eval通过 |
| 各模型调用点/SDK自行重试 | `modeling/ModelCallRuntime` | adapter prepare一次，冻结request后仅做精确transport retry | product package无绕过；逐attempt trace与cancel闭合 |
| mutation-capable node通用retry | mutation replay policy + 新Actor决策 | unknown outcome reconcile/block；仅确定未执行且显式幂等授权才可replay | 所有桌面mutation经同一transaction/evidence合同 |
| session-bound raw action reflection | typed RawDevice primitive + 临时 legacy adapter | 按 primitive 先共享物理 owner；当前有 scroll seam 和默认关闭的 atomic click/double-click 切片 | type/hotkey/drag/draw/held pointer 完成 parity、partial-input 与真实 E2E 后删除 adapter |
| `python/providers/` legacy retry loop | 删除或单独迁移 | 先审计产品可达性，不接成第二个Runtime | P9观察窗口内生产调用为零或等价迁移通过 |

## 15. Bridge纪律

Bridge只能做形状转换：

- legacy TaskContract -> MissionDefinition；
- legacy trace -> shadow TaskProfile/Mission/Broker输入；
- vNext TransitionRecord -> 现有trace/event projection；
- 现有FrameAuthority/Controller/Artifact/Evidence接口适配；
- vNext run result -> Electron history view。

Bridge禁止：

- 根据应用名补 obligation/profile；
- 把legacy stage转成点击步骤；
- vNext失败后偷偷调用legacy Agent；
-复制FrameState、PointerLease或completion status；
-把Actor claim转成旧`done=true`绕过Verifier；
-接受模型提供的run/revision/frame/evidence身份覆盖Runtime绑定。

每个bridge必须有删除条件和调用telemetry。

## 16. 文档维护

每阶段完成时更新：

- 本目录README当前阶段；
- 本迁移计划完成证据与下一删除目标；
- [execution-profile.md](execution-profile.md) 的profile合同；
- [evaluation.md](evaluation.md) 的基线/对照；
- [failure-triage.md](failure-triage.md) 的通用失败模式；
-主架构文档中“已实现/目标设计”边界。

具体run证据留在trace/report。长期文档不保存密钥、本机绝对路径、单次临时截图或未复现成功。
## P1.5：完成、证据与恢复合同闭合（已完成，仍为离线 shadow）

本阶段只扩展 P1 的纯合同与可重放 Reducer，没有进入 P2，也没有改变
prompt、模型调用、工具面、Controller、GUI 动作或 legacy `done` 语义。

已实现并由负向、随机化和 replay 测试覆盖：

- required obligations 全部 verified 后只派生
  `awaiting_mission_verification`；独立的 `mission_verified` 事件必须携带
  新的 mission-level `verification_id` 和当前 run-scoped evidence，之后才
  能进入 terminal `verified`；
- obligation 记录 Runtime 生成的 claim fingerprint、上次已验证 fingerprint、
  activation/verification attempt 计数和 accepted verification identity；同一
  fingerprint 不会重复触发验证；EvidencePolicy 的视觉 Verifier 许可、最大
  次数和 uncertain policy 进入 Reducer 状态机；
- ArtifactRef、TargetBinding、VerifiedFact 的身份和 evidence 关系严格校验；
  TransitionSummary/FailureSummary 绑定 obligation，MissionCard 不向新义务
  泄漏上一义务的反馈；
- legacy split 只有在每一行都被显式 dependency/artifact edge 覆盖时保留，
  否则退回单个 full-goal obligation；未知 legacy capability 只进入 metadata，
  不进入 required_capabilities；
- 完成、重复 fingerprint、超出尝试次数、错误证据 scope、错误身份、stale
  event、terminal write 和 replay/property 情况均 fail-closed。

P1.5 的实现仍是 sidecar/shadow；产品真实客户端入口、WinPilot 全量回归、
`npm test`、typecheck 和 build 只用于行为不变回归，不能把离线合同测试当作
真实 GUI 成功证据。后续 P2/P3/P4a 的旁路实现不改变这一证据边界。

## P2a status (2026-07-21)

P2a is complete in the sidecar only: strict versioned
`TaskDimensions`, `CognitiveServices`, `TaskProfileProposal`,
`ExecutionDepth`, `ExecutionProfile`, and deterministic `ExecutionPolicy` are
implemented with injected shadow tests. The policy validates canonical broad
capabilities, defaults malformed or low-confidence input to safe `standard`,
restricts depth and cognitive services, and records auditable reasons.

P2b now adds the real, tool-free Profiler provider and an at-most-once shadow
hook under the same owner. It is disabled by default and writes only a private
TraceRecorder sidecar; it cannot mutate a desktop or select runtime version.
P2a/P2b do not change legacy prompts, model tool surface, GUI actions,
Controller, FrameAuthority, done semantics, or frontend. Later P3 and P4a
sidecars do not retroactively change this P2 boundary; live Actor, Verifier,
Planner, and Inspector remain later gates.
