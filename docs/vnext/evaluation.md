# vNext 评测与切换门槛

vNext 的目标不是让架构图更整齐，而是在相同模型、相同电脑、相同客户端入口和相同任务下，提高 grounded GUI 完成率、降低无关认知负担，并减少 false done。所有结论必须来自外部证据。

## 1. 可证伪假设

| 编号 | 假设 | 主要指标 |
| --- | --- | --- |
| H1 | 可重放 MissionProjection 外置主线后，Actor 更少遗忘长任务义务 | obligation coverage、遗漏/重复义务、mixed grounded pass |
| H2 | Task Profiler + Execution Policy 比固定全量架构更少过度/不足编排 | over-profile、under-profile、role calls、time-to-first-action、coverage |
| H3 | Broker 每回合小工具面让参数更稳定、决策更快 | tool count/schema chars、invalid args、model elapsed、necessary tool recall |
| H4 | claim 与独立 Verifier 分离后 false done 降低 | verifier reject、false-positive completion、重复 claim |
| H5 | Controller 不做语义改点后，动作可解释性和坐标一致性提高 | target adjustment、frame mismatch、oracle action validity |
| H6 | Planner/Inspector 按事件调用比常驻多 Agent 更高效 | role call count、额外 latency、held-out pass uplift |
| H7 | Runtime envelope 绑定身份比模型复制 ID 更少 stale/串线错误 | identity mismatch、stale decision、invalid evidence reference |
| H8 | 工具/骨架优先整改比 prompt 规则更能跨任务泛化 | held-out uplift、prompt chars、专属规则审计 |
| H9 | 同模型不同角色的隔离降低自我确认，但同源错误仍可测 | Actor/Verifier error correlation、false positive、evidence diversity |
| H10 | 单一冻结请求重试 owner 比 SDK/调用点隐式重试更少重复请求与不确定 action replay | request fingerprint、attempt kind、late result、duplicate mutation |

任一新角色或模块必须对应可证伪假设。不得先实现，再为它寻找用途。

## 2. P0 指标

### 2.1 每次模型角色调用

```text
runtime_version
mission_id / revision / active_obligation_id
model_role
decision_id / purpose / request_fingerprint
model_name / provider / thinking_policy
input_schema_version / output_schema_version
prompt_context_chars
model_tool_count / model_tool_schema_chars
frame_id / epoch / capture_scope（若适用）
model_elapsed_ms
request_retry_count
attempt_count / attempt kind / error kind / retryable
followup_kind / late_result_discarded
validation_failure_count
```

### 2.2 Task Profiler / Execution Policy

```text
profile_call_count
profile_dimensions
recommended_depth
minimum_depth
effective_depth
requested_services
effective_services
profile_confidence
execution_policy_override
execution_policy_override_reason
profile_elapsed_ms
profile_token/cost estimate
over_profile
under_profile
profile_revision_count
```

### 2.3 每个 Actor 回合

```text
mission_card_chars
broker_projection_id
effective_model_tools
active_capability_grants
frame_id / epoch / target_binding_id
pointer_lease_state
actor_proposal_kind
actor_proposal_validation
runtime_envelope_id
invalid_tool_argument_count
duplicate_action_count
no_progress_streak
frame_refresh_count
target_adjustment_px
controller_elapsed_ms
time_to_first_action_ms
```

### 2.4 每个 obligation

```text
activation_count
actor_turn_count
action_count
capability_request/grant/deny_count
claim_count
verification_count
verification_kind
missing_condition_count
replan_request_count
planner_call_count
inspector_call_count
failure_budget_consumed
elapsed_ms
verified / blocked / superseded
```

### 2.5 每个 mission

```text
required_obligation_count
verified_obligation_count
obligation_coverage_ratio
artifact_identity_errors
recipient/target binding errors
false_done_count
external_oracle_pass
total_model_calls
total_model_elapsed_ms
total_controller_elapsed_ms
wall_elapsed_ms
stop/pause/cancel cleanup result
```

“像素变化”“工具 ok”“模型说完成”不进入 grounded pass 统计。

## 3. 对照实验

legacy 与 vNext A/B 固定：

- 同一模型/provider；
- 同一 thinking 策略或记录差异；
- 同一 max steps/time/cost budget；
- 同一应用版本、分辨率、DPI、窗口初态；
- 同一任务原文；
- 同一 cold-start/session policy；
- 受控场景相同 seed；
- 同一 external oracle；
- 不给 vNext 额外预置 obligation、profile、route、提示或工具。

至少比较：

```text
legacy
vnext-fixed-standard
vnext-profiler-shadow
vnext-profiler-enforced
```

Profiler shadow 可以记录建议，但不能改变执行。`legacy/vnext` runtime version 必须显式选择；Task Profiler 只决定 vNext 内部 execution depth，不得暗中切产品 runtime。

真实桌面难以复现时标记 paired/unpaired。一个任务需多个 seed/初态后再报告趋势；单次成功只保留样本。

## 4. 测试入口

所有 GUI E2E 从可见 Electron 客户端提交：

```text
Electron task input
  -> explicit runtime_version
  -> Goal Compiler
  -> Task Profiler / Execution Policy（vNext）
  -> Mission / Broker / Actor
  -> exact outer/child run binding
  -> trace
  -> independent oracle
```

直接调用 Reducer、Profiler、Policy、Broker、Actor 或 `/api/v2` 只是 unit/integration。vNext不能获得额外截图、预置计划、特殊 max steps 或后门工具。

当前可见 Electron 客户端提供按 run 的 `Legacy（默认）` / `vNext（实验）` 选择器。冻结为 `vnext` 时，Server 注入增量 live coordinator，当前工具面为 `click/double_click/scroll/claim/blocked`；`ArpExecution` 完全绕过 legacy handlers，失败不得在同一 run 回退。2026-07-22，`precise-click` seed `987654`（run `arp-20260722-154335-a9e64b`，3 turns）、seed `424242`（run `arp-20260722-154447-9329b4`，2 turns）与 capability-admission 收敛后的 seed `135791`（run `arp-20260722-165702-086da6`，2 turns）已从冷启动、空 session、可见 Electron/UIA 入口通过独立 trusted-browser oracle。suite report 为 `output/playwright/client-visual-e2e/20260722-154302-suite/suite-report.json`、`output/playwright/client-visual-e2e/20260722-154415-suite/suite-report.json` 与 `output/playwright/client-visual-e2e/20260722-165627-suite/suite-report.json`。这三例只进入受控点击 grounded-pass 记录。

ActionEffect 接线前，最终 direction/notches scroll 合同也从同一入口获得一对双向 grounded pass：down seed `20260722`，run `arp-20260722-182113-91882e`，report `output/playwright/client-visual-e2e/20260722-182040-suite/suite-report.json`；up seed `20260723`，run `arp-20260722-181904-2b30b1`，report `output/playwright/client-visual-e2e/20260722-181825-suite/suite-report.json`。随机化 oracle 独立核对 trusted wheel、方向、实际滚动、pane 坐标与目标完全显露。`20260722-174712-suite`、`20260722-175451-suite`、`20260722-180448-suite` 的符号含糊、oracle 过早采样/振荡与旧 signed 合同反向失败仍保留为失败记录；中间 signed-schema down pass 不作为最终合同的主门槛证据。protocol、fake port、单元测试或 legacy shadow 仍不得计入 grounded pass。

ActionEffect 接线后的同入口复测必须单独报告，不能覆盖上述历史基线。首次 down run
`arp-20260722-213219-2ca40d`（report
`output/playwright/client-visual-e2e/20260722-213141-suite/suite-report.json`）由独立 oracle 证明 7 次
真实 wheel、总位移 `+693`，但 trace 把全部动作记为 `unchanged`。原因是立即 post-action 截图早于
浏览器异步完成滚动；这不是 ActionEffect 成功证据。通用 post-action bridge 随后改为在 raw receipt
确认输入完成后做一次有界 120 ms settle，再捕获唯一 post frame。

最终 settle 实现下的冷启动、空 session、可见 Electron/UIA 结果为：

- down seed `20260722`，run `arp-20260722-215529-8af388`，report
  `output/playwright/client-visual-e2e/20260722-215454-suite/suite-report.json`：独立 oracle 通过，13 次
  wheel、总位移 `+693`；trace 为 11 次 `changed` 和 2 次连续 `unchanged`；
- precise-click seed `135791`，run `arp-20260722-215751-b82e5c`，report
  `output/playwright/client-visual-e2e/20260722-215719-suite/suite-report.json`：独立 oracle 通过，3 次
  click，effect 为 `changed/unchanged/unchanged`，再次证明 frame equality 不能解释成语义失败；
- up seed `20260723`，run `arp-20260722-220002-9ddc79`，report
  `output/playwright/client-visual-e2e/20260722-215930-suite/suite-report.json`：24 回合耗尽后失败，初始
  `scrollTop=849`，最终净位移为错误方向 `+142`，目标仍不可见；trace 最长连续 12 次 `unchanged`，
  Controller 的 signed 映射与 Actor proposal 完全一致，因此当前失败 owner 是 Actor 未利用中性反馈
  改变策略，而不是输入方向或 ActionEffect 身份错误。

用户在 run `arp-20260722-214805-c9faaf` 期间切换界面，导致 target identity unavailable；该 run 明确
标记为外部干扰并排除，不计入产品通过或失败。最终 2 pass / 1 fail 的单 seed 结果不能换算成功率，
也不能证明 ActionEffect 提升了恢复能力。

早期真实尝试分别暴露了冗余 full-frame `observe` 循环、Verifier provider 的字符串 confidence 与条件文本复制、harness `SW_RESTORE` 引起的目标几何变化，以及 page-ready 后复用旧几何。修复位于通用 Catalog/Runtime、Verifier schema 和 harness 状态读取层，没有把 case、seed、布局、坐标或成功条件加入生产 Agent。

冷启动在整个 run禁用长期 Memory/Skill，同时保留同一 scenario 明确的 typed artifact dependency。多 turn 中“刚刚”“继续”必须由当前 scenario 的 evidence/artifact ref 解析。

## 5. 测试矩阵

### 5.1 合同与纯状态

- MissionDefinition/Event/Projection round-trip；
- Reducer deterministic replay；
- dependency DAG/property tests；
- stale revision reject；
- claim 不等于 verified；
- ObligationSpec 无 runtime status；
- lifecycle state 只有一个权威 map；
- derived verified/pending集合不序列化为第二真相；
- evidence scope、artifact hash和recipient identity；
- Planner patch 不变量；
- BrokerProjection exhaustive/property test；
- read-only角色mutation权限为零；
- required obligations verified 只推导 `awaiting_mission_verification`；只有带独立 mission
  verification identity 和当前 run-scoped evidence 的 accepted `mission_verified` 才能终结 mission。

### 5.2 Runtime envelope

- 模型proposal没有mission/revision/obligation/frame/evidence ids；
- Runtime绑定当前身份；
- 模型字段不能覆盖envelope；
- stale revision/frame/target reject；
-跨obligation proposal reject；
- evidence scope不能串用旧run/旧artifact；
- late model result不能写入新revision；
- proposal replay在相同envelope下可解释。
- coordinator context `/1` 的 `is_paused` 为非阻塞 read，模型期间 pause 使 admission 为零；
- resume 使用新 frame/new decision，不恢复旧 proposal；
- frame freshness 由显式 max-age（当前默认 30 秒）重算，过龄帧 backend 调用为零；
- canonical ExecuteDesktopTask `max_steps` 与 session budget 冲突时模型调用为零；
- effective turn budget 不超过 canonical `max_steps` 和 coordinator local cap；
- RawDevice 只由 product composition root 注入，缺失依赖无 fallback/backend 调用。

### 5.3 Task Profiler / Execution Policy

- 简单短任务不会无故启用Planner/Inspector/多obligation；
- 显式跨应用工件/交付/审批不会被under-profile；
- task paraphrase保持profile语义稳定；
-仅改变应用名不会自动切专用架构；
- Profiler不输出按钮、工具、坐标或runtime version；
- malformed/unavailable/low-confidence使用safe standard；
- Execution Policy能因安全和provider可用性覆盖建议；
- profile revision只在用户目标修订或已验证事实冲突时发生；
- direct/standard/orchestrated升降档可审计。

### 5.4 Controller 与基础工具

- frame/epoch/HWND过期fail closed；
- 每个坐标空间边界只映射一次并保留provenance；
- click/type/scroll/drag/draw/held pointer cleanup；
-滚轮方向/目标窗口正确；
-多关键点drag按序插值；
- `observe(image_region)` crop与后续坐标一致；
-动画/无关变化不伪造进展；
- stop/pause/cancel释放pointer/key；
- AppResolver优先已运行/可恢复实例；
- Controller无OCR snap、备用按钮或fallback cascade。
- strict target mismatch在零backend调用时拒绝；
- backend异常、非法回执和PostMessage不确定结果进入unknown且不重放；
- Raw receipt的机械三态与`input_completed=true/false/null`一致；
- legacy scroll正常输出保持兼容，安全修正只禁止不确定后的第二种输入机制。

以上 RawDevice 项大多仍只有 unit/characterization 证据。P4 live 路径已用同一个 `AtomicDesktopController` 组装 click/double-click/scroll，覆盖 Runtime permit、双段坐标映射、commit-time Windows identity、紧邻物理输入的 final target guard、post-action authoritative frame/evidence 以及 unknown-no-replay。scroll 只走 unqueued admitted physical SendInput backend，不在不确定结果后切换机制或重放；Actor-visible direction/notches 由 Controller 机械映射到 RawDevice signed amount。三个随机 seed 的历史 Electron/UIA click 与 ActionEffect 接线前最终 scroll 合同的一对 down/up seed 已通过独立 oracle；ActionEffect 最终代码的同入口复测则是 down/click 通过、up 失败。没有 paired legacy 对照，也不代表多原语门槛通过；`type_text`、`hotkey`、`drag`、`draw_strokes`、held pointer 和 ROI observe 仍没有 live parity。权威前后帧的通用 `changed/unchanged/unknown` 与 replay-derived per-obligation streak 已进入 Actor 的中性 `unchanged_frame_streak` 反馈。

ActionEffect 评测必须把 frame fact 与最终 oracle 分开报告，至少覆盖：静止 no-op、滚动边界、
ambient animation、视频、短暂动画、错误 target binding 和不可比较帧。只有解码后像素完全一致才记
`unchanged`；`changed` 既不证明动作因果，也不计作 grounded pass。动态全帧变化可能持续清零 streak
并掩盖 no-op，这是已知召回限制，不得用应用规则、OCR/UIA/MCP fallback 或自动反向动作掩盖。

### 5.5 Broker

- necessary tool recall；
- irrelevant tool rate；
-相同状态projection稳定；
- target/pointer/artifact/adapter/MCP变化响应；
- app/test/prompt leakage为零；
- provider route与GUI Actor mutation互斥；
- capability request grant/deny/expiry正确；
-普通回合通常小于十个工具；
- pointer lease严格小工具面。

### 5.6 受控视觉场景

-点击/输入；
-上下滚动探索；
-静态拖放；
-多转折通道；
-按下后目标出现的跨帧移动；
-限定区域笔画；
-动态/动画背景；
-相似候选/小控件；
-弹窗、遮挡、抢焦点；
-外部参与者变化/persistent wait。

使用trusted pointer/browser oracle。任务prompt不提供路线。

### 5.7 真实任务与 held-out

原始11个任务继续作为黑盒探针，覆盖应用发现、多步导航、工件、交付、自绘/游戏式交互、专业界面和持续会话。生产系统不能知道题号或原句。

组合/held-out至少包括：

- drawing -> capture -> recipient -> send -> delivery；
- media -> search -> play -> system setting；
- office artifact -> conversion -> delivery；
- 多应用切换后回到已有窗口；
- 用户中途修改目标；
- Verifier reject后重新观察；
-未参与实现调试的新应用/布局/随机规则。

至少一半发布判断来自held-out或随机化场景。

## 6. Profiler评测

P2a 已完成纯合同、确定性 Policy 与可注入 shadow 评测接口；P2b 已把真实
Profiler 作为默认关闭、每 run 至多一次的 trace-only shadow 接入同一产品入口。
尚无规模化 paired/held-out 数据，因此当前只能证明调用边界、malformed/低置信
fallback、provider/capability 收紧和确定性 replay，不能证明真实分类质量或 GUI
任务成功。启用 shadow 后必须另行报告 over/under-profile 与延迟/成本。

### 6.1 Over-profile

任务实际可由direct/standard完成，却启动过多obligation、Planner、Inspector或昂贵Verifier。影响指标：

- time-to-first-action；
-额外模型调用/图片请求；
- wall latency/cost；
-角色意见冲突；
-成功率是否反而下降。

### 6.2 Under-profile

任务需要跨应用、artifact identity、外部等待、审批或独立验证，却被裁成过浅结构。影响指标：

- obligation coverage缺失；
- artifact/recipient错误；
- false done；
-运行中被迫profile revision；
-用户要求遗漏。

### 6.3 评价标准

Profiler成功不是“解释听起来合理”，而是：

- grounded pass/coverage提升；
- over/under-profile下降；
-总成本与延迟可接受；
-改写/held-out稳定；
-不产生应用专用路由。

## 7. Verifier评测

当前实现边界：P5 已把 deterministic ActionEffectVerifier、隔离只读的视觉 ObligationVerifier 和本地 `DeterministicMissionCompletionVerifier` 接到 live MissionRuntime。ActionEffect 只比较已由 Registry 核对的完整权威前后帧，并随同一个 accepted transition event 重放；视觉模型只分类 Runtime 编号后的验收条件，Runtime 机械重建 canonical assessment，provider 无可靠数值置信度时保存 `confidence=None`。Runtime 同时核验 EvidencePolicy、真实 evidence trust、claim/revision 与当前权威 frame，随后才写入 attested evidence。P5b 将 MissionVerifier 与 Reducer 使用的同一 `EvidenceRegistry` 成对绑定，并以 aggregate evidence append/read-back -> `mission_verified` 的顺序机械关闭 mission。ActionEffect 同入口首轮复测保留了 down/click 通过和 up 失败，但尚不足以校准动态画面、false-positive/false-negative 或 Actor/Verifier 错误相关性；当前也仍无跨进程 WAL/exactly-once，因此 P5 产品门槛未达。

单独校准：

- true positive；
- true negative；
- false positive（最危险）；
- false negative；
- unavailable/malformed。

测试包含：

- Actor自信但画面相反；
-局部像素变化但业务未完成；
-正确artifact预览但未发送；
-发送成功但recipient错误；
- deterministic/oracle与视觉描述冲突；
-瞬态成功后当前状态变化；
-相同evidence重复claim。

### 7.1 同模型角色相关错误

第一版允许同一模型承担Actor和Verifier，但必须测：

- 对同一错误场景，Actor错误与Verifier误接受的相关率；
-相同压缩图 vs 不同证据视角（高清终态、前后帧、oracle/receipt）；
-共享上下文 vs 独立上下文；
-同模型 vs 可选不同模型；
- false-positive变化与额外成本。

不能把“不同角色名称”当成真正独立性证明。

## 8. Planner 与 Inspector评测

当前 P6 保持禁用：没有至少两个 held-out 任务族的 external-oracle uplift 和成本对照，因此不实现或调用 Planner/Inspector。

Planner：

- obligation coverage提升；
- dependency/artifact错误下降；
- repeated local failure后合法patch；
-不输出GUI步骤；
-普通单obligation调用数为零；
-额外成本可接受。

Inspector：

- 多种随机通道/图表/小控件的grounded uplift；
- Actor仍产生最终动作；
- Inspector无tool call/坐标动作；
- frame/ROI一致；
-相对Actor自行`observe(image_region)`有稳定增益；
-不能只在`game-drag-v2`一个seed有效。

## 9. 失败归因

首个错误owner：

```text
entrypoint_or_harness
goal_compile
task_profiler
execution_policy
mission_reducer
capability_broker
app_or_window_resolution
frame_or_coordinate
model_transport_runtime
controller_or_raw_input
post_action_observation
visual_actor_reasoning
runtime_envelope
verifier
planner
inspector
external_environment
```

只有保存模型实际看到的输入、输出proposal、Runtime envelope、BrokerProjection、TransitionRecord与oracle，才能归因模型能力。缺证据时不能直接改prompt。

## 10. 阶段门槛

### Mission内核

- deterministic replay；
- 100% state mutation经Reducer；
-一个lifecycle真相源；
- illegal transition fail closed；
-无应用/测试分支。

### Profiler/Policy shadow

-所有proposal/override可解释；
- over/under-profile有基线；
-无GUI步骤和runtime切换；
- fixed-standard对照存在。

### Broker shadow

-所有projection有trace；
-状态组合覆盖；
-无关键词泄漏；
-schema明显变小且necessary tool不缺失。

P3a 已覆盖 strict schema、状态组合、route 互斥、grant scope、artifact identity、stale frame、pointer lease 和确定性 replay 的纯测试。它不等价于上述产品门槛：在真实 Electron legacy 回合尚未记录 projection，且没有完整 held-out relevance oracle 前，necessary-tool recall 记为缺失值，不能用 actual legacy tool 命中率代替，也不能开启 enforce。

Broker enforce 的数值门槛采用 fail-closed 标准：只有具备完整独立 relevance oracle 的 turn 才进入计算，controlled、mixed 与 held-out 各分组的 required tool group recall 都必须为 100%，并且 recovery、pointer release、stop/cancel 与证据链工具不得出现一次漏失。未标注样本、样本规模和覆盖到的状态组合必须同时报告；actual legacy tool 是否被允许仅是诊断项，不是 recall oracle。

### 模型调用与重试合同

- WinPilot product package 的 provider SDK 隐式重试为零；
- 同一 request retry 的 decision id、role、purpose 和 fingerprint 完全一致；
- adapter prepare 只发生一次，transport 每次收到冻结请求的新副本；
- poll 不提交第二个请求，redecision 使用新 decision id；
- response parse/schema/semantic validation failure 不触发 transport retry；
- canceled/timed-out late result 不能进入 proposal、tool dispatch 或新 revision；
- mutation outcome unknown 不自动 replay，且 request retry 不能被统计为 action replay；
- 精确函数调用栈 AST guard 阻止 product package 新增绕过 Runtime 的 Chat transport，并拒绝 Responses transport；
- 每个逻辑调用都能区分 semantic call count 与 transport attempt count。

当前合同、主 Agent 请求和精确 AST guard 已存在；Profiler stop_event 也已从 ArpExecution 经
shadow hook/runtime/provider 传到 ModelCallRuntime 并有测试。仍缺的是全角色逐 attempt
sidecar，以及 legacy VLM helper 的 stop_event seam。因此“全角色可观测/可取消”门槛仍未达成；
缺失数据保持 `None`，不能以零代替。外围 `python/providers/` 不进入 product-package 完成分母，
必须在 P9 报告其生产可达性和删除/迁移结论。

### Actor受控执行

-只有Actor提出GUI mutation；
-模型proposal无Runtime身份；
-每笔动作绑定envelope/FrameState；
-受控任务不低于legacy；
-保留真实oracle失败。

当前已有增量 live coordinator、具体模型 Visual Actor、`click/double_click/scroll/claim/blocked` 小工具面和共享 `AtomicDesktopController` 链；客户端可显式选择实验 runtime，legacy 仍默认。历史三个 `precise-click` 与 ActionEffect 接线前最终 scroll 合同的一对 down/up `scroll-reveal` 已通过独立 trusted-browser oracle；ActionEffect 最终代码的首轮同入口复测为 down/click 通过、up 失败。没有 paired legacy、真实任务、多布局、其余原语或规模化 held-out 对照，所以该门槛未达。

queue-time capability admission 门槛已经结构性闭合：同一冻结 runtime 只有一个
`DesktopRuntimeAdmission` owner；vNext 从不可变 `RuntimeCapabilitySnapshot` 核对 canonical
capability/service 与 Catalog/Controller parity，同一 snapshot 继续供 `ExecutionPolicy`、live
`RuntimeState` 和 Broker 使用；legacy 仍保留原顺序的 static/runtime preflight。后续回归必须持续覆盖
两边 missing-capability、snapshot/runtime identity、Catalog/Controller drift，以及 admission 失败时
prepare/model/Controller/legacy fallback 调用数为零。仅有 snapshot 单元测试仍不能替代真实入口证据。

下一门槛是扩大通用 ActionEffect/unchanged-frame 的随机 seed、滚动边界、动态画面、多布局与 paired 覆盖，量化 Actor 是否真正利用中性反馈，而不是扩写 prompt 或增加方向规则：`type_text`、`hotkey`、`drag`、`draw_strokes`、held pointer 和 ROI observe 必须逐项进入同一 snapshot/Catalog/Broker/Controller owner，
并通过必要工具 recall、unknown-no-replay、负向/property 与同入口 Electron oracle。完成这些之前，
现有受控 canary 仍不参与默认切换判定。

### Verifier

-claim不能直接完成；
-deterministic/oracle优先；
-false-positive低于legacy；
-verifier failure不污染state；
-同模型相关错误已报告。

当前还能证明视觉 assessment 的角色隔离、无 mutation 工具、真实 evidence trust 许可与 Runtime attestation 合同；现有受控 Electron pass 只证明真阳性闭环可达，不能证明 false-positive 低于 legacy 或同模型错误已校准。

### Planner/Inspector

-只在预定义事件；
-权限零mutation；
-至少两个任务族held-out uplift；
-收益大于成本。

当前无 A/B 证据，因此两者均禁用。

### 默认切换

-受控、真实、mixed、held-out均有当前报告；
-grounded pass不低于legacy，false done更低；
-模型/tool/context负担下降或代价清晰；
-Profiler没有明显over/under-profile；
-停止、暂停、取消、外部等待和输入清理无回归；
-legacy删除计划可执行，不是无限双栈。

P7 已开始并获得三个随机 seed 的历史点击 grounded pass 与 ActionEffect 接线前最终 scroll 合同的一对双向 grounded pass；ActionEffect 最终代码的首轮复测为 2 pass / 1 fail。paired legacy、11 个真实任务、mixed/held-out、多布局和其余原语矩阵均未完成，因此 P7 仍未达标；P8 默认切换和 P9 净删未开始，legacy 产品职责删除数为 0。这些受控通过不能越过 Verifier 校准、Profiler/Broker 或完整多原语门槛。

## 11. 报告格式

每次A/B至少包含：

```text
commit / branch / runtime_version
model/provider/thinking config
client entry proof
cold-start/session proof
task set/held-out policy
environment/app versions
TaskProfileProposal / ExecutionProfile / policy overrides
per-case outer/child run and trace binding
independent oracle
p50/p95 metric summary
failure owner distribution
verifier confusion matrix
Actor/Verifier error correlation
planner/inspector invocation counts
production line delta / legacy deletion progress
known limitations
```

不得删除失败run、用人工感觉替换oracle，或把入口成功、工具执行、像素变化、Actor claim写成任务通过。
