# vNext 状态与角色合同

本文定义 vNext 各层交换的 typed data。P1/P1.5 已实现纯 Python 合同、Reducer、replay、MissionCard 和 legacy shadow；P2 Profiler 与 P3 Broker 已有默认关闭的 shadow，但评测/enforce 门槛未达；queue-time capability admission 已收敛为一个 runtime-aware owner。P4/P5 现有默认关闭的增量 live coordinator：严格 Actor proposal/view/envelope、模型 Visual Actor、`click/double_click/scroll/claim/blocked` 小工具面、共享 atomic Controller 执行链、独立视觉 ObligationVerifier 与本地 deterministic MissionVerifier 已从同一 per-run 产品选择接通。legacy 仍默认；三个随机 seed 的真实 Electron click canary，以及最终 scroll 合同的一对 down/up canary，已通过独立 oracle。完整 P7 未达，ActionEffectVerifier、其余 GUI 原语、跨进程 exactly-once 与 P6 均未实现。

## 1. 版本与基本规则

建议初始格式：

```text
model-request/0
task-profile-proposal/0
execution-profile/0
mission-definition/0
mission-event/0
mission-projection/0
mission-card/0
actor-mission-view/0
actor-frame-view/0
actor-tool-view/0
actor-turn-input/0
actor-proposal/0
actor-envelope/0
broker-projection/0
runtime-frame/0
runtime-snapshot/0
controller-transition/0
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
```

`/0` 表示尚未发生首个不兼容升级的旁路实验合同；单个合同只有在真实字段边界变化时才升版，
不是等到默认切换时统一改号。
以上版本已按当前代码逐项核对：只有 coordinator context 因新增非阻塞 pause reading 升为
`desktop-runtime-coordinator-context/1`，旧 context `/0` 不再接受；coordinator result 与其余仍列为
`/0` 的合同尚未发生不兼容字段变化，不能为追求版本号整齐而批量改成 `/1`。

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

The canonical event key is `kind`; `type` is accepted only when reading a legacy record and is never serialized. Event `format` must equal `mission-event/0`, payload values must be strict finite JSONValue values, and every accepted event is bound to the current mission/run revision.

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

### 9.1 P4a Actor 可见投影

P4a 不把带 Runtime 身份的 `MissionCard` 或 `BrokerProjection` 原样交给 Actor。Runtime 机械投影：

- `ActorMissionView`：完整 `original_goal`、当前义务的语义字段、相关事实/工件摘要、target 标题及当前义务自己的 transition/failure 摘要；
- `ActorFrameView`：只说明图片是否可用、是否权威/新鲜，以及 `submitted_image_pixels` 坐标系；
- `ActorToolView`：只包含当前 BrokerProjection 允许的 Visual Actor 工具名和严格 JSON schema；
- `ActorTurnInput`：组合上述三种 view，图片字节和 submitted size 通过注入的 `ActorPort` 单独传递。

这些 view 结构上不含 mission/run/revision/obligation/frame/evidence/grant/projection identity。语义文本完整保留，不通过关键词替换或应用规则“清洗”。当前增量 live coordinator 已通过隔离的模型请求实现该 ActorPort；Runtime 仍是机械身份的唯一绑定者，provider 不能扩大 BrokerProjection 或接触 Controller。

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
3. 严格解析一个 `actor-proposal/0`；
4. Actor 返回后重新读取 revision、active obligation 和完整 authority snapshot，并重新投影 Broker；
5. 任一 frame/epoch/HWND/region/image hash/target/pointer/grant/projection 或义务变化都返回 `stale_discarded`，不进入 Controller；
6. 只有仍在最新 BrokerProjection 中的一笔 Visual Actor 动作，才由 Runtime 绑定 envelope 后交给注入 ControllerPort。

P4a Controller 回执使用三态 `ControllerOutcome`：

```text
completed
rejected
unknown
```

`completed` 与 `rejected` 只有在 action identity、当前 post frame 和 evidence 能对账时才可产生 `transition_recorded`。`unknown`、Controller 执行后异常、身份不一致或 post-frame/evidence 无法对账时，Runtime 接受 `obligation_blocked`，且不自动重放 mutation。取消后的迟到 Actor 结果也不会进入 Controller；若 Runtime 已持有物理 pointer lease，只允许注入的确定性 release cleanup。

`ObserveProposal` 只刷新观察；`BlockedReport` 只产生当前义务的 blocked event，其自然语言事实不会升级为 `VerifiedFact`。`CapabilityRequest`、`InspectionRequest` 和 `ReplanRequest` 在 P4a 均返回 `unavailable`；尤其 P6 Planner/Inspector 未启用时必须 fail closed。

当前产品接线只对显式 `vnext` run 组装增量 Actor、Broker、Frame/Controller bridge 与验证端口，工具面为 `click/double_click/scroll/claim/blocked`。三个 click seed 与最终 scroll 合同的一对 down/up seed 已通过真实 Electron 与独立 oracle。这不等于全局 Broker enforce 或完整 P7 已通过；P3 的 necessary-tool recall 门槛仍未达到。任何后续原语仍必须复用现有唯一 frame/admission/execution owner，不能把注入端口实现成第二套执行链。

### 11.2 P4a ControllerTransition

注入式 ControllerPort 返回 `controller-transition/0`，至少绑定 `action_id`、`action`、三态 outcome、`input_completed`、post frame 和 evidence refs。`completed` 必须明确 `input_completed=true` 且带当前 post frame/evidence；`rejected` 必须明确 `input_completed=false` 且带 post frame；`unknown` 必须保持 `input_completed=null`，不能伪装成“确定未执行”。该边界是 Controller 事实回执，不拥有语义恢复或任务完成权限。

### 11.3 P4b 内部机械输入合同（partial）

当前 admitted physical scroll 与 atomic click/double-click 使用 typed `RawBackendResult` 和 `RawActionReceipt`，机械完成三态固定映射为：`APPLIED -> true`、`NOT_APPLIED/NOT_STARTED -> false`、`OUTCOME_UNKNOWN -> null`。backend 必须显式报告 `input_completed`；泛型 `ok:true` 不能升级为已执行。设备调用后的异常或非法 backend result 都进入 `OUTCOME_UNKNOWN`，上层只能重新观察、对账或阻塞，不能自动重放。legacy PostMessage 不确定语义仍保持同一 no-replay 边界。

RawDevice 接收 Controller 已解析的 typed scroll/click 参数，不接收 `action + args`，因此不是第二个 action parser。它不读取 session、截图、OCR/UIA、任务、应用名或恢复状态。Receipt 的 backend facts 是可序列化的只读诊断，不是 run-scoped evidence，`APPLIED` 也不证明像素变化或语义完成。

具体 live coordinator 的 `raw_device` 是必需注入项。产品 composition root 构造
`RawDeviceExecutor` 与 admitted physical click/scroll backend 后注入；coordinator、MissionRuntime 和
Actor 都不得自行创建 backend 或在缺失时 fallback。这样 composition 与执行仍只有一个物理 owner。

该内部 seam 尚未冻结为 vNext `/1` 产品合同。Actor 可见 scroll args 严格为 `{image_x, image_y, direction: "up" | "down", notches: 1..12}`；`up` 表示显露 viewport 上方内容，`down` 表示显露下方内容。旧 signed `amount` 在 Actor 边界不兼容并 fail closed；`AtomicDesktopController` 只机械映射 `up -> +notches`、`down -> -notches` 到既有 RawDevice canonical convention。`AtomicDesktopController` 由 Runtime permit 和权威 frame pin 限定一次 transaction，commit-time 重读 frame/epoch/HWND/PID/region/hit-test，并在 click mouse-down 或 scroll wheel 紧前做 final target guard；后续 `AtomicPostActionBridge` 在同一 physical lease 中 CAS 提交动作后权威帧并写入 exact action/frame evidence。scroll 只调用 unqueued admitted physical SendInput backend，不做机制切换；任一动作后证据缺失都是 `unknown`，不能转成可重放 rejection。当前默认关闭的 live coordinator 接入 `click`/`double_click`/`scroll`；`type_text`、`hotkey`、`drag`、`draw_strokes`、held pointer 与 ROI observe 均未接入。`DesktopRuntimeSelection` 只在 run 开始时冻结；缺失 coordinator 或任一 vNext 端口都必须在 mutation 前拒绝，不能隐式 fallback 到 legacy。

### 11.4 P4c product coordinator 与 per-run selector

`desktop-runtime-coordinator-context/1` 只携带 Runtime 冻结的 run id、任务/contract 只读快照、现有 stop owner、回合入口的 blocking pause gate 与 commit 前的只读 `is_paused`；`desktop-runtime-coordinator-result/0` 只允许严格 terminal 结果。`/1` 不兼容 `/0`，因为新增的非阻塞 pause reading 用于丢弃模型调用期间产生的旧动作，恢复后必须从新权威帧重新决策。客户端只在顶层 ARP run request 发送 `runtime_version`，不把它写入 graph、task contract、Profiler 或模型 payload。`ArpExecution` 仅在 selection 为 `vnext` 且 Server 注入增量 live `DesktopRuntimeCoordinatorPort` 时调用一次 coordinator，并完全绕过 legacy node handlers；结果非法、异常或取消都不得 fallback。缺省/`legacy` run 仍不注入该 coordinator。

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

P5b 构造时必须同时注入 `DeterministicMissionCompletionVerifier` 和 canonical `EvidenceRegistry`，且 `MissionReducer.evidence_source` 必须是该同一 registry。Runtime 先 append/read-back aggregate evidence，再提交 exact `mission_verified` event；Reducer 独立校验 accepted obligation verification ids、证据顺序、aggregate id/hash/kind/trust 和 canonical mission verification id。视觉模型只返回无身份 assessment；许可同时依赖 EvidencePolicy 与 Registry 中真实 evidence trust，Runtime 复核 claim/revision/current authoritative frame 后才写入本地 attested `independent_visual_model` evidence。payload 自报 trust 或 evidence identity 无效。当前仍无 `ActionEffectVerifier`、跨进程 WAL 或已证明的 exactly-once 恢复。

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

这样失败可以归因到 Goal Compiler、Task Profiler、Execution Policy、Mission Reducer、Broker、Actor、Frame/Controller、Verifier、Planner、Inspector或外部环境，而不是笼统归因于“模型不行”。
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
