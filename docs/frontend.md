# 原生前端与运行时视图

当前生产前端不是旧 WinPilot WebUI、iframe 或另一套 Agent 页面。唯一入口是
`src/renderer/App.tsx → components/platform/PlatformWorkbench.tsx`，并通过
`platform/client.ts`、`platform/useControlPlane.ts` 使用 WinPilot `/api/v2` 与
`/ws/events`。本文记录哪些能力必须出现在前端、哪些代码只是兼容层，以及修改时
如何避免再次出现“后端已经有事实，前端却看不到”或“界面能创建但运行时不能执行”
的问题。

## 五个生产入口

| 入口 | 当前职责 | 主要运行时事实 |
| --- | --- | --- |
| 智能代理 | 输入自然语言目标、设置步骤上限、选择冷启动、停止当前任务、处理人工门 | Task Contract、当前 stage、能力可满足性、模型/Controller 工具、工件、证据、Completion Contract、实时画面和指标 |
| 流程工作台 | 编辑、校验并运行 `automation-ir/1` / `arp/1` 投影 | 前端编译器与后端 capability catalog 的交集、IR fingerprint、真实 ARP 运行 |
| 运行记录 | 搜索/筛选统一 Agent/ARP run，并按需加载单次完整详情 | run 摘要、timeline、typed stage trace、capabilities、artifacts、evidence、失败分类和完成合同 |
| 技能与学习 | 区分已验证 Skill、draft/invalid script、trace、长期 Memory 与进程内 session | replay 可信状态、脚本摘要、适用条件、结构化 verification 和 lineage |
| 系统设置 | 修改模型/感知/Controller/集成配置并查看运行时健康 | runtime contract/source/Git 身份、RuntimeJob、Desktop Action Queue、异步动作、Perception daemon、MCP readiness |

导航与真实渲染分支都集中在 `PlatformWorkbench.tsx`。新增顶层能力时，不能只在
后端返回字段：必须明确它属于上述哪个入口、加载态/空态/失败态如何显示，以及它
是否需要历史恢复。

## 自然语言任务的真实路径

```text
智能代理输入
  → POST /api/v2/task-contracts/compile
  → task-contract/1 + typed arp/1 graph
  → POST /api/v2/arp/runs
  → StageRuntime / Controller / Adapter / bounded AgentRunner
  → BUS + WebSocket
  → GET /api/v2/runs/current（活跃详情）
  → GET /api/v2/runs/{run_id}（按需历史详情）
```

前端不会把整句目标直接转换成单个 `agent_goal`。跨应用、工件、通信和实时互动的
阶段、能力、工具与 verifier 都来自同一个 Task Contract。停止按钮统一调用
`POST /api/v2/runs/{run_id}/cancel`，因此 Agent 与 ARP 不再需要前端猜测两套取消
端点。

## 刷新与延迟策略

`useControlPlane.ts` 使用事件驱动刷新：

- WebSocket 普通事件以 80 ms 合并窗口刷新当前 run；连续事件不会逐条触发完整
  platform/runs 请求。
- terminal 事件刷新 platform、当前 run 和运行列表，保证最终指标与证据进入界面。
- WebSocket `ping` 只维持连接，不触发 REST 请求。
- 5 秒 fallback 轮询负责断线恢复和低频平台状态更新，不是 1 秒全量轮询。
- `/api/v2/runs` 只返回导航所需摘要，timeline/evidence/result 为空；选择记录后才从
  `/api/v2/runs/{run_id}` 读取完整详情。
- `/api/v2/platform` 与 `/api/v2/jobs` 的 RuntimeJob 列表不嵌入完整 job result；单个
  job 详情仍可按 id 查询。

这项边界很重要：Task Contract、证据 metadata、ARP history 和模型输出可能很大，
把最近 20–200 个 result 放进每次轮询会直接造成前端卡顿和无意义的 JSON 复制。

## 运行记录与重启恢复

活跃 ARP 直接使用 `ArpExecution.to_dict()`。运行结束时，运行时把有界 audit
snapshot 写进 terminal trace；`arp_run_start` 也持久化原始 Task Contract、
satisfiability 和 experience policy。

服务重启后，`RunProjectionStore` 从 JSONL 事件恢复：

- `arp_node_done(kind=task_stage)` → stage status、工具、required/verifier/exposed/
  missing capabilities、Adapter、evidence provider、artifact id、模型与 Controller
  耗时、尝试和恢复字段；
- `arp_evidence` → 结构化 Evidence Registry 记录；
- `contract.complete` evidence metadata → Completion Contract；
- ArtifactStore `manifest.json` → run-scoped 工件清单。

较老的 trace 没有原始 Task Contract snapshot 时，前端显示“历史阶段运行时与证据”，
并明确标注由原始 trace/manifest 恢复。它不会用当前 parser 重新猜测旧任务意图。
这类记录仍可查看阶段、能力、工件、证据和完成合同，但没有原合同摘要。

工件缩略图使用
`GET /api/v2/runs/{run_id}/artifacts/{artifact_id}`。服务只接受该 run manifest 中、
路径位于 run-scoped store 内，并且当前大小、SHA-256、图片尺寸仍与登记值一致的文件。
观察帧与可发送任务工件使用不同的 `purpose/sendable` 标签，界面不会把截图帧暗示成
用户附件。

## 流程工作台的可达性规则

节点是否可创建由两项同时决定：

```text
isRunnableFlowNodeType(node)
  AND backend capability.flow_node_types contains node
```

这个交集同时约束主节点列表、统一搜索结果和“最近使用”；任一入口都不能绕过它。
因此运行时离线、catalog 尚未加载或后端删除能力时，前端不会继续提供一个看似可用
但必然失败的新节点。

`NodeType`、`NODE_DEFINITIONS` 和属性面板仍包含一部分历史类型，以便打开旧文件并
给出明确错误；“类型存在”不等于“新建入口可见”。旧 `tool_call` 仍可从 Electron
feature registry 加载参数 schema，这是历史流程编辑兼容，不会把动态 feature 自动
加入生产节点库或统一搜索。

## 生产代码、兼容代码与已删除代码

当前生产路径：

- `App.tsx`：主题、快捷键、文件打开和唯一 Workbench mount；
- `PlatformWorkbench.tsx`：五个产品入口和运行审计；
- `platform/contracts.ts`：前后端类型；
- `platform/client.ts`：鉴权 REST/WebSocket client；
- `platform/useControlPlane.ts`：连接、事件合并、刷新与 runtime identity gate；
- `FlowCanvas` / `NodeLibrary` / `PropertyPanel`：Studio 的真实编辑面。

明确保留的兼容层：

- `src/renderer/store/executor.ts`：旧 FlowScript 浏览器/Electron 执行兼容；要求真实
  feature 时 fail-fast，不是新 Agent 执行面；
- `python/engine/`、`python/features/` 与 Electron feature bridge：旧 DSL、MCP、HTTP、
  CLI 和已保存 `tool_call` 的兼容面；
- `python/winpilot_home/winpilot/webui/`：诊断/兼容入口，不是产品主前端；
- 历史 NodeDefinition/PropertyPanel：只负责读取旧 artifact，不能绕过 catalog 新建。

本轮删除的不可达或无效代码：

- 重复且无人引用的 `RegionPickerControl.tsx`；现役区域参数使用对应属性面板中的真实
  region selector；
- 从不接入生产节点可达性、只由测试维持的 renderer `featureRegistry.ts` 及其测试；
- Electron 主进程生成后从未消费的模板/计划任务 context 字符串；
- 重复的 legacy engine `runDsl` 分支和未使用的 renderer/import/state；
- 统一搜索中从不建立索引、永远不会返回结果的动态 feature 分支。

保留兼容代码时应写明消费者；没有 import、没有运行入口、没有序列化兼容责任且只靠
自指测试存活的模块，应删除而不是继续伪装成产品能力。

## 前端契约修改清单

后端增加运行时事实时至少检查：

1. `platform/contracts.ts` 是否有明确类型，禁止用 `Record<string, any>` 掩盖字段漂移；
2. 当前 run 与历史 run 是否都能读取；
3. restart 后是否能从 trace/manifest 恢复，还是必须在界面明确标注仅限内存；
4. 列表 API 是否只返回摘要；
5. loading、empty、error、stale runtime 和 unauthorized 是否有不同状态；
6. capability/verifier 工具是否来自同一合同；
7. artifact URL 是否仍经过 run ownership 与哈希复验；
8. 新节点是否同时进入 compiler、catalog、属性面板和测试；
9. WebSocket 是否只触发必要刷新；
10. Vite 浏览器和 Electron production build 是否都验证。

TypeScript 的 renderer、Electron main、CLI 和 AI 配置都启用
`noUnusedLocals/noUnusedParameters`，`npm run typecheck` 会检查四套配置，防止死 import、
失效参数和只在某个 bundle 中隐藏的代码重新累积。

## 验证层级

- mock 单元测试：contract/client/view helper、组件 loading/empty/error、capability gating；
- 组件集成：历史 run 通过真实 client shape 按需加载合同/trace/artifact/evidence；
- Python API 集成：通用 cancel、轻量 run/job 列表、持久化 audit、受保护 artifact；
- 浏览器视觉验收：启动当前 FastAPI 与 Vite，通过产品导航点击运行记录和系统设置，
  检查真实历史 run、缩略图、滚动布局、筛选及控制台错误；
- Windows E2E：必须走 `/api/v2/task-contracts/compile → /api/v2/arp/runs`，使用外部
  ground truth 或真实目标应用证据，不能用 mock 命名为 E2E。

浏览器组件验收可以读取持久化 run，核对 stage、artifact、evidence 和 completion
展示，但它只证明前端审计链路，不是重新执行真实 GUI，也不能替代 Electron 客户端
E2E。

持续交互的前端状态与终态分离：只有 Task Contract 声明需要真人继续时，运行卡才显示
`waiting_external`、“等待外部参与者”和“持续会话尚未终结”。Agent 当前回合已通过会
单独显示，用户未继续时显示“外部参与者未响应”，不会渲染为整个任务“已完成”，也不会
把普通自主任务的失败改成等待。共享回合需要外部参与者时应从自主终态矩阵排除，
详细通过定义见 `testing.md`。

## 已知限制

- 在本次 snapshot 持久化之前产生的 trace 没有原始 Task Contract 文本；界面只展示
  可直接从事件证明的阶段与证据，不补猜 intent。
- 用户删除本机 trace/artifact 文件后，历史 metadata 仍可见，但哈希校验下载会 404。
- Runtime health 展示最近 job 摘要，不是长期性能数据库或大规模 dashboard。
- 浏览器视觉验收验证布局与 API 数据闭环，不替代真实 Electron 菜单、系统通知、
  Windows 输入和第三方应用 E2E。
