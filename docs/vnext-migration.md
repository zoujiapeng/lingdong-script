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
  "execution_constraint": "auto",
  "execution_context": {
    "format": "...",
    "entrypoint": "...",
    "submitted_at": 0,
    "desktop_handoff": {},
    "target_window": {}
  }
}
```

顶层只允许 `goal`、`execution_constraint` 和 `execution_context`。客户端不得提交：

- `graph`；
- `runtime_version`；
- `task_id`。

服务端 Goal Admission 保留完整目标，当前唯一选择 vNext `live_gui` 路由，构造 exact vNext graph，并把服务端生成的图身份绑定到执行上下文。它不读取应用名或任务关键词来选择 Legacy、工具或固定步骤。vNext live route 不可用时返回失败，不执行 Legacy fallback。

客户端 `execution_context` 是桌面交接事实，不是运行时选择权。它只包含入口、提交时间、桌面 handoff 和目标窗口等客户端可观察信息；`task_id` 在服务端构图后注入。

### 执行约束

`execution_constraint` 目前有两个值：

| 值 | 当前语义 |
| --- | --- |
| `auto` | GUI-first；当前仍由服务端选择同一 vNext live GUI 路由 |
| `gui_required` | 用户明确要求 GUI；不得改走 shell、provider、deterministic executor 或 Legacy |

`auto` 是当前路由策略，不是把 GUI 写死进自然语言 parser。将来若增加非 GUI 路由，也只能在 Goal Admission 内按结构化契约选择，并保持客户端不能指定 runtime。

`gui_required` 被绑定为 Broker runtime state 的 `InteractionMode.GUI_REQUIRED`。该状态进入 fingerprint，并在每次工具投影时重新参与判断，而不是只在 schema 或提示词中出现。

每个 vNext `ToolSpec` 使用 `execution_medium` 声明执行介质：

| 介质 | 用途 |
| --- | --- |
| `gui` | Visual Actor 发起的 GUI mutation |
| `control` | Visual Actor 的非 mutation 控制工具，例如 blocked/claim |
| `local` | 本地非 GUI 执行 |
| `external` | 外部 mutation 或 provider 路径 |

在 `gui_required` 下，Broker：

1. 不选择 provider 或 deterministic executor offer；
2. 只投影 Visual Actor 中介质为 `gui` 或 `control` 的工具；
3. `control` 工具必须绑定当前目标窗口的新鲜权威帧；零 mutation 的只读或已满足目标以 `vnext_gui_observation` 留下 GUI 使用证据，不制造无意义输入；
4. 将未分类的非 mutation 工具按 `local` 处理，因而不能借 `mutation_kind=none` 绕过；
5. 找不到合格 GUI/control 工具时返回 `blocked`，不 fallback。

这条门禁位于工具准入层；提示词只表达目标，不负责实施安全策略。

## 下一切片：Task Profiler

当前 `auto` 暂时是 GUI-first。下一切片在 Goal Admission 内引入
model-native Task Profiler，只产出路由所需的结构化事实和不确定性，不产出
点击步骤、应用专属 recipe 或关键词表：

- `gui_required` 是用户硬约束，优先级高于 profiler，任何分类结果都不能降级；
- `auto` 只有在非 GUI capability、授权、输入输出和独立验收都明确匹配时才选择非 GUI；
- 模糊或需要查看当前桌面状态的目标继续进入同一个 vNext Visual Actor；
- 原始目标原样交给执行模型，profiler 结论不能替换、缩写或预先规划目标；
- 每个新路由先用真实客户端同时验证“应走 GUI”“可不走 GUI”和明确
  `gui_required` 的对抗样例，再允许进入主入口。

在 GUI 基础能力、失败恢复和可信完成没有达到退出门前，不为了扩大
`auto` 的路由覆盖面复制第二套 planner。

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
- 意图/能力选择只产出结构化事实，不增加任务关键词表或应用专属 prompt；
- `auto` 与 `gui_required` 的 admission reason、interaction mode、Broker projection 和 terminal evidence 可追溯；
- 对 shell、provider、deterministic 和伪装成 non-mutation 的 local 工具做对抗性门禁测试。

退出门：目标任务族在 vNext 达到约定的功能与失败语义；`gui_required` 没有合格 GUI 路由时稳定 blocked。

### 3. 完成真实客户端验收

- 从可见 Electron 客户端提交，不以直接 HTTP 调用代替；
- 覆盖 `auto` 和用户明确选择“必须使用 GUI”；
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

2026-07-29 已从源码 Electron 客户端的可见 Agent 页面执行 `goal-admission`
真实 GUI 套件。任务由 UI 填写并选择“必须使用 GUI”，提交链经过
`POST /api/v2/goals/runs`；验收由独立浏览器页面的可信事件完成，不以
runtime 的 `ok` 或模型总结代替结果。

| 场景 | 结果 | 关键证据 |
| --- | --- | --- |
| `precise-click` | 通过；审查修复后复验通过 | run `arp-20260729-092531-f79981`；外部 oracle 收到可信 `scenario_success`；GUI click receipt、两条 `vnext_gui_observation` 与 outer run/mission/target binding 一致 |
| `ordered-clicks` | 失败并登记 | run `arp-20260729-084046-1de1ed`；完成第一个 marker 后产生 10 个可信 `scenario_failure`，最终 `vNext turn budget exhausted`；见 [GitHub #42](https://github.com/zoujiapeng/lingdong-script/issues/42) |

两条 run 均证明 `architecture_version=vnext`、
`execution_constraint=gui_required`、`route=live_gui`、
`no_legacy_fallback=true`，且没有 Legacy `agent_start`/`agent_tool`、
shell 或 provider fallback。首轮双场景套件位于
`output/playwright/client-goal-admission-e2e/20260729-083913-suite/`；
收紧 run/mission 证据绑定后的复验报告位于
`output/playwright/client-post-review-e2e-20260729-092429/20260729-092430-suite/`。

因此本切片的入口边界与 GUI hard gate 已得到真实客户端证据，但“真实
客户端验收”退出门尚未整体满足。`ordered-clicks` 的状态推进问题必须在
观察/动作边界修复并重新跑通；不得用任务特化提示词、关键词 planner、
硬编码 marker 顺序或 Legacy fallback 关闭该问题。
