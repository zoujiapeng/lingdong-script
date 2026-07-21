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

任一新角色或模块必须对应可证伪假设。不得先实现，再为它寻找用途。

## 2. P0 指标

### 2.1 每次模型角色调用

```text
runtime_version
mission_id / revision / active_obligation_id
model_role
model_name / provider / thinking_policy
input_schema_version / output_schema_version
prompt_context_chars
model_tool_count / model_tool_schema_chars
frame_id / epoch / capture_scope（若适用）
model_elapsed_ms
request_retry_count
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
- mission completion只由required obligations verified推导。

### 5.2 Runtime envelope

- 模型proposal没有mission/revision/obligation/frame/evidence ids；
- Runtime绑定当前身份；
- 模型字段不能覆盖envelope；
- stale revision/frame/target reject；
-跨obligation proposal reject；
- evidence scope不能串用旧run/旧artifact；
- late model result不能写入新revision；
- proposal replay在相同envelope下可解释。

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
- image coordinate只映射一次；
- click/type/scroll/drag/draw/held pointer cleanup；
-滚轮方向/目标窗口正确；
-多关键点drag按序插值；
- `observe(image_region)` crop与后续坐标一致；
-动画/无关变化不伪造进展；
- stop/pause/cancel释放pointer/key；
- AppResolver优先已运行/可恢复实例；
- Controller无OCR snap、备用按钮或fallback cascade。

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

P2a 已完成纯合同、确定性 Policy 与可注入 shadow 评测接口；真实 Profiler
模型调用尚未接入。因此当前结果只能证明 malformed/低置信 fallback、provider
和 capability 收紧、over/under-profile 判定、确定性 replay，以及对任务改写和
应用标签变化的结构稳定性，不能证明真实模型分类质量或 GUI 任务成功。

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

### Actor受控执行

-只有Actor提出GUI mutation；
-模型proposal无Runtime身份；
-每笔动作绑定envelope/FrameState；
-受控任务不低于legacy；
-保留真实oracle失败。

### Verifier

-claim不能直接完成；
-deterministic/oracle优先；
-false-positive低于legacy；
-verifier failure不污染state；
-同模型相关错误已报告。

### Planner/Inspector

-只在预定义事件；
-权限零mutation；
-至少两个任务族held-out uplift；
-收益大于成本。

### 默认切换

-受控、真实、mixed、held-out均有当前报告；
-grounded pass不低于legacy，false done更低；
-模型/tool/context负担下降或代价清晰；
-Profiler没有明显over/under-profile；
-停止、暂停、取消、外部等待和输入清理无回归；
-legacy删除计划可执行，不是无限双栈。

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
