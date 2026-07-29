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

## 下一切片：同一 Agent 的工具选择

在引入任何非 GUI 工具前，先用 Pi Agent 风格的最小“模型 → 工具 → 新观察”循环做隔离对照：

- 原始目标、当前 JPEG、有界 task state 和全部允许工具进入同一次模型决策；
- GUI、shell 与 provider 都是普通工具，不由另一个模型或确定性 router 预选；
- 每轮桌面 mutation 仍顺序执行并取得新画面；
- 不增加应用名规则、执行媒介关键词表或软件专属 prompt；
- 只有随机任务 E2E 显著提升、owner 数下降且产品净代码不增长时，才替换现有 loop。

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

## 当前验证状态

2026-07-29 的历史套件曾通过前端策略选择器提交。该机制已于
2026-07-30 从前端、请求、准入、Broker 和证据链删除，历史 run 只作为回归
参照，不能证明删除后的当前源码。当前源码必须从可见 Electron 页面只提交
自然语言 `goal`，并由独立浏览器页面的可信事件验收。

| 场景 | 结果 | 关键证据 |
| --- | --- | --- |
| `precise-click` | 历史通过；等待删除策略链后的当前源码复验 | run `arp-20260729-092531-f79981`；外部 oracle 收到可信 `scenario_success` |
| `ordered-clicks` | 失败并登记 | run `arp-20260729-084046-1de1ed`；完成第一个 marker 后产生 10 个可信 `scenario_failure`，最终 `vNext turn budget exhausted`；见 [GitHub #42](https://github.com/zoujiapeng/lingdong-script/issues/42) |

两条历史 run 均证明当时的 `architecture_version=vnext`、
`route=live_gui`、`no_legacy_fallback=true`，且没有 Legacy
`agent_start`/`agent_tool`、shell 或 provider fallback。首轮双场景套件位于
`output/playwright/client-goal-admission-e2e/20260729-083913-suite/`；
收紧 run/mission 证据绑定后的复验报告位于
`output/playwright/client-post-review-e2e-20260729-092429/20260729-092430-suite/`。

策略选择器删除后的当前源码尚未完成本轮真实客户端复验，因此“真实客户端
验收”退出门尚未满足。`ordered-clicks` 的状态推进问题必须在
观察/动作边界修复并重新跑通；不得用任务特化提示词、关键词 planner、
硬编码 marker 顺序或 Legacy fallback 关闭该问题。
