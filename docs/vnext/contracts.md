# vNext 状态与角色合同

本文定义 vNext 各层交换的 typed data。P1/P1.5 已实现纯 Python 合同、Reducer、replay、MissionCard 和 legacy shadow；P2 Profiler 与 P3 Broker 已有默认关闭的 shadow，但评测/enforce 门槛未达；queue-time capability admission 已收敛为一个 runtime-aware owner。P4/P5 现有默认关闭的增量 live coordinator：严格 Actor proposal/view/envelope、模型 Visual Actor、已绑定状态的 `click/double_click/scroll/type_text/hotkey/claim/blocked` 小工具面、注入式 targetless Runtime 的只读 `discover_target`、共享 atomic Controller 执行链、deterministic ActionEffectVerifier、独立视觉 ObligationVerifier 与本地 deterministic MissionVerifier 已接入同一代码组合。existing-window activation 的 RawDevice/Controller/post-frame/Runtime binding/accepted-event trace 内部闭环也已实现，但 live Catalog、Server raw backend 与 targetless Electron 入口尚未接线，因此产品仍不能抵达该 mutation；legacy 仍默认。P4b.1 keyboard 已有同一 Electron 入口和独立 oracle 证据：`keyboard-entry` 在当前 previous-action 合同下 3/3 通过，`keyboard-select-all` 在修正 layout-independent oracle 后通过。这些配置与早期基线不同，不证明通用 Agent 能力提升，也不满足完整 P7；其余 GUI 原语、跨进程 exactly-once 与 P6 均未实现。

## 1. 版本与基本规则

建议初始格式：

```text
model-request/0
task-profile-proposal/0
execution-profile/0
mission-definition/0
mission-event/1
mission-projection/2
mission-card/2
action-effect/0
actor-mission-view/2
actor-frame-view/0
actor-tool-view/1
actor-turn-input/5
actor-previous-action/0
actor-proposal/1
actor-envelope/2
tool-spec/1
tool-catalog/1
broker-runtime-state/1
broker-projection/1
runtime-frame/0
runtime-snapshot/2
controller-transition/1
mission-runtime-result/0
desktop-runtime-coordinator-context/1
desktop-runtime-coordinator-result/0
runtime-capability-snapshot/0
runtime-admission-decision/0
transition-record/0
verifier-assessment/0
verification-result/0
obligation-verification-request/0
obligation-verification-result/0
accepted-obligation-verification/0
mission-completion-verification-request/0
mission-completion-verification-result/0
mission-completion-aggregate/0
mission-patch-proposal/0
inspection-report/0
execution-context/1
desktop-target/2
```

`/0` 表示尚未发生首个不兼容升级的旁路实验合同；单个合同只有在真实字段边界变化时才升版，
不是等到默认切换时统一改号。
以上版本已按当前代码逐项核对：coordinator context 因新增非阻塞 pause reading 升为
`desktop-runtime-coordinator-context/1`；ActionEffect/no-progress 切片先使 `mission-projection`、
`mission-card` 和 `controller-transition` 升为 `/1`，严格 TargetBinding 又使前两者升为 `/2`，
并使承载 target payload 的 `mission-event` 升为 `/1`。completion handoff 后 `actor-mission-view`
为 `/2`、`actor-turn-input` 为 `/2`；后续因 tool 增加 `requires_local_intent` 将
`actor-tool-view` 升为 `/1`，因 turn 增加可选 `previous_action` 将 `actor-turn-input`
升为 `/3`，再因 ordinal-only target choices 升为 `/4`，最后因加入一次性、无身份的空 discovery
反馈升为 `/5`，并新增 `actor-previous-action/0`。
只读 `discover_target` 增加独立 proposal variant 后，`actor-proposal` 升为 `/1`；旧 `/0`
不再接受，且 discovery query 仍不携带 Runtime 身份或 GUI mutation intent。由于
`ActorDecisionEnvelope.proposal` 嵌套该 breaking contract，envelope 同步升为
`actor-envelope/2`；`actor-envelope/1` 与新 proposal、或新 envelope 与旧 proposal 的混合值均
fail closed，不能只升级内层而保留旧外层版本。
targetless observation 又将 `tool-spec` / `tool-catalog` 升为 `/1`（新增严格 prerequisite 名）、
将 `broker-runtime-state` / `broker-projection` 升为 `/1`（frame scope 与 candidate pair），并将
`runtime-snapshot` 升为 `/2`（targetless frame 必须为 full desktop）。这些旧版本均不再接受；
`action-effect/0` 与 `actor-previous-action/0` 是新合同。其他仍列为 `/0` 的合同未发生
不兼容字段变化，不能为追求版本号整齐而批量改号。

`CommitEnvironment` 与 `RuntimeSnapshot` 是同进程 authority handoff，不是持久化记录或模型
payload；它们以 constructor 的严格 `format` 防止旧 typed value 混入，但故意不提供 JSON
round-trip。特别是 `RuntimeSnapshot` 持有权威帧图像，禁止为了 trace 再序列化完整对象。trace 只写
既有有界 sidecar/fingerprint；若未来需要跨进程传输，必须另建不含图像副本的新版本合同和完整
`to_dict/from_dict` 测试，不能把当前对象暗中当 wire schema。

基本规则：

- 模型输出不携带 run、mission、revision、active obligation、frame、evidence 或 broker projection 身份；
- Runtime 用当前不可变状态把模型 proposal 包成 envelope；
- 只有 Runtime-bound envelope 可以进入 Controller、Reducer、Planner 或 Verifier；
- 所有记录 JSON 可序列化并写入现有 trace；
- 模型输出先做 schema validation；
- stale revision、stale frame、错误 tool grant 或错误 evidence scope fail closed；
- 验证失败不改变完成状态；
- 不保存、传播或要求模型 chain-of-thought；
- 自然语言字段不能绕过 typed 权限边界。

### 1.1 Model request、retry 与 follow-up

`model-request/0` 是 Runtime 到 provider transport 的机械合同，不是模型输出，也不是
`ActorDecisionEnvelope`。`ModelRequestEnvelope` 至少绑定：

```text
decision_id
model_role
purpose
request_fingerprint
```

provider adapter 必须先完成一次请求形状转换，Runtime 再把 JSON request 冻结并计算稳定
fingerprint。每次 transport attempt 都从冻结值解出新的副本；transport 修改自己的副本不能改变下一次
attempt。循环、非 JSON 值、非字符串 object key、错误版本或不一致 fingerprint 必须 fail closed。

`ModelAttempt` 区分 `initial_request` 与 `request_retry`，并记录 attempt number、同一
request fingerprint、elapsed、成功/失败、error kind 和是否属于明确允许的瞬时 transport failure。
provider SDK 的隐式重试必须为零；Runtime 是唯一 request retry owner。成功响应一旦交给调用者，后续
空内容、JSON/schema 或业务验证失败都不能重新归类为 transport retry。

follow-up 语义严格分开：

- `request_retry`：同一 decision、role、purpose 和 fingerprint，调用者尚未消费任何结果；
- `poll`：等待同一 in-flight request，不提交第二个模型请求；
- `redecision`：输入、工具、thinking、预算、purpose 或语义决策发生变化，必须使用新的 decision id；
- `action_replay`：不属于模型 retry，由 mutation replay contract 单独裁决；桌面 mutation 结果不确定时禁止自动重放。

当前该合同覆盖 `python/winpilot_home/winpilot/` product package。外围 legacy
`python/providers/` 仍需生产可达性审计；全角色逐 attempt trace 尚未闭合，legacy VLM helper
也还没有 stop_event seam。Profiler 的 ArpExecution -> shadow hook/runtime/provider ->
ModelCallRuntime 取消链已经闭合并有测试，不属于这些遗留项。
这些遗留项不能被描述成 P4/P5/P6 角色已实现。

## 2. TaskProfileProposal

Task Profiler 是只读模型角色。它提出多维任务画像与认知服务建议，不生成任务步骤。

```python
@dataclass(frozen=True, slots=True)
class TaskProfileProposal:
    dimensions: TaskDimensions
    recommended_depth: Literal["direct", "standard", "orchestrated"]
    minimum_depth: Literal["direct", "standard", "orchestrated"]
    requested_services: CognitiveServices
    broad_capability_hypotheses: tuple[str, ...]
    upgrade_triggers: tuple[str, ...]
    downgrade_conditions: tuple[str, ...]
    confidence: float
    rationale: tuple[str, ...]
```

`TaskDimensions` 建议包含：

```text
step_dependency
application_scope
artifact_flow
external_wait
irreversibility
visual_precision
continuous_pointer
completion_observability
identity_binding
user_interaction
```

禁止字段：

```text
runtime_version
application_workflow
tool_sequence
button
selector
coordinate
next_action
mission_patch
```

## 3. ExecutionProfile

Execution Policy 校验 TaskProfileProposal 后产生：

```python
@dataclass(frozen=True, slots=True)
class ExecutionProfile:
    format: str
    profile_id: str
    mission_id: str
    goal_revision: int
    accepted_depth: Literal["direct", "standard", "orchestrated"]
    services: CognitiveServices
    accepted_capabilities: tuple[str, ...]
    upgrade_triggers: tuple[ProfileTrigger, ...]
    downgrade_conditions: tuple[ProfileTrigger, ...]
    source: Literal["task_profiler", "safe_default", "runtime_revision"]
    confidence: float | None
    policy_reasons: tuple[str, ...]
```

```python
@dataclass(frozen=True, slots=True)
class CognitiveServices:
    planner: bool
    obligation_verifier: bool
    mission_verifier: bool
    visual_inspector: bool
    deep_reasoning: bool
```

Execution Policy 不理解 GUI，也不生成 obligation。它只能：

- 接受、收紧、延后或拒绝建议；
- 保证安全、授权、provider 可用性与用户验收要求；
- 在 Profiler unavailable、malformed 或低置信时选择安全 `standard` 默认；
- 根据 accepted runtime facts 产生有审计理由的 profile revision。

`legacy/vnext` 不属于 ExecutionProfile，由产品或实验配置显式选择。

### P2a implementation status (2026-07-21)

P2a is implemented as a pure, versioned sidecar in
`python/winpilot_home/winpilot/vnext/profiling/`. The top-level
`vnext/execution_profile.py` is imports-only compatibility and is not a second
contract owner. The canonical
wire formats are `task-profile-proposal/0` and `execution-profile/0`.
`TaskDimensions`, `CognitiveServices`, `TaskProfileProposal`,
`ExecutionDepth`, `ExecutionProfile`, and `ExecutionPolicy` reject unknown
fields, wrong versions, non-JSON values, non-canonical capabilities, and
runtime identity or GUI-route fields.  A proposal can express only semantic
dimensions, broad capability hypotheses, cognitive-service requests, semantic
triggers, confidence, and rationale.  It cannot contain runtime version,
application workflow, tools, buttons, selectors, coordinates, next actions,
obligation/mission patches, or runtime identity.

`ExecutionPolicy` is deterministic and injectable.  It validates the
capability catalog, applies a safe `standard` default for missing, malformed,
unsupported, or low-confidence proposals, and records every depth/service
restriction and unavailable provider/capability in `policy_reasons`.
`ProfileShadowEvaluator` accepts only injected proposal/runtime facts and
reports over/under-profile plus an identity-free structural fingerprint for
rewrite and application-label stability checks. P2b adds a separate, tool-free
`OpenAITaskProfilerModel` and an at-most-once legacy shadow hook. Its Runtime
envelope binds request identity outside the model input, SDK retries are zero,
and trace output uses a private recorder sidecar. Broker, Actor, Planner,
Inspector, GUI tool surfaces, Controller and done semantics remain disconnected.

## 4. MissionDefinition

MissionDefinition 是任务语义的 immutable 基础：

```python
@dataclass(frozen=True, slots=True)
class MissionDefinition:
    format: str
    mission_id: str
    run_id: str
    original_goal: str
    goal_revision: int
    obligations: tuple[ObligationSpec, ...]
    stable_topological_order: tuple[str, ...]
    experience_policy: str
```

P1 的 Python 实现将 `ObligationSpec` 暴露为 `ObligationDefinition` 的同一实现别名，自动校验
并序列化 `stable_topological_order`；`experience_policy` 只作为来源合同的无损上下文字段保存，
不会在本阶段选择 runtime 或调用 Profiler。

无法可靠拆分时，Definition 只包含一个 `desired_state=original_goal` 的 obligation。

## 5. ObligationSpec

Canonical rule: `ObligationSpec` is the only new obligation-definition schema. `ObligationDefinition` may remain only as a deprecated Python read alias for old shadow callers; it is never emitted by `to_dict()` or used as a second state fact. A definition with no provably safe decomposition becomes one required full-goal obligation.

```python
@dataclass(frozen=True, slots=True)
class ObligationSpec:
    id: str
    desired_state: str
    acceptance_criteria: tuple[str, ...]
    depends_on: tuple[str, ...]
    forbidden_effects: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    input_artifact_refs: tuple[str, ...]
    output_binding_names: tuple[str, ...]
    evidence_policy: EvidencePolicy
    required: bool
    source_span: tuple[int, int] | None
```

ObligationSpec **不含 `status`**。生命周期只存在于 MissionProjection 的唯一 state map。

### 5.1 Broad capability

允许：

```text
visual.interact
visual.draw
visual.read_text
pointer.dynamic
artifact.capture
artifact.read
application.resolve
communication.bind_recipient
communication.send_text
communication.send_artifact
communication.verify_delivery
mcp.office
external.wait
```

禁止：

```text
click_search_box
qq.send_image
paint.draw_flower
benchmark.game_drag_route
特定按钮、坐标、窗口标题或工具顺序
```

无法可靠确定时使用 `visual.interact`。

canonical 名称的唯一代码 owner 是 `vnext/capability_names.py`；
`vnext/capabilities/names.py` 只做兼容 re-export。当前真实 Compiler 的 ExecuteDesktopTask
required capabilities 包含 `visual.interact` 和 `visual.read_text`，live Runtime state
也声明同一集合：`visual.read_text` 表示 Visual Actor 直接读取当前像素，不等于启用结构化文字
provider；Catalog 仍只用 `visual.interact` 授权 `click`/`double_click`/`scroll`。

### 5.2 EvidencePolicy

```python
@dataclass(frozen=True, slots=True)
class EvidencePolicy:
    required_evidence_kinds: tuple[str, ...]
    preferred_evidence_kinds: tuple[str, ...]
    allow_independent_visual_verifier: bool
    max_verification_attempts: int
    uncertain_policy: Literal["collect_more", "ask_user", "blocked", "fail"]
```

Policy 只表达用户要求所需的最低证据，不允许 Verifier自行增加审美、清理或业务标准。

## 6. MissionEvent 与 MissionProjection

### 6.1 MissionEvent

The canonical event key is `kind`; `type` is not accepted, either alone or
alongside `kind`. Event `format` must equal `mission-event/1`, payload values
must be strict finite JSONValue values, and every accepted event is bound to
the current mission/run revision. `/1` rejects both the former `type` spelling
and the former target payload shape instead of deferring a nominally old event
to Reducer-time guessing.

```python
@dataclass(frozen=True, slots=True)
class MissionEvent:
    event_id: str
    mission_id: str
    run_id: str
    base_revision: int
    kind: str
    payload: dict[str, JSONValue]
    evidence_refs: tuple[str, ...]
    created_at: float
```

事件级 `evidence_refs` 合并到 Reducer 的 run-scoped EvidenceScope。事件时间戳是审计元数据，
不参与自动 event id 的确定性哈希。历史 `type` 记录若需要迁移，必须先在显式离线迁移边界转换为
canonical `/1`，不能让 Reducer 长期维持双 schema。

只有 Reducer可以接受事件。每个事件必须检查 base revision、主体、evidence scope 与不变量。

建议事件：

```text
mission_initialized
execution_profile_accepted
execution_profile_revised
obligation_activated
target_bound
target_invalidated
artifact_bound
transition_recorded
obligation_claimed
verification_recorded
capability_requested
capability_granted
capability_denied
planner_requested
planner_patch_proposed
planner_patch_accepted
planner_patch_rejected
inspection_requested
inspection_recorded
approval_requested
approval_decided
external_change_armed
external_change_recorded
user_goal_revised
mission_verified
mission_blocked
mission_failed
mission_canceled
```

### 6.2 ObligationLifecycle

P1 currently accepts only the pure state events implemented by the reducer. Planner, approval, external-change, capability, inspection, execution-profile and user-goal-revision events are deliberately fail-closed until their complete contracts exist; they must not silently mutate a projection.

```python
@dataclass(frozen=True, slots=True)
class ObligationLifecycle:
    status: Literal[
        "pending", "active", "claimed", "verified",
        "blocked", "superseded", "canceled"
    ]
    activation_attempt_count: int
    verification_attempt_count: int
    claim_fingerprint: str | None
    verified_claim_fingerprint: str | None
    accepted_verification_id: str | None
    failure_budget_key: str | None
    no_progress_streak: int
```

### 6.3 MissionProjection

```python
@dataclass(frozen=True, slots=True)
class MissionProjection:
    format: str
    mission_id: str
    run_id: str
    revision: int
    definition: MissionDefinition
    execution_profile: ExecutionProfile
    obligation_states: dict[str, ObligationLifecycle]
    active_obligation_id: str | None
    artifact_bindings: dict[str, ArtifactRef]
    target_binding: TargetBinding | None
    verified_facts: tuple[VerifiedFact, ...]
    pending_approval: ApprovalRequest | None
    failure_budgets: dict[str, FailureBudget]
    status: MissionStatus
```

MissionProjection 是 `MissionDefinition + accepted event log + EvidenceRegistry` 的可重放视图。它可以缓存，但不能成为脱离事件与证据的第二份真相。

`verified`、`failed`、`canceled` 是 terminal mission statuses；Reducer 在这些状态下拒绝任何后续写入。`blocked` 和 `waiting_external` 等显式等待状态不能被一次普通 recompute 擅自改写。Verification 只有在全部 acceptance criteria 被机械覆盖、`verification_id` 非空且 evidence refs 通过当前 mission/run scope 校验后，才会把 obligation 置为 `verified`。

以下只能是派生 property，不得另行序列化为权威字段：

```text
verified_obligation_ids
pending_obligation_ids
blocked_obligation_ids
obligation_coverage_ratio
```

`MissionStatus`：

```text
running
waiting_approval
waiting_external
awaiting_mission_verification
blocked
verified
failed
canceled
```

MissionProjection 不保存：

- 模型推理；
- GUI 动作列表；
- selector、OCR anchor 或坐标；
- mouse-down 设备细节；
- 应用专属 fallback；
- 未验证 Actor 自述事实。

本轮 P1 不创建 `ExecutionProfile` 或 `MissionGuardrails` 对象；合同中与 profile、Broker、Actor
和 Verifier 相关的字段属于后续阶段，P1 projection/card 只投影已接受的 Mission 与证据状态。

PointerLease、FrameState、DesktopActionLock 属于各自执行 owner；Broker 只读取快照。

## 7. VerifiedFact 与 EvidenceRef

```python
@dataclass(frozen=True, slots=True)
class VerifiedFact:
    id: str
    statement: str
    evidence_refs: tuple[str, ...]
    scope: str
    valid_for_obligation_ids: tuple[str, ...]
    expires_on_epoch_change: bool
```

```python
@dataclass(frozen=True, slots=True)
class EvidenceRef:
    id: str
    kind: str
    producer: str
    mission_id: str
    obligation_id: str | None
    frame_id: int | None
    action_id: str | None
    artifact_id: str | None
    payload_hash: str
    trust: str
    created_at: float
```

`trust`：

```text
deterministic
application_oracle
provider_receipt
trusted_external_harness
independent_visual_model
actor_claim
telemetry_only
```

`actor_claim` 和 `telemetry_only` 不能单独满足 obligation。

所有 Mission 合同里的 frame identity 使用同一严格边界：必填 frame id 必须是正整数；可空的
`EvidenceRef.frame_id`、`TransitionSummary.frame_id`、`target_invalidated.observed_frame_id` 与
`MissionProjection.target_rebind_after_frame_id` 只能是正整数或 `None`。布尔值、零、负数、浮点数和
数字字符串均拒绝，不能用 `0` 伪装“尚未观测”。

## 8. ArtifactRef 与 TargetBinding

```python
@dataclass(frozen=True, slots=True)
class ArtifactRef:
    artifact_id: str
    media_type: str
    sha256: str
    producer_obligation_id: str
    evidence_ref: str
```

```python
@dataclass(frozen=True, slots=True)
class TargetBinding:
    binding_id: str
    hwnd: int
    root_hwnd: int
    process_id: int
    process_identity: str
    title_snapshot: str
    epoch: int
    canonical_identity_sha256: str
    acquisition_action_id: str
    established_by_evidence_ref: str
```

窗口标题不能独立决定身份。`canonical_identity_sha256` 机械覆盖
`hwnd/root_hwnd/process_id/process_identity/epoch`；其中 `process_identity` 必须由 PID、
进程创建时间与绝对可执行路径机械派生。
`binding_id` 进一步绑定 mission、run、该 physical identity 和 Runtime 生成的
`acquisition_action_id`。标题不参与 physical identity，只进入 establishment attestation payload。

`target_bound` 只在 Mission 当前未绑定时接受，payload 只能包含一个 canonical
`target_binding`。其 establishment evidence 必须是当前 run、当前 obligation 下的
`authoritative_target_binding` + `deterministic` evidence；action id 和 payload hash 必须与
binding 机械一致，且不能携带 artifact identity。事件只能引用这一笔 establishment evidence，
不能借 target bind 顺带合并其他证据。

已有绑定不能直接覆盖。失效事件必须精确携带
`expected_binding_id`、`expected_canonical_identity_sha256`、`observed_frame_id` 与非空 `reason`；当前无绑定、
陈旧身份、空原因、额外字段或附带正向 evidence 均拒绝。只有 accepted `target_invalidated` 清除绑定后，Broker
才退回未绑定工具面。`observed_frame_id` 为 Runtime 实际发现失效时的正整数权威帧 id；若该值存在，
`MissionProjection.target_rebind_after_frame_id` 由事件重放派生，后续 `target_bound` 的 establishment
evidence 必须绑定严格更新的帧。`None` 只表示失效发生时没有额外可引用的权威帧；Reducer 仍继承
已接受 establishment evidence 的 frame id 作为下界。payload 不能用更小的 frame id 弱化该下界，
也不得用 0 伪装未知。
projection 不允许 binding 与 rebind barrier 同时存在；barrier 是 Runtime authority gate，不投影进
MissionCard，也不向 Actor 暴露。

## 9. MissionCard

MissionCard 是 Actor 每回合的有界结构化状态：

```python
@dataclass(frozen=True, slots=True)
class MissionCard:
    format: str
    mission_id: str
    mission_revision: int
    original_goal: str
    execution_profile_summary: ExecutionProfileCard
    active_obligation: ActiveObligationCard
    verified_facts: tuple[VerifiedFactCard, ...]
    artifact_bindings: tuple[ArtifactCard, ...]
    target_binding: TargetBindingCard | None
    mission_guardrails: MissionGuardrails
    last_transition: TransitionSummary | None
    failure_summary: FailureSummary | None
    no_progress_streak: int
```

构建规则：

- 完整保留 `original_goal`；
- 只包含 active obligation；
- 只投影相关 verified facts/artifacts；
- 提供少量 preserve/future-dependency guardrails，避免局部最优破坏后续状态；
- 最多一个最新 transition 和有界 failure/verifier feedback；
- `no_progress_streak` 只来自 active obligation 的可重放状态，不携带上一义务的反馈；
- 不包含其他 obligation 的 GUI 细节；
- 不包含旧帧；
- 不包含其他角色隐藏推理。

### 9.1 P4a Actor 可见投影

P4a 不把带 Runtime 身份的 `MissionCard` 或 `BrokerProjection` 原样交给 Actor。Runtime 机械投影：

- `ActorMissionView`：完整 `original_goal`、当前义务的语义字段、相关事实/工件摘要、target 标题、当前义务自己的 transition/failure 摘要，以及中性的 `unchanged_frame_streak`；
- `ActorFrameView`：只说明图片是否可用、是否权威/新鲜，以及 `submitted_image_pixels` 坐标系；
- `ActorToolView`：只包含当前 BrokerProjection 允许的 Visual Actor 工具名、严格 JSON schema 和机械布尔值 `requires_local_intent`；
- `ActorTurnInput`：组合上述三种 view、可选的一条 `previous_action`，以及未绑定目标时可选的连续序号 target choices；图片字节和 submitted size 通过注入的 `ActorPort` 单独传递。

这些 view 结构上不含 mission/run/revision/obligation/frame/evidence/grant/projection identity。`actor-mission-view/2` 不再投影内部 ActionEffect 恒定的 `semantic_completion=unknown`：该值只表示像素比较器没有语义完成权限，不能伪装成 Runtime 对当前义务“尚未完成”的判断。内部 `action-effect/0` 仍保留该哨兵。`actor-tool-view/1` 与 `actor-turn-input/5` 严格拒绝旧版或混合版本。`/5` 的每个 target choice 仅含连续的 1-based ordinal、语义 label、可见 window title 和 visible/recoverable/installed 状态，不含 candidate ref、activation、provenance、query、HWND、PID、路径、AppID、process identity 或 lease/discovery identity。choices 与 `activate_target` 工具必须同时出现，已绑定 target 不得投影 choices。一次空 discovery 只能在下一合格回合增加一个 `actor-target-discovery-feedback/0`：内容严格为原 query 和 `outcome=no_candidates`，不含 fingerprint、count、候选身份、来源 turn/frame、错误分类或路线建议；该反馈不能与 choices/activation 同时出现，投影后即丢弃。语义文本完整保留，不通过关键词替换或应用规则“清洗”。当前增量 live coordinator 已通过隔离的模型请求实现该 ActorPort；Runtime 仍是机械身份的唯一绑定者，provider 不能扩大 BrokerProjection 或接触 Controller。

Target discovery snapshot 与 candidate 是 Runtime-private typed data，不再提供 Actor serialization 或 candidate-ref lookup。`TargetSelectionLease` 是线程安全、进程内、不可序列化的一次性 sidecar：它冻结 ordered candidates，并精确绑定 mission/run/revision/obligation/turn/frame authority/Broker projection；第一次 consume 尝试无论成功、越界或 stale 都耗尽，restart 即丢失。它不是 Mission、Evidence、BUS、trace、Verifier 或 replay 的一部分，也不能形成第二份 target ledger。

live Catalog 当前只加入了 `discover_target`：它要求 unbound、fresh authoritative `full_desktop`
frame、released pointer、healthy Runtime 与 `visual.interact`，mutation kind 为 `none`。Runtime 只在
该状态下把严格 query 交给注入的 `TargetDiscoveryPort`，返回后重新核对 Mission、frame 和 Broker
authority；接受结果只写入一回合的私有 pending snapshot，返回 `OBSERVED`，不申请 mutation permit，
不调用 Controller，不写 Mission event/evidence。coordinator 已注入按请求新建 Windows facts snapshot
的 `FreshWindowsTargetDiscovery`，但产品 coordinator 仍要求已有 foreground handoff，因此 Electron
入口尚不能抵达这条 targetless dispatch。`activate_target` 与 `open_app` 仍未加入 live Catalog；
非空候选在下一回合若没有 activation tool 会 fail closed，不会把 candidate 交给通用 Controller 或
隐式 legacy fallback。

`FreshWindowsTargetDiscovery` 的 discovery `captured_at` 绑定产生候选的同一 OS facts snapshot，
不会因稍后的 installed-app lookup 看起来更“新”。取消只在 facts source 之间、installed resolver
迭代项之间和返回边界协作检查；正在阻塞的默认 installed resolver 调用不能被抢占，可能在其有界
lookup 期间延迟取消。它仍是只读端口，不拥有 focus、launch、Mission mutation 或候选选择权限。

无 target 的 Runtime 现在可以通过同一个 `FrameObserver -> FrameAuthority` 捕获权威全桌面帧。`runtime-snapshot/2` 要求 targetless frame 的 `scope` 必须为 `full_desktop`；真实 desktop HWND、epoch 0 与 virtual desktop region 在 capture 前、commit 前和 commit 后都必须保持一致。窗口帧和桌面帧不建立第二个 frame owner。`broker-runtime-state/1` 保留同一 frame scope，并用成对的 `candidate_fingerprint` / `candidate_count` 区分“尚未发现”“发现为空”和“存在候选”；candidate pair 存在时（包括 count 为 0）必须是 unbound full-desktop 状态。`broker-projection/1` 的 identity 同时绑定完整 frame state 与候选集合摘要，但这些摘要不进入 Actor view。live Catalog/Runtime 已能在注入式 targetless turn 执行只读 discovery；产品 coordinator 仍要求 foreground handoff，因此这只是可测试的 Runtime seam，不代表客户端已经能从未绑定状态发现或激活应用。

### 9.2 上一笔 Actor 动作的有界上下文

`actor-previous-action/0` 不是动作历史或新 ledger，而是只供下一次合格 Actor request
使用的、run-local 的一次性认知侧车。Runtime 只在 GUI mutation 已写入 canonical
`transition_recorded` 后保留有界 `local_intent` 和经投影参数；action、执行结果和像素效果始终
从当前 Mission 的 canonical `last_transition` / `ActionEffect` 重建，不在侧车中复制第二份事实。

投影必须 fail closed：

- pending revision、active obligation、target binding 和 action tool 必须与当前 Mission/Broker 完全一致；该 tool 仍必须是 Broker 允许的 Visual Actor GUI mutation；
- canonical transition 必须带真实 `ActionEffect`，且 `target_binding_valid=true`；provider 还要求 `input_completed=true`、post-action frame 已对账，并且 previous-action 与平面 transition 的 action/effect 字段完全相等；
- 参数采用 default-deny 投影：只接受 closed top-level object 中显式标注 `historyVisible=true` 的 finite JSON scalar，必须覆盖所有 required 和实际参数；任何歧义、未标注或越界都省略整笔动作，不做部分泄漏；
- 当前只有 `click`、`double_click` 和 `scroll` 的完整参数满足该投影；`type_text` 和 `hotkey` 未标注为 history-visible，因此其整笔 native previous-action 均省略。

`actor-tool-view/1.requires_local_intent` 由 Runtime/Catalog 按 mutation kind 机械投影。对 GUI mutation，
provider 把有界非空 `local_intent` 加入函数 schema 并强制模型提供，严格验证后在交给
Controller 前移除。它只说明 Actor 上一笔局部意图，不是计划、完成声明或恢复命令。

当 native previous-action 可用时，provider 在最新 user image 前放入一组原生 assistant
tool-call 和 tool result，同时从 user payload 省略重复的平面 `last_transition`。tool result 只报告
`temporal_full_frame_change`、`frame_comparable`、`target_binding_valid` 等已对账事实；
`semantic_completion` 和 `causal_attribution` 恒为 `unknown`，像素时序变化不得冒充语义进展或因果。

侧车在真正进入下一次 Actor request 前消耗；provider 错误也不恢复它。请求前的暂时
observation/state failure 不伪装成已消费；terminal/cancel 会清理，进程重启则必然丢失。它不写入
MissionDefinition、MissionEvent、MissionProjection、EvidenceRegistry、BUS/WebSocket、replay、claim、
Verifier 或 completion truth，也不能触发动作重放。

## 10. 模型输出：ActorProposal

模型每回合只输出一个 union variant，且**不包含 Runtime 身份**：

```python
ActorProposal = (
    DesktopActionProposal
    | ObserveProposal
    | CapabilityRequest
    | InspectionRequest
    | ClaimObligationDone
    | BlockedReport
    | ReplanRequest
)
```

### 10.1 DesktopActionProposal

```python
@dataclass(frozen=True, slots=True)
class DesktopActionProposal:
    action: str
    args: dict[str, JSONValue]
    local_intent: str
```

`local_intent` 只描述这一笔动作预期的局部状态变化，不承载后续计划。

### 10.2 ObserveProposal

```python
@dataclass(frozen=True, slots=True)
class ObserveProposal:
    observation_kind: Literal["full", "image_region"]
    image_region: ImageRegion | None
    question: str
```

Runtime 不从 `question` 解析任务类型。`image_region` 只引用当前提交图片，由 envelope 绑定真实 frame。

### 10.3 CapabilityRequest

```python
@dataclass(frozen=True, slots=True)
class CapabilityRequest:
    capability: str
    reason: str
```

它是提案，不是授权。Runtime/Broker 校验 broad capability、active obligation、安全、provider、授权和真实状态后，才产生有期限的 CapabilityGrant。

### 10.4 InspectionRequest

```python
@dataclass(frozen=True, slots=True)
class InspectionRequest:
    region: ImageRegion
    question: str
```

只有 ExecutionProfile允许 Inspector、当前 frame有效且 policy trigger满足时才调用。模型不提交 frame id。

### 10.5 ClaimObligationDone

```python
@dataclass(frozen=True, slots=True)
class ClaimObligationDone:
    concise_claim: str
```

模型不提交 obligation id、revision 或 evidence refs。Runtime 绑定 active obligation 和当前可引用的 run-scoped frame evidence；`obligation_claimed` 本身只让义务进入 `CLAIMED`。只有 ExecutionProfile 允许且注入了只读 ObligationVerifier 时，Runtime 才紧接着冻结 claim fingerprint/attempt/evidence 并接受 verification event；否则返回 `verification_required`。Actor claim 永远不能直接进入 `VERIFIED` 或结束 mission。

`claim_obligation_done` 的 Catalog schema 根部拥有唯一的通用工具描述：它只请求独立 Verifier 按当前权威帧、accepted evidence 与 active obligation 的验收条件检查候选完成，不授予完成权。provider adapter 机械把该 schema annotation 投影为 function description，并从 parameters 的根部移除同一 annotation，避免第二份描述或重复上下文。描述必须是无首尾空白的非空文本；缺失描述的其他工具仍合法，显式 `null`、空白或非字符串则在 provider 调用前 fail closed。描述不得包含应用流程、按钮、坐标、测试身份或 Runtime identity。

### 10.6 BlockedReport

```python
@dataclass(frozen=True, slots=True)
class BlockedReport:
    blocker_kind: str
    observed_facts: tuple[str, ...]
```

### 10.7 ReplanRequest

```python
@dataclass(frozen=True, slots=True)
class ReplanRequest:
    reason_kind: str
    observed_conflicts: tuple[str, ...]
    failed_strategy_summaries: tuple[str, ...]
```

Runtime从 trace/evidence绑定可验证的 conflicting facts 和 strategy refs。模型不能通过自然语言直接改 obligation、增加工具或跳过 approval。

## 11. Runtime-bound ActorDecisionEnvelope

```python
@dataclass(frozen=True, slots=True)
class ActorDecisionEnvelope:
    format: str
    decision_id: str
    mission_id: str
    run_id: str
    mission_revision: int
    obligation_id: str
    frame_id: int
    frame_epoch: int
    frame_hwnd: int
    frame_region: tuple[int, int, int, int]
    submitted_image_size: tuple[int, int]
    frame_image_sha256: str
    target_binding_id: str | None
    broker_projection_id: str
    capability_grant_ids: tuple[str, ...]
    proposal: ActorProposal
    allowed_evidence_scope: tuple[str, ...]
```

Runtime构造 envelope。必须拒绝：

- active obligation/revision 已变化；
- frame/epoch/target binding 已失效；
- action 不在 BrokerProjection；
- pointer lease 与 proposal 不兼容；
- 参数混用坐标系统；
- proposal 试图覆盖 envelope 身份；
- capability/inspection/replan trigger 不满足 policy。

### 11.1 P4 MissionRuntime

`MissionRuntime.run_one_turn()` 是纯协调边界。它只持有注入的 `ActorPort`、`RuntimeStatePort`、`ControllerPort`、只读验证端口和可选 `PointerCleanupPort`；具体 live coordinator 负责组装这些端口，但 Runtime 不导入 legacy AgentRunner/ToolSession，也不成为 RawDevice owner。非 `visual_actor` route 不调用 Actor，同一 run 的任何失败都不会偷偷 fallback 到 legacy。

单回合 admission 顺序固定为：

1. 从 Reducer 的当前 Projection/Card 和 `RuntimeStatePort.observe()` 取得权威 snapshot；
2. 确定性投影 Broker，并只把 Actor view、图片和小工具面交给注入 ActorPort；
3. 严格解析一个 `actor-proposal/1`；
4. Actor 返回后重新读取 revision、active obligation 和完整 authority snapshot，并重新投影 Broker；
5. 任一 frame/epoch/HWND/region/image hash/target/pointer/grant/projection 或义务变化都返回 `stale_discarded`，不进入 Controller；
6. 只有仍在最新 BrokerProjection 中的一笔 Visual Actor 动作，才由 Runtime 绑定 envelope 后交给注入 ControllerPort。

P4a Controller 回执使用三态 `ControllerOutcome`：

```text
completed
rejected
unknown
```

`completed` 与带 post observation 的 `rejected` 只有在 action identity、当前 post frame 和 evidence 能对账时才可产生 `transition_recorded`。若 admission 或 RawDevice 已明确证明输入为 `NOT_STARTED/NOT_APPLIED`，即使 post capture/CAS 随后失败，Controller 仍可返回不带 post observation 的 `rejected/input_completed=false`；Runtime 将该旧 decision 记为 `stale_discarded`，不写 transition，也不重放动作。任何可能已执行的结果、Controller 执行后异常、身份不一致或 post-frame/evidence 无法对账仍为 `unknown`，Runtime 接受 `obligation_blocked`，且不自动重放 mutation。取消后的迟到 Actor 结果也不会进入 Controller；若 Runtime 已持有物理 pointer lease，只允许注入的确定性 release cleanup。

`ObserveProposal` 只刷新观察；`BlockedReport` 只产生当前义务的 blocked event，其自然语言事实不会升级为 `VerifiedFact`。`CapabilityRequest`、`InspectionRequest` 和 `ReplanRequest` 在 P4a 均返回 `unavailable`；尤其 P6 Planner/Inspector 未启用时必须 fail closed。

当前产品接线只对显式 `vnext` run 组装增量 Actor、Broker、Frame/Controller bridge 与验证端口，工具面为 `click/double_click/scroll/type_text/hotkey/claim/blocked`。三个 click seed、最终 scroll 合同的部分 canary 以及 keyboard 的 `keyboard-entry`/`keyboard-select-all` 已有同一 Electron 入口与独立 oracle 证据；同时 ActionEffect 复测仍保留 up 失败。这不等于全局 Broker enforce 或完整 P7 已通过；P3 的 necessary-tool recall 门槛仍未达到。任何后续原语仍必须复用现有唯一 frame/admission/execution owner，不能把注入端口实现成第二套执行链。

### 11.2 P4a ControllerTransition

注入式 ControllerPort 返回 `controller-transition/1`，至少绑定 `action_id`、`action`、三态 outcome、`input_completed`、可选 post frame 和 evidence refs。`completed` 必须明确 `input_completed=true` 且带当前 post frame/evidence；`rejected` 必须明确 `input_completed=false`，其 `post_frame_id` 与 evidence 必须同时存在或同时缺失，只有机械 owner 明确证明未执行时才允许后一种形式；`unknown` 必须保持 `input_completed=null`，不能伪装成“确定未执行”。该边界是 Controller 事实回执，不拥有语义恢复或任务完成权限。

### 11.2.1 deterministic ActionEffect

动作输入与 post frame 已闭合后，Runtime 可以调用独立、只读的
`DeterministicActionEffectVerifier`。它不属于 Controller，不读取任务文本、应用名、OCR/UIA/MCP，
也不选择恢复策略。`action-effect/0` 的 canonical summary 至少绑定：

```text
action_id
input_completed
before_frame_id / after_frame_id
before_frame_evidence_ref / after_frame_evidence_ref
before_frame_payload_hash / after_frame_payload_hash
target_binding_valid
frame_comparable
local_visual_change = changed | unchanged | unknown
change_ratio
reason
semantic_completion = unknown
evidence_ref
payload_hash
```

只有 HWND、epoch、capture region、scope、submitted size 一致，两个权威帧均可解码且尺寸正确时才可比较。
解码后像素完全一致才是 `unchanged`；任一像素不同只产生中性的 `changed`；身份、目标、解码或尺寸
不可靠时为 `unknown`。`changed` 不证明该动作造成变化，不证明任务进展，更不能证明 obligation 完成；
动画或视频可能持续产生 `changed` 并掩盖 no-op。

post frame 必须代表一个合理的动作后观测点。当前唯一 `AtomicPostActionBridge` 在 raw receipt 明确
`input_completed=true` 后执行一次通用、有界的 120 ms settle，再捕获唯一 post frame；明确未执行时
不等待，可能已执行但结果未知时仍按 unknown-no-replay 合同阻塞。该时间边界不读取应用、任务、动作
方向或画面语义，也不允许二次动作或由 ActionEffectVerifier 自行补拍。

ActionEffect 与 transition 写入同一个 accepted `transition_recorded` event。Reducer 必须从当前
`EvidenceRegistry` 复核前后 frame 和 attestation 的真实 kind、trust、obligation、action、frame 与内容
hash，不能信任 event payload 自报。只有 `input_completed=true + unchanged` 才递增当前 obligation 的
内部 `no_progress_streak`；`changed` 将该义务的 streak 清零；`unknown`、未执行或缺少 comparison 保留既有值，
不伪造进展。Actor 只看到无身份的分类、可比较性、target binding 事实和名为
`unchanged_frame_streak` 的中性计数；它仍自行选择下一步。

### 11.3 P4b 内部机械输入合同（partial）

当前 admitted physical scroll、atomic click/double-click 以及 P4b.1 的 keyboard mechanical owner 使用 typed `RawBackendResult` 和 `RawActionReceipt`，机械完成三态固定映射为：`APPLIED -> true`、`NOT_APPLIED/NOT_STARTED -> false`、`OUTCOME_UNKNOWN -> null`。backend 必须显式报告 `input_completed`；泛型 `ok:true` 不能升级为已执行。设备调用后的异常或非法 backend result 都进入 `OUTCOME_UNKNOWN`，上层只能重新观察、对账或阻塞，不能自动重放。legacy PostMessage 不确定语义仍保持同一 no-replay 边界。

RawDevice 接收 Controller 已解析的 typed scroll/click 参数、bounded text 或 canonical `HotkeyChord`，不接收 `action + args`，因此不是第二个 action parser。它不读取 session、截图、OCR/UIA、clipboard、任务、应用名或恢复状态。Keyboard backend 只走 SendInput；每个字符和 chord 都由调用方提供的只读 final guard 限定，部分输入、down/release 异常或 guard 在输入后变化均为 unknown，不能重放。Hotkey 一旦尝试 down 就必须逆序尝试 release。Receipt facts 只允许 codepoint/key 数量和 cleanup 状态，不得保存原始文本或 chord；这些 facts 只是可序列化诊断，不是 run-scoped evidence，`APPLIED` 也不证明像素变化或语义完成。

具体 live coordinator 的 `raw_device` 是必需注入项。产品 composition root 构造并注入 admitted physical click/scroll/type-text/hotkey backend；coordinator 在 Actor 创建前机械核对 Catalog GUI tools、Controller supported tools、Controller required raw primitives 与 RawDevice 实际 inventory，任何缺失或漂移都 fail closed。legacy `type_text`/`press_keys` 仍在原有单一 queue 入口内委托同一个 unqueued keyboard mechanical owner。coordinator、MissionRuntime 和 Actor 都不得自行创建 backend 或在缺失时 fallback。这样 composition 与执行仍只有一个物理 owner。

Actor 可见 scroll args 严格为 `{image_x, image_y, direction: "up" | "down", notches: 1..12}`；`up` 表示显露 viewport 上方内容，`down` 表示显露下方内容。Actor 可见 keyboard args 严格为 `type_text:{text: 1..4096 codepoints}` 和 `hotkey:{modifiers: unique subset of ctrl/alt/shift/win, key: one canonical non-modifier}`；Runtime 先按 Catalog schema 验证，Controller 再以固定 modifier 顺序构造 `HotkeyChord`。keyboard 的 `CommitAdmissionRequest` 使用 `input_kind=keyboard` 与 `screen_point=null`，不调用坐标 mapper 或 hit-test；它仍共享 Runtime permit、权威 frame pin、target/foreground 复核、唯一 physical lease、post-action frame/evidence 和 unknown-no-replay。sidecar、evidence 与 raw facts 不保存文本或 chord 内容。`DesktopRuntimeSelection` 只在 run 开始时冻结；缺失 coordinator、任一端口或任一 raw primitive 都必须在 Actor/mutation 前拒绝，不能隐式 fallback 到 legacy。`drag`、`draw_strokes`、held pointer 与 ROI observe 仍未接入。

P4b.1 先把共享 commit admission 从 `commit-admission-request/0` 升级为严格的
`commit-admission-request/1`，显式区分 `input_kind=point|keyboard`。P4b.2 又因 target、foreground 与
hit-test 增加必需的强 `process_identity`，将其升级为 `commit-admission-request/2`，并将内部观察合同升级为
`commit-environment/1`；携带同一嵌套身份、且其 proposal 已升为 `/1` 的
`actor-envelope/2` 与 `runtime-snapshot/2` 也拒绝旧版本。`runtime-snapshot/2` 同时把
`RuntimeFrame.scope` 机械投影到 `broker-runtime-state/1`，并拒绝 targetless 非全桌面帧。
point 必须携带 `screen_point`，并继续
执行 frame/target containment 与 hit-test；keyboard 必须令 `screen_point=null`、只能走 physical route，
跳过无意义的 `WindowFromPoint`，但仍核对同一 frame/epoch/target、pointer released、mutation authority、
stop/cancel 与 foreground root/process。最终 keyboard guard 也只读复核 target/foreground，不选择或聚焦
窗口。P4b.1 后续切片已在不改 prompt 的前提下向显式 vNext run 暴露 `type_text`/`hotkey`；legacy 默认与其既有工具面、done 语义和执行入口保持不变。

同入口 keyboard 证据截至 2026-07-23：

- 早期基线 commit `f26b7de` 的 `keyboard-entry` 失败；suite `20260723-053146-suite`、run `arp-20260723-053225-58685a` 连续 24 次 click，为 0 次 `type_text`；
- 引入最终 previous-action 合同的 commit `515b286` 先在 suite `20260723-070921-suite`、run `arp-20260723-071001-9b5a7c` 取得首次通过，随后 suite `20260723-071108-suite` 三个 `keyboard-entry` 全部通过：run `arp-20260723-071141-0f75a0`、`arp-20260723-071245-fa6469`、`arp-20260723-071350-e03585`；
- `keyboard-select-all` 在 suite `20260723-071428-suite`、run `arp-20260723-071500-ae5d4c` 被旧 oracle 误报为失败；commit `a5c0c95` 把 oracle 改为 layout-independent 的逻辑 hotkey 验证后，suite `20260723-072628-suite`、run `arp-20260723-072705-9dbc00` 通过。

基线与修复后运行的 provider 请求合同和代码配置不同，因此这是失败归因与可达性证据，不是同配置的通用能力 A/B，也不得声称完成 P7。

### 11.4 P4c product coordinator 与 per-run selector

`desktop-runtime-coordinator-context/1` 只携带 Runtime 冻结的 run id、任务/contract 只读快照、现有 stop owner、回合入口的 blocking pause gate 与 commit 前的只读 `is_paused`；`desktop-runtime-coordinator-result/0` 只允许严格 terminal 结果。`/1` 不兼容 `/0`，因为新增的非阻塞 pause reading 用于丢弃模型调用期间产生的旧动作，恢复后必须从新权威帧重新决策。客户端只在顶层 ARP run request 发送 `runtime_version`，不把它写入 graph、task contract、Profiler 或模型 payload。`ArpExecution` 仅在 selection 为 `vnext` 且 Server 注入增量 live `DesktopRuntimeCoordinatorPort` 时调用一次 coordinator，并完全绕过 legacy node handlers；结果非法、异常或取消都不得 fallback。缺省/`legacy` run 仍不注入该 coordinator。

客户端 handoff 的外层继续是稳定的 `execution-context/1`；其中 `target_window` 已不兼容升级为
`desktop-target/2`，必须携带 `hwnd/root_hwnd/process_id/process_identity`。capture 在一次 handoff 内
前后两次读取 foreground HWND、root HWND、PID 与 canonical process identity，任一项变化就把该 target
标为不可用。coordinator 建立 Mission `TargetBinding` 前，再从当前 OS 状态读取强 target identity，并与
handoff 中四项逐项精确比较；不一致时在 Actor 和 Controller 调用前 fail closed。

这里统一的是 `winpilot/process_identity.py` 中不读取 OS 的 canonical **派生与校验 owner**。capture、
discovery、Runtime state 与 commit guard 仍在各自 authority seam 读取所需 OS 事实；不能把它们误写成
一个包揽所有 Windows 观察的 facts owner。当前 process identity 绑定 PID、创建时间和绝对 exe 路径，
能够识别进程实例更换，但不是 Windows 窗口 generation id：若同一进程内 HWND/root HWND 被复用且四项
身份在两次采样和 bind 前均完全相同，当前合同不能绝对区分前后两个窗口代际。该限制必须保留在 trace
和 rollout 判断中，不能据此宣称 unbound target acquisition 或 P7 已完成。

`initial-target-resolution/0` 是 renderer 与 coordinator 同一产品入口使用的 strict seam。它先按 canonical raw
`execution-context/1` 字段集合校验，再决定是否调用现有 normalization：required handoff 只有
fresh、强身份可重验的 `desktop-target/2` 才得到 `bound`；显式 `required=false/not_required`
且没有 target，或 required handoff 携带字段完整、fresh 的 canonical
`target_binding_missing` absence，才得到 `targetless`。缺字段、混合 schema、陈旧 absence、
伪零身份和其他 unusable target 均 fail closed，不能因 normalization 丢字段而降级成 targetless。
capture 生产端也必须满足同一反向不变量：只有前后稳定的 `GetForegroundWindow()==0`，或前后稳定且
精确等于 `GetShellWindow` / `GetDesktopWindow` 的系统 surface，才能投影完整全零的
`target_binding_missing`。非零普通窗口无法建立强身份、geometry/source 读取失败、control-surface
冲突和任意采样漂移都必须保留为其他 typed failure；filtered `list_windows` 清单及其空结果不是
binding 或 absence 证据。`captured_at` 表示最终采样完成时间。
返回值内部冻结 bound target；唯一 JSON-ready 出口是 `target_dict()`，它恢复 canonical `rect`
数组，调用者不得直接序列化内部 immutable mapping。显式选择 vNext 时，canonical absence 已可经
同一 Electron 提交入口进入 targetless Runtime；legacy 对同一 absence 仍 fail closed。真实 Electron
targetless existing-window/cold-launch E2E 尚未达到门槛，因此这里不宣称默认切换完成。

`is_paused` 必须是非阻塞 snapshot read，不得调用 pause wait 或持有 Controller/RawDevice lease。
coordinator 只在每个 turn 入口调用 blocking `wait_if_paused`；Actor 返回后的 authority/Broker
reprojection读取 `is_paused`，暂停则 proposal 失去 admission，resume 后开启新 turn。

live bridge 另有两个机械 contract：权威 frame freshness 使用显式正数
`frame_max_age_s`（默认 30 秒）在每次 projection 时重新计算；canonical turn budget 来自唯一
full-goal ExecuteDesktopTask 的整数 `max_steps`（1..400），session `requested_max_steps` 若存在必须
与之相等，effective budget 为它与 coordinator 上限（默认 24）的较小值。缺失、冲突或非法值均在
模型/桌面 mutation 前 fail closed。

### 11.5 queue-time runtime capability admission

`DesktopRuntimeAdmission` 是产品 queue-time capability admission 的唯一 owner。它接收 Runtime 冻结的
`DesktopRuntimeSelection`、canonical `TaskContract` 和（仅 vNext）composition root 构造的
`RuntimeCapabilitySnapshot`；接受后返回 `RuntimeAdmissionDecision`，拒绝则必须发生在 run prepare、
模型调用、Controller 构造性使用和桌面 mutation 之前。该合同不选择 GUI 路线、工具、坐标、目标或恢复策略。

`runtime-capability-snapshot/0` 是不可变 composition inventory，至少机械绑定：

- `runtime_version`；
- `ToolCatalog` fingerprint 和 capability owner registry；
- Controller 实际提供的 GUI mutation tool names；
- 可用只读/执行服务；
- 由上述 canonical material 派生的 snapshot fingerprint。

构造时必须 fail closed 校验 Catalog 所需 capability 均有 live owner、GUI Catalog tools 与 Controller tool
names 完全一致、GUI mutation 有唯一 desktop controller、像素读取 capability 有 Visual Actor 与权威帧输入
服务，并拒绝未知 service 或没有机械 backing 的 capability。vNext coordinator 持有的同一个 snapshot 实例
同时投影给 queue-time admission、`ExecutionPolicy`、live `RuntimeState` 和 `CapabilityBroker`；任何一层不得
复制另一组 availability 常量或按任务文本扩写 snapshot。

`runtime-admission-decision/0` 只保存 accepted decision：冻结 runtime、required/available canonical
capabilities、required/available services、审计理由和（vNext）snapshot fingerprint。vNext 只接受
`task-contract/1`、`typed-stage-5`、单个 lossless full-goal `ExecuteDesktopTask`，再从 legacy bridge 的
required obligation 机械投影 canonical capability；未知 legacy capability metadata 不进入 required set。
缺 contract、错误 runtime/snapshot identity、缺 capability/service 或 Catalog/Controller parity 失败都
fail closed。legacy 分支不接收 vNext snapshot；有 TaskContract 时仍按原顺序执行既有 static 与 runtime
preflight，无 TaskContract 的兼容 graph 仍保持原行为。private trace sidecar 可以记录 accepted decision，
但不得修改 BUS/WebSocket 产品事件，也不得成为第二份 admission 状态。

## 12. BrokerProjection 与 CapabilityGrant

```python
@dataclass(frozen=True, slots=True)
class BrokerProjection:
    format: Literal["broker-projection/1"]
    projection_id: str
    mission_revision: int
    obligation_id: str
    execution_route: Literal[
        "visual_actor", "deterministic", "provider", "blocked"
    ]
    executor_id: str | None
    target_binding_id: str | None
    pointer_lease_id: str | None
    candidate_fingerprint: str | None
    candidate_count: int | None
    tool_names: tuple[str, ...]
    active_grants: tuple[CapabilityGrant, ...]
    missing_capabilities: tuple[str, ...]
    projection_reasons: tuple[str, ...]
```

`broker-runtime-state/1` 的 `AuthoritativeFrameState` 必须显式携带
`scope: target_window | full_desktop | None`；authoritative/fresh frame 不能省略 scope。
`candidate_fingerprint` 与 `candidate_count` 必须同时出现或同时缺失，count 为严格整数 `0..16`：

- pair 缺失：当前状态没有已接受的 discovery 结果；
- count 为 0：discovery 已完成但没有候选；
- count 大于 0：存在有界候选，才满足 `target_candidates_available`；
- 任何 pair（包括 count 为 0）都只能伴随 unbound `full_desktop` frame；
- BrokerProjection 携带 pair 时 `target_binding_id` 必须为 `None`；
- projection id digest 绑定完整 frame state 与 pair；Actor view 不接收 fingerprint、count 或 frame scope identity。

这些字段是 Runtime-private authorization facts，不是第二份 target ledger。真实 snapshot/lease 与 count、
fingerprint、来源 turn/frame 的一致性由 Runtime 在投影和消费时机械核对。

```python
@dataclass(frozen=True, slots=True)
class CapabilityGrant:
    grant_id: str
    mission_id: str
    run_id: str
    capability: str
    obligation_id: str
    mission_revision: int
    issued_turn: int
    expires_after_turns: int
    policy_reason: str
```

不变量：

- 每个工具来自 ToolCatalog；
- 前置条件由本地状态证明；
- held pointer 时 mutation 只能 move/release；
- provider mutation route 与 GUI Actor route 互斥；
- deterministic/provider route 有精确输入、授权和可验证 receipt；
- MCP 未注册、未授权或输出不可验证时不暴露；
- 工具集变化由 state/profile/grant变化触发，不由应用名或 prompt 触发；
- 所有 projection/grant 写入 trace。

P3a 的可执行合同还要求：

- ToolCatalog、BrokerRuntimeState、BrokerProjection 和 CapabilityGrant 使用严格版本字段并拒绝未知字段；
- Tool schema 是深度不可变的 JSONValue，循环对象、非字符串 key、非有限数字和非 JSON 值 fail closed；
- 布尔状态不接受 `0/1` 或字符串替代，HWND 必须大于零；
- grant id 从其完整 scope 确定性派生，并同时绑定 mission、run、obligation、revision、issued turn 与 expiry；
- BrokerProjection 的工具只能来自同一 ToolCatalog；route 每回合只有一个；
- necessary-tool recall 不是由 legacy 实际调用反推。只有完整可靠 relevance label 才允许形成数值，否则为 `None`。

## 13. TransitionRecord

```python
@dataclass(frozen=True, slots=True)
class TransitionRecord:
    id: str
    mission_id: str
    obligation_id: str
    action_id: str
    requested_action: str
    actual_input: dict[str, JSONValue]
    pre_frame_id: int
    post_frame_id: int | None
    input_completed: bool
    target_binding_valid: bool
    local_visual_change: bool | None
    change_ratio: float | None
    visible_effect_summary: str
    failure: FailureFact | None
    evidence_refs: tuple[str, ...]
```

`visible_effect_summary` 只能写可审计局部事实，不能写“任务完成”。

## 14. VerifierAssessment 与 VerificationResult

当前 P5 实现是进程内的 evidence-first 闭合：P5a 提供严格的 `ObligationVerificationRequest`、无 Runtime 身份的 `VerifierAssessment`、Runtime-bound `ObligationVerificationResult` 和 deterministic-first dispatcher；live coordinator 还注入隔离、只读的视觉模型 Verifier。P5b 提供 `AcceptedObligationVerification`、`MissionCompletionVerificationRequest/Result` 和内容哈希绑定的 mission aggregate evidence。

P5b 构造时必须同时注入 `DeterministicMissionCompletionVerifier` 和 canonical `EvidenceRegistry`，且 `MissionReducer.evidence_source` 必须是该同一 registry。Runtime 先 append/read-back aggregate evidence，再提交 exact `mission_verified` event；Reducer 独立校验 accepted obligation verification ids、证据顺序、aggregate id/hash/kind/trust 和 canonical mission verification id。视觉模型只返回无身份 assessment；许可同时依赖 EvidencePolicy 与 Registry 中真实 evidence trust，Runtime 复核 claim/revision/current authoritative frame 后才写入本地 attested `independent_visual_model` evidence。payload 自报 trust 或 evidence identity 无效。当前仍无跨进程 WAL 或已证明的 exactly-once 恢复，ActionEffect 也尚无动画场景和真实任务的校准结果。

视觉/模型 Verifier 只输出 assessment，不回填 subject 或 evidence identity。当前 live provider
schema 让模型按 Runtime 编号后的条件返回分类，Runtime 再机械映射回 canonical acceptance
criteria；模型复制、改写或漏写 canonical 条件文本不能成为完成事实。provider 没有可靠数值
置信度来源时内部 assessment 记录 `confidence=None`，不能把字符串或默认零伪装为观测值：

```python
@dataclass(frozen=True, slots=True)
class VerifierAssessment:
    verified: bool
    satisfied_conditions: tuple[str, ...]
    missing_conditions: tuple[str, ...]
    contradictions: tuple[str, ...]
    confidence: float | None
    concise_reason: str
```

Runtime用实际输入包成：

```python
@dataclass(frozen=True, slots=True)
class VerificationResult:
    verification_id: str
    scope: Literal["action_effect", "obligation", "mission"]
    subject_id: str
    mission_revision: int
    verified: bool
    satisfied_conditions: tuple[str, ...]
    missing_conditions: tuple[str, ...]
    contradictions: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    verifier_kind: str
    confidence: float | None
```

Reducer接受条件：

- subject/revision来自Runtime envelope且仍当前；
- evidence refs真实存在、scope匹配；
- required criteria无缺失；
- forbidden effects无未处理 contradiction；
-视觉 verdict 不覆盖相反 deterministic/oracle evidence；
- mission verdict 不跳过未 verified required obligation；
- 同一证据 fingerprint 的重复 claim 不重复调用模型；
- 超过 EvidencePolicy `max_verification_attempts` 后按 `uncertain_policy`处理。

## 15. Planner proposal 与 Runtime envelope

Planner模型只输出 patch body：

```python
@dataclass(frozen=True, slots=True)
class MissionPatchProposalBody:
    obligations_to_add: tuple[ObligationDraft, ...]
    obligations_to_update: tuple[ObligationUpdate, ...]
    dependency_changes: tuple[DependencyChange, ...]
    reason: str
```

Runtime绑定：

```python
@dataclass(frozen=True, slots=True)
class MissionPatchProposal:
    proposal_id: str
    mission_id: str
    base_revision: int
    body: MissionPatchProposalBody
    evidence_refs: tuple[str, ...]
```

Reducer检查：

- base revision；
- 依赖无环；
- original goal required coverage不下降；
- verified obligation不被无证据撤销；
- patch不含GUI操作、控件、坐标或工具顺序；
- capability仍是broad capability；
-新 obligation不是应用脚本。

## 16. InspectionReport

Runtime绑定 frame/ROI 后调用只读 Inspector：

```python
@dataclass(frozen=True, slots=True)
class InspectionReport:
    inspection_id: str
    frame_id: int
    region: ImageRegion
    question: str
    visible_facts: tuple[str, ...]
    spatial_relations: tuple[str, ...]
    candidates: tuple[VisualCandidate, ...]
    uncertainty: tuple[str, ...]
    confidence: float
```

禁止字段：

```text
tool_call
click_point
drag_action
next_step
mission_patch
completion_claim
```

Actor可以参考报告，但最终动作仍由 ActorProposal产生并绑定同一有效 FrameState。

## 17. 角色权限矩阵

| 能力 | Profiler | Visual Actor | Verifier | Planner | Inspector | Execution Policy | Reducer | Broker | Controller |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 看任务原文 | 是 | 是 | criteria范围 | 是 | 问题范围 | proposal摘要 | 定义 | 否 | 否 |
| 看当前帧 | 默认否，可选只读初始帧 | 是 | 必要证据帧 | 否 | 指定ROI | 否 | 否 | 否 | 捕获但不理解 |
| 提出GUI mutation | 否 | **唯一允许** | 否 | 否 | 否 | 否 | 否 | 否 | 否，只执行 |
| 直接执行GUI | 否 | 否 | 否 | 否 | 否 | 否 | 否 | 否 | **唯一允许** |
| 提出执行深度 | 是 | 否 | 否 | 否 | 否 | 否 | 否 | 否 | 否 |
| 接受执行深度 | 否 | 否 | 否 | 否 | 否 | **唯一允许** | 否 | 否 | 否 |
| 修改MissionProjection | 否 | 否 | 否 | 否 | 否 | 否 | **唯一允许** | 否 | 否 |
| 标记obligation verified | 否 | 只claim | 提assessment | 否 | 否 | 否 | **唯一提交** | 否 | 否 |
| 选择工具集合 | 否 | 只request capability | 无mutation工具 | 无mutation工具 | 无mutation工具 | 否 | 否 | **唯一允许** | 否 |
| 宣布mission完成 | 否 | 否 | 只assessment | 否 | 否 | 否 | **唯一允许** | 否 | 否 |

## 18. Trace 最低字段

每次角色调用/Actor回合至少记录：

```text
runtime_version
mission_id / revision / obligation_id
execution_profile_id / recommended_depth / effective_depth
model_role / model / thinking_policy / elapsed_ms
previous_action_projected: bool | null
decision_id / purpose / request_fingerprint
attempt_count / request_retry_count / attempt kind / error kind / retryable
followup_kind / late_result_discarded
mission_card_chars / prompt_context_chars
model_tool_count / model_tool_schema_chars
broker_projection_id / active capability grants
frame_id / epoch / target_binding_id
validated model proposal
runtime-bound envelope id
controller transition id
verifier invocation and evidence refs
reducer event and resulting revision
```

其中 `previous_action_projected=true` 只表示该次 Actor request 收到了有界 previous-action view，
`false` 表示 Actor 已被调用但未投影，`null` 表示该回合没有可靠的 Actor-request 来源。
它是不含参数、意图或图像的私有 trace sidecar 事实，不进入 BUS/WebSocket 或 Mission 真相。

这样失败可以归因到 Goal Compiler、Task Profiler、Execution Policy、Mission Reducer、Broker、Actor、Frame/Controller、Verifier、Planner、Inspector或外部环境，而不是笼统归因于“模型不行”。

## 19. Existing-window activation authority（内部闭环，产品入口尚未接线）

`target-activation-envelope/0` 是 Runtime 私有的单次 mutation authority。它绑定
mission/run/revision/obligation、epoch-zero `full_desktop` frame、released pointer、Broker
projection、grants、evidence scope、整个候选列表 fingerprint、Actor 的 ordinal-only
`activate_target` proposal，以及从同一 one-shot selection lease 解析出的完整强
`TargetCandidate`。Actor 永远看不到 HWND、PID、process identity、candidate ref 或 fingerprint。

该 envelope 本身不能证明一个 ordinal 属于某个列表；唯一有效来源是 Runtime 刚刚成功消费的
`TargetSelectionLease`。permit 的 canonical SHA-256 同时覆盖 proposal 与完整私有候选，交叉类型、
篡改或重复消费均 fail closed。wire/replay parser 只接受严格 JSONValue，不接受 tuple、bytes、
dataclass、Pydantic object、NaN 或隐式枚举转换。`TargetCandidate.to_dict/from_dict` 是私有候选
wire schema 的唯一 owner。

`target-activation-transition/0` 只有三种结果：`completed` 必须带强 activated identity、post frame
和 run-scoped deterministic evidence，且 `mutation_completed` 可以是 `false`（目标本来就在前台）
或 `true`（唯一 primitive 已执行）；`rejected` 必须证明 mutation 未发生；任何可能执行但无法闭合
foreground/frame/evidence 的结果是 `unknown`，不得自动重放。Runtime 接受结果前必须精确核对
action/decision/candidate、HWND/PID/process identity、post frame 与 Registry evidence；只有 Runtime
可以据此追加 evidence 并提交 `target_bound` Mission event。

commits `2d1fb1b..845c034` 已按上述合同闭合 existing-window 内部执行链。admitted
`activate_target` primitive 只在最终强身份 guard 后使用一种前台切换机制，并复用同一个
`AtomicDesktopController`、`DesktopActionQueue`、`FrameAuthority`、`RawDeviceExecutor` 与
post-action bridge；禁止调用 legacy focus 级联或在不确定后尝试第二种机制。精确目标本来就在前台
时可以零 mutation 完成，但仍需再次证明 foreground 和强身份。

post-action bridge 在同一 `FrameAuthority` 上提交 `target_window` 权威帧，并为本次 action 注册
独立 deterministic `post_action_frame` evidence。MissionRuntime 只从刚消费的 lease 取得完整候选，
在 permit consume 时重新核对 targetless Mission/revision/obligation、epoch-zero full-desktop
frame、pointer、Broker/grants、evidence scope 和 lease fingerprint；Controller 返回后再精确对账
action/candidate、HWND/PID/process identity、当前 root/region/post frame 及 Registry 中的
mission/run/obligation/action/frame/hash。全部成立后，Runtime 才构造另一份
`authoritative_target_binding` attestation 并接受唯一 `target_bound` event。post-frame evidence
不能冒充 binding evidence；Mission binding 仍只由 accepted event + immutable evidence 重放派生。

rejected、unknown、未消费 permit、stale revision/lease 或任何 transition/frame/evidence 不一致都
不绑定、不 fallback、不 replay。private Runtime-turn trace 只从 accepted `target_bound` 识别本次
activation 已被 Mission 接受，不记录私有 candidate identity，也不修改 BUS/WebSocket 事件。

这仍不是产品可达能力：live Catalog 尚未加入 `activate_target`，Server composition root 尚未注入
admitted activation backend，coordinator 仍强制 foreground handoff，所以 targetless Electron
入口不能执行这条链。`open_app` cold launch、启动前后因果绑定和 existing-instance/cold-launch
两类同入口独立 oracle 都未完成。P4b.2/P7/P8/P9 均未达，legacy 删除数为 0。

### P1.5 completion and recovery contract (canonical)

The Python sidecar is explicit about the distinction between obligation
verification and mission completion. When every required obligation is
`verified`, the reducer derives `awaiting_mission_verification`; it never
derives `verified`. Only one accepted `mission_verified` event can close the
mission. That event must carry a new mission-level `verification_id` and at
least one evidence reference from the current mission/run registry. The
identity is stored as `mission_verification_id`; `verified`, `failed`, and
`canceled` are terminal and reject every later write.

Each `ObligationProjection` stores reducer-generated lifecycle facts:
`claim_fingerprint`, `verified_claim_fingerprint`,
`activation_attempt_count`, `verification_attempt_count`, and the accepted
obligation `verification_id`. Fingerprints are derived from the obligation,
runtime evidence IDs, and the bound transition summary; event IDs and model
text are not inputs. A fingerprint can be verified only once. A new attempt
requires a new runtime claim fingerprint, and the policy's
`max_verification_attempts` and `uncertain_policy` determine whether the
obligation remains active, waits for external recovery, or becomes blocked.
Visual verifier evidence is accepted only when
`allow_independent_visual_verifier` is true.

`ArtifactRef` requires a canonical SHA-256 content identity. Its bound
evidence must carry the same artifact id and the same `content_sha256`, in
the current run scope; an id-only or payload-only proof is rejected.
`TargetBinding` and `VerifiedFact` require non-empty stable identities.
Reducer acceptance additionally checks that their evidence references are in
the current run scope and identify the same target or obligation.
`TransitionSummary` and `FailureSummary` carry an
`obligation_id`; `MissionCard` projects either summary only when it belongs to
the currently active obligation, so feedback from a previous obligation
cannot leak into the next card. Legacy decomposition is retained only when
every split row participates in an explicit dependency or artifact edge.
Unknown legacy capability labels are preserved in metadata and never become
`required_capabilities`. An unprovably decomposable legacy contract falls
back to one required full-goal obligation.
