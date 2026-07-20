# vNext 状态与角色合同

本文定义 vNext 各层交换的 typed data。所有名称均为设计合同，尚未实现。实现时应先固定 schema 与 reducer 测试，再接入模型或桌面动作。

## 1. 版本与基本规则

建议初始格式：

```text
mission-state/0
mission-card/0
actor-decision/0
transition-record/0
verification-result/0
mission-patch/0
inspection-report/0
```

版本 `0` 表示旁路实验合同。进入默认产品前再冻结为 `/1`。所有记录必须：

- 带 `mission_id`、`run_id`、`revision` 或 immutable evidence id；
- JSON 可序列化并写入现有 trace；
- 模型输出先做 schema validation，再进入 Reducer/Controller；
- 验证失败时不改变原状态；
- 不保存或传播模型 chain-of-thought；
- 不允许自然语言字段绕过 typed 权限边界。

## 2. MissionState

MissionState 是跨 obligation 的唯一主线状态，由 Mission Reducer 单写。

```python
@dataclass(frozen=True, slots=True)
class MissionState:
    format: str
    mission_id: str
    run_id: str
    revision: int
    original_goal: str
    goal_revision: int
    obligations: tuple[Obligation, ...]
    active_obligation_id: str | None
    verified_obligation_ids: frozenset[str]
    artifact_bindings: dict[str, ArtifactRef]
    target_binding: TargetBinding | None
    verified_facts: tuple[VerifiedFact, ...]
    pending_approval: ApprovalRequest | None
    failure_budgets: dict[str, FailureBudget]
    status: MissionStatus
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

MissionState 不保存：

- 模型推理；
- 未来 GUI 动作列表；
- 控件 selector、OCR anchor 或坐标；
- 当前 mouse-down 的设备细节；
- 应用专属 fallback；
- 未验证的 Actor 自述事实。

PointerLease、FrameState、DesktopActionLock 属于执行状态，由各自唯一 owner 管理；Broker 只读取其快照，不复制进 Mission Ledger。

## 3. Obligation

```python
@dataclass(frozen=True, slots=True)
class Obligation:
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
    status: ObligationStatus
```

`ObligationStatus`：

```text
pending -> active -> claimed -> verified
                   -> active      # verifier rejected, evidence changed
active  -> blocked
pending/active -> superseded      # accepted user revision/planner patch
any nonterminal -> canceled
```

重要区别：

- `claimed`：Actor 认为局部义务可能完成，只能触发验证。
- `verified`：VerifierResult 被 Reducer 接受，且所有 acceptance criteria 有证据。
- `blocked`：当前事实无法继续，不代表 mission 必然失败；Planner、用户输入或外部状态变化可产生后续事件。

### 3.1 Broad capability

`required_capabilities` 只允许宽能力，例如：

```text
visual.interact
visual.draw
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

它不能包含：

- `click_search_box`；
- `qq.send_image`；
- `paint.draw_flower`；
- 特定按钮、坐标、窗口标题或测试名；
- 工具调用顺序。

无法可靠确定时使用 `visual.interact`，而不是猜更具体的 capability。

## 4. VerifiedFact 与 EvidenceRef

事实只能引用不可变 evidence：

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

建议 `trust` 值：

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

## 5. ArtifactRef 与 TargetBinding

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

窗口标题只是证据的一部分，不能独立决定应用身份。TargetBinding 必须来自运行进程、路径、窗口、时序和当前画面的组合证据。绑定失效时 Reducer 清除引用，Broker 自动退回未绑定工具面。

## 6. MissionCard

MissionCard 是 Actor 每回合的完整结构化状态，不是聊天历史摘要。

```python
@dataclass(frozen=True, slots=True)
class MissionCard:
    format: str
    mission_id: str
    mission_revision: int
    original_goal: str
    active_obligation: ActiveObligationCard
    verified_facts: tuple[VerifiedFactCard, ...]
    artifact_bindings: tuple[ArtifactCard, ...]
    target_binding: TargetBindingCard | None
    last_transition: TransitionSummary | None
    failure_summary: FailureSummary | None
```

Card 构建规则：

- 完整保留 `original_goal`；
- 只包含 active obligation；
- 只投影与当前 obligation 有关的 verified facts/artifacts；
- 最多保留一个最新 transition 和有界 failure summary；
- 不包含其他 obligation 的操作细节；
- 不包含旧帧，最新帧由 FrameAuthority 单独附加；
- 不包含 Planner/Verifier/Inspector 的隐藏推理。

## 7. ActorDecision

Actor 每回合必须选择一个 union variant：

```python
ActorDecision = (
    DesktopActionRequest
    | ObserveRequest
    | ClaimObligationDone
    | BlockedReport
    | ReplanRequest
)
```

### 7.1 DesktopActionRequest

```python
@dataclass(frozen=True, slots=True)
class DesktopActionRequest:
    decision_id: str
    mission_revision: int
    obligation_id: str
    action: str
    frame_id: int
    args: dict[str, JSONValue]
    local_intent: str
```

`local_intent` 只描述这一笔动作预期产生的局部状态变化，例如“聚焦当前可见搜索输入区域”，不能承载后续计划。

Controller 必须拒绝：

- obligation id 不等于 active id；
- mission revision 已变化；
- frame id、epoch 或 target binding 不匹配；
- 工具不在本回合 BrokerProjection；
- pointer lease 与 action 不兼容；
- 参数混用不同坐标系统。

### 7.2 ObserveRequest

```python
@dataclass(frozen=True, slots=True)
class ObserveRequest:
    kind: Literal["full", "image_region"]
    frame_id: int | None
    image_region: ImageRegion | None
    question: str
```

`question` 用于 Actor 自己的注意目标；Runtime 不从问题文本解析任务类型。`image_region` 只引用当前权威图片。

### 7.3 ClaimObligationDone

```python
@dataclass(frozen=True, slots=True)
class ClaimObligationDone:
    obligation_id: str
    mission_revision: int
    evidence_refs: tuple[str, ...]
    concise_claim: str
```

claim 不改变 mission status，不允许包含“顺便把其他义务也完成”的声明。

### 7.4 BlockedReport 与 ReplanRequest

```python
@dataclass(frozen=True, slots=True)
class BlockedReport:
    obligation_id: str
    blocker_kind: str
    observed_facts: tuple[str, ...]
    evidence_refs: tuple[str, ...]
```

```python
@dataclass(frozen=True, slots=True)
class ReplanRequest:
    obligation_id: str
    reason_kind: str
    conflicting_fact_refs: tuple[str, ...]
    failed_strategy_refs: tuple[str, ...]
```

模型不能通过自然语言请求直接增加工具或跳过 approval；Runtime 根据 typed event 判断是否满足 Planner 触发条件。

## 8. BrokerProjection

```python
@dataclass(frozen=True, slots=True)
class BrokerProjection:
    mission_revision: int
    obligation_id: str
    execution_route: Literal[
        "visual_actor", "deterministic", "provider", "blocked"
    ]
    executor_id: str | None
    target_binding_id: str | None
    pointer_lease_id: str | None
    tool_names: tuple[str, ...]
    missing_capabilities: tuple[str, ...]
    projection_reasons: tuple[str, ...]
```

不变量：

- 每个工具来自 ToolCatalog；
- 工具前置条件必须由本地状态证明；
- held pointer 时 mutation 工具只能是 move/release；
- mutation adapter 与 GUI Actor 路线互斥；
- `deterministic/provider` 路线必须有精确 input binding、授权和可验证 receipt，且 `tool_names` 不包含 GUI mutation；
- `visual_actor` 路线不得同时触发 direct executor；
- MCP 未注册、未授权或输出不可验证时不暴露；
- BrokerProjection 写入 trace，便于证明模型当时到底看到了哪些工具；
- 工具集变化只由状态变化触发，不由 prompt 或应用名触发。

## 9. TransitionRecord

Controller 与 Evidence Reducer 产生：

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

`visible_effect_summary` 是可审计的简短事实，不得写“任务完成”。ActionEffectVerifier 可补充局部效果，但不能验证 obligation。

## 10. VerificationResult

```python
@dataclass(frozen=True, slots=True)
class VerificationResult:
    verification_id: str
    scope: Literal["action_effect", "obligation", "mission"]
    subject_id: str
    verified: bool
    satisfied_conditions: tuple[str, ...]
    missing_conditions: tuple[str, ...]
    contradictions: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    verifier_kind: str
    confidence: float | None
```

Reducer 接受条件：

- `subject_id` 与当前 revision 对应；
- evidence refs 存在且 scope 匹配；
- required criteria 没有缺失；
- forbidden effects 没有未处理 contradiction；
- 视觉模型 verdict 不覆盖相反的 deterministic/oracle 证据；
- mission verification 不能跳过未 verified 的 required obligation。

Verifier reject 后 obligation 回到 `active` 只发生在 Reducer 记录 reject 和新 recovery budget 后。相同证据重复 claim 不产生新 verification 调用。

## 11. PlannerPatch

```python
@dataclass(frozen=True, slots=True)
class MissionPatchProposal:
    base_revision: int
    obligations_to_add: tuple[ObligationDraft, ...]
    obligations_to_update: tuple[ObligationUpdate, ...]
    dependency_changes: tuple[DependencyChange, ...]
    reason: str
    evidence_refs: tuple[str, ...]
```

Reducer 必须检查：

- base revision 未过期；
- 依赖图无环；
- original goal 的 required coverage 不下降；
- 已 verified obligation 不被无证据撤销；
- patch 不含 GUI 操作、控件、坐标或工具顺序；
- capability 仍是 broad capability；
- 新 obligation 不是应用专属脚本。

不合格 patch 被拒绝并写 trace，MissionState 不变。

## 12. InspectionReport

```python
@dataclass(frozen=True, slots=True)
class InspectionReport:
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

Actor 可以参考报告，但最终坐标必须仍基于同一权威 FrameState，并通过 Controller preflight。

## 13. Reducer 事件

建议事件集合：

```text
mission_compiled
obligation_activated
target_bound
target_invalidated
artifact_bound
actor_action_requested
transition_recorded
obligation_claimed
verification_recorded
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

每个事件都必须有 revision precondition。Reducer 是纯函数式状态迁移核心：相同 state + event 产生相同 next state，便于 replay、property test 和差分测试。

## 14. 角色权限矩阵

| 能力 | Visual Actor | Verifier | Planner | Inspector | Reducer | Broker | Controller |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 看当前帧 | 是 | 必要证据帧 | 否，除非转换成已验证事实 | 指定 ROI | 否 | 否 | 捕获但不理解 |
| 提出 GUI mutation | **唯一允许** | 否 | 否 | 否 | 否 | 否 | 否，只执行 |
| 直接执行 GUI | 否 | 否 | 否 | 否 | 否 | 否 | **唯一执行** |
| 修改 MissionState | 否 | 否 | 否，只提 patch | 否 | **唯一允许** | 否 | 否 |
| 标记 obligation verified | 否，只 claim | 提 verdict | 否 | 否 | **唯一提交** | 否 | 否 |
| 改依赖 | 否 | 否 | 只提 patch | 否 | 校验后提交 | 否 | 否 |
| 选择工具集合 | 否 | 无 mutation 工具 | 无 mutation 工具 | 无 mutation 工具 | 否 | **唯一允许** | 否 |
| 判断动作是否送达设备 | 否 | 可读 evidence | 否 | 否 | 否 | 否 | **事实 owner** |
| 宣布 mission 完成 | 否 | 只给 verdict | 否 | 否 | **唯一允许** | 否 | 否 |

## 15. Trace 最低字段

每个 Actor 回合至少记录：

```text
mission_id / revision / obligation_id
mission_card_chars
prompt_context_chars
model_tool_count
model_tool_schema_chars
broker_projection
frame_id / epoch / target_binding_id
role / model / thinking_policy / elapsed_ms
validated actor decision
controller transition id
verifier invocation and evidence refs
reducer event and resulting revision
```

这使失败能够被归因到 Compiler、Reducer、Broker、Actor、Frame/Controller、Verifier 或外部环境，而不是笼统归因于“模型不行”。
