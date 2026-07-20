# vNext 旁路迁移计划

本文规定如何在原仓库旁路建设 vNext、如何逐步切换，以及什么时候才能删除旧职责。原则是“小切片、同入口、单写入、可回退、以净删为终点”。

## 1. 分支与产品入口

迁移分支：`codex/vnext-agent-core`。

该分支可以跨多个小 PR 持续建设，但不是第二个产品主线：

- `master` 保持当前可用行为；
- vNext 定期吸收 `master` 的必要修复，避免长期漂移；
- 产品仍只有 Electron“智能代理”入口；
- 未来由每个 run 的显式 runtime version 选择 `legacy` 或 `vnext`，不得根据任务文本自动路由；
- 一个 run 只能有一个桌面 mutation owner；
- shadow 只能比较纯状态、工具投影或 verifier verdict，不能让 legacy/vNext 同时移动鼠标键盘；
- 达到默认切换与删除门槛后，旧职责按证据删除；vNext 可以合并回 `master`，迁移分支是否继续保留由维护者明确决定。

## 2. 未来旁路目录

为了避免继续向过重模块塞职责，建议第一阶段代码放入独立命名空间：

```text
python/winpilot_home/winpilot/vnext/
  mission/
    contracts.py
    reducer.py
    compiler.py
    runtime.py
    card.py
  capabilities/
    catalog.py
    broker.py
  actor/
    loop.py
    decisions.py
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

这只是迁移期间的物理隔离。切换完成后应把它提升为 canonical runtime，删除 `vnext` 命名和 legacy bridge；不能永久保留两套 Agent。

旁路复用现有服务，不复制：

```text
FrameAuthority
FastGuiController / RawDevice / Desktop Action Queue
ScreenWatcher / PointerLease cleanup
ArtifactStore / EvidenceRegistry
AppResolver
provider adapters / MCP registry
Electron run API / trace / E2E harness
```

## 3. 实施纪律

每个实现 PR 只允许一个主要 owner 变化，并必须包含：

- 本轮通用失败假设；
- 新合同和唯一 owner；
- 与 legacy 的桥接点；
- 本轮不做的事情；
- 受影响的旧职责及删除门槛；
- unit/property/replay/E2E 证据；
- 生产代码净增、删除和下一轮预期净删。

建议把单个生产 PR 控制在可独立审查的几百行级别。若必须跨越约 500 行净新增，应优先拆成“合同/纯 Reducer”“Broker”“Actor 接线”“Verifier”多个 PR，而不是一次引入上万行新骨架。

禁止在同一 PR 中同时：

- 改 Compiler 拆分、Actor prompt、Controller 输入和 Verifier；
- 新增多个常驻模型角色；
- 复制 legacy ToolSession/AgentRunner 后再逐步清理；
- 为一个失败样例加入 prompt、adapter 与坐标 fallback；
- 修改真实客户端入口来给 vNext 特殊测试条件。

## 4. P0：先建立可测基线

目的：没有数据就不开始大迁移。

逐回合记录：

```text
runtime_version
mission_or_stage_id
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
planner_call_count
inspector_call_count
```

P0 只添加观测，不改变决策。先从同一 Electron 入口收集 legacy 基线，并冻结一组：

- 受控随机视觉场景；
- 原始 11 个真实任务；
- 组合长任务；
- 与实现者未共同调试的 held-out 任务；
- UIA/OCR 错误或空树负向场景；
- 动画、遮挡、窗口抢焦点和已有实例场景。

P0 完成条件：能够准确回答一次失败的时间花在模型、Controller、等待还是 Verifier，模型当时看到多少上下文和哪些工具，以及 external oracle 为何失败。

## 5. P1：纯 Mission 内核

新增内容仅限：

- versioned contracts；
- 纯 Mission Reducer；
- 机械 active obligation selection；
- MissionCard projection；
- event log/replay；
- property tests。

P1 不启动模型、不操控桌面、不替换 legacy runner。

测试重点：

- dependency DAG 无环；
- 同一事件 replay 得到相同状态；
- claim 永远不能直接变 verified；
- 没有 evidence ref 不能写 verified fact；
- 单 obligation fallback 保留完整 original goal；
- Planner patch 不能加入 GUI 步骤；
- goal revision 不会静默丢失已覆盖的 required condition；
- stale revision event 被拒绝。

P1 完成后可以用历史 trace 离线投影 MissionState，但仍不影响产品行为。

## 6. P2：Capability Broker shadow

建立 ToolCatalog 和纯 Broker：

```python
project_execution(
    mission_state,
    target_state,
    pointer_lease_state,
    adapter_registry,
    approval_state,
) -> BrokerProjection
```

先在 shadow 中把每个 legacy 回合映射为 vNext 工具面，记录：

- legacy 实际调用工具是否包含在 projection；
- route 是否在 `visual_actor`、`deterministic/provider` 和 `blocked` 之间保持单写入互斥；
- projection 是否暴露无关工具；
- 缺失工具来自合同错误还是基础能力缺口；
- 平均/最大工具数与 schema 字符数；
- pointer lease、未绑定窗口、artifact/adapter 状态切换是否正确。

P2 不根据 shadow 结果改变 legacy 工具，不调用第二个模型。

P2 进入执行前的门槛：held-out trace 上没有由应用名/题号产生的投影分支，且核心任务所需原语可以由 broad capability 和真实状态解释。

## 7. P3：Visual Actor 受控旁路

建立最小 Actor loop，只消费 MissionCard、FrameState 和 BrokerProjection。第一批只运行受控视觉目标：

- 点击/输入/滚轮；
- 静态拖拽；
- 多关键点路径；
- held pointer；
- 外部变化等待；
- unknown single obligation。

P3 通过同一 Electron 客户端提交，但 runtime version 由显式实验配置选择，不在 prompt 或任务文本中隐藏。

硬限制：

- 每回合一笔 canonical transaction；
- Actor 不持有全局 todo；
- Actor 无 `done`，只有 claim/blocked/replan；
- Controller 不改变目标；
- legacy 与 vNext 不同时运行；
-失败完整保留，不通过添加靶场路线变绿。

## 8. P4：局部 claim 与独立 Verifier

拆分当前混合 DoneVerifier 语义：

```text
ActionEffectVerifier
ObligationVerifier
MissionVerifier
```

接入顺序：

1. 先实现 deterministic evidence chain；
2. 再接应用 oracle/provider receipt；
3. 最后增加无 mutation 工具的独立视觉 Obligation Verifier；
4. Verifier 只接收必要帧和 evidence，不接 Actor reasoning；
5. Reducer 接受 verdict 后才修改状态。

必须验证：

- 相同证据重复 claim 不重复花费模型调用；
- Actor 与 Verifier 不共享自我完成偏差上下文；
- verifier unavailable 时 fail closed，state 不前进；
- deterministic contradiction 优先于视觉 verdict；
- mission complete 只由全部 required obligation verified 推导。

## 9. P5：Planner 与 Inspector 按证据引入

Planner 和 Inspector 不是 P1-P4 的前置条件。

Planner 只有在 trace 证明以下问题仍是主要瓶颈时引入：

```text
planner_required
```

判据示例：跨应用 artifact dependency 无法由确定性 Compiler 安全表达；两种局部策略失败；用户修改目标；义务与新证据冲突。

Inspector 只有在以下 A/B 成立时引入：

```text
visual_inspector_required
```

- Actor 已使用高清 ROI；
- frame/coordinate/drag 工具均通过确定性核对；
- 单模型仍在多个 held-out 视觉拓扑中失败；
- Inspector 改善 external oracle，而不是只让描述听起来更合理；
- 收益覆盖至少两个不同视觉任务族。

两个角色都只读、按事件调用、无常驻上下文、无 GUI mutation 工具。

## 10. P6：真实任务 canary

在受控场景稳定后，逐项运行：

- 现有 11 个黑盒任务；
- 组合长任务；
- 新增 held-out 未知任务；
- 同一任务不同应用版本/布局；
- 已运行实例、托盘、登录态、弹窗和外部干扰。

每个 case 必须由同一客户端入口提交并隔离 memory。测试不得向 vNext 注入内部 obligation、route 或特殊工具；Compiler 输出和 BrokerProjection 必须作为被测结果，而不是测试 fixture。

canary 失败不自动回退到 legacy 继续操作桌面。可以在下一次独立 run 选择 legacy 进行对照，但不能让两个 runtime 在同一任务中交叉争夺动作所有权。

## 11. P7：默认切换

默认切换需要同时满足：

- contract/reducer/property tests 稳定；
- Broker 未出现应用/测试专属分支；
- 所有只读角色 mutation 权限测试为零；
- 受控 E2E 不低于 legacy 基线；
- held-out 任务的 grounded pass rate 不低于 legacy，false-done 更低；
- mixed 任务中 obligation coverage、artifact identity 和 session reference 正确；
- p50/p95 模型等待与总耗时有可解释结果；
- 至少两种模型配置验证角色合同不依赖单一 provider；
- Stop、pause、cancel、pointer cleanup 和 external wait 无回归；
- 文档明确剩余失败，不使用历史 run 冒充当前通过。

默认切换先做小比例 canary，再扩大；回退是 run 级选择 legacy，不是在一次运行中动态换回旧 Agent。

## 12. P8：删除 legacy 职责

切换不是完成，净删旧代码才是完成。每项删除需满足：

1. vNext owner 已承载生产流量；
2. legacy 调用 trace 连续观察窗口内为零；
3. 等价或更强的 unit/E2E/oracle 证据存在；
4. 不再需要读取旧序列化合同，或已有单向 migration adapter；
5. 删除后不保留第二份状态、guard 或 planner。

## 13. 旧职责迁移矩阵

| 当前职责 | vNext owner | 迁移方式 | 删除门槛 |
| --- | --- | --- | --- |
| `TaskContractCompiler` 对 GUI 的阶段/任务族解释 | Goal Compiler + Mission Reducer | 只保留可靠 obligation/artifact dependency，未知目标单 obligation | vNext contract 覆盖与 cold-start E2E 通过 |
| `_EXECUTE_DESKTOP_MODEL_TOOLS` 固定大工具面 | ToolCatalog + Capability Broker | 先 shadow，再按 run 投影 | 所有执行回合从 BrokerProjection 取工具 |
| `AgentRunner` 的全局 todo/计划维护 | Mission Ledger + Runtime | Actor 只消费 MissionCard | trace 无 legacy plan/state 读取 |
| `AgentRunner` 的 frame/coordinate 镜像状态 | FrameAuthority + typed action | Actor 只引用 FrameState id | 所有动作通过统一 preflight |
| `done` 直接结束 run | `claim_obligation_done` + Reducer | claim 触发 ObligationVerifier | 没有模型 terminal done 路径 |
| `DoneVerifierMixin` 混合验证 | 三层 Verifier | 先 deterministic，再 oracle，再独立视觉 | obligation/mission verdict 全部 typed |
| `RunStateMachine` 与多份进度账本重叠 | 本地 transaction state + MissionState | 保留动作 phase，删除全局语义副本 | 一个事实只有一个 owner |
| `FailureRecoveryLayer` 选择恢复倾向 | FailureFact 分类 + Actor/Planner | 分类不执行；Actor决定局部，Planner提全局 patch | fallback cascade 调用为零 |
| `InteractionStageEngine` 重复维护轮次/等待 | MissionRuntime + PointerLease/Watcher | 保留通用 external wait/lease | vNext 持续任务 E2E 通过 |
| `SubgoalExecutor`/adapter 中 GUI planner | Controller 原语 + Actor | 保留确定性 adapter receipt，不保留 GUI 路线 | 通用路径覆盖并有第二任务族证据 |
| UIA/OCR 自动语义升级 | 显式 probe capability | provenance/frame gate；默认不加载 | negative eval 无反向覆盖像素 |

## 14. Bridge 纪律

Bridge 只能做形状转换，不能承载业务逻辑：

- legacy TaskContract -> initial MissionState；
- vNext TransitionRecord -> 现有 trace/event projection；
- 现有 FrameAuthority/Controller/Artifact/Evidence 的接口适配；
- vNext run result -> 现有 Electron history view。

Bridge 禁止：

- 根据应用名补 obligation；
- 把 legacy stage 转成点击步骤；
- 在 vNext 失败后偷偷调用 legacy Agent；
- 复制一份 FrameState 或 PointerLease；
- 把 Actor claim 转成旧 `done=true` 绕过 Verifier。

每个 bridge 必须有删除 issue 和调用 telemetry；长期无调用后删除。

## 15. 文档与状态维护

每个阶段完成时更新：

- 本目录 README 的当前阶段；
- 本迁移计划的完成证据和下一删除目标；
- [evaluation.md](evaluation.md) 的基线/对照结果；
- [failure-triage.md](failure-triage.md) 中出现的新通用失败模式；
- 主架构文档中“已实现”与“目标设计”的边界。

不得把本机绝对路径、单次临时截图、密钥或未复现的成功结论写入长期文档。具体 run 证据留在 trace/report，文档只记录可复现命令、结论边界和长期不变量。
