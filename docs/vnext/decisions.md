# vNext 架构决策记录

本文件记录已经确定的边界和仍需用实验回答的问题，防止后续实现因为某个失败样例重新引入第二个 GUI Agent、应用剧本或重复状态。

## 已接受决策

### D001：只有一个 Visual Actor

- 状态：accepted
- 决策：只有 Visual Actor 可以提出 GUI mutation；不存在第二个可点击、输入或拖拽的 Planner/Verifier/Inspector。
- 原因：多个桌面模型会争夺窗口、帧、鼠标状态与目标语义，恢复时无法确定唯一 owner。
- 推论：所有其他模型角色的 tool allowlist 必须静态证明为 read-only。

### D002：MissionState 由确定性 Reducer 单写

- 状态：accepted
- 决策：Actor/Planner/Verifier 只提交 typed proposal、claim、verdict，Reducer 校验 revision 和不变量后写状态。
- 原因：让任一模型直接更新主线会重新制造不可 replay 的隐式计划。
- 推论：相同 state + event 必须产生相同 next state；所有状态变化可审计。

### D003：Obligation 描述结果，不描述路线

- 状态：accepted
- 决策：只包含 desired state、acceptance、dependency、forbidden effect、broad capability 和 artifact binding。
- 原因：到达界面前无法知道的按钮和路径会限制模型随机应变。
- 推论：无法可靠拆分时生成一个覆盖 original goal 的 obligation。

### D004：Capability Broker 是纯状态投影

- 状态：accepted
- 决策：Broker 根据 obligation capability、TargetBinding、PointerLease、Artifact、AdapterRegistry 和 approval 投影工具。
- 原因：模型不应额外判断 phase；固定大工具面浪费上下文并增加错误。
- 推论：不得读取应用名、题号或 prompt 关键词选择工具。

### D005：Controller 不做语义修正

- 状态：accepted
- 决策：Controller 校验和执行 Actor 选择的目标，不能 OCR snap、换按钮、fallback 或宣布完成。
- 原因：下层语义修正会形成隐藏 Planner，并使 trace 不能解释实际动作归属。
- 推论：任何 target adjustment 都应为零或是明确、可证明的纯坐标映射。

### D006：claim、verified、mission complete 三层分离

- 状态：accepted
- 决策：Actor claim 触发独立 ObligationVerifier；Reducer 接受 verdict 后写 verified；全部 required obligation verified 后才完成 mission。
- 原因：生成者自评有正向偏差，action success 与业务完成不是同一语义。
- 推论：移除模型直接 `done` 的最终权限。

### D007：Verifier 上下文独立且只读

- 状态：accepted
- 决策：Verifier 不读取 Actor chain-of-thought，不持有 mutation 工具，优先 deterministic/oracle/receipt。
- 原因：独立评价比要求同一 Actor 更“自我批判”更可控。
- 推论：Verifier unavailable 时状态不前进。

### D008：Planner 与 Inspector 按事件调用

- 状态：accepted
- 决策：两个角色都不常驻；只有预定义事件与跨任务 A/B 证据存在时才调用。
- 原因：常驻角色增加延迟、上下文和意见冲突，可能成为新的主管层。
- 推论：普通单 obligation 回合不得调用 Planner；普通清晰画面不得调用 Inspector。

### D009：先修基础工具和调度骨架

- 状态：accepted
- 决策：按入口 -> Mission -> Broker -> App/Window -> Frame/Coordinate -> Input -> Transition -> Verifier -> Model -> Prompt 顺序诊断。
- 原因：大量“模型不会”的表现实际来自工具缺失、旧帧、错误窗口、动作语义或重复状态。
- 推论：没有前层证据时，prompt PR 和新模型角色 PR 不准入。

### D010：旁路共用同一产品入口

- 状态：accepted
- 决策：legacy/vNext 按 run 显式选择，共用 Electron、Frame/Controller、Artifact/Evidence 和 E2E；同一 run 不交叉执行。
- 原因：第二套入口会制造无法与用户体验比较的假通过。
- 推论：shadow 只允许纯计算，不允许双桌面 mutation。

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

### Q005：何时提升 vNext 为默认并删除命名空间

- 状态：deferred
- 判据：以 [migration-plan.md](migration-plan.md) 默认切换和 P8 删除门槛为准。

## 修改决策的规则

修改 accepted decision 必须：

1. 新增一条替代决策，不静默改写历史；
2. 给出至少两个不同任务族或一个系统级不变量冲突的证据；
3. 说明对唯一 GUI writer、MissionState writer 和真实客户端入口的影响；
4. 说明新增复杂度将删除什么旧职责；
5. 更新 architecture、contracts、migration 和 evaluation；
6. 不得以单个测试变绿作为充分理由。
