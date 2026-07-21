# Task Profiler 与 ExecutionProfile

状态：**P2a 已完成；P2b 真实 Profiler 调用已接入默认关闭的只读 shadow**。

本文补充 vNext 的低频架构选择入口。它解决的问题不是“任务应该点击哪里”，而是：当前任务需要多深的编排、哪些只读认知服务、什么证据强度，以及哪些运行事实应触发升档或降档。

## 1. 为什么需要独立 Task Profiler

如果让 Visual Actor 同时判断任务难度、选择运行架构、维护主线并操作当前画面，职责过载会重新出现。如果完全用关键词和本地规则判断，又会把系统做成任务分类路由器。

因此 vNext 增加一个轻量、只读、低频的模型角色：`TaskProfiler`。

它只回答：

- 用户目标包含哪些结构性压力；
- 建议的初始执行深度；
- 是否需要 Planner、独立语义 Verifier、Visual Inspector 或更强 reasoning；
- 哪些事实出现时应升档、降档或重新 profiling；
- 哪些 broad capability 可能需要，但不直接决定本回合工具。

它不回答：

- 打开哪个菜单；
- 点击哪个按钮；
- 使用哪个坐标；
- 滚动几次；
- 某应用的固定流程；
- 当前画面的下一笔动作；
- legacy 还是 vNext。Runtime 版本必须由显式产品/实验配置选择。

## 2. 与其他角色的边界

| 组件 | 回答的问题 | 不回答的问题 |
| --- | --- | --- |
| Goal Compiler | 用户明确要求哪些结果、依赖和工件关系 | 运行深度、GUI 路线 |
| Task Profiler | 需要多少编排、验证和认知服务 | obligation 内容、按钮和动作 |
| Execution Policy | Profiler 建议是否可接受，当前实际启用什么 | 用户语义和 GUI 路线 |
| Planner | 必要时提出高层 obligation/dependency patch | 当前工具、坐标和 GUI 微步骤 |
| Capability Broker | 这一回合真实允许哪些工具/执行路线 | 任务类型和业务计划 |
| Visual Actor | 当前像素下的下一笔语义动作 | 全局任务状态和最终验收 |
| Verifier | criteria 是否被证据满足 | 修复动作和任务规划 |

Task Profiler 不是 Planner，也不是 Broker。Profiler 提出“可能需要什么认知结构”；Planner 只在被允许的事件上提出任务结构 patch；Broker 根据已接受的 broad capability 与实时状态投影本回合工具。

## 3. 输入

Profiler 默认在 Goal Compiler 产生 lossless 初始 MissionDefinition 后调用一次。输入保持短小：

```json
{
  "original_goal": "用户原始目标，完整保留",
  "initial_obligations": [
    {
      "desired_state": "...",
      "depends_on": [],
      "required_capabilities": ["visual.interact"]
    }
  ],
  "verified_session_facts": [],
  "available_runtime_capabilities": [],
  "safety_constraints": [],
  "current_surface_summary": null
}
```

`current_surface_summary` 默认为空。只有任务明确依赖“当前画面”且纯文本无法判断结构时，Runtime 才可提供一张只读初始帧或一个来源明确的简短 surface fact；Profiler 永远没有动作工具。

Profiler 不读取：

- 应用专属 playbook；
- benchmark、题号或预期路线；
- 历史成功坐标；
- Visual Actor 的完整历史；
- 未验证 OCR/UIA 推断；
- 当前工具全集。

## 4. 多维任务画像

禁止只输出 `type=drawing`、`difficulty=hard` 之类标签。建议输出可解释的多维特征：

```text
step_dependency: atomic | local_sequence | multi_stage
application_scope: current_surface | single_application | cross_application | unknown
artifact_flow: none | produce | consume | handoff
external_wait: none | bounded | persistent
irreversibility: low | medium | high
visual_precision: ordinary | fine | geometric
continuous_pointer: none | atomic_drag | cross_frame
completion_observability: deterministic | visible | mixed | weak
identity_binding: none | target | recipient | artifact | multiple
user_interaction: none | approval | clarification | ongoing
```

这些维度用于解释服务需求，不直接映射到应用专属架构。

## 5. 执行深度

`ExecutionDepth` 只有三档：

### `direct`

适合一个短、明确、低风险的义务。通常不启动 Planner；完成可由确定性证据或一次局部语义验收闭合。

### `standard`

默认档。使用 MissionProjection、Visual Actor、Capability Broker 和 obligation verification；通常保持一个 lossless obligation，由 Actor 在当前应用内自主探索。

### `orchestrated`

仅用于有明确跨义务依赖、跨应用工件、外部等待、审批或多项独立验收条件的任务。可以启用多 obligation、MissionVerifier 和按需 Planner。

执行深度不是永久锁定。运行中可以：

```text
direct -> standard -> orchestrated
orchestrated -> standard/direct
```

升降档必须由 accepted profile、当前 Mission facts、失败证据、用户修订或安全策略触发，不能由应用名、窗口标题或任务标签触发。

## 6. CognitiveServices

```python
@dataclass(frozen=True, slots=True)
class CognitiveServices:
    planner: bool
    obligation_verifier: bool
    mission_verifier: bool
    visual_inspector: bool
    deep_reasoning: bool
```

默认原则：

- `planner`：普通单 obligation 为 `false`；只在安全拆分不足、跨义务冲突或重复策略失败时开启。
- `obligation_verifier`：涉及语义 GUI 结果、身份、交付、工件或风险时开启；纯确定性低风险结果可由本地 verifier 完成。
- `mission_verifier`：多 required obligation 或复杂 forbidden effects 时开启。
- `visual_inspector`：默认 `false`；Actor 自己的高清 ROI 仍失败且 held-out A/B 有收益后才允许。
- `deep_reasoning`：首帧、复杂几何、重复失败、Verifier reject 或 profile 低置信时按事件开启。

Profiler 只能建议服务。Execution Policy 可以因安全、成本、provider 可用性或当前证据覆盖进行收紧、延后或拒绝，不能降低用户明确的验收要求。

## 7. TaskProfileProposal

模型输出不携带 run、mission、revision、evidence 或 frame 身份：

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

禁止字段：

```text
application_workflow
tool_sequence
button
selector
coordinate
next_action
mission_patch
runtime_version
```

## 8. ExecutionProfile

Runtime 使用当前 run 身份包裹并校验 proposal：

```python
@dataclass(frozen=True, slots=True)
class ExecutionProfile:
    format: str
    profile_id: str
    mission_id: str
    goal_revision: int
    accepted_depth: ExecutionDepth
    services: CognitiveServices
    accepted_capabilities: tuple[str, ...]
    upgrade_triggers: tuple[ProfileTrigger, ...]
    downgrade_conditions: tuple[ProfileTrigger, ...]
    source: Literal["task_profiler", "safe_default", "runtime_revision"]
    confidence: float | None
    policy_reasons: tuple[str, ...]
```

Execution Policy 必须验证：

- capability 属于通用 catalog；
- proposal 不含 GUI 战术；
- `minimum_depth` 不绕过安全与验收；
- Planner/Inspector 只读；
- profile 不选择 runtime version；
- 用户明确要求的工件、recipient、approval、delivery 和 forbidden effects 没有被裁掉；
- provider 不可用时有 typed fallback，而不是静默假装服务存在。

Profiler unavailable、格式错误或低置信时，默认使用 `standard`、单个 lossless obligation、必要的独立验收；不得退化为应用关键词规则。

## 9. 调用时机

Profiler 只在以下时机调用：

1. 新 mission 的初始 MissionDefinition 已生成；
2. 用户对原始目标作实质修订；
3. 已验证事实证明初始 profile 的结构假设错误；
4. 当前 depth 无法表达新发现的跨应用、工件、审批或持续等待需求。

普通 GUI 回合不重新调用 Profiler。局部视觉失败优先进入 Actor recovery、ROI 观察或基础工具诊断，不应每次失败都重新分类整项任务。

## 10. 同一模型的角色隔离

第一版可以让同一个底层模型依次承担 Task Profiler、Planner、Visual Actor、Verifier 和 Inspector，但必须使用：

- 独立请求上下文；
- 独立系统合同；
- 独立输出 schema；
- 独立工具 allowlist；
- 独立推理与 token 预算；
- 只通过 typed envelope 交换事实；
- 不共享 chain-of-thought。

同模型角色仍可能产生相关误判。Verifier 必须优先使用不同证据视角，例如 deterministic fact、oracle、receipt、前后帧或高清终态 ROI，而不是只让同一个模型重复观看同一压缩图。

## 11. 评测

至少比较：

```text
fixed-standard
profiler-shadow
profiler-enforced
```

记录：

- profile 调用次数、耗时与成本；
- recommended/effective depth；
- Execution Policy override 原因；
- over-profile：简单任务启动过多角色；
- under-profile：复杂任务遗漏依赖、验证或认知服务；
- 动态升档/降档次数；
- Planner、Verifier、Inspector 的实际调用率；
- 任务改写、应用名变化与 held-out 场景下的 profile 稳定性；
- grounded pass、false done、p50/p95 latency；
- Actor 与同模型 Verifier 的错误相关性。

Profiler 的价值必须体现为未知任务能力、长任务义务覆盖或成本下降，不能以分类解释听起来合理作为成功。

## P2a implementation status (2026-07-21)

P2a is complete for the pure contracts and deterministic shadow policy. The
implementation is independently owned by
`python/winpilot_home/winpilot/vnext/profiling/`. It validates the versioned `TaskProfileProposal` and
`ExecutionProfile` schemas, semantic `TaskDimensions`, `CognitiveServices`,
`ExecutionDepth`, canonical broad capabilities, and policy audit reasons.
Missing, malformed, unsupported, or low-confidence proposals resolve to the
safe `standard` profile. Depth and cognitive services can only be tightened
or restricted from semantic dimensions and injected availability facts.

P2b adds one isolated, tool-free provider request per run behind the default-off
`vnext.profiler_shadow.enabled` flag. Runtime identity stays outside the model
payload; exact transport retry uses the shared model-call Runtime, and the
result is written only through `TraceRecorder.record_sidecar`. It does not
authorize Broker, Visual Actor, Planner, Inspector, GUI tool-surface,
Controller, done, or legacy execution changes.
