# GUI Agent 全量架构审计

审计日期：2026-07-18
审计目标：让真实 Electron 客户端中的 GUI agent 依靠可迁移的视觉理解、鼠标键盘闭环和结果验证完成未知任务；11 个样例只作为黑盒探针，不作为生产规格。

## 结论

当前项目不是“缺少几个样例补丁”，而是已经出现明显的架构膨胀和职责重叠。继续逐例加 guard、ledger、route、prompt 或专属 adapter 会进一步降低未知任务能力。

本轮确认的硬事实：

- 全仓排除依赖、构建产物、缓存和运行产物后约有 526 个源码文件、231,147 物理行；其中生产源码约 385 文件、163,319 行，测试源码约 141 文件、67,828 行。
- 交接文档指定的核心范围 `python/winpilot_home/winpilot + src + scripts` 有 269 个 Python/TypeScript/JavaScript 文件、约 120,856 行非空源码。
- 当前工作树相对 `HEAD` 的生产代码（含未跟踪文件）净增约 10,736 行；整个工作树约 `+21,049 / -2,519`，净增 18,530 行。
- `AgentRunner` 已接近一个隐式操作系统：`agent/loop.py` 物理行数约 12,793，单类约 11,691 行、176 个方法、135 个初始化状态字段。
- 同一个 GUI 动作会经过或被重复解释于 `AgentRunner`、`ToolSession`、`FastGuiController`、`SubgoalExecutor`、`InteractionStageEngine`、`StageRuntime`、ChatGPT bridge 和 orchestrator handler。
- 默认感知顺序仍是 UIA → Win32 → OCR → CV → VLM；`vlm_first` 在代码中只是保留字符串。UIA/OCR 丰富时仍可跳过 VLM。
- 真实客户端入口存在且最终进入 `AgentRunner`，但大量所谓 E2E 实际直接调用 HTTP API；真实客户端 harness 的结果判定也会把 runtime 自报 `done` 当成通过。
- 当前工作树能通过测试并不代表架构可接受：聚焦测试 `235 passed`；Python 全量 `2025 passed, 13 skipped, 2 warnings`；TypeScript `211 passed`；类型检查通过。

因此本审计的决定是：冻结功能扩张；先统一动作表示、感知权威、会话隔离、客户端验收和执行入口，再讨论增加能力。

## 审计范围与方法

已审查：

- `python/winpilot_home/winpilot` 下全部运行时模块；
- `src` 下 Electron main、preload、renderer、CLI、AI interface 和相应测试；
- `scripts` 下全部 runner；
- `package.json` 中开发、测试和评估入口；
- 当前 git diff、未跟踪生产模块、现有 trace/report 和测试映射；
- Python import 图、文件/函数规模、入口调用链、样例关键词、UIA/OCR/VLM 使用点、动作 dispatch 和完成判定。

审计只把静态未引用标为“候选”，不直接当作死代码；动态 import、IPC、FastAPI route、Electron preload 和模型工具注册都需要调用链或 trace 才能决定删除。

## 当前规模基线

### 核心目录

| 范围 | 文件数 | 非空源码行 | 主要职责 |
|---|---:|---:|---|
| `python/.../action` | 7 | 3,490 | 原子输入执行、队列、安全和动作验证 |
| `python/.../agent` | 39 | 33,632 | 主循环、工具、prompt、guard、状态和恢复 |
| `python/.../fast` | 12 | 12,048 | 批量动作、低延迟 controller、subgoal、视觉 servo |
| `python/.../tasking` | 22 | 20,201 | intent、contract、stage、interaction、artifact、evidence |
| `python/.../perception` | 20 | 5,613 | UIA、OCR、Win32、CV、VLM 和融合 |
| `python/.../orchestrator` | 8 | 3,163 | ARP graph、handler、projection 和 runtime |
| `python/.../memory` | 13 | 2,912 | 长期经验、检索、适用性和反馈 |
| `python/.../recorder` | 5 | 3,020 | trace、回放、示教和人工记录 |
| 其他 Python runtime | 25 | 7,024 | server、配置、集成、eval 和 utils |
| `src/main` | 12 | 3,220 | Electron 生命周期、IPC、托管 Python 服务 |
| `src/renderer`（不含测试） | 64 | 16,959 | 客户端 UI、control plane、流程编辑器和 store |
| `src/renderer/test` | 21 | 3,623 | renderer 单元/组件测试 |
| `src/cli + src/ai-interface` | 18 | 2,020 | 旧 CLI、HTTP/MCP 外部入口 |
| `scripts` | 6 | 3,949 | integration、真实客户端 E2E 和报告脚本 |

### 当前差异的主要来源

| 子系统 | 已跟踪新增 | 已跟踪删除 | 净增 |
|---|---:|---:|---:|
| `python/.../agent` | 7,701 | 2,201 | 5,500 |
| `python/.../tasking` | 1,907 | 114 | 1,793 |
| `python/.../action` | 241 | 18 | 223 |
| `python/.../perception` | 237 | 25 | 212 |
| `python/.../fast` | 207 | 21 | 186 |
| `src` | 173 | 7 | 166 |
| 其他 Python runtime | 114 | 3 | 111 |
| `scripts`（不含未跟踪 harness） | 134 | 37 | 97 |

未跟踪生产模块还包括：

| 文件 | 物理行数约 | 审计判断 |
|---|---:|---|
| `agent/goal_state.py` | 706 | 由“全部点赞/关注/收藏”等样例推动的语言状态机；不得继续接入通用 runtime |
| `agent/graphical_toggle_ledger.py` | 424 | 把灰→彩局部变化推成可复用控件语义，证据不足 |
| `agent/scene_coverage.py` | 794 | 像素场景记录有泛化价值，但与现有 progress/no-progress 状态重叠 |
| `agent/graphical_affordances.py` | 232 | 像素候选可保留为无语义候选，但不能证明可点击或完成 |
| `integrations/mcp_client.py` | 272 | 可选能力来源；不得替代 GUI 闭环 |
| `agent/mcp_tools.py` | 27 | MCP 薄适配层；应保持可选 |
| `tasking/conversation.py` | 74 | 会话/引用解析候选；需与 `agent/session.py` 归并 owner |

## 最大复杂度位置

| 排名 | 位置 | 行数约 | 问题 |
|---:|---|---:|---|
| 1 | `InteractionStageEngine.execute` | 2,436 | 一函数同时规划、执行、验证、等待、恢复和完成 |
| 2 | `AgentRunner._handle_tool_calls` | 1,251 | 手工串接大量 guard/ledger/dispatch/trace |
| 3 | `SubgoalExecutor.execute` | 1,004 | 多任务族快捷路径和专属完成话术 |
| 4 | `TaskContractCompiler.compile` | 902 | intent 到 stage 的大量规则分支 |
| 5 | `AgentRunner._map_normalized_frame_coordinates` | 836 | 坐标映射、约束、兼容和策略混在一起 |
| 6 | `_canonicalize_model_decision` | 737 | 模型 JSON 修补、动作语义和几何规则混合 |
| 7 | `StageRuntime._execute` | 728 | 特殊 stage、child runner、artifact 和完成逻辑 |
| 8 | `_resolve_region` | 585 | 区域语义、几何和 fallback 重叠 |
| 9 | `AgentRunner._inject_visual_frame` | 582 | 截图、UIA/OCR assist、图形候选、账本和 prompt 注入混合 |
| 10 | `refine_interaction_geometry` | 571 | 几何候选、模型判断和修补混合 |
| 11 | `FastGuiController._execute_in_lease` | 531 | lease、dispatch、验证、trace 和恢复混合 |
| 12 | `FastGuiController._ensure_input_mode` | 478 | 输入模式探测包含应用/控件先验 |
| 13 | `SubgoalExecutor._build_actions` | 465 | 子目标快捷字段再次编译为动作 |
| 14 | `RunProjectionStore.on_event` | 440 | 事件投影承担过多兼容分支 |
| 15 | `AgentRunner._compact_tool_evidence` | 425 | 多种结果协议的二次解释 |
| 16 | `orchestrator.graph_compiler.task_contract_to_arp` | 350 | contract/graph 映射过大 |
| 17 | `DoneVerifierMixin._call_done_judge` | 344 | 完成 prompt、证据和模型调用耦合 |
| 18 | `fusion.observe` | 329 | 感知调度和优先级规则集中但权威顺序错误 |
| 19 | `AgentRunner._visual_semantic_grounding_assist` | 311 | UIA/OCR 被自动注入权威截图回合 |
| 20 | `verify_interaction_effect` | 297 | 动作结果、视觉变化和完成语义边界不清 |

## Python 循环依赖

静态 import 图发现三个循环：

1. 七模块大循环：`agent.desktop_tools → tasking.adapters → agent.tools → agent.perception_tools → agent.loop → agent.tool_schemas → tasking.capability`。
2. 三模块循环：`automation_ir ↔ orchestrator.contracts ↔ recorder.trace`。
3. 双模块循环：`tasking.interaction ↔ tasking.interaction_controller`。

第一条循环直接穿过 GUI 主循环、工具层、tasking 和应用 adapter，是小改动不断扩散的主要原因。目标依赖方向应为：`contract/model → policy → executor → evidence`，上层可以依赖下层，下层不得反向 import AgentRunner 或 domain adapter。

## 真实客户端端到端调用链

真实 `npm run dev` 用户路径已确认：

```text
package.json npm run dev
  → Electron main 创建窗口并启动托管 Python
  → PlatformWorkbench Agent Cockpit 的“运行任务”
  → useControlPlane.startGoal
  → POST /api/v2/task-contracts/compile
  → TaskContractCompiler.compile_graph
  → renderer 构造 native_agent_cockpit execution context
  → Electron native desktop handoff（最小化控制面）
  → POST /api/v2/arp/runs
  → server._start_arp_execution
  → orchestrator ArpExecution / TASK_STAGE handler
  → StageRuntime.execute
  → InteractionStageEngine 或 AgentRunner
  → ToolSession / FastGuiController / Action executor
  → fresh frame / verifier / trace / projection
```

这条路径是唯一可作为用户体验验收的入口。单元测试、直接构造 `AgentRunner`、直接 POST compile/run 和 synthetic target 都只能证明局部能力。

## 多套竞争入口

| 入口 | 当前路径 | 判断 |
|---|---|---|
| Agent Cockpit | compile → native handoff → ARP → stage runtime | 唯一产品 owner，应保留 |
| `/api/chat` | 直接 `build_runner` → AgentRunner | 旁路 contract/stage；应转发或删除 |
| legacy Electron IPC `agentStart` | 启动旧 `python -m agent` | 与产品入口竞争；应转发或删除 |
| `/api/agent-start` | 旧 runner | 应降级为兼容转发，不得有独立行为 |
| `AGENT_GOAL` 环境变量 | 自动走旧路径 | 仅测试兼容；应删除或走同一 control plane |
| `gui_eval_run.py` | 固定端口直接 POST compile/ARP | runtime integration，不是客户端 E2E |
| Windows task-contract “e2e” tests | 直接 API/runtime | 必须重命名为 integration |

## E2E 审计

### 真正走客户端的 runner

- `scripts/client_e2e_run.py` 会找到真实 Electron 窗口，通过可见控件填写任务、冷启动和步数，并点击真实“运行任务”。
- `scripts/client_visual_e2e_run.py` 复用上述客户端提交，再用 synthetic visual target 的独立状态做结果判断。

### 当前存在的假通过

1. 普通 11 题只要求 runtime terminal `done` 加长期经验隔离；没有独立最终屏幕、文件、应用状态或外部副作用 oracle。
2. 持续聊天只要求一次成功 `open_conversation` 加任意 `wait/read_text`，没有证明目标联系人、收到 `@电脑`、解析命令、执行命令或验证结果。
3. trace 用运行前后文件名集合差绑定，可能混入并发或残留运行；必须绑定点击后出现的准确 outer run id 和 child ids。
4. 会话清空按钮只在 UI 计数大于 0 时点击；UI 显示 0 时可能跳过真实清空。已有报告声称 `before=0, after=0, cleared=false`，对应 trace 却带入上一条 canceled run。
5. case 3 通过 `case_id == "3"` 特判是否保留 case 2 上下文，这是测试编号控制流，不是 scenario contract。

验收规则必须改成：runtime `done` 只能叫“候选完成”；只有独立 oracle、准确 run 绑定、客户端入口证明和会话隔离同时成立才可 `passed=true`。

## 动作执行重复

已经存在 `fast/action_contract.py`，但以下位置仍分别解析 direct、`run_action_batch` 和 `execute_subgoal`：

- `fast/controller.py::_normalise_action`；
- `agent/partial_output_guard.py::_flatten_actions`；
- `agent/scope_guard.py::_batch_action_parts`；
- `agent/run_state.py` 的 `_batch_*`；
- `agent/goal_state.py` 的动作解析和重写；
- `agent/loop.py` 的空间动作、坐标、连续指针和 guard 分支；
- `agent/done_verifier.py` 的嵌套通信动作提取；
- `agent/failure_classifier.py`；
- `agent/plan_tools.py`；
- `tasking/interaction.py` 和 `interaction_stage_runtime.py` 的模型动作 canonicalization。

`run_action_batch` 和 `execute_subgoal` 在 24 个生产文件出现约 241 次，仅 `loop.py` 就有约 128 次。任何新 drag、scroll、held pointer 或游戏输入都会被迫在多层同步修改。

唯一 owner 应是 `fast/action_contract.py` 中的纯表示层：它把 direct/batch/subgoal 展开为有父动作、顺序、来源、frame/epoch 和 mutation/observe 分类的原子 `ActionEnvelope`。guard 只能消费 envelope，不能再次解释模型 JSON。

## 状态、账本和 guard 重复

当前并行存在：

- `RunStateMachine`；
- visible evidence ledger；
- mutation ledger / `PartialOutputDestructiveGuard`；
- activated click targets；
- `GraphicalToggleTemplateLedger`；
- viewport goal-state obligations；
- `SceneCoverageLedger`；
- `ContinuousSemanticProgress`；
- native no-progress streak；
- target/surface/frame binding；
- `FailureRecoveryLayer`；
- done reject/recovery 状态；
- fast controller cache/watcher/servo 状态。

这些机制在 `_handle_tool_calls` 中按手写 if/elif 顺序互相覆盖，没有统一 policy decision、优先级或 provenance。一个 guard 阻止动作后，另一个 guard 又可能把它改写或放行。

目标结构应只有三类运行状态：

1. `FrameAuthority`：当前 hwnd、capture region、frame id、epoch、坐标系和新鲜度；
2. `ActionTransaction`：一个原子动作及其前帧、执行结果、后帧和因果证据；
3. `TaskProgress`：由根目标 obligations 和已验证 transaction 投影出的进度。

场景覆盖、重复动作和恢复都应是这三类状态的视图，而不是各自保存一套真相。

## 样例特化和产品硬编码

| 位置 | 特化内容 | 处置 |
|---|---|---|
| `agent/goal_state.py` | 点赞/关注/收藏/订阅/全部等词表和正反状态语言机 | 停止接入；后续删除，保留的一般像素变化证据下沉到 transaction verifier |
| `agent/graphical_toggle_ledger.py` | 灰→彩模板被推成通用激活控件 | 停止接入；模板只能是候选，不得带状态语义 |
| `tasking/adapters.py` | `QQ_DESKTOP_LAYOUT` 和 QQ 弹窗固定比例 fallback | 删除专属布局；由当前帧视觉定位 |
| `tasking/compiler.py::_apply_session_context` | 特判“刚刚画的画截图/发送”并删 `draw` | 用已验证 typed artifact/reference 解析替代措辞特判 |
| `tasking/stage_runtime.py` | `DrawArtifact` 专属工具白名单和成功路径 | 将 click/drag/draw/region/verify 原语下沉，stage 只保留产物语义 |
| `fast/subgoal.py` | 绘画、表格、文档、计算器、保存等大量快捷 routine | 逐类迁移到通用动作+证据；未被真实客户端 trace 命中的删除 |
| `agent/task_rules.py` | 抖音、网易云、music 等搜索任务关键词 | 删除应用/样例词；本地应用发现必须查询运行态和安装态 |
| `agent/loop.py` | 通信、上传、研究、绘画等额外 prompt 段 | 用 contract 字段表达，不在主循环按词追加 prompt |
| `scripts/client_e2e_run.py` | case 3 编号特判和弱 persistent oracle | 用 scenario turns/dependencies 和独立 oracle 替代 |
| `scripts/gui_eval_run.py` | Paint 等专属 integration suite | 可保留测试 fixture，但不得影响生产策略，也不得叫客户端 E2E |

测试文件可以包含 11 个样例原文和精确 oracle；生产 runtime、prompt、router、adapter、state key 和坐标不得包含为这些样例服务的控制流。

## UIA/OCR/VLM 权威审计

当前实现与目标存在直接冲突：

- `config.py` 默认 UIA 第一、VLM 最后且默认关闭；当前机器虽然配置了视觉模型，融合顺序仍未改变。
- `fusion.observe` 的 `vlm_first` 明确标记为 future/reserved。
- UIA rich 或 OCR readable 达阈值后可以跳过 VLM，这等于让无视觉理解的信号决定是否进行视觉理解。
- 空快照救援仍先尝试 OCR，再尝试 VLM。
- `structured_ui` 明确跳过 OCR/VLM，QQ communication adapter 使用该模式。
- `AgentRunner._inject_visual_frame` 在初始帧和恢复帧自动调用 `_visual_semantic_grounding_assist`，将 UIA/OCR 候选与权威 JPEG 同时交给模型。
- `InteractionToolMixin._t_click` 可直接对 UIA element 调 `Invoke/Toggle/Select`；坐标点击还会被附近 OCR/UIA 候选重新吸附。
- `FastGuiController._click_element` 会在失败时自行跑 targeted OCR 并把 OCR 文本框当控制候选。
- `action.verify` 仍可用 UIA/OCR 命中确认效果，完成 verifier 又会二次解释这些结果。

整改规则：

1. 当前真实像素和能看图的主模型是唯一语义权威。
2. UIA/OCR 默认不注入视觉回合；只有模型明确请求文字定位，且结果与同一 frame/epoch 的可见像素互证时才返回。
3. OCR 只能给“这个像素框可能包含这些字符”，不能给 role、状态、列表关系或成功语义。
4. UIA element 必须同时满足当前 hwnd、当前 epoch、可见边界和同帧像素文字确认；否则不得成为点击目标。
5. `uia_invoke`、`uia_set_value` 和 OCR 自动吸附默认关闭；通过严格 gate 时记录 provenance。
6. UIA/OCR 不能独立触发 done、successor binding、模板学习或页面分类。
7. E2E 报告必须统计 `uia_only_action_count`、`ocr_only_action_count`，验收目标均为 0。

## 通用能力责任矩阵

| 能力 | 当前实现位置 | 当前问题 | 唯一 owner 目标 | 验证方式 |
|---|---|---|---|---|
| 应用发现 | `perception/apps.py`、`system_tools.py`、`desktop_tools.py`、alias 表 | 多套查找/启动/重开；会忽略已运行实例 | `AppResolver`（安装态+运行态+窗口态） | 三种未预置应用、已运行/托盘/未运行负例 |
| 窗口绑定 | `surface_binding.py`、`scope_guard.py`、ToolSession、loop | 多套 rebind/successor/foreground 逻辑 | `FrameAuthority` | launcher→子窗口、弹窗、跨进程 owner 负例 |
| 权威截图 | `utils/image_capture.py`、`screenshot.py`、loop | capture scope 和坐标映射散落 | `FrameAuthority.capture()` | 多屏/DPI/遮挡/窗口移动 |
| 点击 | `interaction_tools.py`、fast controller、executor | UIA invoke、OCR snap、物理 click 多路线 | 原子 `click` transaction | 文本按钮、图标、画布、游戏三类 |
| 拖动/按住 | executor、interaction tools、action contract、interaction runtime | DrawArtifact 内能力更完整，通用层不一致 | 原子 pointer session | 滑块、拖拽排序、游戏连续拖动 |
| 滚轮 | controller、loop、scene coverage | 方向/区域/边界状态重复 | 原子 `scroll` + transaction delta | 主页面、侧栏、缩放画布 |
| 输入 | executor、fast input locator、ToolSession | UIA SetValue/剪贴板/物理键盘多套 fallback | 原子 text input policy | 原生输入、Chromium、自绘输入 |
| 视觉变化 | `action/verify.py`、`visual_effects.py`、interaction runtime | ok/pixel change/语义完成边界混淆 | `ActionTransaction.verification` | 局部变化、无变化、动画噪声负例 |
| 任务完成 | done verifier、tasking completion、stage runtime | 多层 done 规则和样例 prompt | root obligation verifier | 独立 oracle；open-only/tool-ok 必须失败 |
| 会话引用 | `agent/session.py`、compiler、conversation | 清空竞态；措辞特判；无 generation | typed scenario/session store | 冷启动空上下文、同场景引用、迟到写回 |
| MCP | mcp tools/client、ChatGPT bridge | 可变成绕过 GUI 的第二产品 | 可选 capability provider | 明确来源、失败回退 GUI、不影响未知任务 |

## 删除、归并和迁移顺序

### 第 0 轮：让测试诚实

预算：不新增 runtime 模块；不超过 6 个生产/测试入口文件；生产净新增不超过 150 行。

- 真实客户端 reset 始终可调用并使用 session generation，阻止旧 run 迟到写入新会话。
- harness 必须从准确 outer run id 绑定 trace。
- cold-start 独立 case 必须断言 `session_context=[]`；多 turn scenario 显式声明共享上下文。
- runtime `done` 政名为 candidate completion；无独立 oracle 时 `passed=false`。
- `gui_eval_run.py` 和 direct HTTP tests 明确标记 integration；package 提供真正的 client E2E 命令。

### 第 1 轮：唯一动作规范化入口

预算：最多 4 个生产文件，净代码不增加。

- 在现有 `fast/action_contract.py` 提供唯一 `iter_atomic_actions` / `ActionEnvelope`。
- 首先迁移 `partial_output_guard`、`scope_guard`、`run_state`，删除它们各自的 batch 解析。
- 保留 direct/batch/subgoal 的真实顺序、parent、index、frame/epoch；不加入任务语义。
- 黄金测试覆盖显式格式、shorthand、subgoal 快捷字段和 held pointer。

### 第 2 轮：撤销样例驱动状态栈

预算：目标净删 1,500 行以上，不新增 ledger。

- 断开并删除 `goal_state.py` 的词表/任务语言状态机。
- 断开 `GraphicalToggleTemplateLedger` 的状态语义；需要保留的局部前后帧证据并入 transaction verifier。
- `SceneCoverageLedger` 只保留无法由现有 semantic/no-progress 机制表达的像素场景边，随后删除重复状态。
- 删除 loop 中对应 fields、guard 顺序、prompt hints 和 completion 特判。

### 第 3 轮：像素/VLM 权威 gate

预算：最多 5 个生产文件，目标净删或持平。

- 关闭自动 semantic grounding assist、targeted OCR 控制推断和默认 UIA invoke。
- 引入同一现有模块内的小型 evidence gate，不创建第二套 perception manager。
- 所有 UIA/OCR 使用都必须带 frame、epoch、pixel confirmation 和 provenance。
- 删除 UIA/OCR rich 导致跳过视觉模型的逻辑。

### 第 4 轮：删除特殊 stage/adapter

预算：每次只迁移一个调用面，单轮不超过 8 个生产文件或 500 行触碰。

- 将 DrawArtifact 的 pointer/region/verify 下沉到通用 action transaction。
- stage 只描述 artifact obligation，不拥有鼠标实现。
- 删除 QQ 固定布局和应用专属 fallback。
- 依据真实客户端 trace 删除未命中的 direct/fast/subgoal/interaction 平行路线，最终只保留一个 planner 和一个 executor owner。

## 变更准入门

以后任何 GUI 能力改动都必须在 PR/交付报告中回答：

1. 缺失的通用原语或闭环是什么？
2. 真实客户端 trace 是否进入这条代码？
3. 是否跨至少三种结构不同的 UI/任务验证？
4. 是否有“不该点击、不该完成、不该相信 UIA/OCR”的负例？
5. 生产新增/删除行数是多少？删除了哪条旧路径？
6. 是否新增第二套状态机、guard、ledger、route 或 prompt 规则？如果是，默认拒绝。
7. E2E 是否从 Electron 可见入口提交、冷启动隔离、准确绑定 run，并使用独立结果 oracle？

## 全量文件清单说明

下方附录覆盖交接文档规定的全部核心源文件。`静态状态` 只说明入口/import/测试关系，不宣称动态未引用文件就是死代码；删除前仍需用真实客户端 trace 复核。

## 附录：核心源码全量静态清单

范围：`python/winpilot_home/winpilot`、`src`、`scripts` 中所有 Python/TypeScript/JavaScript 源文件。LOC 为本次审查时物理行数；Git 状态只表示工作树状态，不代表代码质量或运行时可达性。

| 文件 | LOC | Git 状态 | 静态职责 |
|---|---:|---|---|
| `python/winpilot_home/winpilot/__init__.py` | 3 | clean | package support / utility |
| `python/winpilot_home/winpilot/action/__init__.py` | 0 | clean | native action, transaction, or verification |
| `python/winpilot_home/winpilot/action/desktop_queue.py` | 517 | clean | native action, transaction, or verification |
| `python/winpilot_home/winpilot/action/executor.py` | 1774 | M | native action, transaction, or verification |
| `python/winpilot_home/winpilot/action/raw_device.py` | 42 | clean | native action, transaction, or verification |
| `python/winpilot_home/winpilot/action/safety.py` | 88 | clean | native action, transaction, or verification |
| `python/winpilot_home/winpilot/action/shell.py` | 251 | clean | native action, transaction, or verification |
| `python/winpilot_home/winpilot/action/verify.py` | 818 | M | native action, transaction, or verification |
| `python/winpilot_home/winpilot/agent/__init__.py` | 0 | clean | agent loop, tools, policy, or completion |
| `python/winpilot_home/winpilot/agent/coding.py` | 22 | clean | agent loop, tools, policy, or completion |
| `python/winpilot_home/winpilot/agent/coding_tools.py` | 382 | clean | agent loop, tools, policy, or completion |
| `python/winpilot_home/winpilot/agent/context_guidance.py` | 177 | M | agent loop, tools, policy, or completion |
| `python/winpilot_home/winpilot/agent/desktop_tools.py` | 1030 | M | agent loop, tools, policy, or completion |
| `python/winpilot_home/winpilot/agent/done_verifier.py` | 2501 | clean | agent loop, tools, policy, or completion |
| `python/winpilot_home/winpilot/agent/failure_classifier.py` | 844 | clean | agent loop, tools, policy, or completion |
| `python/winpilot_home/winpilot/agent/failure_recovery.py` | 425 | clean | agent loop, tools, policy, or completion |
| `python/winpilot_home/winpilot/agent/fast_tools.py` | 412 | M | agent loop, tools, policy, or completion |
| `python/winpilot_home/winpilot/agent/file_tools.py` | 134 | clean | agent loop, tools, policy, or completion |
| `python/winpilot_home/winpilot/agent/goal_state.py` | 649 | ?? | agent loop, tools, policy, or completion |
| `python/winpilot_home/winpilot/agent/graphical_affordances.py` | 232 | ?? | agent loop, tools, policy, or completion |
| `python/winpilot_home/winpilot/agent/graphical_toggle_ledger.py` | 402 | ?? | agent loop, tools, policy, or completion |
| `python/winpilot_home/winpilot/agent/gui_plan_helpers.py` | 132 | M | agent loop, tools, policy, or completion |
| `python/winpilot_home/winpilot/agent/interaction_tools.py` | 1734 | M | agent loop, tools, policy, or completion |
| `python/winpilot_home/winpilot/agent/loop.py` | 12344 | M | agent loop, tools, policy, or completion |
| `python/winpilot_home/winpilot/agent/mcp_tools.py` | 27 | ?? | agent loop, tools, policy, or completion |
| `python/winpilot_home/winpilot/agent/memory_tools.py` | 215 | clean | agent loop, tools, policy, or completion |
| `python/winpilot_home/winpilot/agent/partial_output_guard.py` | 914 | clean | agent loop, tools, policy, or completion |
| `python/winpilot_home/winpilot/agent/perception_tools.py` | 1133 | clean | agent loop, tools, policy, or completion |
| `python/winpilot_home/winpilot/agent/plan_tools.py` | 573 | clean | agent loop, tools, policy, or completion |
| `python/winpilot_home/winpilot/agent/planner.py` | 593 | M | agent loop, tools, policy, or completion |
| `python/winpilot_home/winpilot/agent/playbooks.py` | 160 | clean | agent loop, tools, policy, or completion |
| `python/winpilot_home/winpilot/agent/prompts.py` | 200 | M | agent loop, tools, policy, or completion |
| `python/winpilot_home/winpilot/agent/reflection.py` | 58 | clean | agent loop, tools, policy, or completion |
| `python/winpilot_home/winpilot/agent/router.py` | 39 | clean | agent loop, tools, policy, or completion |
| `python/winpilot_home/winpilot/agent/run_state.py` | 279 | clean | agent loop, tools, policy, or completion |
| `python/winpilot_home/winpilot/agent/runtime_profile.py` | 303 | M | agent loop, tools, policy, or completion |
| `python/winpilot_home/winpilot/agent/scene_coverage.py` | 757 | ?? | agent loop, tools, policy, or completion |
| `python/winpilot_home/winpilot/agent/scope_guard.py` | 449 | M | agent loop, tools, policy, or completion |
| `python/winpilot_home/winpilot/agent/semantic_progress.py` | 743 | clean | agent loop, tools, policy, or completion |
| `python/winpilot_home/winpilot/agent/session.py` | 133 | M | agent loop, tools, policy, or completion |
| `python/winpilot_home/winpilot/agent/system_tools.py` | 1073 | M | agent loop, tools, policy, or completion |
| `python/winpilot_home/winpilot/agent/task_rules.py` | 733 | clean | agent loop, tools, policy, or completion |
| `python/winpilot_home/winpilot/agent/tool_result.py` | 19 | clean | agent loop, tools, policy, or completion |
| `python/winpilot_home/winpilot/agent/tool_schemas.py` | 1730 | M | agent loop, tools, policy, or completion |
| `python/winpilot_home/winpilot/agent/tools.py` | 1661 | M | agent loop, tools, policy, or completion |
| `python/winpilot_home/winpilot/agent/window_tools.py` | 304 | M | agent loop, tools, policy, or completion |
| `python/winpilot_home/winpilot/agent/working_set_tools.py` | 116 | clean | agent loop, tools, policy, or completion |
| `python/winpilot_home/winpilot/automation_ir.py` | 324 | clean | package support / utility |
| `python/winpilot_home/winpilot/config.py` | 204 | clean | package support / utility |
| `python/winpilot_home/winpilot/eval/__init__.py` | 1 | clean | evaluation support |
| `python/winpilot_home/winpilot/eval/__main__.py` | 23 | clean | evaluation support |
| `python/winpilot_home/winpilot/eval/runner.py` | 145 | clean | evaluation support |
| `python/winpilot_home/winpilot/eval/summary.py` | 803 | clean | evaluation support |
| `python/winpilot_home/winpilot/eval/tasks.py` | 111 | clean | evaluation support |
| `python/winpilot_home/winpilot/fast/__init__.py` | 6 | clean | fast controller, subgoal, or action contract |
| `python/winpilot_home/winpilot/fast/action_contract.py` | 378 | M | fast controller, subgoal, or action contract |
| `python/winpilot_home/winpilot/fast/controller.py` | 3944 | M | fast controller, subgoal, or action contract |
| `python/winpilot_home/winpilot/fast/element_cache.py` | 396 | clean | fast controller, subgoal, or action contract |
| `python/winpilot_home/winpilot/fast/input_locator.py` | 606 | clean | fast controller, subgoal, or action contract |
| `python/winpilot_home/winpilot/fast/input_mode.py` | 175 | clean | fast controller, subgoal, or action contract |
| `python/winpilot_home/winpilot/fast/perception_daemon.py` | 579 | clean | fast controller, subgoal, or action contract |
| `python/winpilot_home/winpilot/fast/subgoal.py` | 4840 | M | fast controller, subgoal, or action contract |
| `python/winpilot_home/winpilot/fast/types.py` | 83 | clean | fast controller, subgoal, or action contract |
| `python/winpilot_home/winpilot/fast/vision.py` | 93 | clean | fast controller, subgoal, or action contract |
| `python/winpilot_home/winpilot/fast/visual_servo.py` | 442 | clean | fast controller, subgoal, or action contract |
| `python/winpilot_home/winpilot/fast/watcher.py` | 506 | clean | fast controller, subgoal, or action contract |
| `python/winpilot_home/winpilot/integrations/__init__.py` | 1 | clean | external provider integration |
| `python/winpilot_home/winpilot/integrations/chatgpt_mcp.py` | 1573 | clean | external provider integration |
| `python/winpilot_home/winpilot/integrations/mcp_client.py` | 272 | ?? | external provider integration |
| `python/winpilot_home/winpilot/memory/__init__.py` | 0 | clean | experience or skill memory |
| `python/winpilot_home/winpilot/memory/applicability.py` | 188 | clean | experience or skill memory |
| `python/winpilot_home/winpilot/memory/compiler.py` | 222 | clean | experience or skill memory |
| `python/winpilot_home/winpilot/memory/experience.py` | 804 | clean | experience or skill memory |
| `python/winpilot_home/winpilot/memory/feedback.py` | 87 | clean | experience or skill memory |
| `python/winpilot_home/winpilot/memory/migration.py` | 156 | clean | experience or skill memory |
| `python/winpilot_home/winpilot/memory/packer.py` | 141 | clean | experience or skill memory |
| `python/winpilot_home/winpilot/memory/replay_anchors.py` | 112 | clean | experience or skill memory |
| `python/winpilot_home/winpilot/memory/retriever.py` | 234 | clean | experience or skill memory |
| `python/winpilot_home/winpilot/memory/runtime.py` | 40 | clean | experience or skill memory |
| `python/winpilot_home/winpilot/memory/safety.py` | 51 | clean | experience or skill memory |
| `python/winpilot_home/winpilot/memory/store.py` | 820 | clean | experience or skill memory |
| `python/winpilot_home/winpilot/memory/types.py` | 57 | clean | experience or skill memory |
| `python/winpilot_home/winpilot/model_adapter.py` | 348 | clean | package support / utility |
| `python/winpilot_home/winpilot/orchestrator/__init__.py` | 13 | clean | ARP orchestration / adapters |
| `python/winpilot_home/winpilot/orchestrator/adapters.py` | 499 | clean | ARP orchestration / adapters |
| `python/winpilot_home/winpilot/orchestrator/catalog.py` | 155 | clean | ARP orchestration / adapters |
| `python/winpilot_home/winpilot/orchestrator/contracts.py` | 298 | clean | ARP orchestration / adapters |
| `python/winpilot_home/winpilot/orchestrator/evidence.py` | 93 | clean | ARP orchestration / adapters |
| `python/winpilot_home/winpilot/orchestrator/graph_compiler.py` | 367 | M | ARP orchestration / adapters |
| `python/winpilot_home/winpilot/orchestrator/projection.py` | 794 | clean | ARP orchestration / adapters |
| `python/winpilot_home/winpilot/orchestrator/runtime.py` | 944 | clean | ARP orchestration / adapters |
| `python/winpilot_home/winpilot/perception/__init__.py` | 0 | clean | capture, UIA/OCR/CV/VLM perception |
| `python/winpilot_home/winpilot/perception/app_aliases.py` | 58 | clean | capture, UIA/OCR/CV/VLM perception |
| `python/winpilot_home/winpilot/perception/apps.py` | 354 | M | capture, UIA/OCR/CV/VLM perception |
| `python/winpilot_home/winpilot/perception/fusion.py` | 701 | M | capture, UIA/OCR/CV/VLM perception |
| `python/winpilot_home/winpilot/perception/groundtruth.py` | 142 | clean | capture, UIA/OCR/CV/VLM perception |
| `python/winpilot_home/winpilot/perception/incremental.py` | 125 | clean | capture, UIA/OCR/CV/VLM perception |
| `python/winpilot_home/winpilot/perception/intent.py` | 96 | clean | capture, UIA/OCR/CV/VLM perception |
| `python/winpilot_home/winpilot/perception/model.py` | 61 | clean | capture, UIA/OCR/CV/VLM perception |
| `python/winpilot_home/winpilot/perception/ocr.py` | 486 | clean | capture, UIA/OCR/CV/VLM perception |
| `python/winpilot_home/winpilot/perception/ocr_lines.py` | 55 | clean | capture, UIA/OCR/CV/VLM perception |
| `python/winpilot_home/winpilot/perception/reducer.py` | 481 | clean | capture, UIA/OCR/CV/VLM perception |
| `python/winpilot_home/winpilot/perception/search.py` | 359 | clean | capture, UIA/OCR/CV/VLM perception |
| `python/winpilot_home/winpilot/perception/surface_binding.py` | 383 | clean | capture, UIA/OCR/CV/VLM perception |
| `python/winpilot_home/winpilot/perception/tray.py` | 158 | clean | capture, UIA/OCR/CV/VLM perception |
| `python/winpilot_home/winpilot/perception/uia.py` | 312 | clean | capture, UIA/OCR/CV/VLM perception |
| `python/winpilot_home/winpilot/perception/vision_cv.py` | 603 | clean | capture, UIA/OCR/CV/VLM perception |
| `python/winpilot_home/winpilot/perception/vlm.py` | 508 | M | capture, UIA/OCR/CV/VLM perception |
| `python/winpilot_home/winpilot/perception/web_search.py` | 168 | clean | capture, UIA/OCR/CV/VLM perception |
| `python/winpilot_home/winpilot/perception/win32.py` | 258 | clean | capture, UIA/OCR/CV/VLM perception |
| `python/winpilot_home/winpilot/perception/world.py` | 305 | clean | capture, UIA/OCR/CV/VLM perception |
| `python/winpilot_home/winpilot/recorder/__init__.py` | 0 | clean | trace / replay recorder |
| `python/winpilot_home/winpilot/recorder/apprentice.py` | 142 | clean | trace / replay recorder |
| `python/winpilot_home/winpilot/recorder/human.py` | 245 | clean | trace / replay recorder |
| `python/winpilot_home/winpilot/recorder/replay_control.py` | 157 | clean | trace / replay recorder |
| `python/winpilot_home/winpilot/recorder/trace.py` | 2476 | clean | trace / replay recorder |
| `python/winpilot_home/winpilot/runtime_info.py` | 159 | clean | package support / utility |
| `python/winpilot_home/winpilot/runtime_jobs.py` | 193 | clean | package support / utility |
| `python/winpilot_home/winpilot/scheduler.py` | 295 | clean | package support / utility |
| `python/winpilot_home/winpilot/server.py` | 1720 | M | Python HTTP product entrypoint |
| `python/winpilot_home/winpilot/tasking/__init__.py` | 16 | clean | intent, contract, stage, or interaction runtime |
| `python/winpilot_home/winpilot/tasking/adapters.py` | 1435 | M | intent, contract, stage, or interaction runtime |
| `python/winpilot_home/winpilot/tasking/artifacts.py` | 758 | M | intent, contract, stage, or interaction runtime |
| `python/winpilot_home/winpilot/tasking/capability.py` | 569 | M | intent, contract, stage, or interaction runtime |
| `python/winpilot_home/winpilot/tasking/compiler.py` | 1035 | M | intent, contract, stage, or interaction runtime |
| `python/winpilot_home/winpilot/tasking/completion.py` | 294 | M | intent, contract, stage, or interaction runtime |
| `python/winpilot_home/winpilot/tasking/contracts.py` | 248 | M | intent, contract, stage, or interaction runtime |
| `python/winpilot_home/winpilot/tasking/conversation.py` | 74 | ?? | intent, contract, stage, or interaction runtime |
| `python/winpilot_home/winpilot/tasking/evidence.py` | 68 | clean | intent, contract, stage, or interaction runtime |
| `python/winpilot_home/winpilot/tasking/execution_context.py` | 197 | clean | intent, contract, stage, or interaction runtime |
| `python/winpilot_home/winpilot/tasking/intent.py` | 1259 | M | intent, contract, stage, or interaction runtime |
| `python/winpilot_home/winpilot/tasking/interaction.py` | 3390 | M | intent, contract, stage, or interaction runtime |
| `python/winpilot_home/winpilot/tasking/interaction_controller.py` | 287 | clean | intent, contract, stage, or interaction runtime |
| `python/winpilot_home/winpilot/tasking/interaction_stage_runtime.py` | 3833 | M | intent, contract, stage, or interaction runtime |
| `python/winpilot_home/winpilot/tasking/message_content.py` | 158 | clean | intent, contract, stage, or interaction runtime |
| `python/winpilot_home/winpilot/tasking/recovery.py` | 56 | clean | intent, contract, stage, or interaction runtime |
| `python/winpilot_home/winpilot/tasking/stage_runtime.py` | 1488 | M | intent, contract, stage, or interaction runtime |
| `python/winpilot_home/winpilot/tasking/visual_appearance.py` | 262 | clean | intent, contract, stage, or interaction runtime |
| `python/winpilot_home/winpilot/tasking/visual_effects.py` | 790 | clean | intent, contract, stage, or interaction runtime |
| `python/winpilot_home/winpilot/tasking/visual_geometry.py` | 1533 | clean | intent, contract, stage, or interaction runtime |
| `python/winpilot_home/winpilot/tasking/visual_path.py` | 613 | clean | intent, contract, stage, or interaction runtime |
| `python/winpilot_home/winpilot/tasking/visual_regions.py` | 1838 | clean | intent, contract, stage, or interaction runtime |
| `python/winpilot_home/winpilot/utils/__init__.py` | 0 | clean | package support / utility |
| `python/winpilot_home/winpilot/utils/dpi.py` | 30 | clean | package support / utility |
| `python/winpilot_home/winpilot/utils/image_capture.py` | 541 | M | package support / utility |
| `python/winpilot_home/winpilot/utils/log.py` | 61 | clean | package support / utility |
| `python/winpilot_home/winpilot/utils/screenshot.py` | 105 | clean | package support / utility |
| `python/winpilot_home/winpilot/utils/trace_payload.py` | 112 | clean | package support / utility |
| `scripts/clean-artifacts.cjs` | 82 | clean | dev, integration, or E2E runner |
| `scripts/client_e2e_run.py` | 1035 | ?? | dev, integration, or E2E runner |
| `scripts/client_visual_e2e_run.py` | 527 | ?? | dev, integration, or E2E runner |
| `scripts/eval_summary.py` | 78 | clean | dev, integration, or E2E runner |
| `scripts/gui_eval_run.py` | 2105 | M | dev, integration, or E2E runner |
| `scripts/setup-claude-code.js` | 122 | clean | dev, integration, or E2E runner |
| `src/ai-interface/index.ts` | 76 | clean | AI interface package |
| `src/ai-interface/prompt/index.ts` | 118 | clean | AI interface package |
| `src/ai-interface/server/http.ts` | 249 | clean | AI interface package |
| `src/ai-interface/server/mcp.ts` | 114 | clean | AI interface package |
| `src/ai-interface/tools/ask-ai.ts` | 150 | clean | AI interface package |
| `src/ai-interface/tools/create-flow.ts` | 73 | clean | AI interface package |
| `src/ai-interface/tools/list-flows.ts` | 67 | clean | AI interface package |
| `src/ai-interface/tools/registry.ts` | 87 | clean | AI interface package |
| `src/ai-interface/tools/run-flow.ts` | 129 | clean | AI interface package |
| `src/ai-interface/tools/screen-capture.ts` | 260 | clean | AI interface package |
| `src/ai-interface/tools/types.ts` | 77 | clean | AI interface package |
| `src/cli/commands/ask.ts` | 23 | clean | CLI entrypoint |
| `src/cli/commands/create.ts` | 51 | clean | CLI entrypoint |
| `src/cli/commands/list.ts` | 74 | clean | CLI entrypoint |
| `src/cli/commands/run.ts` | 89 | clean | CLI entrypoint |
| `src/cli/commands/serve.ts` | 159 | clean | CLI entrypoint |
| `src/cli/deepseek.ts` | 107 | clean | CLI entrypoint |
| `src/cli/index.ts` | 117 | clean | CLI entrypoint |
| `src/main/api.ts` | 602 | clean | Electron main / managed backend |
| `src/main/cronMatcher.ts` | 56 | clean | Electron main / managed backend |
| `src/main/engine.ts` | 175 | clean | Electron main / managed backend |
| `src/main/env.ts` | 11 | clean | Electron main / managed backend |
| `src/main/featureCallBridge.ts` | 109 | clean | Electron main / managed backend |
| `src/main/macroPaths.ts` | 16 | clean | Electron main / managed backend |
| `src/main/main.ts` | 1446 | clean | Electron main / managed backend |
| `src/main/mcp-handler.ts` | 186 | clean | Electron main / managed backend |
| `src/main/preload.ts` | 140 | clean | Electron main / managed backend |
| `src/main/schedulerBridge.ts` | 189 | clean | Electron main / managed backend |
| `src/main/state.ts` | 33 | clean | Electron main / managed backend |
| `src/main/winpilotServer.ts` | 257 | M | Electron main / managed backend |
| `src/renderer/App.tsx` | 63 | clean | visible client UI / control plane |
| `src/renderer/components/Canvas/CanvasControls.tsx` | 231 | clean | visible client UI / control plane |
| `src/renderer/components/Canvas/EdgeLayer.tsx` | 167 | clean | visible client UI / control plane |
| `src/renderer/components/Canvas/FlowCanvas.tsx` | 418 | clean | visible client UI / control plane |
| `src/renderer/components/Canvas/FlowNode.tsx` | 449 | clean | visible client UI / control plane |
| `src/renderer/components/Canvas/SubFlowBreadcrumb.tsx` | 41 | clean | visible client UI / control plane |
| `src/renderer/components/common/AppErrorBoundary.tsx` | 32 | clean | visible client UI / control plane |
| `src/renderer/components/common/ScriptBrowser.tsx` | 124 | clean | visible client UI / control plane |
| `src/renderer/components/common/UnifiedSearchDialog.tsx` | 377 | clean | visible client UI / control plane |
| `src/renderer/components/NodeLibrary/NodeLibrary.tsx` | 433 | clean | visible client UI / control plane |
| `src/renderer/components/platform/PlatformWorkbench.tsx` | 1767 | M | visible client UI / control plane |
| `src/renderer/components/PropertyPanel/controls/WheelSelectControl.tsx` | 69 | clean | visible client UI / control plane |
| `src/renderer/components/PropertyPanel/params/ArpNodeParams.tsx` | 173 | clean | visible client UI / control plane |
| `src/renderer/components/PropertyPanel/params/CameraCaptureParams.tsx` | 90 | clean | visible client UI / control plane |
| `src/renderer/components/PropertyPanel/params/CameraRecordParams.tsx` | 90 | clean | visible client UI / control plane |
| `src/renderer/components/PropertyPanel/params/ClickParams.tsx` | 80 | clean | visible client UI / control plane |
| `src/renderer/components/PropertyPanel/params/ClickTextParams.tsx` | 154 | clean | visible client UI / control plane |
| `src/renderer/components/PropertyPanel/params/ColorCheckParams.tsx` | 74 | clean | visible client UI / control plane |
| `src/renderer/components/PropertyPanel/params/CommonNodeSettings.tsx` | 42 | clean | visible client UI / control plane |
| `src/renderer/components/PropertyPanel/params/ConditionParams.tsx` | 70 | clean | visible client UI / control plane |
| `src/renderer/components/PropertyPanel/params/ImageClickParams.tsx` | 277 | clean | visible client UI / control plane |
| `src/renderer/components/PropertyPanel/params/InputTextParams.tsx` | 77 | clean | visible client UI / control plane |
| `src/renderer/components/PropertyPanel/params/KeyboardParams.tsx` | 122 | clean | visible client UI / control plane |
| `src/renderer/components/PropertyPanel/params/LoopParams.tsx` | 39 | clean | visible client UI / control plane |
| `src/renderer/components/PropertyPanel/params/PopupCloseParams.tsx` | 44 | clean | visible client UI / control plane |
| `src/renderer/components/PropertyPanel/params/ProgressWaitParams.tsx` | 82 | clean | visible client UI / control plane |
| `src/renderer/components/PropertyPanel/params/RegionWaitParams.tsx` | 81 | clean | visible client UI / control plane |
| `src/renderer/components/PropertyPanel/params/SceneConditionParams.tsx` | 29 | clean | visible client UI / control plane |
| `src/renderer/components/PropertyPanel/params/ScreenshotParams.tsx` | 33 | clean | visible client UI / control plane |
| `src/renderer/components/PropertyPanel/params/SubScriptParams.tsx` | 61 | clean | visible client UI / control plane |
| `src/renderer/components/PropertyPanel/params/SwipeParams.tsx` | 100 | clean | visible client UI / control plane |
| `src/renderer/components/PropertyPanel/params/ToolCallParams.tsx` | 168 | clean | visible client UI / control plane |
| `src/renderer/components/PropertyPanel/params/VariableParams.tsx` | 45 | clean | visible client UI / control plane |
| `src/renderer/components/PropertyPanel/params/WaitParams.tsx` | 63 | clean | visible client UI / control plane |
| `src/renderer/components/PropertyPanel/params/WebBrowserParams.tsx` | 81 | clean | visible client UI / control plane |
| `src/renderer/components/PropertyPanel/PropertyPanel.tsx` | 965 | clean | visible client UI / control plane |
| `src/renderer/main.tsx` | 9 | clean | visible client UI / control plane |
| `src/renderer/platform/client.ts` | 437 | M | visible client UI / control plane |
| `src/renderer/platform/contracts.ts` | 705 | M | visible client UI / control plane |
| `src/renderer/platform/executionContext.ts` | 105 | M | visible client UI / control plane |
| `src/renderer/platform/flowToArpGraph.ts` | 268 | clean | visible client UI / control plane |
| `src/renderer/platform/taskbarNotification.ts` | 70 | clean | visible client UI / control plane |
| `src/renderer/platform/useControlPlane.ts` | 236 | clean | visible client UI / control plane |
| `src/renderer/store/defaults.ts` | 186 | clean | visible client UI / control plane |
| `src/renderer/store/executor.ts` | 1004 | clean | visible client UI / control plane |
| `src/renderer/store/index.ts` | 1428 | clean | visible client UI / control plane |
| `src/renderer/store/persistence.ts` | 52 | clean | visible client UI / control plane |
| `src/renderer/test/appErrorBoundary.test.tsx` | 16 | clean | test / regression evidence |
| `src/renderer/test/automationIR.test.ts` | 191 | clean | test / regression evidence |
| `src/renderer/test/constants.test.ts` | 19 | clean | test / regression evidence |
| `src/renderer/test/executionContext.test.ts` | 158 | M | test / regression evidence |
| `src/renderer/test/executor.test.ts` | 673 | clean | test / regression evidence |
| `src/renderer/test/interactionRuntimeView.test.tsx` | 373 | clean | test / regression evidence |
| `src/renderer/test/macroConverter.test.ts` | 76 | clean | test / regression evidence |
| `src/renderer/test/macroPaths.test.ts` | 15 | clean | test / regression evidence |
| `src/renderer/test/nodeDefinitions.test.ts` | 110 | clean | test / regression evidence |
| `src/renderer/test/nodeLibraryCapabilities.test.tsx` | 33 | clean | test / regression evidence |
| `src/renderer/test/platformContracts.test.ts` | 484 | M | test / regression evidence |
| `src/renderer/test/pseudoParser.test.ts` | 209 | clean | test / regression evidence |
| `src/renderer/test/runsView.test.tsx` | 174 | clean | test / regression evidence |
| `src/renderer/test/setup.ts` | 1 | clean | test / regression evidence |
| `src/renderer/test/store.test.ts` | 175 | clean | test / regression evidence |
| `src/renderer/test/taskbarNotification.test.ts` | 72 | clean | test / regression evidence |
| `src/renderer/test/templateMatcher.test.ts` | 69 | clean | test / regression evidence |
| `src/renderer/test/templateRegistry.test.ts` | 107 | clean | test / regression evidence |
| `src/renderer/test/textGenerator.test.ts` | 527 | clean | test / regression evidence |
| `src/renderer/test/winpilotServer.test.ts` | 61 | M | test / regression evidence |
| `src/renderer/test/wpsToFlowScript.test.ts` | 80 | clean | test / regression evidence |
| `src/renderer/types/electron.d.ts` | 113 | clean | visible client UI / control plane |
| `src/renderer/types/index.ts` | 850 | clean | visible client UI / control plane |
| `src/renderer/utils/automationIR.ts` | 462 | clean | visible client UI / control plane |
| `src/renderer/utils/constants.ts` | 48 | clean | visible client UI / control plane |
| `src/renderer/utils/displayLabels.ts` | 54 | clean | visible client UI / control plane |
| `src/renderer/utils/itemMarker.ts` | 74 | clean | visible client UI / control plane |
| `src/renderer/utils/llmSearcher.ts` | 99 | clean | visible client UI / control plane |
| `src/renderer/utils/macroConverter.ts` | 180 | clean | visible client UI / control plane |
| `src/renderer/utils/nodeDefinitions.ts` | 917 | clean | visible client UI / control plane |
| `src/renderer/utils/nodeIconMap.ts` | 22 | clean | visible client UI / control plane |
| `src/renderer/utils/pseudoParser.ts` | 1059 | clean | visible client UI / control plane |
| `src/renderer/utils/templateMatcher.ts` | 38 | clean | visible client UI / control plane |
| `src/renderer/utils/templateRegistry.ts` | 155 | clean | visible client UI / control plane |
| `src/renderer/utils/textGenerator.ts` | 850 | clean | visible client UI / control plane |
| `src/renderer/utils/unifiedSearch.ts` | 208 | clean | visible client UI / control plane |
| `src/renderer/utils/wpsToFlowScript.ts` | 118 | clean | visible client UI / control plane |
| `src/renderer/vite-env.d.ts` | 1 | clean | visible client UI / control plane |
