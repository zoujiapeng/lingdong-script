# vNext 评测与切换门槛

vNext 的目标不是让架构图更整齐，而是在相同模型、相同电脑、相同客户端入口和相同任务下，提高 grounded GUI 完成率、降低无关认知负担，并减少 false done。所有结论必须来自外部证据。

## 1. 核心假设

| 编号 | 假设 | 主要指标 |
| --- | --- | --- |
| H1 | Mission Ledger 外置主线后，Actor 更少遗忘长任务义务 | obligation coverage、遗漏/重复义务、mixed grounded pass |
| H2 | Broker 每回合只暴露小工具面后，模型更快且工具参数更稳定 | tool count/schema chars、invalid args、model elapsed |
| H3 | claim 与独立 Verifier 分离后，false done 降低 | verifier reject、false-positive completion、重复 claim |
| H4 | Controller 不做语义改点后，动作可解释性和坐标一致性提高 | target adjustment、frame mismatch、oracle action validity |
| H5 | Planner/Inspector 按事件调用比常驻多 Agent 更高效 | role call count、额外 latency、held-out pass uplift |
| H6 | 工具/骨架优先整改比增加 prompt 规则更能跨任务泛化 | 第二任务族与 held-out uplift、prompt chars、专属规则审计 |

任一新角色或模块必须对应一个可证伪假设。不得先实现，再为它寻找用途。

## 2. P0 指标

### 2.1 每回合

```text
runtime_version
mission_id / revision / active_obligation_id
model_role
model_name / thinking_policy
model_tool_count
model_tool_schema_chars
prompt_context_chars
mission_card_chars
frame_id / epoch / capture_scope
target_binding_id
pointer_lease_state
invalid_tool_argument_count
duplicate_action_count
no_progress_streak
frame_refresh_count
verifier_reject_count
target_adjustment_px
model_elapsed_ms
controller_elapsed_ms
time_to_first_action_ms
```

### 2.2 每个 obligation

```text
activation_count
actor_turn_count
action_count
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

### 2.3 每个 mission

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

## 3. 对照实验规则

legacy 与 vNext A/B 必须固定：

- 同一模型和 provider 设置；
- 同一 thinking 策略或明确记录差异；
- 同一 max steps/time budget；
- 同一应用版本、桌面分辨率、DPI 与窗口初始状态；
- 同一任务原文；
- 同一 cold-start/session policy；
- 受控场景使用相同 seed；
- 同一 external oracle；
- 不给 vNext 额外内部 obligation、route、提示或工具。

真实桌面状态很难完全复现时，报告必须标记 paired/unpaired，不把不同初态直接计算为架构提升。

一个任务应至少重复多个 seed/初态后再报告趋势。单次成功只保留为样本，不能声称成功率。

## 4. 测试入口

所有 GUI E2E 继续从可见 Electron 客户端提交：

```text
Electron task input
  -> compile
  -> run with explicit runtime_version
  -> exact outer/child run binding
  -> trace
  -> independent oracle
```

直接调用 Reducer、Broker、Actor 或 `/api/v2` 只属于 unit/integration。vNext 不能因为旁路建设而获得额外截图、预置计划、特殊 max steps 或后门工具。

冷启动必须在整个 run 禁用长期 Memory/Skill，同时保留同一 scenario 明确的 session artifact dependency。独立 case 先清 session；多 turn case 必须通过 typed artifact/fact ref 解析“刚刚”“继续”，不能读全局最近文件。

## 5. 测试矩阵

### 5.1 合同与纯状态

- MissionState schema round-trip；
- Reducer deterministic replay；
- dependency DAG/property tests；
- stale revision reject；
- claim 不等于 verified；
- evidence scope 与 artifact hash；
- Planner patch 不变量；
- BrokerProjection 对状态组合的 exhaustive/property test；
- read-only 角色工具权限为零 mutation；
- mission completion 只能由 required obligations verified 推导。

### 5.2 Controller 与基础工具

- frame/epoch/HWND 过期时 fail closed；
- image coordinate 到物理坐标只映射一次；
- click/type/scroll/drag/draw/held pointer 的 input cleanup；
- 滚轮方向和目标窗口正确；
- 多关键点 drag 按顺序插值；
- `observe(image_region)` crop 与后续坐标一致；
- 动画/无关区域变化不伪造目标进展；
- stop/pause/cancel 释放 pointer/key；
- AppResolver 优先发现已运行/可恢复实例；
- Controller 不出现 OCR snap、备用按钮或 fallback cascade。

### 5.3 受控视觉场景

- 点击与输入；
- 上下滚动探索；
- 静态拖放；
- 多转折通道；
- 按下后目标出现的跨帧移动；
- 限定区域笔画；
- 动态/动画背景；
- 相似候选与小控件；
- 意外弹窗、遮挡和窗口抢焦点；
- 外部参与者变化与 persistent wait。

受控场景使用 trusted pointer/browser oracle。页面中的可见规则可以变化，任务 prompt 不提供路线。

### 5.4 真实任务

原始 11 个任务继续作为黑盒探针，覆盖：

- 应用发现、已有实例和登录态；
- 多步应用内导航；
- 绘制、工件捕获和跨应用交付；
- 错误/重复回复；
- 本地应用与网页候选；
- 办公 artifact dependency；
- 自绘/游戏式视觉交互；
- 抓包等复杂专业界面；
- 持续外部会话。

生产系统不能知道题号或原句。测试报告按通用失败类别归因，不按“为第 N 题加修复”组织。

### 5.5 组合与 held-out

- drawing -> capture -> recipient -> send -> delivery；
- media -> search -> play -> system setting；
- office artifact -> conversion -> delivery；
- 多应用切换后回到已有窗口；
- 用户中途修改目标；
- 验证失败后重新观察；
- 未参与实现调试的新应用、新布局和随机视觉规则。

至少一半发布判断应来自 held-out 或随机化场景，避免通过反复观察固定 11 题把测试集变成训练集。

## 6. Verifier 评测

Verifier 需要单独校准，不只看整体 pass：

- true positive：条件确实满足且接受；
- true negative：条件缺失/矛盾且拒绝；
- false positive：最危险，未完成却接受；
- false negative：完成却拒绝，影响效率但不能通过放宽规则掩盖；
- unavailable/malformed：fail closed，状态不前进。

测试集应包含：

- Actor 自信但画面相反；
- 局部像素变化但业务条件未满足；
- 正确 artifact 预览但尚未发送；
- 发送成功但 recipient 错误；
- deterministic hash/oracle 与视觉描述冲突；
- 瞬态成功后当前状态又变化；
- 相同证据重复 claim。

Verifier 不读取 Actor reasoning 的 A/B 应与共享上下文版本比较，重点观察 false positive，而不是语言解释质量。

## 7. Broker 评测

每个回合保存 BrokerProjection，并检查：

- necessary tool recall：真正需要的通用工具是否可见；
- irrelevant tool rate：与当前状态/义务无关的工具比例；
- projection stability：相同状态是否产生相同集合；
- state responsiveness：target bind、pointer lease、artifact/adapter 注册变化后是否正确切换；
- leakage：是否因 app/test/prompt 文本改变工具面；
- mutation exclusivity：adapter 与 Actor 是否可能同时获得写权限。

Broker 工具数目标通常不超过十个；若超过，报告必须说明是哪项真实状态需要，而不是用“大模型也许会用”作为理由。

## 8. Planner 与 Inspector 评测

### 8.1 Planner

Planner 价值必须体现在：

- obligation coverage 提升；
- dependency/artifact binding 错误下降；
- repeated local failure 后能够提出合法 patch；
- 不输出 GUI 步骤；
- 额外 latency 和 token 成本可接受。

普通单 obligation 任务 Planner 调用数应为零。

### 8.2 Inspector

Inspector 价值必须体现在 external oracle，而不是“描述更详细”：

- 多种随机通道/图表/小控件的 grounded pass uplift；
- Actor 最终动作仍来自 Actor decision；
- Inspector 不输出 tool call/坐标动作；
- frame/ROI 一致；
- 相比 Actor 自己 `observe(image_region)` 有稳定增益。

如果只在 `game-drag-v2` 一个 seed 上有效，不进入生产。

## 9. 失败归因

每个失败首先分到一个 owner：

```text
entrypoint_or_harness
mission_compile
mission_reducer
capability_broker
app_or_window_resolution
frame_or_coordinate
controller_or_raw_input
post_action_observation
visual_actor_reasoning
verifier
planner
inspector
external_environment
```

只有保存了模型实际看到的 frame、MissionCard、BrokerProjection、ActorDecision 和 TransitionRecord，才能归因 `visual_actor_reasoning`。缺少这些证据时不能直接说模型笨，也不能直接改 prompt。

详细顺序见 [failure-triage.md](failure-triage.md)。

## 10. 阶段门槛

### P1 Mission 内核

- reducer/property tests 全绿；
- 100% state mutation 经 Reducer；
- illegal transition fail closed；
- 不含应用名/测试文本分支。

### P2 Broker shadow

- 所有 projection 可解释并有 trace；
- pointer/target/artifact/adapter 状态组合覆盖；
- 无任务关键词泄漏；
- 工具 schema 明显小于 legacy 基线，necessary tool 不缺失。

### P3 Actor 受控执行

- 只有 Actor 提出 mutation；
- 每笔动作绑定 FrameState；
- 受控基础任务不低于 legacy；
- failure 保留真实 oracle 结论。

### P4 Verifier

- claim 无法直接完成 obligation；
- deterministic/oracle 证据优先；
- false-positive completion 低于 legacy；
- verifier failure 不污染 state。

### P5 Planner/Inspector

- 只在预定义事件调用；
- 权限测试证明零 mutation；
- 至少两个任务族的 held-out uplift；
- 收益大于额外延迟/成本。

### 默认切换

- 受控、真实、mixed、held-out 四组均有当前版本报告；
- grounded pass 不低于 legacy，false done 更低；
- model/tool/context 负担下降或有清晰代价解释；
- 停止、暂停、取消、外部等待和输入清理无回归；
- legacy 删除计划已能逐项执行，不是无限双栈。

## 11. 报告格式

每次 A/B 报告至少包含：

```text
commit / branch / runtime_version
model/provider/thinking config
client entry proof
cold-start and session proof
task set and held-out policy
environment/app versions
per-case outer/child run and trace binding
independent oracle
metric summary with p50/p95 where meaningful
failure owner distribution
false-positive/false-negative verifier matrix
planner/inspector invocation counts
production line delta and legacy deletion progress
known limitations
```

不得删除失败 run、用人工感觉替换 external oracle，或把入口成功、工具执行、像素变化、Actor claim 写成任务通过。
