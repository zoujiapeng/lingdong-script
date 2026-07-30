# 原生前端与运行时视图

当前生产前端不是旧 WinPilot WebUI、iframe 或另一套 Agent 页面。唯一入口是
`src/renderer/App.tsx → components/platform/PlatformWorkbench.tsx`，并通过
`platform/client.ts`、`platform/useControlPlane.ts` 使用 WinPilot `/api/v2` 与
`/ws/events`。本文记录哪些能力必须出现在前端、哪些代码只是兼容层，以及修改时
如何避免再次出现“后端已经有事实，前端却看不到”或“界面能创建但运行时不能执行”
的问题。

## 单一悬浮入口与按需工作区

客户端默认只显示一个圆角悬浮任务输入窗。固定侧栏和常驻 dashboard 已移除；次要
信息不会被删除，而是按使用频率分层收起：

| 层级 | 打开方式 | 当前职责 | 主要运行时事实 |
| --- | --- | --- | --- |
| 智能代理 | 默认主窗 | 原样输入自然语言目标；运行中只显示目标、真实阶段、人工确认、失败或已验证结果 | vNext mission、Visual Actor 当前动作、completion obligation |
| 当前执行 | 任务窗底部活动胶囊，或右侧“工作台” | 暂停/继续/停止、阶段、事件、画面、上下文、证据和运行审计 | 实时 run、timeline、typed stage trace、artifacts、evidence |
| 运行与资料 | 输入窗角标、聚焦后的快速入口，或 `Ctrl+K` | 历史运行、Skill/Memory、系统设置 | 完整 run 详情、replay 可信状态、runtime health、集成和 capability catalog |
| 流程工作台 | 右侧工作台的“流程”，或 `Ctrl+K` | 按需展开完整编辑、校验并运行 `automation-ir/1` / `arp/1` 投影 | 编译器与 capability catalog 的交集、IR fingerprint、真实 ARP 运行 |

运行详情仍然完整可达，但默认不会把 token、事件列表、证据 metadata、模型配置和
系统健康同时铺在任务主面上。活动胶囊负责渐进披露当前节点、工具、阶段和必要控制；
右侧工作台负责完整执行事实；左侧资料浮层负责跨任务信息。人机确认、失败原因和
已验证结果属于任务语义，不能因为界面收缩而隐藏到无法发现的位置。

Windows 客户端使用 Electron 42+ 的透明无框 `BrowserWindow`、Windows 原生圆角合成
与渲染器自绘标题栏，外层保留大圆角和自定义最小化/最大化/关闭控件；圆角之外不得使用
原生阴影或 CSS 外投影，像素必须完全透明。渲染器只用 `border-radius` 与
`overflow: hidden` 约束内容，不能用 `BrowserWindow.setShape()` 拼接整数像素矩形，
也不能再叠加会创建硬裁剪合成层的外框 `clip-path`。主题底层只使用 `--ld-page` 纯色，
不叠加背景图片、网点、渐变或装饰圆形。模型名称沿用输入框内的文字式展开入口；主题
设置沿用 newui 的日夜动效开关。技能与学习在常规窗口宽度下使用紧凑 2×2 网格，
窄于 700px 时才回退为可滚动单列。

任务进入完成、回复、失败、中断、停止或取消等终态后，主结果和失败事实继续保留，同时
在底部持续显示紧凑输入条。首轮与长对话复用 newui 原始 composer 的同一套文本区、
圆形上下文入口、文字式模型入口和发送控件；任务态只应用模板自带的 compact 尺寸，
不能再替换为另一套 follow-up 输入框。当前对话按提交顺序保存最近 40 个 product run id，历史摘要
来自 `/api/v2/runs`，完整结果按需从 `/api/v2/runs/{run_id}` 恢复；因此刷新页面后仍能
继续浏览同一段长对话，而不会把全部 result 塞进 5 秒轮询。长回复默认折叠为 8 行，用户
可随时展开全文。运行期间输入条也保持可见并允许提前起草，但发送和新建对话会暂时禁用，
避免绕过原有单活跃任务边界。

这个连续对话层只组织前端记录，不改写执行语义：每次发送仍把本轮完整原句作为一个新的
`goal` 交给原有 `/api/v2/goals/runs`，不会拼接历史、注入 prompt、切换 runtime 或增加
第二个 Agent。点击“新建对话”只清空当前前端会话索引，不删除 trace、运行记录或证据；
旧轮次仍可从“运行与资料”查看。输入条不会替换活动胶囊、失败原因、证据入口或工作台。

所有入口与真实渲染分支都集中在 `PlatformWorkbench.tsx`。新增前端能力时，不能只在
后端返回字段：必须明确它属于默认任务面、当前执行、资料还是流程工作台，定义其
加载态/空态/失败态，并说明是否需要历史恢复。不能恢复固定侧栏、执行模式开关、
关键词路由或其他前端执行策略。

## 自然语言任务的真实路径

```text
智能代理输入
  → POST /api/v2/goals/runs { original goal, execution_context }
  → Goal Admission
  → one full-goal mission / LiveVisualActor
  → CapabilityBroker / AtomicDesktopAdapter / RawDevice
  → BUS + WebSocket
  → GET /api/v2/runs/current（活跃详情）
  → GET /api/v2/runs/{run_id}（按需历史详情）
```

前端把整句目标原样提交给 Goal Admission，不编译 Task Contract，不提交执行模式，
也不根据关键词选择 GUI、shell 或 provider。运行控制绑定返回的 run；停止使用统一
run cancel。取消是协作式机械边界：已经开始的单个输入先安全收尾，held pointer
必须释放。流程工作台和历史兼容入口仍可显式使用 Task Contract/ARP，但它们不是
“智能代理”的 fallback，也不能替代真实产品 E2E。

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
- `PlatformWorkbench.tsx`：悬浮任务主窗、活动胶囊、资料/执行浮层、命令面板和完整
  运行审计；
- `styles/floating.css`：悬浮层级、圆角窗口、紧凑/响应式布局及深浅色视觉 token；
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
- 浏览器视觉验收：启动当前 FastAPI 与 Vite，从悬浮任务窗依次打开资料、执行工作台、
  命令面板和完整流程编辑器，检查真实历史 run、缩略图、滚动布局、筛选、小窗口响应式
  布局及控制台错误；
- Windows E2E：必须从可见 Electron“智能代理”走 `/api/v2/goals/runs`，使用外部
  ground truth 或真实目标应用证据，不能用 mock 命名为 E2E。

浏览器组件验收可以读取持久化 run，核对 stage、artifact、evidence 和 completion
展示，但它只证明前端审计链路，不是重新执行真实 GUI，也不能替代 Electron 客户端
E2E。

Legacy 持续交互记录仍可显示 `waiting_external`，但 vNext 主 Agent 不由前端或
Task Contract 推断任务应等待。模型只能从原始 goal、当前画面和可见工具决定继续、
claim 或 blocked；前端不能把普通自主任务失败改写成等待或完成。共享回合的测试
边界见 `testing.md`。

## 已知限制

- 在本次 snapshot 持久化之前产生的 trace 没有原始 Task Contract 文本；界面只展示
  可直接从事件证明的阶段与证据，不补猜 intent。
- 用户删除本机 trace/artifact 文件后，历史 metadata 仍可见，但哈希校验下载会 404。
- Runtime health 展示最近 job 摘要，不是长期性能数据库或大规模 dashboard。
- 浏览器视觉验收验证布局与 API 数据闭环，不替代真实 Electron 菜单、系统通知、
  Windows 输入和第三方应用 E2E。
