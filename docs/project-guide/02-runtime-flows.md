# 02. 运行时全流程：从点击发送到外部结果

> 本章沿真实调用顺序讲解当前源码。伪代码省略日志和类型样板，但保留 owner、校验顺序、失败语义与并发边界。

## 1. 应用启动

```mermaid
sequenceDiagram
  participant OS as Windows
  participant EM as Electron main
  participant V as Vite/Renderer
  participant WP as winpilotServer.ts
  participant PY as serve.py/FastAPI

  OS->>EM: 启动应用
  EM->>EM: requestSingleInstanceLock
  EM->>EM: createWindow(contextIsolation=true)
  EM->>V: dev 加载 :5200 / prod 加载 dist HTML
  par 托管 Python
    EM->>WP: startWinpilotServer()
    WP->>WP: 选择 >=12000 的可用 loopback 端口
    WP->>PY: spawn python -u serve.py PORT 127.0.0.1
    WP->>PY: GET /api/status + Bearer token
    PY-->>WP: contract_version/source/git identity
  and 兼容设施
    EM->>EM: startApiServer(:7880)
    EM->>EM: scheduler.start
    EM->>EM: 延迟 program_refresh
  end
  V->>EM: preload.ensureWinpilot()
  EM-->>V: 动态 URL + token
  V->>PY: GET /api/v2/platform
  V->>V: 校验 contract version 3 与 source/commit
```

通俗伪代码：

```text
启动 Electron:
  如果已有实例:
    激活已有窗口并退出新实例

  创建隔离窗口
  后台启动 Python Runtime
  主进程最多等待约 120 秒，轮询 /api/status，直到：
    HTTP 可用
    contract_version == 3
    loaded source stamp == current source stamp
    source.drifted == false
    可比较的 Git commit 相同

  Renderer 获取动态 URL/token，并只在首次连接阶段轮询约 35 秒
  获取 platform、当前 run、最近 runs
  建立 /ws/events，并保留 5 秒 HTTP fallback poll
```

如果 readiness 超时、协议不兼容或源码漂移，UI 进入 degraded/offline；不会把任务发给旧服务。这里有一个真实恢复缺口：主进程 readiness 最多可等约 120 秒，而 Renderer 首次轮询只约 35 秒；只有首次连接成功后才安装 5 秒 fallback interval。因此 Python 若在第 35～120 秒才 ready，当前 UI 不会自动重新连接，通常要重载 Renderer。

## 2. 用户发送一条 goal

`PlatformWorkbench.tsx` 中的 Agent composer 先做 **Renderer 全局单活 gate**：只要 `plane.currentRun` 的状态不在终态集合，就会阻止另一个 Thread 提交。服务端另有较窄的 **每 Thread gate**，拒绝该 Thread 已有 `queued/running/paused` Turn。Enter 或可见发送按钮最终调用 `plane.startGoal(goal, threadId)`。

```text
execute():
  若 goal 为空或正在提交 -> 返回
  若全局 current run 是任意非终态（包括 canceling/未知非终态） -> 显示“当前任务仍在运行”
  若 runtime identity 不兼容 -> 显示错误，不发送

  POST /api/v2/goals/runs {
    goal: 原始文本 trim 后内容,
    thread_id: 当前服务端 Thread ID 或空串
  }

  保存返回 run_id 到 UI 的 Thread run 列表（最多 40）
  清空已发送输入框
```

前端不会在这里：

- 捕获桌面；
- 选择 GUI、connector、CLI 或模型；
- 编译 Task Contract；
- 提交 ARP 图；
- 注入应用类型、坐标、目标窗口或执行策略。

## 3. Goal Admission 与 Thread/Turn

服务端的顺序很重要：

```mermaid
flowchart TD
  Request["GoalRunRequest"] --> Drift{"runtime_source_drifted?"}
  Drift -->|是| R409["409，要求重启"]
  Drift -->|否| Config["CONFIG.snapshot"]
  Config --> Admit["admit_goal"]
  Admit --> Enabled{"vnext.live_agent_enabled == true?"}
  Enabled -->|否| NoFallback["409，无 Legacy fallback"]
  Enabled -->|是| Exact["build_exact_live_vnext_graph(goal)"]
  Exact --> Thread["AgentThreadStore.begin_turn"]
  Thread --> Prepare["_start_arp_execution(vnext)"]
  Prepare --> Bind["bind run ↔ thread/turn"]
  Bind --> Response["run_id/thread_id/turn_id/admission/graph"]
```

`AgentThreadStore.begin_turn` 的行为：

```text
如果 thread_id 非空但不存在 -> 拒绝
如果该 Thread 有 queued/running/paused Turn -> 拒绝
否则：
  读取最多最近 6 个成功 Turn
  每个 Turn 展开为 user goal + assistant result
  总计最多 12 条消息、16384 字符；从最旧 pair 开始裁剪
  新建 queued Turn
  单 Thread 最多 40 个 Turn；全局最多 100 个 Thread
```

Actor 请求层还会二次执行 `_bounded_thread_messages`：把“成功历史 + 当前 goal”共同限制在 16384 字符内，并继续按完整 user/assistant pair 丢弃最旧历史。因此 ThreadStore 的 16384 是历史自身上限，不保证一个接近上限的新 goal 能与全部历史同时发送。

只有 `arp_run_done` 状态为 `done` 或 `reply` 且存在非空 `user_result` 的 Turn 才进入下一轮历史和 continuation checkpoint。失败、取消、blocked、空结果不会污染对话。

## 4. 精确三节点壳与唯一语义节点

产品图不是模型计划。它严格固定为 `start → full-goal → end` 三个节点、两条边；只有 `full-goal` 是 `AGENT_GOAL` 语义节点。policy 要求单次 node visit、fail fast、done 需要 evidence、shell 禁止、desktop mutation 允许。图 ID 由完整 goal 确定性绑定。

```text
build_exact_live_vnext_graph(goal):
  assert goal 是非空原文
  nodes = [START(id="start"), AGENT_GOAL(id="full-goal", goal=goal), END(id="end")]
  edges = [start -> full-goal, full-goal -> end]
  policy = {
    max_node_visits: 1,
    fail_fast: true,
    require_evidence_on_done: true,
    allow_shell: false,
    allow_desktop_mutation: true
  }
  graph = Graph(nodes, edges, policy)
  assert len(graph.nodes) == 3 and len(graph.edges) == 2
  return graph
```

`prepare_live_vnext_execution` 再次完整校验图，而不是相信调用者；初始 inputs 必须为空，禁止预绑定桌面。

## 5. 初始 Actor 轮：三种分支

`_PreparedHandler` 是 call-once ARP node handler。它先创建模型资源和 ConnectorGateway，然后调用 `LiveVisualActor.begin_turn`。

```mermaid
flowchart TD
  Begin["begin_turn(goal, successful history)"] --> Model["一次 provider 调用"]
  Model --> Choice{"模型输出"}
  Choice -->|纯文本| Reply["reply 终态\nElectron 一直可见"]
  Choice -->|observe_desktop| Desktop["申请 desktop lease"]
  Choice -->|search/call connector| MCP["Gateway 执行只读调用"]
  MCP --> Model
  Choice -->|未批准工具/多工具/坏结构| Fail["initial turn fail closed"]
```

初始轮只提供：`observe_desktop`，以及 Gateway 可用时的两个 connector 元工具。最多进行有界 connector 往返，最后一次只保留终止工具，防止无限 connector 循环。

### 5.1 直接 reply

如果模型返回非空文本且没有工具调用：

```text
runtime_state.user_result = 原始 reply
output.status = "reply"
发布 live_agent_done(status="reply")
ARP node 以 branch="reply" 成功结束
Thread 将 reply 保存为成功 Turn
Electron 不申请 lease、不隐藏、不产生 GUI physical_attempt
```

这条路径不是“伪装成已完成的 GUI task”，而是同一语义 Actor 对普通问题的合法终态。

### 5.2 Connector

模型选择 connector 后：

```text
解析恰好一个 connector tool call
Gateway 搜索或重新核验并调用 read-only 工具
把结构化结果作为 provider tool message 追加
同一 Actor 再决定：
  回复
  请求桌面
  或再做一次有界 connector 调用
```

Connector 不会创建第二个 Thread、Mission 或 Agent。

### 5.3 请求桌面

模型调用 `observe_desktop` 后，`DesktopLeaseSlot` 从 `idle` 转为 `waiting`，生成并广播精确 `lease_id`。

## 6. Desktop lease：为什么要两阶段交接

若在 Electron 仍位于前台时捕获“目标窗口”，首帧很可能是控制面自身。因此产品要求先让出、后捕获、再绑定。

```mermaid
sequenceDiagram
  participant P as Python DesktopLeaseSlot
  participant WS as /ws/events
  participant R as Renderer useControlPlane
  participant E as Electron main
  participant D as Desktop context API

  P->>WS: desktop_lease_requested(run_id, lease_id)
  WS->>R: event
  R->>E: prepareDesktopHandoff()
  E->>E: setSkipTaskbar(true) + minimize + wait 160ms
  E-->>R: yielded, control_surface_pid, prepared_at
  R->>D: GET desktop-context?exclude_process_id=Electron PID
  D-->>R: HWND/PID/title/class/region/captured_at
  R->>E: bindDesktopHandoff(run_id)
  R->>P: POST desktop-lease(lease_id, execution_context)
  P->>P: freeze target + validate exact identity
  P-->>R: status=delivered, target identity
```

服务端 lease 状态机：

```mermaid
stateDiagram-v2
  [*] --> idle
  idle --> waiting: Actor requests desktop
  waiting --> validating: matching run_id + lease_id bind
  validating --> delivered: target frozen successfully
  validating --> waiting: target validation rejected
  waiting --> closed: client fail / cancel / timeout
  validating --> closed: client fail / cancel
  delivered --> [*]
  closed --> [*]
```

Renderer 还有一个不同于服务端状态机的一次性去重集合：它在真正调用交接前就把 `run_id:lease_id` 加入 `desktopLeaseHandled`，仅“此刻还没有 client”这一种情况会删除。因此同一 Renderer 对同一 lease 的一次 fulfill 若已经开始后失败，不会因后续 WebSocket 或 HTTP snapshot 再自动重试；通常等待服务端 fail-close 或超时。上图的 `validating → waiting` 只表示服务端可接受的状态迁移，不承诺客户端会再次提交。

### 6.1 响应丢失竞态

如果 `POST desktop-lease` 在网络层报错，Renderer 不能立刻恢复窗口，因为服务端可能已经收到了 bind，只是响应丢失。它会查询精确 run：

```text
若同 run_id + lease_id 的服务端状态是 delivered:
  认为绑定成功，继续保持让出
否则若 run 已终态或 lease closed:
  恢复 Electron
否则若 lease 仍 waiting:
  尝试 fail 同一 lease
  只有服务端确认 closed 才恢复
否则:
  保持让出并抛出原始错误（避免抢回正在执行的桌面）
```

### 6.2 正常终态恢复与现存缺口

Electron 本地保存 `_desktopHandoffRunId`；`releaseDesktopHandoff(run_id)` 自身要求 ID 精确匹配。正常的同一 Renderer 生命周期里，匹配 run 的终态事件，或此前已观察为 active 后又通过 HTTP 看到的终态 transition，会调用它恢复任务栏、显示、restore 和 focus；旧 run、重复 terminal 或空 ID 不能通过这个函数释放当前 handoff。

但这还不是无条件架构保证，源码中有两个真实缺口：点击系统通知、启动第二个应用实例都会直接 restore/show/focus，并不检查或清除 pending handoff，可能在任务中途抢回前台；如果 Renderer 在 run 已终态后才重载，首次 HTTP hydration 没有 previous active transition，也可能不触发 release，使主进程保留 pending ID。测试和故障分析必须把这两种情况单列，不能笼统声称“任何终态都可由 HTTP 自愈”。

## 7. 冻结目标

服务端把客户端 execution context 与 server-owned graph `task_id` 绑定，再从 target facts 构造 `_FrozenTarget`。主要事实包括：

- `hwnd` 与 root HWND；
- process ID；
- 可执行文件 identity；
- title 与 class name；
- 目标物理 region；
- capture timestamp；
- 基于这些事实生成的 binding ID；
- 后续观察窗口生命周期所需的通用 probe。

冻结并不表示窗口永远不变。默认 full-desktop 帧允许模型点击屏幕上的任意可见窗口；binding 只帮助下一帧观察和通用窗口生命周期，不是前台或 hit-test veto。

## 8. 构造 live session

收到 `_FrozenTarget` 后，`_build_live_session` 建立共享对象：

```mermaid
flowchart TD
  Target["FrozenTarget"] --> Mission["MissionDefinition + Reducer"]
  Target --> Workspace["DesktopWorkspace"]
  Target --> Observer["FrameObserver"]
  Observer --> Authority["FrameAuthority"]
  Authority --> State["_LiveRuntimeState"]
  Catalog["8-tool ToolCatalog"] --> Broker["CapabilityBroker"]
  Actor["同一个 LiveVisualActor"] --> Runtime["MissionRuntime"]
  Mission --> Runtime
  State --> Runtime
  Broker --> Runtime
  Adapter["AtomicDesktopAdapter"] --> Runtime
  Registry["EvidenceRegistry"] --> Runtime
```

组合时只核对一次 Runtime、Reducer、Broker、Catalog 与 Registry 的共享 owner；binder 不在每轮 proposal 上重放整套 Mission/Broker 组合校验。

## 9. 每个视觉 Turn 的完整算法

`MissionRuntime.run_one_turn` 用非阻塞锁保证同一 Runtime 只有一个 turn 获得 admission。

```text
run_one_turn(turn_index):
  若另一个 turn 持锁 -> BUSY
  若 Mission 已 VERIFIED/CANCELED/BLOCKED/FAILED -> 返回对应终态
  若收到 cancel -> 写 mission_canceled

  initial = state.observe(current projection, turn_index)
  broker_projection = broker.project(projection, initial.broker_state)
  若 route != VISUAL_ACTOR -> UNAVAILABLE

  turn = project_actor_turn_input(
    original mission card,
    current frame facts,
    broker tool views,
    bounded task_state,
    previous action local intent,
    safe completed action summaries
  )

  raw_decision = actor.propose(turn, current JPEG, exact submitted_size):
    同时暴露本轮 Broker action/terminal tools 与两个 connector 元工具
    若选择 connector -> Gateway 执行只读调用并把 tool result 交回同一 Actor
    本地只读工具链（status/shell/connector/window_text）只受剩余 model-call budget 约束
    模型可调用 refresh_desktop 主动结束局部工具链换取新整屏，最终提交一笔 action / claim / blocked
  尝试应用有界 task_state_update（它不是权威 Mission 状态；格式错误只忽略该更新）
  在 MissionRuntime 入口把 raw proposal 做唯一一次 typed parse

  再取 current snapshot：
    若 frame/target/broker projection 在模型思考期间变化 -> STALE_DISCARDED

  bind ActorDecisionEnvelope 到：
    mission/run/revision/obligation
    frame/target/submitted image
    allowed evidence scope
    broker projection

  如果是 desktop action -> 执行一笔机械调用
  如果是 claim -> 记录并完成 Mission
  如果是 blocked -> 写 obligation_blocked
```

Actor 每次都收到原始 goal，而不是仅收到局部计划。Connector 不只存在于初始分流：每个视觉 Turn 的 `propose` 也可在当前 JPEG 和 Broker 工具旁调用 connector，只受剩余 model-call 预算约束；同一个 Actor 在同一 Turn 最终提交恰好一笔 Runtime action、claim 或 blocked。`task_state` 与紧邻一笔 provider 原生 exchange 只帮助短期连续性；它们不是 Runtime 计划或完成判据。

## 10. 模型响应的单次边界解析

视觉轮由 `LiveVisualActor` 解码 provider 返回的一笔 function call，确认工具名、JSON object
与该动作必需的 provider 形状，并输出 raw proposal。`MissionRuntime` 入口随后只调用一次
`parse_actor_proposal()` 得到 typed proposal；Envelope binder 和 `AtomicDesktopAdapter` 不再
各跑一次完整工具 schema。模型附带的无关提示字段不会让一笔本可执行的桌面动作假失败；
格式错误的辅助 `task_state_update` 只被忽略，物理 adapter 只读取动作真正需要的字段。

malformed、零调用、多调用或未知工具仍作为可纠正的 `INVALID_PROPOSAL` 回到同一个模型；
这只是格式恢复，不修改模型坐标，不按应用套配方，也不在物理动作后重放。

## 11. 坐标映射

Actor坐标的唯一语义是“它收到的 JPEG 像素”。映射关系为：

```text
submitted image point (sx, sy)
  按 submitted_width/height 与 authoritative frame width/height 做一次比例映射
  加 authoritative frame region 的物理左上角
  得到 virtual desktop physical point (px, py)
```

Envelope 不按 submitted image 的宽高拒绝模型坐标。Adapter只要求数值有限，按同一线性关系
映射一次；RawDevice 在最终 SendInput 边界处理操作系统可表示范围。默认 full-desktop 路径
不强制目标窗口置前，也不以 foreground 或 hit-test 推断否决模型指向的屏幕位置。

Runtime 不做最近按钮吸附、DPI 猜测 offset、OCR重瞄或按应用校正。若 raw point、canonical point 与 physical point 的确定性映射一致，错误目标属于模型选择层。

## 12. 一笔物理动作事务

```mermaid
sequenceDiagram
  participant MR as MissionRuntime
  participant AD as AtomicDesktopAdapter
  participant FA as FrameAuthority
  participant Q as DesktopActionQueue
  participant RD as RawDevice
  participant FO as FrameObserver

  MR->>AD: execute(bound envelope)
  AD->>FA: run_if_current(frame identity)
  FA-->>AD: pinned before frame
  AD->>AD: finite numbers + one canonical→physical mapping
  AD->>Q: queue one physical action
  Q->>AD: invoke callback at queue head
  AD->>RD: click/drag/scroll/type/hotkey once
  RD-->>AD: RawActionReceipt
  alt release cleanup incomplete
    AD->>AD: 同一 queue lease 内最多两轮 pointer/key UP cleanup
  end
  AD->>FO: capture post-action frame
  FO-->>AD: next authoritative screen
  AD-->>MR: ControllerTransition
  MR->>MR: append mission event; trace observes alongside
```

### 12.1 Controller outcome 语义

| Outcome | `input_completed` | Runtime 行为 |
| --- | --- | --- |
| `COMPLETED` | `true` | 写 transition event，下一 Turn 让同一个 Actor 观察新屏幕 |
| `REJECTED` | `false` | 若是明确可恢复的陈旧/目标变化原因，返回 `STALE_DISCARDED`；绝不自动重放 |
| `UNKNOWN` | `true` | 写 `transition_recorded`、返回 `ACTION_RECORDED`；下一帧恢复，不重复输入 |
| `UNKNOWN` | `false` 或 `null` | identity 一致且释放清理完成时返回 `STALE_DISCARDED`，在新屏幕上交给 Actor 恢复；不重放 |
| 异常 | 未知 | 视为 mutation 后不确定，fail closed，禁止重复调用 |

任何路径都不能把同一个 proposal 执行两次。

## 13. 六种 GUI 动作

### 13.1 click / double_click

输入一个 submitted-image 点；Adapter映射并执行一次单击或系统双击事务。`double_click` 不是两个独立 Actor Turn，因此不会在两击间重新观察。

### 13.2 drag

输入起点、终点、最多 32 个有序中间 `image_points` 和 `keep_holding`。空中间点表示直线。路径只表达通用连续指针，不携带“画图”“滑块”或“走廊”语义。

### 13.3 scroll

输入点和非零整数 amount，范围 `-1200..1200`；负值向下、正值向上。点位仍需命中当前目标。

### 13.4 type_text

输入 1..4096 个 Unicode 码点；RawDevice 在目标/前台/final guard 成立后向当前目标的焦点发送 OS 输入。它不检测焦点控件是否可编辑，因此 receipt 可以显示输入已完成但文本没有落入期望输入框；通常需要前一 Turn 先正确聚焦。

### 13.5 hotkey / provider 名 `press_key`

输入唯一 canonical key 数组。单独一个 modifier 合法；多键组合必须按 `ctrl → alt → shift → win` 的 canonical modifier 顺序，最后恰好一个主键，长度最多“修饰键数 + 1”。模型面为避免特定 provider/tool 语义冲突使用 `press_key` 名，解析后回到 Runtime `hotkey`。

### 13.6 held pointer

当 drag 设 `keep_holding=true` 且物理输入完成，Adapter保留一个 run-owned lease。下一帧 Broker只提供 drag；新 drag 可以继续移动并再次保持，或释放。超时、异常、取消、close 都会做有界 `mouse_up` 清理。

### 13.7 launch 与 capture

`launch_application` 发现已运行进程时稳定合并可见/隐藏候选，优先普通窗而不是
`WS_EX_TOOLWINDOW`，只恢复排序后的一个候选；没有应用 recipe。`capture_screen` 不在
vNext重复验证绝对路径、扩展名或文件存在性，相对 path由物理 backend按 Runtime cwd处理。

## 14. 动作后的新屏幕

完成一笔输入后，Runtime只确认 ControllerTransition 回指同一 action，并把 receipt 与下一张
权威屏幕交给同一个 Actor。trace 可以旁路记录 model arguments、physical attempt 和 receipt
以定位首分歧，但不再有独立 reconcile/verification 层据此判 task pass。外层
`_PreparedHandler` 在 `ACTION_RECORDED` 或 `STALE_DISCARDED` 后继续下一 Turn。

## 15. claim、blocked 与终止

### 15.1 claim

```text
Actor: claim_obligation_done(concise_claim)
Runtime:
  接受同一个 Actor 基于当前真实屏幕作出的完成判断
  在任何辅助 history/snapshot 之前缓存 claim 与最终 narration
  将 claim 投影为 Mission 完成状态
  保存 concise_claim 为 user_result
```

这里没有第二个视觉模型、deterministic pass verifier、trace gate 或 memory gate。辅助
Mission history、completion snapshot 或诊断投影失败不能推翻已缓存的 claim，也不能丢失
`user_result`。E2E harness仍可观察真实应用终态来衡量产品完成率，但不会把测试判定反馈
进生产执行线。

### 15.2 blocked

Actor提交规范 `blocker_kind` 与去重的 `observed_facts`，Runtime写 `obligation_blocked` 并终止。它适用于当前可见事实确实无法用供应动作推进，不应被用作模型不愿尝试的普通退出。

### 15.3 预算耗尽

默认 `max_turns=200`、`max_runtime_s=7200`、`max_model_calls=400`。配置只要求三项都是正整数，不再用派生上限拒绝更长任务；预算仍在 run 开始冻结，耗尽返回 `STATE_REJECTED`。

## 16. Pause、resume、cancel

- pause 只在安全边界等待；暂停耗时会补回 deadline；
- 如果 pause 在 Actor admission 前到达，当前 `UNAVAILABLE` 可投影为安全的 stale boundary；
- cancel 设置 run stop event；Actor、connector、lease wait、queue 和外层 deadline 都观察它；
- 若输入尚未开始，明确 canceled/rejected；
- 若输入已开始或结果未知，先记录真实 receipt，再进入保守终态；
- close 时无论任务结果如何都必须清理 held pointer 与模型资源。

## 17. Runtime 状态循环

```mermaid
stateDiagram-v2
  [*] --> InitialActor
  InitialActor --> Reply: text reply
  InitialActor --> LeaseWaiting: observe_desktop
  InitialActor --> InitialActor: connector result
  LeaseWaiting --> VisualTurn: target delivered
  LeaseWaiting --> Failed: timeout/fail/cancel
  VisualTurn --> VisualTurn: action recorded
  VisualTurn --> VisualTurn: stale discarded
  VisualTurn --> VisualTurn: malformed/error redecision within remaining budget
  VisualTurn --> Verified: claim accepted
  VisualTurn --> Blocked: blocked report
  VisualTurn --> Failed: state rejected/budget/error
  VisualTurn --> Canceled: cancel
  Reply --> [*]
  Verified --> [*]
  Blocked --> [*]
  Failed --> [*]
  Canceled --> [*]
```

## 18. WebSocket 与 UI 刷新

`/ws/events` 发布 BUS 事件。Renderer：

- `desktop_lease_requested` 立即触发 lease fulfillment；
- `agent_start` / `arp_run_start` 标记真实 live start；
- 非 ping 事件保留最近 200 条 UI 列表；
- 活动事件在约 80ms 后只刷新 current run；
- 终态事件刷新 platform/current/recent runs；
- 每 5 秒做一次 fallback 全刷新；
- 正常同一 Renderer 生命周期内，终态触发任务栏通知并尝试匹配 handoff 恢复。

UI 事件流只是投影；HTTP 快照能恢复大多数 run 状态，但不能重放所有一次性客户端副作用。特别是终态后才重载 Renderer 的 handoff release 缺口，仍按第 6.2 节处理。

## 19. Studio / Automation IR 流程

Studio 与产品 Agent 共用 Electron 和部分 Python基础设施，但入口、输入与成功语义不同。

```mermaid
flowchart LR
  Editor["Studio 当前 FlowScript"] --> Convert["automationIrFromFlowScript\n固定 runtime.kind=arp_graph"]
  Convert --> Validate["前后端结构验证"]
  Validate --> StartAutomation["POST /api/v2/automation-ir/runs\nControlPlaneClient 要求 response.run"]
  StartAutomation --> Graph["arp_graph_from_automation_ir"]
  Graph --> LegacyARP["默认 ArpRuntimeVersion.LEGACY"]

  WpsCaller["其他 API 调用者提交\nruntime.kind=wps_replay"] --> WpsBranch["同一路由的 WPS 分支\n返回 replay_id + job，不返回 run"]
  Assets["资料页的非 arp_graph 资产"] --> OldReplay["旧 POST /api/replay"]
  WpsBranch --> Replayer["RuntimeJob + trace.Replayer"]
  OldReplay --> Replayer

  LegacyARP --> Nodes{"节点 kind"}
  Nodes -->|ACTION_BATCH| ToolSession["run 内复用 ToolSession"]
  Nodes -->|REPLAY_STEP| SharedReplayer["共享 Replayer"]
  Nodes -->|control built-ins| Direct["直接执行"]
  Nodes -->|SKILL_REPLAY / TASK_STAGE| Handler["各自 handler"]
  Nodes -->|AGENT_GOAL| OldAgent["旧 AgentRunner"]
```

通俗伪代码：

```text
点击 Studio 的运行按钮:
  Renderer 将当前图导出为 canonical Automation IR
  导出器固定 runtime.kind = arp_graph
  前后端都做结构验证
  POST automation-ir/runs，并要求响应包含 ARP run
  编译为 ARP graph；未显式 runtime_version 时使用 legacy ARP runtime
  对每条控制流路径检查 spatial action 前已有 target
  control built-ins 直接执行
  只有 ACTION_BATCH 在 run 内复用 ToolSession
  REPLAY_STEP 用共享 Replayer；SKILL_REPLAY/TASK_STAGE 用各自 handler
  AGENT_GOAL 明确调用旧 AgentRunner，不是产品 LiveVisualActor
  未知/历史 LEGACY_FLOW -> 明确迁移错误，禁止 fallback
  每节点都记录 history 并发布 arp_node_done
  只有 NodeResult.evidence 非空才追加/发布 evidence
  End 按 graph policy / contract 检查已有 evidence

若其他调用者向 automation-ir/runs 提交 wps_replay:
  服务端直接启动 RuntimeJob + Replayer
  响应是 replay_id/job，不是 Studio client 期待的 run

若资料页重放非 arp_graph 资产:
  走旧 /api/replay，不经过 Studio startAutomation
```

无模型确定性节点的 run 应有 `model_calls=0`。如果一个旧节点仍需要模型，这必须由该节点明确 owner；例如 Studio catalog 的 `agent.goal` 当前就是 Legacy `AgentRunner`，不能因为路由带 `/api/v2/` 就称它为产品 Visual Actor。

## 20. Human Apprentice、导出和 replay

这三条相关但不等价：

```mermaid
flowchart TD
  HumanA["普通 /api/human/start|stop"] --> RecorderA["HumanRecorder"]
  RecorderA --> WPS["WPS + 可导出的 IR"]

  HumanB["Apprentice start|stop"] --> RecorderB["HumanRecorder + metadata"]
  RecorderB --> Canonical["canonical Automation IR"]
  Canonical --> Provisional["直接创建 provisional memory"]
  Canonical -. "可选人工审查" .-> Studio["Studio"]
  Provisional --> Verification["memory verification run\n真实 Replayer + structured postflight"]
  Verification -->|通过| Verified["replay_verified memory"]

  AgentRuns["成功/失败 Agent runs"] --> Exporter["Thread/run trace exporter"]
  Exporter --> Partial["完整或 partial manifest"]
  Partial -. "可选" .-> Studio
```

- 普通 Human recording 只生成 WPS/IR；Apprentice 才追加 metadata 并在 stop 时直接创建 provisional memory，Studio 审查是可选步骤；
- Human recording 证明“录下的人类动作可否规范化并忠实重放”；
- Thread export 证明“已有 Agent trace 中哪些普通动作可提取”；held gesture 等不完整类型可以被明确拒绝并标为 partial；
- Memory verification 证明某个 recipe 在当前条件下重放通过；不证明冷启动 Actor 能自主完成任务；
- replay 忠实复制错误动作时，重放链可能通过忠实性检查，但原 Agent目标仍失败。

## 21. 服务重启与 Thread continuation

成功 Thread 的最小 checkpoint 写入 traces 下的私有 continuation 文件，采用临时文件、flush、fsync、`os.replace` 原子替换。启动时只加载格式、大小、ID、时间、状态和上限均合法的成功 Turn。

它保存的只是最近原始 `goal/result` 文本，不保存模型隐藏状态、截图、工具内部计划或失败 Turn。因此服务重启后的上下文是有界对话连续性，不是长期 memory。

## 22. 退出清理

Electron `window-all-closed` / `before-quit` 会停止旧 engine、feature 子进程、托管 WinPilot、兼容 API、scheduler 和全局快捷键。Python session 的 finally 块关闭：

- held pointer；
- evidence journal 并生成 anchor；
- 模型/connector 资源；
- completion snapshot；
- BUS terminal event。

如果任务未验证且清理失败，终态会进一步降为 fail closed；只有已验证结果不会因非语义 telemetry 清理问题被任意改写。
