# vNext 架构决策记录

本文件记录已经确定的边界和仍需用实验回答的问题，防止后续实现因为某个失败样例重新引入第二个 GUI Agent、应用剧本、模型伪造运行时身份或重复状态。

## 已接受决策

### D001：只有一个 Visual Actor

- 状态：accepted
- 决策：只有 Visual Actor 可以提出 GUI mutation；不存在第二个可点击、输入或拖拽的 Planner、Task Profiler、Verifier 或 Inspector。
- 原因：多个桌面模型会争夺窗口、帧、鼠标状态与目标语义，恢复时无法确定唯一 owner。
- 推论：所有其他模型角色的 tool allowlist 必须静态证明为 read-only。

### D002：Mission 由确定性 Reducer 单写且可重放

- 状态：accepted
- 决策：Actor、Profiler、Planner、Verifier 只提交 typed proposal、claim 或 assessment；Reducer 校验 revision 和不变量后接受事件并产生新的 MissionProjection。
- 原因：让任一模型直接更新主线会重新制造不可 replay 的隐式计划；另建一份独立完成账本会与 TaskContract/Evidence 产生双重真相。
- 推论：MissionProjection 必须能从 MissionDefinition、accepted event log 和 immutable run-scoped evidence 重建；相同 state + event 必须产生相同 next state。

### D003：Obligation 描述结果，不描述路线

- 状态：accepted
- 决策：ObligationSpec 只包含 desired state、acceptance、dependency、forbidden effect、broad capability 和 artifact binding，不保存 GUI 路线或生命周期状态。
- 原因：到达界面前无法知道的按钮和路径会限制模型随机应变；spec 与 runtime status 混在一起会产生重复真相。
- 推论：无法可靠拆分时生成一个覆盖 original goal 的 obligation；生命周期由 MissionProjection 中唯一的 state map 表达。

### D004：Capability Broker 是纯状态投影

- 状态：accepted
- 决策：Broker 根据 accepted broad capability、ExecutionProfile、TargetBinding、PointerLease、Artifact、AdapterRegistry 和 approval 投影工具。
- 原因：模型不应额外判断 phase；固定大工具面浪费上下文并增加错误。
- 推论：Broker 不读取应用名、题号或 prompt 关键词选择工作流；Actor 可以提交 capability proposal，但授予权只在 Runtime/Broker。

### D005：Controller 不做语义修正

- 状态：accepted
- 决策：Controller 校验和执行 Actor 选择的目标，不能 OCR snap、换按钮、fallback 或宣布完成。
- 原因：下层语义修正会形成隐藏 Planner，并使 trace 不能解释实际动作归属。
- 推论：任何 target adjustment 都应为零或是明确、可证明的纯坐标映射。

### D006：claim、verified、mission complete 三层分离

- 状态：accepted
- 决策：Actor claim 触发独立 ObligationVerifier；Reducer 接受 verdict 后写入 verification event；全部 required obligation verified 后才允许 mission complete。
- 原因：生成者自评有正向偏差，action success 与业务完成不是同一语义。
- 推论：移除模型直接 `done` 的最终权限。

### D007：Verifier 上下文独立且只读

- 状态：accepted
- 决策：Verifier 不读取 Actor chain-of-thought，不持有 mutation 工具，优先 deterministic/oracle/receipt。
- 原因：独立评价比要求同一 Actor 更“自我批判”更可控。
- 推论：Verifier unavailable 时状态不前进；同模型 Verifier 仍需不同证据视角，不能只重复看同一压缩图。

### D008：Planner 与 Inspector 按事件调用

- 状态：accepted
- 决策：两个角色都不常驻；只有预定义事件与跨任务 A/B 证据存在时才调用。
- 原因：常驻角色增加延迟、上下文和意见冲突，可能成为新的主管层。
- 推论：普通单 obligation 回合不得调用 Planner；普通清晰画面不得调用 Inspector。

### D009：先修基础工具和调度骨架

- 状态：accepted
- 决策：按入口 -> Goal/Profile/Mission -> Broker -> App/Window -> Frame/Coordinate -> Input -> Transition -> Verifier -> Model -> Prompt 顺序诊断。
- 原因：大量“模型不会”的表现实际来自工具缺失、旧帧、错误窗口、动作语义或重复状态。
- 推论：没有前层证据时，prompt PR 和新模型角色 PR 不准入。

### D010：旁路共用同一产品入口

- 状态：accepted
- 决策：legacy/vNext 按 run 显式选择，共用 Electron、Frame/Controller、Artifact/Evidence 和 E2E；同一 run 不交叉执行。
- 原因：第二套入口会制造无法与用户体验比较的假通过。
- 推论：shadow 只允许纯计算，不允许双桌面 mutation；runtime version 不由任务文本或 Profiler选择。

### D011：Task Profiler 是低频只读角色

- 状态：accepted
- 决策：Goal Compiler 产生 lossless 初始 MissionDefinition 后，Task Profiler 可提出多维任务画像、执行深度、认知服务和升降档触发条件；确定性 Execution Policy 校验后形成 ExecutionProfile。
- 原因：完全本地分类会死板，让 Visual Actor 决定架构又会把职责压回 Actor。
- 推论：Profiler 不生成 obligation、工具序列、GUI 步骤、应用专属架构或 runtime version；普通 GUI 回合不重复调用。

### D012：执行深度与产品 Runtime 版本分离

- 状态：accepted
- 决策：`legacy/vnext` 由显式产品或实验配置选择；vNext 内部的 `direct/standard/orchestrated` 由 ExecutionProfile 和运行证据决定。
- 原因：任务语义可以决定需要多少认知服务，但不能偷偷切换到另一套产品主线。
- 推论：测试必须分别记录 runtime version、recommended depth、effective depth 与 policy override。

### D013：模型输出不携带运行时身份

- 状态：accepted
- 决策：模型只输出无身份的 typed proposal。`mission_id`、`run_id`、revision、active obligation、frame id、evidence refs、broker projection id 等由 Runtime envelope 绑定。
- 原因：要求模型复制这些字段增加认知负担，并允许 stale、串线或伪造身份。
- 推论：Controller、Reducer 和 Verifier只接受 Runtime-bound envelope；自然语言或模型字段不能覆盖绑定身份。

### D014：ObligationSpec 与生命周期状态分离

- 状态：accepted
- 决策：ObligationSpec 不含 `status`；MissionProjection 只保存一份 `obligation_states[id]`。`verified_obligation_ids` 等集合只能是派生 property，不得另行序列化为权威字段。
- 原因：在 spec 和 mission 顶层同时保存状态会产生内部双账本。
- 推论：任何缓存都必须可从 lifecycle map 和 accepted verification evidence 重建。

### D015：CapabilityRequest 是提案，不是授权

- 状态：accepted
- 决策：Visual Actor 可以请求 broad capability 或只读支持，但 Runtime/Broker 校验 catalog、active obligation、当前状态、安全和授权后才授予有界 grant。
- 原因：完全封死工具会误裁剪；让模型直接加载工具又会破坏 Broker owner。
- 推论：grant 必须绑定 obligation/revision，有有效期并写 trace；不得从自由文本解析应用模式。

### D016：同一底层模型可以承担不同角色，但请求必须隔离

- 状态：accepted
- 决策：第一版允许同一个 provider/model 依次承担 Profiler、Planner、Actor、Verifier 和 Inspector。
- 原因：可先验证架构而不引入多模型变量。
- 推论：每个角色使用独立上下文、系统合同、输出 schema、工具 allowlist 和预算；角色只通过 typed data 交换，不共享 chain-of-thought。

### D017：迁移分支是临时集成线

- 状态：accepted
- 决策：`codex/vnext-agent-core` 可在迁移期间持续存在并吸收 `master` 修复，但 `master` 仍是唯一长期 canonical 产品分支。
- 原因：永久双栈和长期分支会漂移，尤其不利于以 Codex 为主的维护方式。
- 推论：默认切换完成后合并回 `master`，旧职责净删，迁移分支归档或删除；耐久备份使用 tag。

## 延后决策

### Q001：是否需要独立 Visual Inspector

- 状态：deferred
- 先验：优先使用 Actor 自己的 `observe(image_region)`。
- 需要证据：正确 frame/ROI/path 工具下，多个 held-out 视觉拓扑仍失败；只读 Inspector 对 external oracle 有稳定提升。
- 禁止：仅因一个 `game-drag-v2` seed 失败就实现。

### Q002：Planner 使用规则模型还是强模型

- 状态：deferred
- 先验：确定性 Compiler/Reducer 优先；模型 Planner 只处理无法安全表达的高层依赖冲突。
- 需要证据：Planner 调用样本、合法 patch rate、coverage uplift、延迟/成本。

### Q003：Actor 默认模型与 reasoning 档位

- 状态：deferred
- 先验：首帧、复杂几何、重复失败、Verifier reject 使用更强 reasoning，已验证例行回合轻量。
- 需要证据：相同任务/seed 的模型等待、动作有效率和 grounded pass A/B。

### Q004：Mission Compiler 能安全拆分到什么粒度

- 状态：deferred
- 先验：只拆显式结果依赖；无法证明就单 obligation。
- 需要证据：人工标注 requirement coverage、artifact identity 与错误拆分率，而不是 parser 命中率。

### Q005：Profiler 是否值得每个新任务一次额外模型调用

- 状态：deferred
- 先验：先 shadow；低置信或 unavailable 时使用 safe `standard` 默认。
- 需要证据：over/under-profile、角色调用成本、time-to-first-action、mixed/held-out coverage 与 false done。

### Q006：是否需要不同模型的 Verifier 或 Inspector

- 状态：deferred
- 先验：第一版同模型、不同上下文与不同证据。
- 需要证据：Actor/Verifier 相关错误率、外部 oracle 校准，以及不同模型带来的增益与成本。

### Q007：何时提升 vNext 为默认并删除命名空间

- 状态：deferred
- 判据：以 [migration-plan.md](migration-plan.md) 默认切换和 legacy 删除门槛为准。

## 修改决策的规则

修改 accepted decision 必须：

1. 新增一条替代决策，不静默改写历史；
2. 给出至少两个不同任务族或一个系统级不变量冲突的证据；
3. 说明对唯一 GUI writer、Mission Reducer、Execution Policy 和真实客户端入口的影响；
4. 说明新增复杂度将删除什么旧职责；
5. 更新 architecture、execution-profile、contracts、migration 和 evaluation；
6. 不得以单个测试变绿作为充分理由。
### D018: P1.5 closes completion and recovery without a second ledger

- status: accepted
- decision: Required obligation verification derives
  `awaiting_mission_verification`. Only an accepted `mission_verified` event
  with a distinct mission-level `verification_id` and current run-scoped
  evidence may derive terminal `verified`.
- rationale: An obligation verifier proves one desired state; it cannot prove
  that the whole mission is complete. Keeping the identity and evidence in the
  existing event-derived projection prevents a second completion ledger.
- recovery: Runtime claims receive a deterministic fingerprint and lifecycle
  counters. The same fingerprint is never verified twice. EvidencePolicy
  controls visual-verifier permission, attempt limits, and uncertain recovery;
  reducer states remain explicit (`waiting_external` or `blocked`) instead of
  being recomputed into success.
- identity: ArtifactRef, TargetBinding, VerifiedFact, TransitionSummary and
  FailureSummary must carry stable identity/evidence relationships. MissionCard
  filters feedback by active obligation. Legacy split is retained only when
  every row has an explicit dependency/artifact edge; unknown capabilities are
  metadata, never requirements.

### D019: P2a uses a pure proposal contract and deterministic policy

- status: accepted
- decision: `TaskDimensions`, `CognitiveServices`, `TaskProfileProposal`,
  `ExecutionDepth`, and `ExecutionProfile` are strict versioned values. A
  proposal contains semantic dimensions, broad capability hypotheses,
  cognitive-service requests, semantic triggers, confidence, and rationale;
  it cannot contain runtime identity, application workflows, tools, buttons,
  selectors, coordinates, next actions, or obligation/mission patches.
- policy: `ExecutionPolicy` is deterministic and injectable. It validates the
  canonical capability catalog, applies a safe `standard` profile for missing,
  malformed, unsupported, or low-confidence proposals, restricts depth and
  services from semantic pressure and availability facts, and records policy
  reasons. It never calls a model or selects a GUI route.
- owner: `python/winpilot_home/winpilot/vnext/profiling/` is the sole P2a
  implementation owner. Its injected shadow evaluator compares over/under
  profile and identity-free structural fingerprints without invoking a model.
- rollout: P2a is shadow-only. Profiler model invocation, Broker, Actor,
  Planner, Inspector, prompts, tool surfaces, Controller, done semantics,
  legacy execution, and frontend integration remain disconnected.

### D020: P2b is an at-most-once, trace-only Profiler shadow

- status: accepted
- decision: a Task Profiler semantic call is permitted at most once per run,
  has no tools or runtime identity, and can only propose the strict P2a schema.
- retry: provider SDK retries are disabled. Runtime may retry only the exact
  frozen request before any result is consumed; poll, redecision and action
  replay are distinct contracts. Unknown desktop mutation outcomes are never
  automatically replayed.
- rollout: `vnext.profiler_shadow.enabled` defaults to false. When enabled, the
  same Electron/ARP entry records a private trace sidecar and leaves legacy
  prompt, tools, actions and completion unchanged. P3 remains evidence-gated.

### D021: P3a Broker is a pure state projection and remains non-enforcing

- status: accepted
- decision: ToolCatalog associates existing tool schemas with generic policy;
  CapabilityBroker deterministically projects one mutually exclusive route and
  a small tool surface from MissionProjection, ExecutionProfile, authoritative
  target/frame/pointer/artifact state, live executor offers, authorization and
  bounded grants. It cannot inspect application names, task prose, benchmark
  identities, layouts or remembered coordinates.
- identity: grants bind mission, run, obligation, revision, issued turn and
  expiry. Artifact-dependent routes require matching run, artifact id and
  content hash. Provider mutations require live authorization and a receipt.
- rollout: P3a is pure and injected-test-only. The legacy tool surface is
  unchanged. A real-turn sidecar and complete held-out relevance labels are
  still required; without them necessary-tool recall is `None`, so enforce is
  prohibited and no legacy tool owner may be deleted.

### D022: P3b partial shadow observes a turn and never becomes a second executor

- status: accepted
- decision: an opt-in observer runs immediately before the existing legacy
  model request, reads the same FrameAuthority and pointer snapshot, and writes
  one private BrokerProjection sidecar. Its return value is ignored and the
  legacy tool schema object and ordering are preserved.
- replay: target binding changes use accepted Mission events backed by
  deterministic run-scoped EvidenceRef values. The sidecar carries the outer
  run, child run, step and Definition/event/evidence deltas needed for replay;
  legacy tool success or `done` never advances the shadow Mission.
- limits: multi-obligation alignment, artifact bindings, provider/MCP liveness
  and authorization remain unavailable unless a trusted Runtime owner supplies
  them. The feature defaults off, has no BUS/WebSocket output, and cannot be
  used for enforcement until the independent relevance-oracle gate passes.
- lifecycle: capability availability comes from an owner-scoped Runtime
  registry, not a server tuple or model payload. Each observer has a monotonic
  outer turn index and is detached from every runner when the run finishes.
  P3b remains explicitly partial until Mission Runtime supplies a reliable
  active-obligation envelope for multi-obligation tasks.

### D023: Model request retry is Runtime-owned and is not redecision or action replay

- status: accepted
- decision: Every logical model request in the WinPilot product package is
  bound once as `model-request/0` after provider-adapter preparation. Provider
  SDK retries are disabled. `ModelCallRuntime` is the sole request-retry owner
  and may retry only the exact frozen provider request, with the same decision
  id, role, purpose and fingerprint, before any response is consumed.
- boundaries: A poll submits no new request. A changed message, tool surface,
  thinking mode, output budget, purpose or decision is a redecision with a new
  decision id. Empty content, parsing, schema and semantic validation failures
  happen after transport success and cannot trigger a request retry. Action
  replay is governed by the mutation replay contract; an unknown desktop
  mutation outcome is never automatically replayed.
- observability: Each transport attempt has an initial/retry kind, stable
  fingerprint, elapsed time, outcome, error kind and retryable classification.
  A static AST guard verifies the exact permitted Chat Completions transport
  function stacks and rejects Responses or any unowned call path. Profiler
  cancellation is propagated from ArpExecution through its shadow hook,
  runtime and provider into `ModelCallRuntime` and is covered by tests. What
  remains incomplete is all-role per-attempt sidecar coverage and a stop-event
  seam for the legacy VLM helper; those remain rollout gates rather than
  inferred successes.
- scope: This decision's completed implementation scope is
  `python/winpilot_home/winpilot/`. The root `python/providers/` Flow/feature
  stack still owns legacy SDK/direct-HTTP retry loops. It must be proven
  unreachable from the product runtime and removed in P9, or migrated in a
  separate behavior-reviewed slice. It must not become a second model Runtime.
- non-claim: Moving legacy planner, done-judge, conversation, message-content
  or VLM helper calls through this transport does not implement P4 Visual
  Actor, P5 Verifier or P6 Planner/Inspector semantics.

### D024: P4a fixes the Actor boundary before product execution

- status: accepted
- decision: P4a implements strict versioned Actor-visible views, one
  identity-free `ActorProposal`, a Runtime-owned `ActorDecisionEnvelope`, and
  an injected single-turn `MissionRuntime`. Only the `visual_actor` route can
  invoke ActorPort; provider, deterministic and blocked routes cannot fall
  back to another GUI Agent in the same run.
- admission: Runtime snapshots Mission/authority state and BrokerProjection,
  then repeats all three after Actor returns. Any revision, active-obligation,
  frame, target, pointer, grant or projection change discards the stale
  proposal. Runtime admits only one action still present in the latest tool
  surface and binds every runtime identity itself.
- mutation result: Controller facts are `completed`, `rejected` or `unknown`.
  An unknown outcome, exception after admission, identity mismatch or failed
  post-frame/evidence reconciliation blocks the obligation and is never
  automatically replayed.
- lifecycle: `claim_obligation_done` itself accepts only an
  `obligation_claimed` event and derives `CLAIMED`. P5a may then invoke one
  injected read-only ObligationVerifier when the ExecutionProfile permits it;
  P5b may close an awaiting mission only through the paired local
  deterministic verifier and canonical EvidenceRegistry. Capability,
  Inspector and Planner requests whose services are not wired return
  unavailable and fail closed; P6 remains evidence-gated.
- rollout: The P4a Runtime calls only injected ports. It has no real model or
  device adapter, no Electron/legacy integration, no Broker enforcement and
  no live canary, so product behavior is unchanged. P4b must bridge to the
  existing single FrameAuthority/admission/Controller chain without copying
  an owner.
- deletion gate: P4a tests do not authorize legacy deletion. AgentRunner's
  global todo, direct `done`, and fixed large tool surface remain until paired
  canary and real Electron E2E meet the documented thresholds and trace proves
  MissionCard, P5 verification and BrokerProjection own every corresponding
  vNext turn.

### D025: P4b migrates shared owners one primitive at a time

- status: accepted
- decision: P4b first makes legacy delegate reusable mechanical owners, then
  connects the same owners to vNext. It does not copy FastGuiController or
  create a second capture, coordinate, action-parser, pointer or input chain.
- completed partial seams: two-phase `FrameObserver`, one-way
  submitted-image -> normalized-frame -> physical-screen mapping, immutable
  per-run `DesktopRuntimeSelection`, a typed scroll RawDevice seam, and a
  default-off injected `AtomicClickController` for click/double-click behind
  the existing DesktopActionQueue.
- atomic click: Runtime consumes a single-use mutation permit while the
  authoritative frame is pinned; commit-time Windows facts bind target/frame
  identity, every physical mouse-down has a final target guard, and the same
  physical lease captures a CAS-published post-action frame with deterministic
  run-scoped evidence. Missing post-action evidence becomes unknown and never
  makes the action replayable.
- raw boundary: RawDevice accepts typed physical arguments after Controller
  parsing. It does not interpret pixels, select a target, recover, switch
  mechanisms or replay. A mechanical receipt is diagnostic input truth, not
  visible-effect evidence or obligation completion.
- uncertainty: a backend must explicitly report input completion. PostMessage
  uncertainty, post-call exceptions and malformed results become unknown and
  cannot trigger SendInput fallback or automatic replay. Telemetry cannot
  affect this decision.
- runtime selection: runtime version is frozen once from Runtime configuration;
  task text, model proposals and graph metadata cannot override it. On the
  normal Server path, explicit vNext selection fails before desktop mutation
  while no coordinator is injected, and never falls back to legacy in the
  same run.
- product seam: P4c defines an injectable coordinator protocol. With a frozen
  vNext selection and an explicitly injected port, ArpExecution bypasses all
  legacy handlers while reusing the same run, BUS, stop and pause owners.
  Server injects no coordinator by default and there is no live implementation,
  so the normal product path remains fail-closed.
- non-claim: this is still an injected click/double-click slice, not a complete
  ControllerPort. Type, hotkey, drag, draw and held-pointer parity, a concrete
  Visual Actor, Broker enforcement, live coordinator and Electron canary
  remain unimplemented. The temporary legacy adapter is deleted only after all
  primitives meet parity, partial-input and real E2E gates.

### D026: P5 verification is Runtime-bound and evidence-first

- status: accepted
- obligation boundary: the Actor emits only a claim. Runtime freezes the
  accepted claim fingerprint, attempt and current run-scoped evidence, invokes
  an injected read-only ObligationVerifier only when policy permits it, and
  binds the identity-free assessment into a Reducer event. Stale, malformed,
  unavailable and contradictory results fail closed.
- mission boundary: the local `DeterministicMissionCompletionVerifier` and
  canonical `EvidenceRegistry` must be injected as one pair, and the Reducer
  must read that exact registry. Runtime freezes every required obligation's
  accepted verification and evidence, appends one content-hash-bound aggregate
  evidence object, reads it back, then submits `mission_verified`.
- permissions: no verifier receives GUI mutation tools and neither verifier
  changes Mission state directly. There is no model or visual verifier call in
  this slice, and `ActionEffectVerifier` remains unimplemented.
- durability limit: the evidence-first ordering is process-local. There is no
  cross-process WAL or proven exactly-once recovery if the process terminates
  between aggregate evidence append and accepted mission event.
- rollout: P5 remains injection-only until a live vNext coordinator exists;
  unit/property tests do not establish false-positive improvement or authorize
  deletion of legacy `done`/`DoneVerifierMixin`.

### D027: click-only live canary uses the product selector without fallback

- status: accepted
- supersedes: D024–D026 的“尚无 live coordinator/model Actor/visual Verifier”仅是当时 rollout
  快照；其权限、身份、证据和删除门槛不变。
- product selection: 可见 Electron 客户端只在顶层 run request 提供
  `Legacy（默认）` / `vNext（实验）`。Runtime version 每 run 冻结一次，不进入 task
  contract、graph、Profiler 或模型 payload。Server 只为冻结为 `vnext` 的 run 注入具体
  coordinator；该 run 完全绕过 legacy handlers，任何失败都 fail closed。回滚只能新建独立
  `legacy` run，禁止 run 内 fallback。
- actor and mutation: live coordinator 只有一个模型 Visual Actor。Actor 只看无身份
  MissionCard 投影、当前权威帧和 Broker 小工具面；当前工具为 `click`、`double_click`、
  `claim_obligation_done`、`blocked`。Runtime 每回合自动捕获最新权威帧，不投影会得到
  同一 full-frame 的冗余 `observe`。Runtime 绑定全部身份，唯一
  Controller/RawDevice 链执行 click/double-click。ExecuteDesktopTask 与 live Runtime state
  同时声明 canonical `visual.interact` 和 `visual.read_text`，后者表示模型直接读取像素；
  Catalog 只以 `visual.interact` 授权 click/double-click，不能借 `visual.read_text` 隐式加载
  OCR/UIA。canonical 名称的唯一 owner 是 `vnext/capability_names.py`，兼容 re-export、
  Catalog 和 Policy 都不得按应用、测试或原句另起名称。
- verification: claim 进入独立、无 mutation 工具的视觉 ObligationVerifier。视觉许可必须
  同时满足 EvidencePolicy 与 Registry 中真实 evidence trust；模型只能返回无身份
  assessment。live provider 只要求模型分类 Runtime 编号后的条件，Runtime 机械重建 canonical
  assessment；没有可靠 provider confidence 时保存 `None`，不接受字符串或伪造数值。Runtime
  复核当前 claim/revision/frame 后生成 attested evidence。最终 mission
  仍只由 paired deterministic MissionVerifier、canonical EvidenceRegistry 和 Reducer 闭合。
- limits: `type_text`、`hotkey`、Actor 可见 `scroll`、`drag`、`draw_strokes`、held
  pointer、ROI observe、应用发现、provider/MCP、live Profiler、Planner/Inspector 尚未接入。
  legacy 仍默认，P6 继续禁用，legacy 删除数为零。
- evaluation: `precise-click` 只是随机化受控 canary 和独立 browser oracle，不是生产规则。
  2026-07-22，seed `987654`（run `arp-20260722-154335-a9e64b`，3 turns）与 seed
  `424242`（run `arp-20260722-154447-9329b4`，2 turns）已从冷启动、空 session、可见
  Electron/UIA 产品入口通过独立 trusted-browser oracle。两个 click-only grounded pass 不包含
  paired legacy、真实任务、多原语或 Verifier 校准，不能授权 P7 完成、默认切换或 legacy 删除。

### D028: coordinator `/1` freezes pause, freshness, budget and device ownership

- status: accepted
- pause contract: `desktop-runtime-coordinator-context/1` replaces context `/0` by adding a
  non-blocking `is_paused` read beside the turn-boundary blocking `wait_if_paused` gate. A pause
  that begins during a model call invalidates admission; resume starts from a new authoritative
  frame and decision, never the old proposal. The read cannot acquire or wait on an input lease.
- freshness: every live Broker reprojection computes frame freshness from an explicit positive
  max-age (currently default 30 seconds). A stale frame cannot be made admissible by model output,
  payload metadata or a cached boolean.
- budget: one full-goal ExecuteDesktopTask `max_steps` is the canonical turn-budget source.
  Session `requested_max_steps`, when present, must match; invalid/conflicting budgets fail before a
  model or mutation. The coordinator may only tighten it with its bounded local cap (default 24).
- device composition: the product composition root constructs and injects the sole
  `RawDeviceExecutor`; the coordinator rejects a missing device and never creates a fallback
  backend. Runtime, Broker and Actor remain unable to construct physical input owners.
- version audit: coordinator result and the other contracts still listed as `/0` have no
  incompatible field change and remain `/0`; version numbers are not advanced in bulk.
- admission follow-up: the duplicate legacy capability interpretation at the vNext boundary is
  superseded by D029. The pause, freshness, budget and device-ownership decisions above are unchanged.

### D029: one queue-time admission consumes one immutable runtime capability snapshot

- status: accepted
- owner: `DesktopRuntimeAdmission` is the only product-level queue-time capability admission owner.
  Server calls it once after freezing `DesktopRuntimeSelection` and, for vNext, composing the concrete
  runtime snapshot, but before run prepare, model invocation or desktop mutation. It does not choose a
  GUI route, tool, target, coordinate or recovery tactic.
- vNext inventory: the product composition root builds one immutable `RuntimeCapabilitySnapshot` from
  the canonical capability registry, ToolCatalog, Controller GUI tool names and available services.
  Constructor validation rejects catalog/controller drift, unknown services and unbacked capabilities.
  Admission mechanically projects canonical full-goal Mission requirements and fails closed on a
  missing contract, capability, service or runtime identity mismatch.
- one fact source: the exact same snapshot is consumed by queue-time admission, the safe
  `ExecutionPolicy` profile, live `RuntimeState` and `CapabilityBroker`. Its fingerprint is observable
  through a private trace sidecar; it is not model input, a mutable ledger or a second Runtime state.
- legacy preservation: the legacy branch remains inside the same admission owner and executes the
  existing static then runtime preflight in their previous order. Legacy graphs without TaskContract
  preserve their compatibility behavior. vNext never runs those legacy capability assumptions and
  never falls back to legacy after rejection.
- scope: this slice does not change prompts, model tools, Controller, GUI actions, done/Verifier
  semantics, front end or task-contract schema. It does not make Broker globally enforced, expand the
  click-only Catalog or satisfy P7/P8/P9. P6 remains disabled. The next capability gate is incremental
  multi-primitive parity and same-entry evidence, not application- or benchmark-specific rules.

### D030: scroll extends the same atomic mutation owner one primitive at a time

- status: accepted
- supersedes: D027 and D029 remain authoritative for product selection, fail-closed behavior,
  identity, verification, queue-time admission and historical click evidence. This decision only
  supersedes their then-current click-only tool-surface snapshot.
- actor surface: the live Catalog/Broker/Runtime snapshot now projects `click`, `double_click`,
  `scroll`, `claim_obligation_done` and `blocked`. Actor-visible scroll args are strictly
  `{image_x, image_y, direction: "up" | "down", notches: 1..12}`; direction means revealing content
  above or below the viewport. The old signed `amount` is rejected. `scroll` is authorized by the
  same canonical `visual.interact` capability; no application, task phrase, benchmark, layout,
  coordinate rule, system prompt or additional model call was added. The ToolCatalog/Runtime snapshot
  fingerprint changes because this generic tool schema intentionally changed.
- transaction owner: `AtomicDesktopController` is the single Controller transaction for click,
  double-click and scroll. It consumes the same Runtime permit, authoritative frame pin, target and
  coordinate mapping, commit-time identity checks, physical lease and post-action evidence path.
  The Controller mechanically maps `up -> +notches` and `down -> -notches` to the existing RawDevice
  convention. The final target guard runs immediately before the physical mouse-down or wheel input.
- physical mechanism: vNext scroll uses one unqueued admitted physical SendInput backend. The raw
  receipt preserves `true/false/null` mechanical completion; exceptions or unprovable execution are
  `unknown`. It never switches to a second mechanism or automatically replays an uncertain mutation.
  Legacy scroll compatibility remains separate and unchanged.
- evaluation: the generic randomized `scroll-reveal` target and independent oracle verify trusted
  wheel provenance, direction, actual `scrollTop` movement, target-pane coordinates and complete
  target visibility. They are eval-only and are not read by production Runtime, Actor, Broker or
  Controller. With the final direction/notches schema, down seed `20260722` (run
  `arp-20260722-182113-91882e`) and up seed `20260723` (run
  `arp-20260722-181904-2b30b1`) passed the visible Electron/UIA, cold-start, empty-session entry and
  independent oracle. Earlier signed-schema failures remain recorded: ambiguous sign/actor error,
  premature wheel sampling followed by oscillation, and an up case repeating the wrong direction for
  24 turns. Intermediate signed-schema passes are not the primary evidence for the final contract.
- limits: `type_text`, `hotkey`, `drag`, `draw_strokes`, held pointer and ROI observe still lack live
  parity. Broker enforce/necessary-tool recall, P7, default selection and legacy deletion gates are
  not met; P6 remains disabled and no legacy product responsibility is deleted. Explicit no-progress
  feedback for an unchanged post-scroll frame remains a separate recovery slice.
