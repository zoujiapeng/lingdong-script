# vNext 状态与角色合同

本文定义 vNext 各层交换的 typed data。P1 已实现纯 Python 合同、Reducer、replay、MissionCard 和
legacy shadow；Task Profiler、Execution Policy、Broker、模型和桌面动作仍未接入。实现时先固定
schema、Reducer 与 envelope 测试；任何运行时接线都必须经过迁移计划的后续阶段门槛。

## 1. 版本与基本规则

建议初始格式：

```text
task-profile-proposal/0
execution-profile/0
mission-definition/0
mission-event/0
mission-projection/0
mission-card/0
actor-proposal/0
actor-envelope/0
broker-projection/0
transition-record/0
verifier-assessment/0
verification-result/0
mission-patch-proposal/0
inspection-report/0
```

版本 `0` 表示旁路实验合同。进入默认产品前再冻结为 `/1`。

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

P1 同时接受历史实现使用的 `type` 与本合同的 `kind` 拼写，并将事件级 `evidence_refs` 合并到
Reducer 的 run-scoped EvidenceScope。事件时间戳是审计元数据，不参与自动 event id 的确定性哈希。

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

```python
@dataclass(frozen=True, slots=True)
class ObligationLifecycle:
    status: Literal[
        "pending", "active", "claimed", "verified",
        "blocked", "superseded", "canceled"
    ]
    activation_count: int
    claim_fingerprint: str | None
    accepted_verification_id: str | None
    failure_budget_key: str | None
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
    process_id: int
    executable_identity: str
    title_snapshot: str
    established_by_evidence_ref: str
    epoch: int
```

窗口标题不能独立决定身份。绑定来自进程、路径、窗口、时序和当前画面的组合证据。失效时由 accepted event 清除，Broker退回未绑定工具面。

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
```

构建规则：

- 完整保留 `original_goal`；
- 只包含 active obligation；
- 只投影相关 verified facts/artifacts；
- 提供少量 preserve/future-dependency guardrails，避免局部最优破坏后续状态；
- 最多一个最新 transition 和有界 failure/verifier feedback；
- 不包含其他 obligation 的 GUI 细节；
- 不包含旧帧；
- 不包含其他角色隐藏推理。

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
    kind: Literal["full", "image_region"]
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

模型不提交 obligation id、revision 或 evidence refs。Runtime绑定 active obligation 并收集当前可引用 evidence。Claim 不改变状态，只触发 verification event。

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
    frame_id: int | None
    frame_epoch: int | None
    target_binding_id: str | None
    broker_projection_id: str
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

## 12. BrokerProjection 与 CapabilityGrant

```python
@dataclass(frozen=True, slots=True)
class BrokerProjection:
    projection_id: str
    mission_revision: int
    obligation_id: str
    execution_route: Literal[
        "visual_actor", "deterministic", "provider", "blocked"
    ]
    executor_id: str | None
    target_binding_id: str | None
    pointer_lease_id: str | None
    tool_names: tuple[str, ...]
    active_grants: tuple[CapabilityGrant, ...]
    missing_capabilities: tuple[str, ...]
    projection_reasons: tuple[str, ...]
```

```python
@dataclass(frozen=True, slots=True)
class CapabilityGrant:
    grant_id: str
    capability: str
    obligation_id: str
    mission_revision: int
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

视觉/模型 Verifier 只输出 assessment，不回填 subject 或 evidence identity：

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

这样失败可以归因到 Goal Compiler、Task Profiler、Execution Policy、Mission Reducer、Broker、Actor、Frame/Controller、Verifier、Planner、Inspector或外部环境，而不是笼统归因于“模型不行”。
