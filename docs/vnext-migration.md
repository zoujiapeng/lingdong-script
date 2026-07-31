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
  "thread_id": "可选的不透明对话标识"
}
```

顶层只允许 `goal` 和可选 `thread_id`。客户端不得提交：

- 执行媒介或策略；
- 预绑定 desktop target 或 execution context；
- `graph`；
- `runtime_version`；
- `task_id`。

服务端 Goal Admission 保留完整目标，当前唯一选择 vNext `live_gui` 路由并构造 exact
vNext graph。它不读取应用名或任务关键词来选择 Legacy、工具或固定步骤。vNext live
route 不可用时返回失败，不执行 Legacy fallback。

同一个 `LiveVisualActor` 先直接回复、调用显式配置的通用 connector，或请求
`observe_desktop`。只有最后一种选择才创建 run-bound lease；Electron 让出前台后，
客户端再用精确 `run_id + lease_id` 交付 desktop target。该交接是机械事实，不是
运行时选择权。

### 删除前端执行策略

前端执行策略选择器及其完整状态链已经删除：

```text
renderer selector
  -> request execution field
  -> Goal Admission policy
  -> Broker interaction mode
  -> trace/evidence policy field
```

用户口头强调 GUI 时，只提交原始 `goal`。显式配置 MCP 后，同一个 Actor 当前会同时
看到两个通用 connector 工具与 reply/GUI 决策；没有配置时工具不出现。它们不读取
任务关键词，当前只准入 read-only 远端调用。shell 与 external mutation 尚未加入；
后续仍必须让同一个 Agent 直接看到工具，不能增加 profiler、关键词路由、前端开关
或第二套 planner。

`ToolSpec.execution_medium` 暂时保留为执行事实和审计字段，不再参与用户意图的二次编码。Broker 只做工具 schema、runtime 可写性、目标/画面新鲜度和指针所有权等机械准入。真实 E2E 报告记录实际 OS 输入、shell 或 provider 回执，而不是验证某个策略选项。

## Connector、模型协议与缓存切片

`LiveVisualActor` 现在在文本首轮和视觉回合都能看到
`search_connector_tools` / `call_connector_tool`。目录只来自显式 MCP 配置，结果回到
同一个 Actor；没有 connector 角色、service adapter 或 router。调用只允许明确
`readOnlyHint=true` 且非 destructive 的 descriptor，并在发现、Gateway 和物理
`call_tool` 边界重新核对 fingerprint/descriptor，服从同一 deadline/cancel。外部写入
授权没有进入 live 产品。

`.mcp.example.json` 提供 GitHub 官方 remote MCP 的只读配置和有限 toolsets，token 只
通过 `${env:GITHUB_PAT_TOKEN}` 注入。仓库不携带 credential，也不实现 OAuth 登录；
同一 remote HTTP / stdio 配置结构是其他 connector 的基础，不增加 GitHub 专属代码。
通用 fixture 的可见 Electron 长 Thread 已完成 20 个聊天 Turn `20/20`，新鲜 Thread
的 GitHub-like read 与 connector→Notepad 外部结果为 `2/2`。GitHub 官方 remote 也已
完成真实 discover、`get_file_contents` 与 `get_commit` 物理调用；后者对
`owner=zoujiapeng, repo=lingdong-script, sha=master, detail=none` 返回独立 `gh api`
核对的 SHA。首个中文 Electron run 在 before-submit 已把目标损坏为大量 `?`，只能记
测试输入干扰。随后 ASCII run `arp-20260731-220327-1ed4e4` 完整提交目标，官方 remote
有真实会话/POST；五次模型响应后 Actor 选择未批准工具，Runtime 以
`state_rejected` fail closed，回复为空、零 GUI 动作，usage 为 `8126` prompt、`539`
completion、`4160` hit、`3966` miss。这个干净任务的首次分歧属于
模型工具选择/停止，不属于 transport，也不准入 GitHub 专属修正。显式 MCP 配置最高
优先级与按 server 配置 fingerprint 隔离目录缓存已经修复，聚焦测试 `21 passed`。

模型后端保留 OpenAI-compatible Chat Completions，同时支持通过
`openai_responses`、`anthropic`、`gemini` wire。协议只由
`api.protocol` / `api.api_style` 显式选择；`api.provider`、模型名和 `base_url` 都不
切换 wire。adapter 当前仅覆盖非流式文本、Base64 图片、function tools 与原生 replay
sidecar。审计发现的 provider 误切 wire 与 Anthropic 丢失 canonical `strict:true` 已
修复，相关 5 个测试文件 `214 passed`；三条线路尚未经过真实 provider credential
调用，只能作为 provisional 实现，外部兼容仍是未验证边界。

缓存审计删除了从未生效的 `cache_strategy` 标签，但仓库整体不是零稳定前缀：冻结
Legacy `agent/loop.py` 在 32 KiB 前保持 append-only history，并把 session continuity
放在 cache-friendly prefix。当前唯一活动 vNext 不走该 owner，也不创建
`prompt_cache_key`、`cache_control` 或 `cachedContent`；它只利用请求自然稳定部分的
provider 自动缓存。20-turn 报告累计 `21626` prompt、`13248` cache hit、`8378`
miss、`0` creation；`agent_usage` / run `model_usage` 已真实记录这些上游字段，但观测
不等于 vNext 主动创建缓存。

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
整体引入。当前下一切片不是继续包 Pi，也不是切换模型，而是：

1. `type_text` 跨 Notepad/Explorer 的冻结复验已得到 delivery `8/8`、外部任务
   `7/8`，没有字符丢失复现，故不修改当前文本输入实现；
2. 不改生产代码，只在至少两个任务族对 physical-key/focus/application-consumption
   做一个测试型单变量 A/B；只有两族外部完整成功率都提高才替换并删除旧 owner；
3. connector 已按同一 Actor 的通用工具落位；未来加入 shell 或外部 mutation 时也
   必须复用同一选择面，仍不增加 profiler、关键词 router、前端开关或第二 planner。

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

## 主分支提升与冻结分支边界

2026-07-30，GitHub 已有默认分支 `master` 是
`codex/lean-gui-agent-core` 的严格祖先，因此直接将当前完整 HEAD 快进到
`master`。这次提升没有 force push、历史改写、merge commit 或把任何冻结分支整体
并入主线。`master` 从此是默认、活动和唯一长期产品分支；原活动分支及 Draft
PR #45/#46 均由提升后的 `master` 取代。

其余分支仍只作只读恢复或审计输入。每个独有能力先在当前 `master` 构造随机失败
样本，只提取最小 owner，并在至少两个任务族上用外部 oracle 复验。没有实质收益、
只补当前模型精度、扩大 prompt/state 或不能删除旧 owner 的候选一律丢弃。不能为
获得表面上的“唯一分支”整体 merge 冻结历史；审计完成的引用直接删除。

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
| `answer-all-questions` | failed，4/6 | 与此前 6/6、5/6 和错误 `81` 样本共同说明模型波动 |
| 四个 corridor 变体 | failed | 模型折线与物理折线一致，但路径切角/出界 |

总计 `5/10`。本次目标由操作系统键盘输入可见 Electron，再用真实鼠标发送；
UIA 只定位和读回。报告见
`output/playwright/github-open-e2e-20260731/controlled-suite-r2/20260731-081832-suite/`。
同一源码的 WinPilot 全量回归为 `3377 passed, 13 skipped`。

这证明 Goal Admission、单 Visual Actor、坐标/键盘/窗口基础链和短多轮状态已经能
闭环，但“真实客户端验收”总退出门仍未满足：五项原始长任务没有当前源码 5/5，
随机 corridor 仍暴露当前模型视觉几何瓶颈，试卷的一次通过也不能抹去此前算术
失败。不得用任务提示、答案修正、路径规则、Legacy fallback 或更多 verifier
关闭这些失败。本次没有修改生产 Runtime；动作面、角色数和 45 词系统提示均未
增长。

同一轮原生软件复验得到 Notepad 精确落盘，以及网易云周杰伦、抖音 `kimi k3`、
Wireshark 最近流量摘要的外部核对通过；Calculator、Paint、Krita、QQ 群摘要、
QQ 空间点赞、能力演示和 OneNote→Word 仍失败或 blocked。生产代码 `+0/-0`，
详细 run、oracle 债务和动作时确认阻塞见架构重置文档 13.16。模型切换当前暂停；
三字段去重的 8-run 严格交叉配对已完成：A 为 Explorer `0/2`、Notepad `2/2`，
B 为 Explorer `0/2`、Notepad 原始结果 `1/2`，且 B 的缺字样本发生在输入交付/
应用边界，具体根因待跨族复现，不能证明字段删除导致回归。删除臂因没有跨族完整
成功率收益而回退。后续 `type_text` 跨族冻结复验的 8 个槽位均有完整参数、回执和
稳定后帧，没有丢字；外部任务 `7/8`。唯一失败是小窗口水平视口只显示后缀后，模型
重复重输且从未保存；最大化探索对照已精确落盘，但保存后的 provider 超时/断连使
Runtime 误报失败。详细证据见架构重置文档 13.18。下一实验只检查跨族通用按键消费
边界，不新增提示、自动最大化、重复按键规则或应用适配。

后续 13.19 的真实前端复现证明当前 goal 入口在模型判断前无条件最小化 Electron，
纯聊天也被预绑定到 GUI target，终态又没有由产品自行恢复。当前迁移出口因此新增一项
前置门：同一个 `LiveVisualActor` 必须能在零系统提示下直接回复或申请
`observe_desktop`，只有 run-bound lease 才允许隐藏；matching
success/fail/cancel 必须 `restore/show`。测试型零提示矩阵严格 `9/12`，虽比 113 字
提示的 `7/12` 更好且 6/6 GUI 均申请桌面，仍有聊天误租约与 provider 干扰，尚不
准入预计净增 `250–330` 行的生产切片。下一唯一实验改为稳定 provider 下重复冻结的
零提示 reply/lease 矩阵；不增加 router、第二模型角色、旧聊天链或提示教学。通用
按键消费 A/B 顺延。

13.20 已满足这项前置门并取代上述候选状态。主入口现在只提交 `goal + optional
thread_id`；同一个 `LiveVisualActor` 在零系统提示首轮直接回复或请求
`observe_desktop`，只有后者才触发精确 run-bound lease、Electron yield、target
bind 和原有视觉线。matching terminal 自行恢复窗口；response loss、TOCTOU、
stale/non-current run 和未启动 terminal 都有机械合同。服务端有界 Thread/Turn
只把成功且有用户结果的原生交换交给下一轮，不增加第二模型或长期 memory。

真实可见 Electron 已在同一 Thread 完成
`6 个连续聊天 → 1 个显式鼠标键盘 Notepad 保存 → 1 个上下文追问`：外部文件精确为
`DESKTOP-c419bb42`，恢复后追问也精确回复该值；聊天 Turn 均为 `reply`、lease idle，
桌面 Turn 只在 Actor 请求后隐藏并在终态自行恢复。两次一次性 provider
`Connection error` 经普通用户原样重发成功，失败 Turn 未污染历史。该迁移切片为
生产 `+1372/-155`，比预估大，所以下一轮冻结生产改动，只用至少 20 Turn、两个原生
任务族的随机交错矩阵验证长时收益。当前剩余迁移边界是 Thread 跨服务重启持久化、
pause 与 waiting lease 的真实 E2E、长历史下 connector 选择压力、provisional native
model protocol 的真实凭据验收，以及未来把 shell/外部 mutation 与 GUI 同时交给同一
模型；它们都不得通过预分类器实现。
