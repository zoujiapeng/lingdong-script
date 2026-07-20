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

本轮明确未实施 P2：没有 TaskProfiler、Execution Policy、ToolCatalog、Capability Broker、
shadow 工具投影或任何第二模型调用。

先实现：

- TaskProfileProposal schema；
- ExecutionProfile schema；
- read-only TaskProfiler调用边界；
- deterministic Execution Policy；
- `direct/standard/orchestrated` depth；
- CognitiveServices；
- profile trace。

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

## 7. P3：Capability Broker shadow

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

进入 enforce 前：

- held-out trace无应用名/题号分支；
- necessary tool recall达到门槛；
- 普通工具面通常小于十个；
- pointer lease工具面严格受限；
- grant有revision/obligation/expiry并可审计。

## 8. P4：Visual Actor 受控旁路

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
| Actor自行判断任务复杂度 | TaskProfiler + ExecutionPolicy | 先shadow，再accept profile | over/under-profile达标 |
| `_EXECUTE_DESKTOP_MODEL_TOOLS` 固定大工具面 | ToolCatalog + Broker | shadow后按run投影 | 所有Actor回合取BrokerProjection |
| `AgentRunner`全局todo | MissionProjection + Runtime | Actor只消费MissionCard | trace无legacy plan读取 |
| 模型复制frame/revision/obligation ids | Runtime envelope | proposal与身份分离 | 所有决策经envelope校验 |
| `AgentRunner` frame/coordinate镜像 | FrameAuthority + typed envelope | 动作只引用当前绑定 | 统一preflight |
| `done`直接结束run | claim + Verifier + Reducer | claim不改变状态 | 无模型terminal done |
| `DoneVerifierMixin`混合验证 | 三层Verifier | deterministic -> oracle -> visual | typed verdict全覆盖 |
| `RunStateMachine`与进度账本重叠 | transaction state + MissionProjection | 保留动作phase，删除全局语义副本 | 一个事实一个owner |
| `FailureRecoveryLayer`选择恢复倾向 | FailureFact + Actor/Planner | 分类不执行 | fallback cascade为零 |
| `InteractionStageEngine`重复轮次/等待 | MissionRuntime + Lease/Watcher | 保留通用wait/lease | 持续任务E2E |
| Subgoal/adapter GUI planner | Controller原语 + Actor | 保留receipt，不保留GUI路线 | 第二任务族证据 |
| UIA/OCR自动语义升级 | 显式probe capability | provenance/frame gate | negative eval通过 |

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
