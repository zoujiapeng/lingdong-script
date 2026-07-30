# vNext 迁移

本文记录主 Agent 从 Legacy 向 vNext 迁移时已经建立的边界、暂留兼容面和删除门。它不记录一次性 run，也不把尚未执行的真实客户端验收写成已通过。

## 当前迁移切片

### 主 Agent 入口

Electron“智能代理”页只通过以下入口提交自然语言目标：

```http
POST /api/v2/goals/runs
```

请求契约为：

```json
{
  "goal": "完整用户目标",
  "execution_context": {
    "format": "...",
    "entrypoint": "...",
    "submitted_at": 0,
    "desktop_handoff": {},
    "target_window": {}
  }
}
```

顶层只允许 `goal` 和 `execution_context`。客户端不得提交：

- 执行媒介或策略；
- `graph`；
- `runtime_version`；
- `task_id`。

服务端 Goal Admission 保留完整目标，当前唯一选择 vNext `live_gui` 路由，构造 exact vNext graph，并把服务端生成的图身份绑定到执行上下文。它不读取应用名或任务关键词来选择 Legacy、工具或固定步骤。vNext live route 不可用时返回失败，不执行 Legacy fallback。

客户端 `execution_context` 是桌面交接事实，不是运行时选择权。它只包含入口、提交时间、桌面 handoff 和目标窗口等客户端可观察信息；`task_id` 在服务端构图后注入。

### 删除前端执行策略

前端执行策略选择器及其完整状态链已经删除：

```text
renderer selector
  -> request execution field
  -> Goal Admission policy
  -> Broker interaction mode
  -> trace/evidence policy field
```

用户口头强调 GUI 时，只提交原始 `goal`。当前 live vNext 工具目录仍只有 GUI/control 工具，所以当前运行不会改走 shell 或 provider；但这只是当前工具事实，不是新的隐藏 GUI 策略。后续加入命令行或 provider 时，同一个 Agent 必须看到这些工具并自行选择，不能再增加独立 profiler、关键词路由、前端开关或第二套 planner。

`ToolSpec.execution_medium` 暂时保留为执行事实和审计字段，不再参与用户意图的二次编码。Broker 只做工具 schema、runtime 可写性、目标/画面新鲜度和指针所有权等机械准入。真实 E2E 报告记录实际 OS 输入、shell 或 provider 回执，而不是验证某个策略选项。

## 已完成的 Pi 对照与下一切片

短命、未进入主依赖的 Pi Agent `0.83.0` spike 已完成。它证明 provider 原生
`user image → assistant tool call → tool result → new image` 序列能让
`ordered-clicks` 在两个随机 seed 上按序完成，但同一 spike 仍会算错随机试卷。

主线因此只保留最近一笔原生 tool exchange：

- 不安装 Pi 包；
- 不增加 Node↔Python RPC 或第二 runtime；
- 不增加角色、动作、应用规则和 prompt；
- 下一回合覆盖旧 exchange，避免无界聊天历史。

Pi 只有在能整体替换并删除 Python 模型循环 owner、且跨任务 E2E 明显更好时才
整体引入。当前下一切片不是继续包 Pi，而是：

1. 用更强原生视觉模型在完全相同的 10 项矩阵做 A/B；
2. 若模型直接改善试卷和 corridor，不改 Runtime；
3. 未来加入 shell/provider 时，让同一个 Visual Actor 同时看到全部工具并自主
   选择，仍不增加 profiler、关键词 router、前端执行开关或第二 planner。

## 暂留兼容面

以下接口和模块仍在仓库中，但不属于主 Agent 用户入口：

| 兼容面 | 暂留用途 | 禁止事项 |
| --- | --- | --- |
| `POST /api/v2/task-contracts/compile` | Studio、合同/图编辑和旧调用方 | 不得替主 Agent 识别 intent 或选择 runtime |
| `POST /api/v2/arp/runs` | Studio、显式图执行和兼容测试 | 不得接回智能代理页；不得作为 vNext 失败 fallback |
| Task Contract/TASK_STAGE | 旧图和工件合同 | 不得承载新 GUI 能力的第二份实现 |
| `StageRuntime` / `AgentRunner` | Legacy run | 不得成为 vNext 的隐式恢复路径 |
| legacy IPC、FlowScript、旧 feature system | 仍有明确消费者的兼容功能 | 不得决定主 Agent 行为 |

服务端内部仍可复用 ArpExecution 的 run 生命周期；这不等于允许主 Agent 客户端通过 `/api/v2/arp/runs` 提交 graph 或选择 runtime。

## 按依赖删除 Legacy

删除遵循“先消费者，后入口与提供者；先证明替代能力，再删兼容实现”。

### 1. 清除主产品消费者

- 智能代理页及其恢复、重试、继续入口都只调用 `/api/v2/goals/runs`；
- 删除主产品中的 runtime selector、客户端构图和 Legacy 自动降级；
- 为 CLI、scheduler、integration 等入口分类：自然语言 Agent 目标接 Goal Admission，Studio/显式自动化保留清楚标识；
- trace 能区分 product goal、Studio graph 和兼容 run。

退出门：生产主 Agent 路径中不存在 `/task-contracts/compile` 或客户端 `/arp/runs` 调用，vNext 不可用时能观察到明确失败而非 Legacy run。

### 2. 补齐 vNext 能力与证据

- 将仍只存在于 Legacy 的通用 GUI 能力逐项迁入 vNext；
- 同一语义 Agent 直接选择全部允许工具，不增加 Task Profiler、任务关键词表或应用专属 prompt；
- trace 记录模型实际选择的工具、执行媒介和 terminal evidence，不记录前端策略状态；
- 对 shell、provider 和 GUI 工具做对抗性测试，确保没有确定性 owner 替模型预选任务路线。

退出门：目标任务族在 vNext 达到约定的功能与失败语义；用户口头要求、实际工具选择和外部结果能够由同一 run 追溯。

### 3. 完成真实客户端验收

- 从可见 Electron 客户端提交，不以直接 HTTP 调用代替；
- 覆盖普通自然语言目标和口头强调“必须使用 GUI”的目标；
- 用 OS 输入、窗口/进程、文件或目标应用状态等外部 oracle 验证结果；
- trace 证明没有 Legacy、shell 或 provider fallback；
- 覆盖冷启动、失败恢复和至少一个不同于实现样例的任务族。

退出门：当前版本的验收报告和 trace 满足 [测试规范](testing.md)。历史 run、工具 `ok`、像素变化或模型总结不能代替本轮验证。

### 4. 隔离 Studio/兼容消费者

- 给 `/task-contracts/compile` 和 `/arp/runs` 的剩余调用建立可枚举清单；
- 把 Studio/自动化编辑与主 Agent 的类型、UI 和 telemetry 分开；
- 停止向 Task Contract、TASK_STAGE、StageRuntime 和 AgentRunner 增加新 GUI 行为；
- 迁移或淘汰每个仍依赖 Legacy runtime 的显式调用方。

退出门：Legacy 入口只服务已登记的兼容消费者；连续约定发布窗口内没有未知生产调用。

### 5. 从上游到下游删除

按依赖链逐段删除：

```text
Legacy 产品/兼容消费者
  -> /api/v2/task-contracts/compile
  -> TaskContractCompiler
  -> client-submitted /api/v2/arp/runs legacy branch
  -> TASK_STAGE / StageRuntime adapters
  -> InteractionStageEngine / SubgoalExecutor
  -> AgentRunner legacy-only loop and tools
  -> legacy IPC / feature flags / configuration
```

每删除一段，先确认没有剩余 import、路由、持久化快照或 replay 需要该契约。`/api/v2/goals/runs` 可继续使用内部 run lifecycle；不得为了删除外部 `/arp/runs` 而复制一套执行状态机。

最终退出门：

- 主 Agent、Studio 和自动化的所有受支持入口均有明确 owner；
- 主 Agent 只有 vNext runtime 和一套 GUI writer；
- 仓库不再存在可被主 Agent 触发的 Legacy fallback；
- 旧 wire format 如仍需读取，仅存在于隔离的只读迁移器；
- 单元、常规回归和真实客户端 E2E 均在删除后的当前版本重新通过。

## 唯一分支收敛

`master` 是唯一长期分支。完成收敛前，`codex/lean-gui-agent-core` 是唯一活动
集成分支；不要再创建平行开发分支。Draft PR #45/#46 是恢复与逐项审查载体，
不是整体 merge 单元。

2026-07-30 本次交接提交后，相对当前活动分支的本地提交差异：

| 分支 | 当前独有 | 对方独有 | 决策 |
| --- | ---: | ---: | --- |
| `master` | 7 | 0 | 最终唯一长期分支 |
| `codex/vnext-gui-e2e-foundation` | 6 | 0 | 已是祖先，停止演化 |
| `codex/vnext-agent-core` | 97 | 193 | 冻结，禁止整体 merge |
| `fix/pr10-python-ci` | 97 | 194 | 只审查额外 CI 候选 |
| `recovery/c4-canonical-from-d418-local` | 48 | 1 | 仅恢复 |
| `recovery/c4-fa2e9caf-local` | 50 | 2 | 仅恢复 |
| `test/pr15-legacy-electron-oracle` | 94 | 2 | 只审查 oracle 候选 |

每个对方独有能力先在当前主线构造随机失败样本，只提取最小 owner，并在至少两个
任务族上用外部 oracle 复验。没有实质收益、只补当前模型精度、扩大 prompt/state
或不能删除旧 owner 的候选一律丢弃。通过门槛的小提交最终落到 `master`，随后关闭
#45/#46 并删除其他本地和远端临时分支；不能靠一次性合并冻结分支获得表面上的
“唯一分支”。

## 当前验证状态

前端策略链删除、窗口生命周期和一笔原生 exchange 落地后的当前精确源码，已从
可见 Electron 页面只提交自然语言 `goal`，用真实鼠标键盘和独立浏览器 oracle
完成 10 项受控矩阵：

| 场景 | 结果 | 归因 |
| --- | --- | --- |
| `precise-click` | passed | 提交坐标唯一映射与物理参数一致 |
| `ordered-clicks` | passed | 最近一笔原生 exchange 修复跨帧遗忘 |
| `drag-fit` | passed | 真实物理拖放 |
| `slider` | passed | 真实物理拖动 |
| `dynamic-hold` | passed | 跨帧 held-pointer 完整释放 |
| `answer-all-questions` | failed，5/6 | 模型两次把 `8×9` 提交为 `81` |
| 四个 corridor 变体 | failed | 模型折线与物理折线一致，但路径切角/出界 |

总计 `5/10`。WinPilot 全量回归为 `3373 passed, 13 skipped`。详细 run 与
submitted/physical 证据见
[`GUI_AGENT_ARCHITECTURE_RESET_2026-07-29.md`](GUI_AGENT_ARCHITECTURE_RESET_2026-07-29.md)
第 13.10 节。

这证明 Goal Admission、单 Visual Actor、坐标/键盘/窗口基础链和短多轮状态已经能
闭环，但“真实客户端验收”总退出门仍未满足：五项原始长任务没有当前源码 5/5，
随机 corridor 和试卷仍暴露当前模型智力/视觉几何瓶颈。不得用任务提示、答案修正、
路径规则、Legacy fallback 或更多 verifier 关闭这些失败。
